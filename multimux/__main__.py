"""multimux command line.

  python3 -m multimux serve     run the web GUI + process supervisor (the service)
  python3 -m multimux doctor    check the installation
  python3 -m multimux plan      show the frequency plan for the current config
  python3 -m multimux combiner  run only the combiner in the foreground
"""

import argparse
import signal
import sys
import threading

from . import bands, config as mconfig, doctor


def cmd_serve(args):
    from .supervisor import Supervisor
    from .web import App, serve

    path = args.config
    cfg = mconfig.load(path)
    if not cfg["web"]["password"] and not args.no_password:
        cfg["web"]["password"] = mconfig.generate_password()
        print(f"Generated web password (user '{cfg['web']['username']}'): "
              f"{cfg['web']['password']}", flush=True)
    try:
        mconfig.save(cfg, path)
    except OSError as e:
        print(f"Warning: cannot write config {path}: {e}", flush=True)

    sup = Supervisor(cfg, path, mconfig.state_dir())
    app = App(cfg, sup, path)
    httpd = serve(app)
    print(f"multimux {app.version} - web GUI on http://{cfg['web']['host']}:"
          f"{cfg['web']['port']}/", flush=True)

    errors, _, _ = mconfig.validate(cfg)
    if cfg.get("autostart") and not errors:
        threading.Thread(target=sup.start_all, daemon=True).start()
    elif errors:
        print("Not autostarting, config errors: " + "; ".join(errors), flush=True)

    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    while not stop.is_set() and not app.restart_requested.is_set():
        stop.wait(0.5)
    print("Shutting down", flush=True)
    sup.stop_all()
    httpd.shutdown()
    # Exit non-zero on a requested restart so systemd (Restart=always) brings us back
    return 3 if app.restart_requested.is_set() else 0


def cmd_doctor(args):
    ok = doctor.print_report(doctor.run(mconfig.load(args.config), check_ports=args.ports))
    return 0 if ok else 1


def cmd_plan(args):
    cfg = mconfig.load(args.config)
    hw = cfg["hackrf"]
    try:
        plan = bands.make_plan([c["block"] for c in cfg["channels"] if c["enabled"]],
                               hw["center_freq"], hw["sample_rate"])
    except bands.PlanError as e:
        print("Error:", e)
        return 1
    print(f"Centre {plan['center_hz']/1e6:.3f} MHz, sample rate {plan['sample_rate']/1e6:.3f} MS/s")
    for b, off in plan["offsets"].items():
        print(f"  {b:>4} {plan['freqs'][b]/1e6:.3f} MHz  offset {off/1e6:+.3f} MHz")
    for w in plan["warnings"]:
        print("Warning:", w)
    return 0


def cmd_combiner(args):
    from . import combiner
    return combiner.main(["--config", args.config])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="multimux")
    ap.add_argument("--config", default=mconfig.config_path())
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--no-password", action="store_true", help="disable web login")
    d = sub.add_parser("doctor")
    d.add_argument("--ports", action="store_true", help="also check ports are free")
    sub.add_parser("plan")
    sub.add_parser("combiner")
    args = ap.parse_args(argv)
    return {"serve": cmd_serve, "doctor": cmd_doctor, "plan": cmd_plan,
            "combiner": cmd_combiner}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
