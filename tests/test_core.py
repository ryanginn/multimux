import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multimux import bands, config, odr  # noqa: E402


class PlanTests(unittest.TestCase):
    def test_original_layout(self):
        # Matches the original hand-written script: 12A/12C/12D at 8.192 MS/s
        p = bands.make_plan(["12A", "12C", "12D"])
        self.assertEqual(p["center_hz"], 226_504_000)
        self.assertEqual(p["sample_rate"], 8_192_000)
        self.assertEqual(p["offsets"], {"12A": -2_568_000, "12C": 856_000, "12D": 2_568_000})

    def test_all_four_blocks_avoid_lo(self):
        p = bands.make_plan(["12A", "12B", "12C", "12D"])
        for f in p["freqs"].values():
            self.assertGreater(abs(f - p["center_hz"]), bands.DAB_HALF_BW)

    def test_lo_moved_off_middle_block(self):
        p = bands.make_plan(["12A", "12B", "12C"])  # midpoint is 12B itself
        self.assertNotEqual(p["center_hz"], bands.BAND_III["12B"])
        for f in p["freqs"].values():
            self.assertGreater(abs(f - p["center_hz"]), bands.DAB_HALF_BW)

    def test_single_block(self):
        p = bands.make_plan(["11D"])
        self.assertNotEqual(p["center_hz"], bands.BAND_III["11D"])

    def test_errors(self):
        for bad in ([], ["12A", "12A"], ["12A", "12N"], ["99Z"], ["5A", "13F"]):
            with self.assertRaises(bands.PlanError):
                bands.make_plan(bad)

    def test_manual_rate_too_low(self):
        with self.assertRaises(bands.PlanError):
            bands.make_plan(["12A", "12D"], sample_rate=4_096_000)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_ports(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            cfg = config.load(path)
            self.assertEqual([c["block"] for c in cfg["channels"]], ["12A", "12C", "12D"])
            self.assertEqual(len({c["iq_port"] for c in cfg["channels"]}), 3)
            errors, _, plan = config.validate(cfg)
            self.assertEqual(errors, [])
            config.save(cfg, path)
            self.assertEqual(config.load(path), cfg)

    def test_rendered_configs(self):
        cfg = config.load("/nonexistent/x.json")
        ch = cfg["channels"][0]
        mux = odr.render_mux_config(ch)
        self.assertIn(f"zmq+tcp://*:{ch['eti_port']}", mux)
        self.assertNotIn(odr.ETI_TOKEN, mux)
        mod = odr.render_mod_config(ch)
        self.assertIn(f"listen=tcp://*:{ch['iq_port']}", mod)
        self.assertIn(f"source=tcp://localhost:{ch['eti_port']}", mod)


if __name__ == "__main__":
    unittest.main()
