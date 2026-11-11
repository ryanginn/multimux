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

    def test_rendered_mod_configs(self):
        cfg = config.load("/nonexistent/x.json")
        ch = cfg["channels"][0]
        mod = odr.render_mod_config(ch)
        self.assertIn("transport=zeromq", mod)
        self.assertIn("source=tcp://localhost:9100", mod)
        self.assertIn(f"listen=tcp://127.0.0.1:{ch['iq_port']}", mod)
        ch.update(input_type="edi", source="udp://:12000")
        self.assertIn("transport=edi", odr.render_mod_config(ch))

    def test_remote_iq_skips_modulator(self):
        cfg = config.load("/nonexistent/x.json")
        ch = cfg["channels"][1]
        ch.update(input_type="iq", source="tcp://10.0.0.5:54001")
        self.assertFalse(odr.runs_modulator(ch))
        self.assertEqual(odr.combiner_endpoint(ch), "tcp://10.0.0.5:54001")

    def test_validation(self):
        cfg = config.load("/nonexistent/x.json")
        cfg["channels"][0]["source"] = ""
        cfg["channels"][1]["source"] = "192.168.1.1:9100"
        errors, _, _ = config.validate(cfg)
        self.assertEqual(len(errors), 2)

    def test_old_config_migrates(self):
        cfg = {"channels": [{"block": "12a", "manage_mux": True, "mux_config": "x",
                             "eti_port": 9100, "extra_processes": []}]}
        config.normalize(cfg)
        self.assertEqual(set(cfg["channels"][0]), set(config.CHANNEL_DEFAULTS))


if __name__ == "__main__":
    unittest.main()
