"""
test_hephaestus.py — hermetic tests for tools/hephaestus.py.

No real hardware read: every psutil call is mocked, so the 1-second
cpu_percent(interval=1) sample never runs. Covers the two entry points'
different failure contracts (§2) — fetch() raises for Kairos, handle() degrades
to an error dict for Pythia — plus the happy-path shape and the disk anchor
(§1: __file__, never sys.argv[0]).

    python -X utf8 -m unittest tests.test_hephaestus
"""

import os
import sys
import time
import types
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hephaestus


@contextmanager
def _mock_psutil():
    """Patch every psutil call hephaestus makes with plausible readings.

    Yields the mock dict so a test can override one reading — set .side_effect
    to fail it, or .return_value to change it.
    """
    mem = types.SimpleNamespace(
        total=16 * 1024**3, available=8 * 1024**3, percent=50.0
    )
    disk = types.SimpleNamespace(
        total=931 * 1024**3, free=400 * 1024**3, percent=57.0
    )
    with mock.patch.multiple(
        "tools.hephaestus.psutil",
        cpu_percent=mock.DEFAULT,
        cpu_count=mock.DEFAULT,
        virtual_memory=mock.DEFAULT,
        disk_usage=mock.DEFAULT,
        boot_time=mock.DEFAULT,
    ) as m, mock.patch("tools.hephaestus.brontes.read", return_value=_GPU) as gpu:
        m["cpu_percent"].return_value = 12.5
        m["cpu_count"].return_value = 8
        m["virtual_memory"].return_value = mem
        m["disk_usage"].return_value = disk
        m["boot_time"].return_value = time.time() - 3 * 86400 - 4 * 3600
        m["gpu"] = gpu
        yield m


_GPU = {"name": "Test GPU", "temp_c": 48, "memory_used_gb": 1.7,
        "memory_total_gb": 6.0, "memory_pct": 28.3, "load_pct": 12}
# Felhaven's own drive, as hephaestus works it out.
_HERE = os.path.splitdrive(os.path.abspath(hephaestus.__file__))[0]
_HERE = _HERE + os.sep if _HERE else "/"


class TestHappyPath(unittest.TestCase):
    def test_fetch_shapes_the_vitals(self):
        with _mock_psutil():
            out = hephaestus.fetch()
        self.assertNotIn("error", out)
        self.assertEqual(out["cpu"]["usage_percent"], 12.5)
        self.assertEqual(out["cpu"]["cores"], 8)
        self.assertEqual(out["memory"]["total_gb"], 16.0)
        self.assertEqual(out["memory"]["percent_used"], 50.0)
        self.assertEqual(out["storage"]["free_gb"], 400.0)
        self.assertIn("timestamp", out)

    def test_handle_passes_the_happy_path_through(self):
        with _mock_psutil():
            self.assertEqual(hephaestus.handle()["cpu"]["usage_percent"], 12.5)


class TestFailureContracts(unittest.TestCase):
    """The two entry points read the same data and fail differently — §2.

    They were once the same object (fetch = handle), so only one of the two
    behaviours could exist at a time. Asserting both is what holds them apart:
    re-aliasing either way breaks one of these tests.
    """

    def test_handle_degrades_and_never_raises(self):
        with mock.patch("tools.hephaestus.psutil.cpu_percent",
                        side_effect=OSError("psutil is unhappy")):
            out = hephaestus.handle()          # must not raise
        # §2's shared word, then the class, never the message's text.
        self.assertEqual(out, {"error": "unavailable: vitals read raised OSError"})

    def test_handle_degrades_on_a_memory_failure_too(self):
        # Every _get_*_status() read is behind the guard, not just the first.
        with _mock_psutil() as m:
            m["virtual_memory"].side_effect = RuntimeError("no memory info")
            self.assertEqual(hephaestus.handle(),
                             {"error": "unavailable: vitals read raised RuntimeError"})

    def test_handle_degrades_on_a_storage_failure_too(self):
        with _mock_psutil() as m:
            m["disk_usage"].side_effect = OSError("no such drive")
            self.assertEqual(hephaestus.handle(),
                             {"error": "unavailable: vitals read raised OSError"})

    def test_fetch_still_raises_for_kairos(self):
        # Kairos needs the throw: it logs the cause and hands the panel None so
        # VitalsPanel holds its last state. A fetch that returned an error dict
        # would sail past Kairos and KeyError inside VitalsPanel.update().
        with mock.patch("tools.hephaestus.psutil.cpu_percent",
                        side_effect=OSError("psutil is unhappy")):
            with self.assertRaises(OSError):
                hephaestus.fetch()


class TestStorageAnchor(unittest.TestCase):
    """§1: anchor to __file__, never sys.argv[0]."""

    def test_disk_read_ignores_argv0(self):
        # A launcher on some other drive — the old bug read this one instead.
        argv0 = "Q:\\elsewhere\\felhaven.py" if os.name == "nt" else "/elsewhere/felhaven.py"
        with _mock_psutil() as m, mock.patch.object(sys, "argv", [argv0]):
            out = hephaestus.fetch()
            roots = [c.args[0] for c in m["disk_usage"].call_args_list]
        self.assertEqual(out["storage"]["drive"], _HERE)
        self.assertIn(_HERE, roots)
        self.assertFalse(any("Q:" in r for r in roots))


class TestTheNewReadings(unittest.TestCase):
    """The boot drive, uptime and the GPU (the HEPHAESTUS tab's depth)."""

    def test_the_boot_drive_is_its_own_row_when_it_differs(self):
        other = "Q:" if not _HERE.upper().startswith("Q") else "R:"
        with _mock_psutil() as m, mock.patch.dict(os.environ, {"SystemDrive": other}):
            out = hephaestus.fetch()
            roots = [c.args[0] for c in m["disk_usage"].call_args_list]
        self.assertEqual(out["boot_storage"]["drive"], other + "\\")
        self.assertIn(other + "\\", roots)
        self.assertEqual(out["storage"]["drive"], _HERE)

    def test_one_drive_is_one_row(self):
        with _mock_psutil() as m, \
                mock.patch.dict(os.environ, {"SystemDrive": _HERE.rstrip("\\/")}):
            out = hephaestus.fetch()
        self.assertIsNone(out["boot_storage"])
        self.assertEqual(m["disk_usage"].call_count, 1)

    def test_uptime_comes_from_the_boot_time(self):
        with _mock_psutil():
            out = hephaestus.fetch()
        self.assertAlmostEqual(out["uptime_s"], 3 * 86400 + 4 * 3600, delta=5)

    def test_the_gpu_reading_is_brontes(self):
        with _mock_psutil():
            out = hephaestus.fetch()
        self.assertEqual(out["gpu"], _GPU)

    def test_no_gpu_is_a_field_not_a_failure(self):
        with _mock_psutil() as m:
            m["gpu"].return_value = {"error": "no_nvidia_gpu"}
            out = hephaestus.fetch()
            self.assertEqual(hephaestus.handle()["gpu"], {"error": "no_nvidia_gpu"})
        self.assertEqual(out["gpu"], {"error": "no_nvidia_gpu"})
        self.assertEqual(out["cpu"]["usage_percent"], 12.5)     # the rest still reads


if __name__ == "__main__":
    unittest.main(verbosity=2)
