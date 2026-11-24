"""
test_brontes.py — the GPU reading through NVML.

Hermetic: brontes._load is patched to hand back a stand-in with the same few
methods the real binding has, so no test needs nvml.dll or a GPU. The one
exception, test_the_real_read_never_raises, calls the real library where there
is one and only checks the contract: a dict, a reading or an error, never an
exception (on a machine with no NVIDIA card that is the no-GPU path).

    python -X utf8 -m unittest tests.test_brontes
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import brontes

_GB = 1024 ** 3


class FakeNvml:
    """The methods brontes calls, recording how often init runs."""

    def __init__(self, *, init_error=None, device=object(), fail=None):
        self.inits = 0
        self._init_error = init_error
        self._device = device
        self._fail = fail or {}

    def _maybe_fail(self, what):
        if what in self._fail:
            raise self._fail[what]

    def init(self):
        self.inits += 1
        if self._init_error:
            raise self._init_error

    def first_device(self):
        return self._device

    def name(self, handle):
        self._maybe_fail("name")
        return "NVIDIA GeForce GTX 1660"

    def temperature(self, handle):
        self._maybe_fail("temperature")
        return 48

    def memory(self, handle):
        self._maybe_fail("memory")
        return int(1.7 * _GB), 6 * _GB

    def load(self, handle):
        self._maybe_fail("load")
        return 12


class _BrontesCase(unittest.TestCase):
    def setUp(self):
        brontes._reset()
        self.addCleanup(brontes._reset)

    def use(self, fake):
        patch = mock.patch.object(brontes, "_load", return_value=fake)
        patch.start()
        self.addCleanup(patch.stop)
        return fake


class TestReading(_BrontesCase):

    def test_a_reading_has_every_field(self):
        self.use(FakeNvml())
        self.assertEqual(brontes.read(), {
            "name": "NVIDIA GeForce GTX 1660", "temp_c": 48,
            "memory_used_gb": 1.7, "memory_total_gb": 6.0,
            "memory_pct": 28.3, "load_pct": 12})

    def test_nvml_starts_once(self):
        fake = self.use(FakeNvml())
        for _ in range(4):
            brontes.read()
        self.assertEqual(fake.inits, 1)

    def test_a_failed_read_is_an_error_and_the_next_can_succeed(self):
        fake = self.use(FakeNvml(fail={"temperature": brontes.NvmlError(
            "nvmlDeviceGetTemperature", 15)}))
        self.assertEqual(brontes.read(),
                         {"error": "nvml_failed: nvmlDeviceGetTemperature returned 15"})
        fake._fail.clear()
        self.assertEqual(brontes.read()["temp_c"], 48)

    def test_anything_unexpected_is_an_error_not_an_exception(self):
        self.use(FakeNvml(fail={"load": RuntimeError("driver hiccup")}))
        with self.assertLogs("METIS.brontes", "WARNING"):
            self.assertEqual(brontes.read(), {"error": "nvml_failed: RuntimeError"})


class TestNoGpu(_BrontesCase):

    def test_no_library_is_no_gpu(self):
        self.use(None)
        self.assertEqual(brontes.read(), {"error": "no_nvidia_gpu"})

    def test_no_device_is_no_gpu(self):
        self.use(FakeNvml(device=None))
        self.assertEqual(brontes.read(), {"error": "no_nvidia_gpu"})

    def test_a_failed_init_is_remembered_not_retried(self):
        fake = self.use(FakeNvml(init_error=brontes.NvmlError("nvmlInit_v2", 9)))
        with self.assertLogs("METIS.brontes", "WARNING") as logged:
            first = brontes.read()
            second = brontes.read()
        self.assertEqual(first, {"error": "nvml_failed: nvmlInit_v2 returned 9"})
        self.assertEqual(second, first)
        self.assertEqual(fake.inits, 1)
        self.assertEqual(len(logged.records), 1)                 # logged once

    def test_a_library_that_will_not_load_is_an_error(self):
        with mock.patch.object(brontes, "_load", side_effect=OSError("bad image")):
            with self.assertLogs("METIS.brontes", "WARNING"):
                self.assertEqual(brontes.read(), {"error": "nvml_failed: OSError"})


class TestTheDllPath(_BrontesCase):
    """Loaded by absolute path, so a nvml.dll planted beside Felhaven (searched
    first for a bare name) can never be the one that runs."""

    def test_the_path_is_absolute_and_in_system32(self):
        self.assertTrue(os.path.isabs(brontes._NVML_PATH))
        self.assertTrue(brontes._NVML_PATH.lower().endswith(
            os.path.join("system32", "nvml.dll")))

    def test_load_uses_exactly_that_path(self):
        with mock.patch.object(brontes.sys, "platform", "win32"), \
                mock.patch.object(brontes.os.path, "isfile", return_value=True), \
                mock.patch.object(brontes, "_Nvml") as binding:
            brontes._load()
        binding.assert_called_once_with(brontes._NVML_PATH)

    def test_no_file_at_that_path_is_no_library(self):
        with mock.patch.object(brontes.sys, "platform", "win32"), \
                mock.patch.object(brontes.os.path, "isfile", return_value=False), \
                mock.patch.object(brontes, "_Nvml") as binding:
            self.assertIsNone(brontes._load())
        binding.assert_not_called()

    def test_off_windows_there_is_no_library(self):
        with mock.patch.object(brontes.sys, "platform", "linux"), \
                mock.patch.object(brontes, "_Nvml") as binding:
            self.assertIsNone(brontes._load())
        binding.assert_not_called()


class TestTheRealLibrary(_BrontesCase):

    def test_the_real_read_never_raises(self):
        out = brontes.read()
        self.assertIsInstance(out, dict)
        if "error" in out:
            self.assertRegex(out["error"], r"^(no_nvidia_gpu|nvml_failed: .+)$")
        else:
            self.assertIsInstance(out["name"], str)
            self.assertGreater(out["memory_total_gb"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
