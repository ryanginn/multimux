"""Check that everything the chain needs is installed and working."""

import os
import shutil
import socket
import subprocess
import sys

from . import config as mconfig


def _run(argv, timeout=15):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def _check(name, ok, detail, fix=""):
    return {"name": name, "ok": bool(ok), "detail": detail, "fix": fix if not ok else ""}


def _port_free(port):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def run(cfg, check_ports=False):
    results = []
    py = cfg["binaries"]["python"]

    results.append(_check("Python 3.8+", sys.version_info >= (3, 8), sys.version.split()[0]))

    rc, out = _run([py, "-c", "from gnuradio import gr, zeromq; print(gr.version())"])
    results.append(_check("GNU Radio (with ZeroMQ)", rc == 0, out.splitlines()[-1] if out else "",
                          "sudo apt install gnuradio"))

    rc, out = _run([py, "-c", "import osmosdr; print('ok')"])
    results.append(_check("gr-osmosdr", rc == 0, out.splitlines()[-1] if out else "",
                          "sudo apt install gr-osmosdr"))

    rc, out = _run([py, "-c", "import numpy; print(numpy.__version__)"])
    results.append(_check("numpy", rc == 0, out.splitlines()[-1] if out else "",
                          "sudo apt install python3-numpy"))

    for key, pkg in (("odr_dabmux", "ODR-DabMux"), ("odr_dabmod", "ODR-DabMod")):
        b = cfg["binaries"][key]
        path = shutil.which(b)
        detail = path or "not found"
        if path:
            _, out = _run([path, "--version"], timeout=5)
            if out:
                detail = out.splitlines()[0]
        results.append(_check(pkg, path, detail,
                              "re-run install.sh, or set the path under Settings > Binaries"))

    hi = shutil.which("hackrf_info")
    if hi:
        rc, out = _run([hi], timeout=10)
        found = "Serial number" in out
        detail = "HackRF found" if found else (out.splitlines()[-1] if out else "no device")
        if "busy" in out.lower() or "resource" in out.lower():
            detail += " (it may just be in use by the combiner)"
        results.append(_check("HackRF device", found or "busy" in out.lower(), detail,
                              "Plug in the HackRF; check the service user is in the plugdev group"))
    else:
        results.append(_check("HackRF tools", False, "hackrf_info not found",
                              "sudo apt install hackrf"))

    errors, warnings, plan = mconfig.validate(cfg)
    results.append(_check("Configuration", not errors, "; ".join(errors) or "valid"))
    for w in warnings:
        results.append({"name": "Configuration warning", "ok": True, "detail": w,
                        "fix": "", "warn": True})

    if check_ports:
        busy = []
        for c in cfg["channels"]:
            if c.get("enabled"):
                busy += [p for p in (c["eti_port"], c["iq_port"]) if not _port_free(p)]
        results.append(_check("Ports free", not busy,
                              "busy: " + ", ".join(map(str, busy)) if busy else "all free",
                              "Stop other ODR processes using these ports"))

    git_ok = os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".git"))
    results.append(_check("Self-update (git checkout)", git_ok,
                          "yes" if git_ok else "not a git checkout",
                          "Install with install.sh to enable updates"))
    return results


def print_report(results):
    for r in results:
        mark = "WARN" if r.get("warn") else ("OK  " if r["ok"] else "FAIL")
        print(f"[{mark}] {r['name']}: {r['detail']}")
        if r["fix"]:
            print(f"       fix: {r['fix']}")
    return all(r["ok"] for r in results)
