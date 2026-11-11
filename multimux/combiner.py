#!/usr/bin/env python3
"""Combine several ODR-DabMod baseband streams into one HackRF transmission.

Run by the multimux service, or by hand:
    python3 -m multimux.combiner --config /etc/multimux/config.json

Each enabled channel's ODR-DabMod publishes complex float32 IQ at 2.048 MS/s
on a ZMQ PUB socket. Each stream is upsampled, frequency shifted to its slot,
summed and sent to the HackRF. Level statistics are printed as
'@@STATS {json}' lines that the web GUI picks up.
"""

import argparse
import json
import signal
import sys
import threading
import time

import numpy as np
from gnuradio import gr, blocks, zeromq
from gnuradio import filter as grfilter
from gnuradio.filter import firdes
import osmosdr

from multimux import bands, config as mconfig


class LevelProbe(gr.sync_block):
    """Pass-through block that reports sample flow, RMS and peak levels."""

    def __init__(self, name, sample_rate, interval=2.0):
        gr.sync_block.__init__(self, name=f"probe_{name}",
                               in_sig=[np.complex64], out_sig=[np.complex64])
        self.label = name
        self.sample_rate = sample_rate
        self.interval = interval
        self._reset()

    def _reset(self):
        self.count = 0
        self.peak = 0.0
        self.power = 0.0
        self.clipped = 0
        self.t0 = time.monotonic()

    def work(self, input_items, output_items):
        x = input_items[0]
        output_items[0][:] = x
        if len(x):
            # Clipping on HackRF happens per I/Q component, not on magnitude
            comp = np.maximum(np.abs(x.real), np.abs(x.imag))
            self.peak = max(self.peak, float(comp.max()))
            self.clipped += int(np.count_nonzero(comp >= 1.0))
            self.power += float(np.vdot(x, x).real)
            self.count += len(x)
        now = time.monotonic()
        if now - self.t0 >= self.interval:
            dt = now - self.t0
            rms = (self.power / self.count) ** 0.5 if self.count else 0.0
            stats = {
                "probe": self.label,
                "rate": self.count / dt,
                "expected_rate": self.sample_rate,
                "rms": rms,
                "peak": self.peak,
                "clip_pct": 100.0 * self.clipped / self.count if self.count else 0.0,
                "time": time.time(),
            }
            print("@@STATS " + json.dumps(stats), flush=True)
            self._reset()
        return len(x)


def zmq_source(endpoint):
    try:  # GNU Radio 3.10 signature (with key)
        return zeromq.sub_source(gr.sizeof_gr_complex, 1, endpoint, 100, False, -1, "")
    except TypeError:  # GNU Radio 3.9
        return zeromq.sub_source(gr.sizeof_gr_complex, 1, endpoint, 100, False, -1)


class Combiner(gr.top_block):
    def __init__(self, cfg):
        gr.top_block.__init__(self, "multimux combiner")
        hw = cfg["hackrf"]
        chans = [c for c in cfg["channels"] if c.get("enabled")]
        plan = bands.make_plan([c["block"] for c in chans],
                               hw["center_freq"], hw["sample_rate"])
        self.plan = plan
        fs = plan["sample_rate"]
        interp = plan["interp"]

        # Low-pass keeps the 1.536 MHz ensemble, kills interpolation images.
        taps = firdes.low_pass(interp, fs, 800_000, 200_000)

        adder = blocks.add_cc()
        for i, ch in enumerate(chans):
            endpoint = f"tcp://127.0.0.1:{ch['iq_port']}"
            src = zmq_source(endpoint)
            probe = LevelProbe(ch["block"], bands.BASE_RATE)
            resamp = grfilter.interp_fir_filter_ccf(interp, taps)
            rot = blocks.rotator_cc(bands.phase_increment(plan["offsets"][ch["block"]], fs))
            gain = blocks.multiply_const_cc(float(ch.get("gain", 1.0)))
            self.connect(src, probe, resamp, rot, gain, (adder, i))

        scale = hw["master_scale"]
        scale = 1.0 / len(chans) if scale in (None, "", "auto") else float(scale)
        master = blocks.multiply_const_cc(scale)
        out_probe = LevelProbe("output", fs)

        self.sink = osmosdr.sink(args=hw["device"])
        self.sink.set_sample_rate(fs)
        self.sink.set_center_freq(plan["center_hz"])
        self.sink.set_freq_corr(float(hw.get("ppm", 0)))
        self.sink.set_gain(14 if hw.get("amp") else 0, "RF")
        self.sink.set_gain(float(hw["tx_gain"]), "IF")
        bw = hw["bandwidth"]
        self.sink.set_bandwidth(fs if bw in (None, "", "auto") else float(bw))

        self.connect(adder, master, out_probe, self.sink)

        print(f"Centre {plan['center_hz']/1e6:.3f} MHz, {fs/1e6:.3f} MS/s "
              f"(x{interp}), master scale {scale:.3f}", flush=True)
        for ch in chans:
            print(f"  {ch['block']}: tcp://127.0.0.1:{ch['iq_port']} -> "
                  f"{plan['offsets'][ch['block']]/1e6:+.3f} MHz", flush=True)
        for w in plan["warnings"]:
            print("WARNING: " + w, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=mconfig.config_path())
    args = ap.parse_args(argv)
    cfg = mconfig.load(args.config)

    tb = Combiner(cfg)
    done = threading.Event()

    def stop(*_):
        done.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    tb.start()
    print("Combiner running", flush=True)
    done.wait()
    print("Stopping combiner", flush=True)
    tb.stop()
    tb.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
