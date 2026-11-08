"""
hypnos.py — God of Sleep
========================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Tell whether the person has stepped away: Windows locked, the PC
             asleep, or no keyboard or mouse input for a while. It only reads;
             it decides nothing.

Contract:    presence() -> Presence, three readings taken together:
               • idle_s         — seconds since the last keyboard or mouse
                                  input anywhere on the PC (GetLastInputInfo),
                                  not only in Felhaven. Whole-PC on purpose:
                                  working in another app is not being away.
               • windows_locked — True / False from the session's lock flag
                                  (WTSQuerySessionInformationW, WTSSessionInfoEx);
                                  None if Windows won't say.
               • asleep_s       — total seconds this PC has spent asleep or
                                  hibernating since boot: GetTickCount64 (which
                                  counts sleep) minus QueryUnbiasedInterruptTime
                                  (which doesn't). It only ever grows, and only
                                  while the PC sleeps, so a caller that compares
                                  two readings learns whether it slept between.
             Raises HypnosError if a Windows call fails. Off Windows every
             reading is neutral (0.0, None, 0.0), so nothing ever looks away.

             No state, no policy. Cerberus's Kairos worker (cerberus.fetch) owns
             what "away" means and what to do about it.

Why polling: Windows can also *announce* a lock or a sleep, but only to a window
             procedure, and Tk doesn't expose one. Hearing the announcement
             would mean patching Tk's own window through ctypes or running a
             hidden window on a message-loop thread. Three read-only calls on a
             2 s Kairos tick cost almost nothing and need neither (§12).

Measured:    On Obelisk, 2026-09-30: the WTSINFOEXW buffer Windows returned was
             232 bytes, equal to sizeof(_WTSInfoEx) below, with SessionFlags 1
             (unlocked). At SessionFlags' offset (16), the struct's SessionId
             matched ProcessIdToSessionId, WinStationName read "Console" and
             UserName matched USERNAME. The asleep counter held within ±10 ms
             over 5 s awake.
             Live, with Felhaven running (the owner's check, read from
             Cerberus's Ledger and Windows' System log):
               • Win+L → `session·lock windows_lock` (2026-09-30 23:24:35):
                 the flag reads locked while Windows is locked.
               • An hour untouched → `session·lock idle` (2026-10-01
                 00:44:52), 60 min after the last input.
               • A 4.4 h sleep: asleep_s rose 15,827 s, against 15,833 s in
                 Windows' own record (Power-Troubleshooter event 1), and
                 stayed flat (−0.009 s over 10 s) while awake.
               • Sleep chosen with a session open → `session·lock
                 windows_lock` 2 s after the sleep began (2026-10-01
                 06:33:18). Obelisk locks Windows when it sleeps (sign-in on
                 wake), so the lock flag fires first, before suspend; the
                 asleep counter is the backstop for a PC that doesn't.

Upstream:    cerberus.py (fetch)
Downstream:  user32, kernel32, wtsapi32 (Windows, through ctypes)

Requires:    stdlib only (ctypes, dataclasses, logging, sys)
"""

import logging
import sys
from dataclasses import dataclass

log = logging.getLogger("METIS.hypnos")


class HypnosError(Exception):
    """A Windows presence call failed. The message names the call only."""


@dataclass(frozen=True)
class Presence:
    idle_s: float
    windows_locked: bool | None
    asleep_s: float


_NEUTRAL = Presence(idle_s=0.0, windows_locked=None, asleep_s=0.0)

# WTS_INFO_CLASS.WTSSessionInfoEx, and the SessionFlags values Windows reports.
# (Windows 7 / Server 2008 R2 swapped the two flags; Felhaven runs on 11.)
_WTS_SESSION_INFO_EX = 25
_WTS_CURRENT_SESSION = 0xFFFFFFFF
_WTS_SESSIONSTATE_LOCK = 0
_WTS_SESSIONSTATE_UNLOCK = 1


def _elapsed_ms(now: int, then: int) -> int:
    """now - then for two 32-bit millisecond tick counts, across the wrap
    GetTickCount takes every ~49.7 days."""
    return (now - then) & 0xFFFFFFFF


