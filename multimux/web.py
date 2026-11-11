"""Web GUI + JSON API (standard library only, nothing to pip install)."""

import base64
import copy
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import bands, config as mconfig, doctor, odr, updater

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class App:
    def __init__(self, cfg, supervisor, config_path):
        self.cfg = cfg
        self.sup = supervisor
        self.config_path = config_path
        self.lock = threading.Lock()
        self.version = updater.version()
        self.started = time.time()
        self.restart_requested = threading.Event()

    def public_config(self):
        c = copy.deepcopy(self.cfg)
        c["web"]["password"] = "********" if c["web"]["password"] else ""
        return c


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "multimux"

        def log_message(self, fmt, *args):
            pass

        # ---- helpers -------------------------------------------------
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self):
            pw = app.cfg["web"].get("password")
            if not pw:
                return True
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    user, _, given = base64.b64decode(h[6:]).decode().partition(":")
                except Exception:
                    return False
                return (hmac.compare_digest(user, app.cfg["web"].get("username", "admin"))
                        and hmac.compare_digest(given, pw))
            return False

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def _guard(self, write=False):
            if not self._authorised():
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="multimux"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            # Block cross-site form posts: browsers can't add custom headers cross-origin.
            if write and self.headers.get("X-Multimux") != "1":
                self._send(403, {"error": "missing X-Multimux header"})
                return False
            return True

        # ---- routes --------------------------------------------------
        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            p, q = url.path, parse_qs(url.query)
            if p in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if p == "/api/status":
                errors, warnings, plan = mconfig.validate(app.cfg)
                return self._send(200, {
                    **app.sup.status(), "plan": plan, "errors": errors, "warnings": warnings,
                    "version": app.version, "service_uptime": time.time() - app.started})
            if p == "/api/config":
                return self._send(200, app.public_config())
            if p == "/api/blocks":
                return self._send(200, bands.BAND_III)
            if p == "/api/template":
                block = q.get("block", ["12A"])[0].upper()
                idx = int(q.get("index", ["0"])[0])
                return self._send(200, {"mux_config": odr.default_mux_config(block, idx)})
            if p.startswith("/api/logs/"):
                proc = app.sup.get(p[len("/api/logs/"):])
                if not proc:
                    return self._send(404, {"error": "no such process"})
                since = int(q.get("since", ["0"])[0])
                return self._send(200, {"lines": proc.logs(since)})
            if p.startswith("/api/files/"):
                # show the generated config files for a channel
                name = os.path.basename(p[len("/api/files/"):])
                path = os.path.join(app.sup.run_dir, name)
                if not os.path.isfile(path):
                    return self._send(404, {"error": "not generated yet"})
                with open(path, "rb") as f:
                    return self._send(200, f.read(), "text/plain; charset=utf-8")
            if p == "/api/doctor":
                return self._send(200, doctor.run(app.cfg, check_ports=not app.sup.running))
            if p == "/api/update/check":
                u = app.cfg["update"]
                return self._send(200, updater.check(u["remote"], u["branch"]))
            self._send(404, {"error": "not found"})

        def do_PUT(self):
            if not self._guard(write=True):
                return
            if urlparse(self.path).path != "/api/config":
                return self._send(404, {"error": "not found"})
            try:
                new = self._body()
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            with app.lock:
                # keep the existing password unless a new one is supplied
                pw = (new.get("web") or {}).get("password")
                if pw in (None, "********"):
                    new.setdefault("web", {})["password"] = app.cfg["web"]["password"]
                try:
                    mconfig.normalize(new)
                except Exception as e:
                    return self._send(400, {"error": f"bad config: {e}"})
                errors, warnings, _ = mconfig.validate(new)
                if errors:
                    return self._send(400, {"error": "; ".join(errors)})
                mconfig.save(new, app.config_path)
                app.cfg = new
                app.sup.apply(new)
            self._send(200, {"ok": True, "warnings": warnings})

        def do_POST(self):
            if not self._guard(write=True):
                return
            p = urlparse(self.path).path
            if p == "/api/start":
                threading.Thread(target=app.sup.start_all, daemon=True).start()
                return self._send(200, {"ok": True})
            if p == "/api/stop":
                app.sup.stop_all()
                return self._send(200, {"ok": True})
            if p == "/api/restart":
                threading.Thread(target=app.sup.start_all, daemon=True).start()
                return self._send(200, {"ok": True})
            if p.startswith("/api/process/"):
                name, _, action = p[len("/api/process/"):].rpartition("/")
                proc = app.sup.get(name)
                if not proc or action not in ("start", "stop", "restart"):
                    return self._send(404, {"error": "unknown process or action"})
                threading.Thread(target=getattr(proc, action), daemon=True).start()
                return self._send(200, {"ok": True})
            if p == "/api/update/apply":
                u = app.cfg["update"]
                res = updater.apply(u["remote"], u["branch"])
                self._send(200, res)
                if res.get("ok") and res.get("updated"):
                    app.restart_requested.set()
                return
            if p == "/api/service/restart":
                self._send(200, {"ok": True})
                app.restart_requested.set()
                return
            self._send(404, {"error": "not found"})

    return Handler


def serve(app):
    w = app.cfg["web"]
    httpd = ThreadingHTTPServer((w["host"], int(w["port"])), make_handler(app))
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd
