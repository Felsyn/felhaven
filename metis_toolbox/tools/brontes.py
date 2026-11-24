"""
brontes.py — The Thunderer (the GPU)
====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Read the NVIDIA GPU's name, temperature, memory and load through NVML.
             Named for Brontes, the Cyclops who worked the forge in
             Hephaestus's smithy: the furnace behind the vitals, where
             Pythia's language model actually runs.

Contract:    Plain functions, not a brain tool (Hephaestus carries the reading
             to Pythia, through get_machine_status part=vitals):
               read() -> {"name", "temp_c", "memory_used_gb",
                          "memory_total_gb", "memory_pct", "load_pct"}
                         or {"error": "no_nvidia_gpu"}
                         or {"error": "nvml_failed: <call> returned <code>"}
             read() never raises: a missing GPU is a normal state on another
             machine, and Hephaestus folds this into its own fetch() per field
             (§2's per-field degradation), so one dead card can't blank CPU,
             RAM and disks.

Source:      NVML, the NVIDIA Management Library every NVIDIA driver installs
             (nvml.dll), called through ctypes: stdlib only, no new package
             (§11), and no nvidia-smi process every five seconds. The first
             GPU is the one read; Felhaven runs on a one-GPU machine.

The DLL path: Loaded by ABSOLUTE path, %SystemRoot%\\System32\\nvml.dll, where
             the driver puts it. A bare "nvml.dll" would be searched for in
             the app folder and the working directory first, so a file of
             that name planted beside Felhaven would load and run in its
             process. A file that isn't at the absolute path is no GPU.

Lifecycle:   nvmlInit_v2 once, on the first read; the device handle is kept.
             A failed init is remembered for the process and logged once, so
             a machine without the library doesn't retry and log every 5 s.
             A lock serialises reads: Kairos's worker and Pythia's tool call
             can both reach read() through Hephaestus.

Upstream:    tools/hephaestus.py (fetch(), and handle() behind get_machine_status).
Downstream:  nvml.dll (the driver's).

Requires:    ctypes, logging, os, sys, threading (stdlib).

V0.03:       Walked through 2026-11-17 (the V0.03 walkthrough, CONVENTIONS
             §3), its first audit (born 2026-11-09, #59). Found clean: the DLL
             by absolute path, the failed start logged once, the read lock,
             never-raise, and errors built from slugs, never an exception's
             text. Its no_nvidia_gpu and nvml_failed stay its own: no other
             module can have either condition (§2).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from typing import Any, Optional

log = logging.getLogger("METIS.brontes")

# ── NVML ──────────────────────────────────────────────────────────────────────

_NVML_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "nvml.dll")
_SUCCESS = 0
_TEMPERATURE_GPU = 0          # NVML_TEMPERATURE_GPU, the die sensor
_NAME_BUFFER = 96             # NVML_DEVICE_NAME_V2_BUFFER_SIZE
_GB = 1024 ** 3               # the unit Hephaestus's own GB figures use

ERR_NO_GPU = "no_nvidia_gpu"


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class NvmlError(Exception):
    """One NVML call returned something other than success."""

    def __init__(self, call: str, code: int) -> None:
        super().__init__(f"{call} returned {code}")
        self.call, self.code = call, code


class _Nvml:
    """The few NVML calls Brontes makes, with their ctypes signatures. Tests
    stand in an object with the same methods, so no test needs the DLL."""

    def __init__(self, path: str) -> None:
        self._dll = ctypes.CDLL(path)
        dll = self._dll
        dll.nvmlInit_v2.restype = ctypes.c_int
        dll.nvmlDeviceGetCount_v2.argtypes = [ctypes.POINTER(ctypes.c_uint)]
        dll.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint,
                                                     ctypes.POINTER(ctypes.c_void_p)]
        dll.nvmlDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_uint]
        dll.nvmlDeviceGetTemperature.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                 ctypes.POINTER(ctypes.c_uint)]
        dll.nvmlDeviceGetMemoryInfo.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(_Memory)]
        dll.nvmlDeviceGetUtilizationRates.argtypes = [ctypes.c_void_p,
                                                      ctypes.POINTER(_Utilization)]
        for name in ("nvmlDeviceGetCount_v2", "nvmlDeviceGetHandleByIndex_v2",
                     "nvmlDeviceGetName", "nvmlDeviceGetTemperature",
                     "nvmlDeviceGetMemoryInfo", "nvmlDeviceGetUtilizationRates"):
            getattr(dll, name).restype = ctypes.c_int

    @staticmethod
    def _check(call: str, code: int) -> None:
        if code != _SUCCESS:
            raise NvmlError(call, code)

    def init(self) -> None:
        self._check("nvmlInit_v2", self._dll.nvmlInit_v2())

    def first_device(self) -> Optional[ctypes.c_void_p]:
        count = ctypes.c_uint(0)
        self._check("nvmlDeviceGetCount_v2",
                    self._dll.nvmlDeviceGetCount_v2(ctypes.byref(count)))
        if count.value == 0:
            return None
        handle = ctypes.c_void_p()
        self._check("nvmlDeviceGetHandleByIndex_v2",
                    self._dll.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)))
        return handle

    def name(self, handle: ctypes.c_void_p) -> str:
        buf = ctypes.create_string_buffer(_NAME_BUFFER)
        self._check("nvmlDeviceGetName",
                    self._dll.nvmlDeviceGetName(handle, buf, _NAME_BUFFER))
        return buf.value.decode("utf-8", "replace")

    def temperature(self, handle: ctypes.c_void_p) -> int:
        temp = ctypes.c_uint(0)
        self._check("nvmlDeviceGetTemperature",
                    self._dll.nvmlDeviceGetTemperature(handle, _TEMPERATURE_GPU,
                                                       ctypes.byref(temp)))
        return int(temp.value)

    def memory(self, handle: ctypes.c_void_p) -> tuple[int, int]:
        """(used, total) bytes."""
        mem = _Memory()
        self._check("nvmlDeviceGetMemoryInfo",
                    self._dll.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)))
        return int(mem.used), int(mem.total)

    def load(self, handle: ctypes.c_void_p) -> int:
        util = _Utilization()
        self._check("nvmlDeviceGetUtilizationRates",
                    self._dll.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)))
        return int(util.gpu)


# ── State ─────────────────────────────────────────────────────────────────────

class _State:
    def __init__(self) -> None:
        self.tried = False
        self.nvml: Any = None             # an _Nvml, or a test's stand-in
        self.handle: Any = None
        self.no_gpu = False                # no library, or no device
        self.broken: Optional[str] = None  # why NVML would not start


_lock = threading.Lock()
_state = _State()


def _load() -> Any:
    """The NVML binding, or None when this machine has no library at the
    absolute path (or isn't Windows)."""
    if sys.platform != "win32" or not os.path.isfile(_NVML_PATH):
        return None
    return _Nvml(_NVML_PATH)


def _start() -> None:
    """First read only: load, init, find the device. Any failure is kept."""
    _state.tried = True
    try:
        nvml = _load()
        if nvml is None:
            _state.no_gpu = True
            log.info("Brontes: no NVML library at %s — no GPU readings", _NVML_PATH)
            return
        nvml.init()
        handle = nvml.first_device()
        if handle is None:
            _state.no_gpu = True
            log.info("Brontes: NVML found no NVIDIA GPU")
            return
        _state.nvml, _state.handle = nvml, handle
    except NvmlError as exc:
        _state.broken = f"{exc.call} returned {exc.code}"
        log.warning("Brontes: NVML would not start (%s returned %s)", exc.call, exc.code)
    except OSError as exc:
        _state.broken = type(exc).__name__
        log.warning("Brontes: could not load NVML (%s)", type(exc).__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def read() -> dict[str, Any]:
    """The first GPU's reading, or an error dict. Never raises."""
    with _lock:
        if not _state.tried:
            _start()
        if _state.no_gpu:
            return {"error": ERR_NO_GPU}
        if _state.broken is not None:
            return {"error": f"nvml_failed: {_state.broken}"}
        nvml, handle = _state.nvml, _state.handle
        try:
            used, total = nvml.memory(handle)
            return {
                "name": nvml.name(handle),
                "temp_c": nvml.temperature(handle),
                "memory_used_gb": round(used / _GB, 2),
                "memory_total_gb": round(total / _GB, 2),
                "memory_pct": round(100 * used / total, 1) if total else 0.0,
                "load_pct": nvml.load(handle),
            }
        except NvmlError as exc:
            return {"error": f"nvml_failed: {exc.call} returned {exc.code}"}
        except Exception as exc:
            log.warning("Brontes: GPU read failed (%s)", type(exc).__name__)
            return {"error": f"nvml_failed: {type(exc).__name__}"}


def _reset() -> None:
    """Forget the binding and any failure, so the next read starts again.
    For tests; the app never needs it."""
    global _state
    with _lock:
        _state = _State()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"[Brontes] NVML at {_NVML_PATH}: {os.path.isfile(_NVML_PATH)}")
    print(f"[Brontes] {read()}")