def _lock_state(flags: int) -> bool | None:
    """WTSINFOEX SessionFlags -> locked? None for anything Windows calls unknown."""
    if flags == _WTS_SESSIONSTATE_LOCK:
        return True
    if flags == _WTS_SESSIONSTATE_UNLOCK:
        return False
    return None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    class _WTSInfoExLevel1(ctypes.Structure):
        _fields_ = [
            ("SessionId", wintypes.ULONG),
            ("SessionState", ctypes.c_int),
            ("SessionFlags", wintypes.LONG),
            ("WinStationName", wintypes.WCHAR * 33),
            ("UserName", wintypes.WCHAR * 21),
            ("DomainName", wintypes.WCHAR * 18),
            ("LogonTime", ctypes.c_longlong),
            ("ConnectTime", ctypes.c_longlong),
            ("DisconnectTime", ctypes.c_longlong),
            ("LastInputTime", ctypes.c_longlong),
            ("CurrentTime", ctypes.c_longlong),
            ("IncomingBytes", wintypes.DWORD),
            ("OutgoingBytes", wintypes.DWORD),
            ("IncomingFrames", wintypes.DWORD),
            ("OutgoingFrames", wintypes.DWORD),
            ("IncomingCompressedBytes", wintypes.DWORD),
            ("OutgoingCompressedBytes", wintypes.DWORD),
        ]

    class _WTSInfoEx(ctypes.Structure):
        _fields_ = [("Level", wintypes.DWORD), ("Data", _WTSInfoExLevel1)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

    _user32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LastInputInfo)]
    _user32.GetLastInputInfo.restype = wintypes.BOOL
    _kernel32.GetTickCount.argtypes = []
    _kernel32.GetTickCount.restype = wintypes.DWORD
    _kernel32.GetTickCount64.argtypes = []
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _kernel32.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    _kernel32.QueryUnbiasedInterruptTime.restype = wintypes.BOOL
    _wtsapi32.WTSQuerySessionInformationW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    _wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
    _wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    _wtsapi32.WTSFreeMemory.restype = None

    def _idle_s() -> float:
        info = _LastInputInfo(ctypes.sizeof(_LastInputInfo), 0)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            raise HypnosError("GetLastInputInfo failed")
        return _elapsed_ms(int(_kernel32.GetTickCount()), int(info.dwTime)) / 1000.0

    def _windows_locked() -> bool | None:
        buf = ctypes.c_void_p()
        size = wintypes.DWORD()
        if not _wtsapi32.WTSQuerySessionInformationW(
                None, _WTS_CURRENT_SESSION, _WTS_SESSION_INFO_EX,
                ctypes.byref(buf), ctypes.byref(size)):
            raise HypnosError("WTSQuerySessionInformationW failed")
        try:
            if size.value < ctypes.sizeof(_WTSInfoEx):
                return None
            info = ctypes.cast(buf, ctypes.POINTER(_WTSInfoEx)).contents
            flags = int(info.Data.SessionFlags)
        finally:
            _wtsapi32.WTSFreeMemory(buf)
        return _lock_state(flags)

    def _asleep_s() -> float:
        unbiased = ctypes.c_ulonglong()
        if not _kernel32.QueryUnbiasedInterruptTime(ctypes.byref(unbiased)):
            raise HypnosError("QueryUnbiasedInterruptTime failed")
        # GetTickCount64 is milliseconds including sleep; the unbiased interrupt
        # time is 100 ns units excluding it. Their difference is time asleep.
        ticks_ms = int(_kernel32.GetTickCount64())
        return (ticks_ms - unbiased.value / 10_000) / 1000.0

    def presence() -> Presence:
        """Read all three now. Raises HypnosError if a Windows call fails."""
        return Presence(idle_s=_idle_s(), windows_locked=_windows_locked(),
                        asleep_s=_asleep_s())

else:

    def presence() -> Presence:
        """Off Windows nothing can be read, so nothing ever looks away."""
        return _NEUTRAL


if __name__ == "__main__":
    print(f"[Hypnos] {presence()}")
