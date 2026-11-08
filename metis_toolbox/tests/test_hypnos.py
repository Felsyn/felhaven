"""
test_hypnos.py — tools/hypnos.py, the presence probes Cerberus locks on.

The arithmetic is pinned with literals. The Windows calls run for real (CI is
windows-latest), but only their SHAPE is asserted: a test runner has no
keyboard, may run in a service session, and can't be locked or put to sleep,
so whether it reads "away" says nothing. The live behaviour (Win+L, an hour
idle, a real sleep) was the owner's check, and its readings are recorded,
with dates, in tools/hypnos.py's Measured note.

    python -X utf8 -m unittest tests.test_hypnos
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import hypnos


class TestArithmetic(unittest.TestCase):

    def test_elapsed_without_a_wrap(self):
        self.assertEqual(hypnos._elapsed_ms(5_000, 2_000), 3_000)

    def test_elapsed_across_the_32_bit_wrap(self):
        # 16 ms before the wrap, then 5 ms after it: 21 ms, not -4 billion.
        self.assertEqual(hypnos._elapsed_ms(5, 0xFFFFFFF0), 21)

    def test_lock_flags(self):
        self.assertIs(hypnos._lock_state(0), True)
        self.assertIs(hypnos._lock_state(1), False)
        self.assertIsNone(hypnos._lock_state(0xFFFFFFFF))
        self.assertIsNone(hypnos._lock_state(-1))


@unittest.skipUnless(sys.platform == "win32", "the probes are Windows calls")
class TestOnWindows(unittest.TestCase):

    def test_the_session_struct_is_the_size_windows_returns(self):
        # Windows handed back a 232-byte WTSINFOEXW on Obelisk (2026-09-30).
        import ctypes
        self.assertEqual(ctypes.sizeof(hypnos._WTSInfoEx), 232)

    def test_session_flags_sit_where_windows_puts_them(self):
        # The one field read. Measured on Obelisk, 2026-09-30: at offset 16,
        # SessionId matched ProcessIdToSessionId, WinStationName read "Console"
        # and UserName matched USERNAME. A field typed wrong ahead of it would
        # move it, and the lock would be read from the wrong bytes.
        self.assertEqual(hypnos._WTSInfoEx.Data.offset
                         + hypnos._WTSInfoExLevel1.SessionFlags.offset, 16)

    def test_the_struct_reads_this_process_s_own_session(self):
        # Live cross-check, CI included: the SessionId read through the struct
        # is the session Windows says this process runs in.
        import ctypes
        from ctypes import wintypes
        sid = wintypes.DWORD()
        self.assertTrue(ctypes.WinDLL("kernel32").ProcessIdToSessionId(
            os.getpid(), ctypes.byref(sid)))
        buf, size = ctypes.c_void_p(), wintypes.DWORD()
        self.assertTrue(hypnos._wtsapi32.WTSQuerySessionInformationW(
            None, hypnos._WTS_CURRENT_SESSION, hypnos._WTS_SESSION_INFO_EX,
            ctypes.byref(buf), ctypes.byref(size)))
        try:
            info = ctypes.cast(buf, ctypes.POINTER(hypnos._WTSInfoEx)).contents
            self.assertEqual(info.Level, 1)
            self.assertEqual(info.Data.SessionId, sid.value)
        finally:
            hypnos._wtsapi32.WTSFreeMemory(buf)

    def test_presence_has_the_right_shape(self):
        p = hypnos.presence()
        self.assertIsInstance(p, hypnos.Presence)
        self.assertIsInstance(p.idle_s, float)
        self.assertGreaterEqual(p.idle_s, 0.0)
        self.assertIn(p.windows_locked, (True, False, None))
        self.assertIsInstance(p.asleep_s, float)

    def test_the_asleep_counter_does_not_run_while_awake(self):
        # Measured drift on Obelisk: within ±10 ms over 5 s. Two back-to-back
        # reads can't differ by anything near the 5 s Cerberus treats as sleep.
        first = hypnos.presence().asleep_s
        second = hypnos.presence().asleep_s
        self.assertLess(abs(second - first), 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
