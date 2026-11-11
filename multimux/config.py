"""Configuration loading, defaults, validation and atomic saving."""

import copy
import json
import os
import secrets
import tempfile

from . import bands, odr

DEFAULT_CONFIG_PATH = "/etc/multimux/config.json"
DEFAULT_STATE_DIR = "/var/lib/multimux"


def config_path():
    return os.environ.get("MULTIMUX_CONFIG", DEFAULT_CONFIG_PATH)


def state_dir():
    return os.environ.get("MULTIMUX_STATE", DEFAULT_STATE_DIR)


DEFAULTS = {
    "autostart": True,
    "web": {"host": "0.0.0.0", "port": 8080, "username": "admin", "password": ""},
    "update": {"remote": "origin", "branch": "main"},
    "hackrf": {
        "device": "hackrf=0",
        "tx_gain": 30,           # TX VGA, 0-47 dB
        "amp": False,            # +14 dB RF amp
        "center_freq": "auto",
        "sample_rate": "auto",
        "bandwidth": "auto",     # baseband filter, auto = sample rate
        "master_scale": "auto",  # auto = 1/number_of_channels
        "ppm": 0,
    },
    "binaries": {
        "odr_dabmod": "odr-dabmod",
        "python": "/usr/bin/python3",
    },
    "channels": [],
}

CHANNEL_DEFAULTS = {
    "block": "12A",
    "enabled": True,
    "input_type": "zmq",     # see odr.INPUT_TYPES
    "source": "",            # e.g. tcp://192.168.1.10:9100
    "gain": 1.0,             # combiner gain for this channel
    "mod_digital_gain": 0.8,
    "iq_port": None,         # local port between ODR-DabMod and the combiner
}
OBSOLETE_CHANNEL_KEYS = ("manage_mux", "manage_mod", "eti_port", "mux_config", "extra_processes")


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def new_channel(cfg, block):
    ch = copy.deepcopy(CHANNEL_DEFAULTS)
    ch["block"] = block
    cfg["channels"].append(ch)
    normalize(cfg)
    return cfg["channels"][-1]


def normalize(cfg):
    """Fill in defaults and assign stable ports. Mutates and returns cfg."""
    merged = _merge(DEFAULTS, cfg)
    cfg.clear()
    cfg.update(merged)
    chans = []
    for raw in cfg.get("channels") or []:
        ch = _merge(CHANNEL_DEFAULTS, raw)
        for k in OBSOLETE_CHANNEL_KEYS:
            ch.pop(k, None)
        ch["block"] = str(ch["block"]).strip().upper()
        ch["source"] = str(ch["source"] or "").strip()
        chans.append(ch)
    cfg["binaries"].pop("odr_dabmux", None)
    used_iq = {c["iq_port"] for c in chans if c["iq_port"]}
    for idx, ch in enumerate(chans):
        if not ch["iq_port"]:
            p = 54001
            while p in used_iq:
                p += 1
            ch["iq_port"] = p
            used_iq.add(p)
    cfg["channels"] = chans
    return cfg


def validate(cfg):
    """Return (errors, warnings, plan_or_None)."""
    errors, warnings = [], []
    enabled = [c for c in cfg["channels"] if c.get("enabled")]
    plan = None
    try:
        plan = bands.make_plan([c["block"] for c in enabled],
                               cfg["hackrf"]["center_freq"], cfg["hackrf"]["sample_rate"])
        warnings += plan["warnings"]
    except bands.PlanError as e:
        errors.append(str(e))
    ports = []
    for c in enabled:
        if c["input_type"] not in odr.INPUT_TYPES:
            errors.append(f"{c['block']}: unknown input type '{c['input_type']}'")
            continue
        if not c["source"]:
            errors.append(f"{c['block']}: no input feed set")
        elif c["input_type"] != "eti_file" and not c["source"].startswith(("tcp://", "udp://")):
            errors.append(f"{c['block']}: feed should look like tcp://host:port or udp://:port")
        if odr.runs_modulator(c):
            ports.append(c["iq_port"])
    if len(set(ports)) != len(ports):
        errors.append("IQ port numbers clash between channels")
    g = cfg["hackrf"]["tx_gain"]
    if not isinstance(g, (int, float)) or not 0 <= g <= 47:
        errors.append("TX gain must be between 0 and 47 dB")
    return errors, warnings, plan


def load(path=None):
    path = path or config_path()
    try:
        with open(path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    normalize(cfg)
    if not cfg["channels"]:
        for i, b in enumerate(("12A", "12C", "12D")):
            new_channel(cfg, b)["source"] = f"tcp://localhost:{9100 + i}"
    return cfg


def save(cfg, path=None):
    path = path or config_path()
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".config-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def generate_password():
    return secrets.token_urlsafe(12)
