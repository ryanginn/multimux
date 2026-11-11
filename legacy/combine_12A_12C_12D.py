#!/usr/bin/env python3
"""
Combine three ODR-DabMod baseband streams (blocks 12A, 12C, 12D) into a
single wideband IQ stream for transmission on one HackRF.

Prerequisites:
  - GNU Radio 3.9/3.10 with gr-osmosdr installed
  - Three odr-dabmod instances running, each with:
        [output]
        output=zmq
        [zmqoutput]
        listen=tcp://*:54001   (54002, 54003 for the others)
        socket_type=pub
    and native rate=2048000 (no internal resampling).

Layout (Band III):
    12A = 223.936 MHz  -> offset -2.568 MHz
    12C = 227.360 MHz  -> offset +0.856 MHz
    12D = 229.072 MHz  -> offset +2.568 MHz
    center = 226.504 MHz
"""

import math
from gnuradio import gr, blocks, analog, zeromq
from gnuradio import filter as grfilter
import osmosdr

BASE_RATE = 2_048_000          # native odr-dabmod output rate
INTERP = 4                     # integer upsample factor
OUT_RATE = BASE_RATE * INTERP  # 8,192,000 -> combined TX sample rate

CENTER_FREQ = 226_504_000      # HackRF tuning frequency

# (zmq endpoint, frequency offset from CENTER_FREQ in Hz)
STREAMS = [
    ("tcp://127.0.0.1:54001", -2_568_000),  # 12A
    ("tcp://127.0.0.1:54002", +856_000),    # 12C
    ("tcp://127.0.0.1:54003", +2_568_000),  # 12D
]

HACKRF_ARGS = "hackrf=0"
TX_GAIN = 30  # start conservative, raise slowly while watching for splatter


class DabCombiner(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "DAB 12A-12C-12D Combiner")

        adder = blocks.add_cc()

        for i, (endpoint, offset_hz) in enumerate(STREAMS):
            # ZMQ SUB source: connects to each odr-dabmod's PUB output.
            # odr-dabmod's zmq output sends complex float32 samples.
            src = zeromq.sub_source(gr.sizeof_gr_complex, 1, endpoint, 100, False, -1, "")

            # Integer upsample 2.048 MS/s -> 8.192 MS/s
            resamp = grfilter.rational_resampler_ccc(
                interpolation=INTERP,
                decimation=1,
                taps=[],
                fractional_bw=0,
            )

            # Frequency-shift this branch to its slot within the combined band
            shifter = analog.sig_source_c(
                OUT_RATE, analog.GR_COS_WAVE, offset_hz, 1.0, 0
            )
            mixer = blocks.multiply_cc()

            self.connect(src, resamp, (mixer, 0))
            self.connect(shifter, (mixer, 1))
            self.connect(mixer, (adder, i))

        # Scale down before TX to avoid clipping when 3 signals sum
        scale = blocks.multiply_const_cc(5 / len(STREAMS))
        self.connect(adder, scale)

        # HackRF sink via SoapySDR/osmosdr
        self.sink = osmosdr.sink(args=HACKRF_ARGS)
        self.sink.set_sample_rate(OUT_RATE)
        self.sink.set_center_freq(CENTER_FREQ)
        self.sink.set_freq_corr(0)
        self.sink.set_gain(TX_GAIN)
        self.sink.set_bandwidth(OUT_RATE)

        self.connect(scale, self.sink)


def main():
    tb = DabCombiner()
    print(f"Center freq: {CENTER_FREQ/1e6:.3f} MHz, sample rate: {OUT_RATE/1e6:.3f} MS/s")
    print("Streams (offset from center):")
    for endpoint, offset in STREAMS:
        print(f"  {endpoint}: {offset/1e6:+.3f} MHz")
    tb.start()
    try:
        input("Running. Press Enter to stop...\n")
    except KeyboardInterrupt:
        pass
    tb.stop()
    tb.wait()


if __name__ == "__main__":
    main()
