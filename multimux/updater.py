"""Self-update from the GitHub repository the service was installed from."""

import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args, timeout=60):
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"git {args[0]} failed")
    return r.stdout.strip()


def version():
    try:
        return _git("describe", "--always", "--dirty", "--tags")
    except Exception:
        return "unknown"


def check(remote="origin", branch="main"):
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return {"ok": False, "error": "Not installed from git, cannot self-update"}
    try:
        _git("fetch", "--quiet", remote, branch, timeout=120)
        local = _git("rev-parse", "HEAD")
        target = f"{remote}/{branch}"
        upstream = _git("rev-parse", target)
        log = _git("log", "--oneline", "--no-decorate", f"HEAD..{target}")
        behind = [l for l in log.splitlines() if l]
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
        return {"ok": True, "current": local[:10], "latest": upstream[:10],
                "behind": len(behind), "changes": behind[:50], "dirty": dirty,
                "branch": branch, "version": version()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def apply(remote="origin", branch="main"):
    """Fast-forward to the latest commit. Caller must restart the service."""
    st = check(remote, branch)
    if not st["ok"]:
        return st
    if st["dirty"]:
        return {"ok": False, "error": "Local files were modified in the install "
                "directory; refusing to overwrite them"}
    if st["behind"] == 0:
        return {"ok": True, "updated": False, "message": "Already up to date"}
    try:
        _git("checkout", "--quiet", branch)
        _git("merge", "--ff-only", "--quiet", f"{remote}/{branch}")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "updated": True, "version": version(),
            "message": f"Updated {st['behind']} commit(s); restarting"}
