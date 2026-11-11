"""Start, watch and automatically restart all the processes in the chain."""

import collections
import json
import os
import signal
import subprocess
import threading
import time

from . import odr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ManagedProcess:
    """One child process with auto-restart (exponential backoff) and a log buffer."""

    def __init__(self, name, kind, argv, cwd=None, env=None, on_line=None):
        self.name = name
        self.kind = kind
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.on_line = on_line
        self.log = collections.deque(maxlen=1000)
        self.log_seq = 0
        self.state = "stopped"
        self.proc = None
        self.restarts = 0
        self.last_exit = None
        self.started_at = None
        self._want = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None

    def _append(self, line):
        self.log_seq += 1
        self.log.append((self.log_seq, time.time(), line))

    def start(self):
        with self._lock:
            self._want = True
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
            self._thread.start()

    def stop(self, timeout=8):
        with self._lock:
            self._want = False
        self._wake.set()
        self._kill(timeout)
        if self._thread:
            self._thread.join(timeout + 2)
        self.state = "stopped"

    def restart(self):
        self.stop()
        self.start()

    def _kill(self, timeout):
        p = self.proc
        if not p or p.poll() is not None:
            return
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            self._append("[multimux] did not exit, sending SIGKILL")
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _run(self):
        backoff = 1
        while self._want:
            self.state = "starting"
            self._append("[multimux] starting: " + " ".join(self.argv))
            try:
                self.proc = subprocess.Popen(
                    self.argv, cwd=self.cwd, env=self.env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=True, text=True, errors="replace", bufsize=1)
            except OSError as e:
                self._append(f"[multimux] failed to start: {e}")
                self.last_exit = str(e)
            else:
                self.started_at = time.time()
                self.state = "running"
                if not self._want:  # stop() raced with startup
                    self._kill(5)
                for line in self.proc.stdout:
                    line = line.rstrip("\n")
                    if line.startswith("@@STATS ") and self.on_line:
                        self.on_line(self, line)
                        continue
                    self._append(line)
                rc = self.proc.wait()
                self.last_exit = rc
                ran = time.time() - self.started_at
                self._append(f"[multimux] exited with code {rc} after {ran:.0f}s")
                if ran > 60:
                    backoff = 1
            if not self._want:
                break
            self.restarts += 1
            self.state = "backoff"
            self._append(f"[multimux] restarting in {backoff}s")
            self._wake.clear()
            self._wake.wait(backoff)
            backoff = min(backoff * 2, 30)
        self.state = "stopped"

    def status(self):
        running = self.state == "running"
        return {
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "pid": self.proc.pid if self.proc and running else None,
            "uptime": time.time() - self.started_at if running and self.started_at else None,
            "restarts": self.restarts,
            "last_exit": self.last_exit,
            "command": " ".join(self.argv),
        }

    def logs(self, since=0):
        return [{"seq": s, "time": t, "line": l} for s, t, l in list(self.log) if s > since]


class Supervisor:
    def __init__(self, cfg, config_path, state_dir):
        self.config_path = config_path
        self.run_dir = os.path.join(state_dir, "run")
        self.procs = collections.OrderedDict()
        self.stats = {}
        self.lock = threading.RLock()
        self.cfg = cfg
        self.running = False

    def _on_stats(self, proc, line):
        try:
            s = json.loads(line[len("@@STATS "):])
            self.stats[s["probe"]] = s
        except (ValueError, KeyError):
            pass

    def _build(self):
        cfg = self.cfg
        os.makedirs(self.run_dir, exist_ok=True)
        bins = cfg["binaries"]
        procs = collections.OrderedDict()
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["MULTIMUX_CONFIG"] = self.config_path
        enabled = [c for c in cfg["channels"] if c.get("enabled")]
        for ch in enabled:
            b = ch["block"]
            for ex in ch.get("extra_processes") or []:
                if ex.get("enabled", True) and ex.get("command"):
                    n = f"{b}-{ex['name']}"
                    procs[n] = ManagedProcess(n, "extra", ["/bin/sh", "-c", ex["command"]],
                                              cwd=self.run_dir, env=env)
            if ch.get("manage_mux"):
                path = os.path.join(self.run_dir, f"mux-{b}.mux")
                _write(path, odr.render_mux_config(ch))
                procs[f"{b}-mux"] = ManagedProcess(f"{b}-mux", "mux", [bins["odr_dabmux"], path],
                                                   cwd=self.run_dir, env=env)
            if ch.get("manage_mod"):
                path = os.path.join(self.run_dir, f"mod-{b}.ini")
                _write(path, odr.render_mod_config(ch))
                procs[f"{b}-mod"] = ManagedProcess(f"{b}-mod", "mod", [bins["odr_dabmod"], path],
                                                   cwd=self.run_dir, env=env)
        if enabled:
            procs["combiner"] = ManagedProcess(
                "combiner", "combiner",
                [bins["python"], "-u", "-m", "multimux.combiner", "--config", self.config_path],
                cwd=ROOT, env=env, on_line=self._on_stats)
        return procs

    def start_all(self):
        with self.lock:
            self.stop_all()
            self.procs = self._build()
            self.stats = {}
            # muxes/encoders first, then modulators, then the combiner
            order = ["extra", "mux", "mod", "combiner"]
            for kind in order:
                group = [p for p in self.procs.values() if p.kind == kind]
                for p in group:
                    p.start()
                if group and kind != "combiner":
                    time.sleep(1)
            self.running = True

    def stop_all(self):
        with self.lock:
            # reverse order: combiner first so the HackRF is released cleanly
            for p in reversed(list(self.procs.values())):
                p.stop()
            self.running = False

    def apply(self, cfg):
        with self.lock:
            was_running = self.running
            self.cfg = cfg
            if was_running:
                self.start_all()

    def get(self, name):
        return self.procs.get(name)

    def status(self):
        return {
            "running": self.running,
            "processes": [p.status() for p in self.procs.values()],
            "stats": self.stats,
        }


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
