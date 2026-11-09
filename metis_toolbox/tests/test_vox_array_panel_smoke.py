"""
test_vox_array_panel_smoke.py — headless Tk smoke test for the Vox Array host
and the Echo and Tunes tab bodies.

Same approach as test_morpheus_panel_smoke.py: a real tk.Tk() root we withdraw()
(no window flashes) and drive with update()/update_idletasks() instead of
mainloop(). No exception raised == pass. In the host test Morpheus, Orpheus,
AND Echo are all forced to their disabled (no-binary) path so it never depends
on mpv/yt-dlp/ffmpeg being installed; the dedicated Echo panel test below forces
the ENABLED path and mocks the conversion, so no kokoro model or ffmpeg is
touched either.

THE LEDGER IS REDIRECTED FOR EVERY CASE IN THIS FILE, and not only for the ones
that press SAVE. Building the host now builds the Tunes tab, which opens the
ledger; a suite that pointed at the real one would show the author's listening
history to CI on a good day and overwrite it on a bad one. The redirect happens
before the panel is constructed, because the panel reads on construction.

    python -X utf8 -m unittest tests.test_vox_array_panel_smoke
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import calliope
import theme
import voices
from tools import morpheus
from tools import echo
from tools import euterpe
from tools import orpheus


class _LedgerRedirect:
    """Point euterpe at a temp ledger for the life of one test.

    A mixin rather than a base class because the two suites below already have
    their own setUp; both call this first, before any panel is built.
    """

    def _redirect_ledger(self, plays=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = euterpe._LEDGER_PATH
        euterpe._LEDGER_PATH = os.path.join(tmp.name, "euterpe_ledger.json")
        self.addCleanup(lambda: setattr(euterpe, "_LEDGER_PATH", saved))
        if plays:
            with open(euterpe._LEDGER_PATH, "w", encoding="utf-8") as handle:
                json.dump({"plays": list(plays)}, handle, indent=2)
        return tmp.name


class TestVoxArrayPanelSmoke(_LedgerRedirect, unittest.TestCase):
    """Host builds, exposes every tab body, and switches tabs without crashing."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.vox_array_panel import VoxArrayPanel
        from panels.morpheus_panel import MorpheusPanel
        from panels.echo_panel import EchoPanel
        from panels.orpheus_panel import OrpheusPanel
        from panels.euterpe_panel import EuterpePanel
        # Before the host is built: the Tunes body reads the ledger on construction.
        self._redirect_ledger()
        # Force every binary-gated tab to its disabled path regardless of the host.
        self._orig_avail = morpheus.available
        self._orig_orpheus_avail = orpheus.available
        self._orig_echo_avail = echo.available
        morpheus.available = lambda: {"mpv": None, "ytdlp": None}
        orpheus.available = lambda: {"ffmpeg": None}
        echo.available = lambda: {"ffmpeg": None}
        self.MorpheusPanel = MorpheusPanel
        self.EchoPanel = EchoPanel
        self.OrpheusPanel = OrpheusPanel
        self.EuterpePanel = EuterpePanel
        self.panel = VoxArrayPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        morpheus.available = self._orig_avail
        orpheus.available = self._orig_orpheus_avail
        echo.available = self._orig_echo_avail
        self.panel.destroy()

    def test_builds_with_all_tab_bodies(self):
        self.assertIsInstance(self.panel.morpheus, self.MorpheusPanel)
        self.assertIsInstance(self.panel.echo, self.EchoPanel)
        self.assertIsInstance(self.panel.orpheus, self.OrpheusPanel)
        self.assertIsInstance(self.panel.euterpe, self.EuterpePanel)

    def test_every_tab_label_carries_its_tooltip(self):
        from panels import virgil_tooltip
        tips = virgil_tooltip._shared(self.root)._tips
        self.assertEqual(sorted(self.panel._TIPS), sorted(self.panel._tabs))
        for key, label in self.panel._tabs.items():
            self.assertEqual(tips.get(str(label)), self.panel._TIPS[key], key)

    def test_echo_says_why_when_its_ffmpeg_is_refused(self):
        from panels.echo_panel import _UNVERIFIED_MSG
        echo.available = lambda: {"ffmpeg": None,
                                  "problem": "binary_unverified: ffmpeg.exe does not match"}
        panel = self.EchoPanel(self.root)
        try:
            texts = [w.cget("text") for w in panel.winfo_children()
                     if isinstance(w, tk.Label)]
            self.assertIn(_UNVERIFIED_MSG, texts)
        finally:
            panel.destroy()

    def test_starts_on_morpheus_tab(self):
        self.assertEqual(self.panel._active, "morpheus")

    def test_tab_switch(self):
        for key in ("echo", "orpheus", "tunes", "morpheus"):
            self.panel._show_tab(key)
            self.root.update_idletasks()
            self.assertEqual(self.panel._active, key)

    def test_tunes_defines_no_update_so_it_cannot_be_polled(self):
        """Request-driven, like Echo. update(data) is the single entry point
        Kairos calls (§7), so defining one here would invite a registration —
        and a ledger re-read every 2 s would fight whatever is being typed.
        The inherited tk.Frame.update() is Tk's own and takes no data."""
        self.assertNotIn("update", self.EuterpePanel.__dict__)

    def test_morpheus_child_still_polls_when_hidden(self):
        # The Kairos-registered child keeps working regardless of which tab is
        # visible (Moderati precedent). update() on the disabled child is a no-op.
        self.panel._show_tab("echo")
        self.panel.morpheus.update({"running": True, "title": "X", "pos": 1, "dur": 2})
        self.root.update_idletasks()

    def test_orpheus_child_still_polls_when_hidden(self):
        self.panel._show_tab("morpheus")
        self.panel.orpheus.update({"playing": False, "files": []})
        self.root.update_idletasks()


class TestEchoPanelSmoke(unittest.TestCase):
    """The Echo tab body: gating, single-flight, and the daemon+drain round-trip."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.echo_panel import EchoPanel
        self.EchoPanel = EchoPanel
        # Force the ENABLED (ffmpeg-present) path so the convert UI is built
        # regardless of the host — the conversion itself is mocked per test.
        self._orig_echo_avail = echo.available
        echo.available = lambda: {"ffmpeg": "ffmpeg.exe"}
        # The voice bar seeds itself from the saved narration voice — point that
        # at a temp dir so no real user state is read or written.
        self._dir = tempfile.mkdtemp(prefix="echo_panel_")
        self._orig_voices = voices._DATA_PATH
        voices._DATA_PATH = os.path.join(self._dir, "voice_config.json")
        self.panel = EchoPanel(self.root)
        self.panel.pack()

    def tearDown(self):
        echo.available = self._orig_echo_avail
        voices._DATA_PATH = self._orig_voices
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)
        self.panel.destroy()

    def test_disabled_when_ffmpeg_missing(self):
        # No ffmpeg → a calm placeholder, no convert UI, no crash (§7).
        echo.available = lambda: {"ffmpeg": None}
        panel = self.EchoPanel(self.root)
        try:
            self.assertFalse(panel._enabled)
            self.assertFalse(hasattr(panel, "_send_btn"))
            # The voice bar is built after the gate too — a disabled tab is a
            # bare placeholder, not a placeholder with a dropdown under it.
            self.assertFalse(hasattr(panel, "_voice"))
        finally:
            panel.destroy()

    def _fill(self, text="hello world", name="out"):
        self.panel._text.delete("1.0", "end")
        self.panel._text.insert("1.0", text)
        self.panel._fname_var.set(name)
        self.panel._refresh_gate()

    def test_button_gated_until_both_fields_ready(self):
        # The button switches cursor hand2 (enabled) <-> arrow (disabled).
        # Nothing entered → disabled.
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")
        # Only text → still disabled (filename sanitises to empty).
        self._fill(text="hi", name="")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")
        # A filename that is all-illegal also stays disabled.
        self._fill(text="hi", name="///")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")
        # Both usable → enabled.
        self._fill(text="hi", name="out")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "hand2")

    def test_single_flight_guard(self):
        self._fill()
        self.panel._converting = True   # pretend a conversion is in flight
        with mock.patch.object(echo, "text_to_audio") as m:
            self.panel._on_send()
            m.assert_not_called()

    def test_success_round_trip(self):
        self._fill()
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"path": "C:\\x\\out.opus"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertFalse(self.panel._converting)
        self.assertEqual(self.panel._status.cget("fg"), theme.C["green"])
        self.assertIn("out.opus", self.panel._status.cget("text"))
        self.assertIsNone(self.panel._drain_after_id)   # chain stopped itself

    def test_success_clears_text_and_filename(self):
        # A successful save clears both fields so the next paste starts fresh
        # — no manual delete needed before the next conversion.
        self._fill(text="hello world", name="out")
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"path": "C:\\x\\out.opus"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(self.panel._text.get("1.0", "end").strip(), "")
        self.assertEqual(self.panel._fname_var.get(), "")
        self.assertEqual(self.panel._send_btn.cget("cursor"), "arrow")   # gate re-closed

    def test_error_round_trip_is_red(self):
        self._fill()
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"error": "ffmpeg_unavailable"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(self.panel._status.cget("fg"), theme.C["red"])
        self.assertIsNone(self.panel._drain_after_id)

    def test_error_leaves_fields_intact_for_retry(self):
        self._fill(text="hello world", name="out")
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"error": "ffmpeg_unavailable"}):
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(self.panel._text.get("1.0", "end").strip(), "hello world")
        self.assertEqual(self.panel._fname_var.get(), "out")

    # ── Voice bar (session-only, independent of narration) ──────────────────

    def test_voice_starts_at_the_saved_narration_voice(self):
        self.assertEqual(self.panel._voice, voices.pythia_voice())

    def test_selected_voice_reaches_the_conversion(self):
        self.panel._set_voice("bm_george")
        self._fill()
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"path": "C:\\x\\out.opus"}) as m:
            self.panel._on_send()
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(m.call_args.kwargs["voice"], "bm_george")

    def test_picking_a_voice_never_changes_narration(self):
        # Echo's bar controls the ECHO tab alone — Pythia keeps her voice.
        before = calliope.narration_voice()
        self.panel._set_voice("bf_isabella")
        self.assertEqual(calliope.narration_voice(), before)

    def test_voice_is_not_persisted(self):
        # Session-only by design: a pick must leave no file behind.
        self.panel._set_voice("am_onyx")
        self.assertFalse(os.path.exists(voices._DATA_PATH))

    def test_mid_conversion_repick_does_not_change_the_job_in_flight(self):
        self.panel._set_voice("bm_george")
        self._fill()
        with mock.patch.object(echo, "text_to_audio",
                               return_value={"path": "C:\\x\\out.opus"}) as m:
            self.panel._on_send()
            self.panel._set_voice("af_bella")     # user re-picks while it runs
            self._pump_until(lambda: not self.panel._converting)
        self.assertEqual(m.call_args.kwargs["voice"], "bm_george")

    # ── Right-click context menus (Pythia home_panel precedent) ─────────────

    def test_select_all_then_copy_puts_text_on_clipboard(self):
        self._fill(text="hello world")
        self.panel._select_all_text()
        self.panel._text.event_generate("<<Copy>>")
        self.root.update_idletasks()
        self.assertIn("hello world", self.root.clipboard_get())

    def test_text_menu_disables_cut_copy_without_selection(self):
        self._fill(text="hello world")
        self.panel._text.tag_remove("sel", "1.0", "end")   # ensure no selection
        event = tk.Event()
        event.x_root = event.y_root = 0
        with mock.patch.object(tk.Menu, "tk_popup"), \
                mock.patch.object(tk.Menu, "grab_release"), \
                mock.patch.object(tk.Menu, "add_command") as add_cmd:
            self.panel._show_text_menu(event)
        states = {call.kwargs["label"]: call.kwargs.get("state") for call in add_cmd.call_args_list}
        self.assertEqual(states["Cut"], "disabled")
        self.assertEqual(states["Copy"], "disabled")
        self.assertIsNone(states["Paste"])          # always enabled, no explicit state
        self.assertIsNone(states["Select All"])      # always enabled

    def test_entry_menu_select_all_and_copy(self):
        self.panel._fname_var.set("out")
        self.panel._fname_entry.select_range(0, "end")
        self.panel._fname_entry.event_generate("<<Copy>>")
        self.root.update_idletasks()
        self.assertIn("out", self.root.clipboard_get())

    def _pump_until(self, cond, timeout=3.0):
        """Drive the Tk event loop (processes after() callbacks) until cond()."""
        deadline = time.time() + timeout
        while not cond() and time.time() < deadline:
            self.root.update()
            time.sleep(0.02)


class TestEuterpePanelSmoke(_LedgerRedirect, unittest.TestCase):
    """The TUNES tab body: what it shows, what it saves, and what it refuses.

    The refusals carry the weight. This tab hands the operator a text box over
    the file that decides what the DJ plays, so "SAVE wrote nothing and said
    why" has to be true of a broken edit, and of a save racing the 2 s tick
    that Awen records plays on.
    """

    _ROW = {"title": "Artist - Song", "key": "artist - song", "video_id": None,
            "url": "", "channel": "", "duration": 240.0, "source": "dj",
            "at": "2026-09-17T09:00:00"}

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from panels.euterpe_panel import EuterpePanel
        self._redirect_ledger(plays=[dict(self._ROW)])
        self.panel = EuterpePanel(self.root)
        self.panel.pack()
        self.root.update_idletasks()

    def tearDown(self):
        self.panel.destroy()

    def _text(self):
        return self.panel._editor.get("1.0", "end-1c")

    def _retype(self, document):
        self.panel._editor.delete("1.0", "end")
        self.panel._editor.insert("1.0", json.dumps(document, indent=2))

    # ── what it shows ─────────────────────────────────────────────────────────

    def test_it_opens_on_the_ledger(self):
        self.assertEqual(json.loads(self._text()), {"plays": [self._ROW]})

    def test_the_summary_counts_by_source(self):
        """Named "user" and "dj" because those are the values about to be
        edited — a friendlier synonym would have to be translated back."""
        self.assertIn("1 plays", self.panel._summary.cget("text"))
        self.assertIn("0 user", self.panel._summary.cget("text"))
        self.assertIn("1 dj", self.panel._summary.cget("text"))

    def test_an_empty_ledger_says_what_is_missing(self):
        from panels.euterpe_panel import EuterpePanel
        os.remove(euterpe._LEDGER_PATH)
        panel = EuterpePanel(self.root)
        self.addCleanup(panel.destroy)
        self.assertIn("No plays logged yet", panel._summary.cget("text"))
        self.assertEqual(json.loads(panel._editor.get("1.0", "end-1c")),
                         {"plays": []})

    def test_the_editor_does_not_out_request_the_controls_below_it(self):
        """§7's trap, and the reason this Text is height=1 rather than a real
        line count. Pack satisfies every child's REQUESTED height in order and
        only then hands the leftover to the expand=True ones, so a tall Text
        squeezes SAVE and the status line to nothing on a small window instead
        of shrinking itself — which is how PhosphorScroll once collapsed Home's
        chat bar. Whatever is scrolled is what shrinks."""
        self.root.update_idletasks()
        controls = (self.panel._btn_row.winfo_reqheight()
                    + self.panel._base_row.winfo_reqheight()
                    + self.panel._status.winfo_reqheight())
        self.assertLessEqual(
            self.panel._editor_row.winfo_reqheight(), controls,
            "the editor asks for more room than the controls it must not squeeze")

    # ── saving ────────────────────────────────────────────────────────────────

    def test_flipping_a_source_to_user_changes_the_taste(self):
        """The whole point of the tab, end to end through the widget."""
        self.assertEqual(euterpe.taste_anchor(), [])
        self._retype({"plays": [dict(self._ROW, source="user")]})
        self.panel._on_save()
        self.assertEqual(euterpe.taste_anchor(), ["artist - song"])
        self.assertIn("Saved", self.panel._status.cget("text"))

    def test_a_save_reloads_so_the_box_still_shows_the_file(self):
        self._retype({"plays": [dict(self._ROW, source="user")]})
        self.panel._on_save()
        self.root.update_idletasks()
        self.assertEqual(self._text().rstrip("\n"),
                         euterpe.raw_text().rstrip("\n"))

    def test_the_summary_follows_a_save(self):
        self._retype({"plays": [dict(self._ROW, source="user")]})
        self.panel._on_save()
        self.assertIn("1 user", self.panel._summary.cget("text"))

    # ── refusing ──────────────────────────────────────────────────────────────

    def test_broken_json_is_refused_and_nothing_is_written(self):
        before = euterpe.raw_text()
        self.panel._editor.delete("1.0", "end")
        self.panel._editor.insert("1.0", '{\n  "plays": [\n')
        self.panel._on_save()
        self.assertEqual(euterpe.raw_text(), before)
        self.assertIn("not valid JSON", self.panel._status.cget("text"))
        self.assertEqual(self.panel._status.cget("fg"), theme.C["red"])

    def test_a_refusal_leaves_the_typed_text_alone(self):
        """A refusal must not reload. The operator needs the broken text in
        front of them to fix it — throwing it away would be the second thing
        lost in the same click."""
        broken = '{\n  "plays": [\n'
        self.panel._editor.delete("1.0", "end")
        self.panel._editor.insert("1.0", broken)
        self.panel._on_save()
        self.assertEqual(self._text(), broken)

    def test_a_bad_source_names_the_row(self):
        self._retype({"plays": [dict(self._ROW), dict(self._ROW, source="robot")]})
        self.panel._on_save()
        self.assertIn("Play 2", self.panel._status.cget("text"))

    def test_pasting_a_song_twice_is_refused_and_says_which_rows(self):
        """Duplicate protection through the widget. The message has to carry
        both row numbers or it sends you hunting through the box."""
        before = euterpe.raw_text()
        self._retype({"plays": [dict(self._ROW),
                                dict(self._ROW, title="Other - Track",
                                     key="other - track"),
                                dict(self._ROW)]})
        self.panel._on_save()
        self.assertEqual(euterpe.raw_text(), before)
        status = self.panel._status.cget("text")
        self.assertIn("the same song", status)
        self.assertIn("plays 1 and 3", status)
        self.assertIn("artist - song", status)

    def test_an_unknown_slug_is_shown_rather_than_swallowed(self):
        """A save that did not happen must never report as silence."""
        with mock.patch.object(euterpe, "replace_all", return_value="kraken"):
            self.panel._on_save()
        self.assertIn("kraken", self.panel._status.cget("text"))

    # ── the 2 s tick ──────────────────────────────────────────────────────────

    def test_a_save_is_refused_when_auto_dj_wrote_underneath(self):
        """Awen records plays every 2 s. Without this the operator's SAVE would
        silently delete the song that just finished."""
        euterpe.record("Other - Track", duration=240, source="dj")
        landed = euterpe.raw_text()
        self._retype({"plays": [dict(self._ROW, source="user")]})
        self.panel._on_save()
        self.assertEqual(euterpe.raw_text(), landed)
        self.assertIn("RELOAD", self.panel._status.cget("text"))

    def test_reload_clears_the_staleness_and_lets_the_save_through(self):
        euterpe.record("Other - Track", duration=240, source="dj")
        self.panel._on_reload()
        rows = euterpe.entries()
        self._retype({"plays": [dict(row, source="user") for row in rows]})
        self.panel._on_save()
        self.assertIn("Saved", self.panel._status.cget("text"))
        self.assertEqual(len(euterpe.entries()), 2)

    def test_reload_discards_unsaved_edits(self):
        self._retype({"plays": []})
        self.panel._on_reload()
        self.assertEqual(json.loads(self._text()), {"plays": [self._ROW]})

    # ── the base copy ─────────────────────────────────────────────────────────

    def test_with_no_base_the_caption_says_so(self):
        self.assertEqual(self.panel._base_caption.cget("text"), "none yet")

    def test_restore_with_no_base_explains_how_to_make_one(self):
        before = euterpe.raw_text()
        self.panel._on_restore()
        self.assertIn("no base copy yet", self.panel._status.cget("text"))
        self.assertEqual(euterpe.raw_text(), before)

    def test_taking_a_base_updates_the_caption(self):
        self.panel._on_take_base()
        self.assertIn("1 plays", self.panel._base_caption.cget("text"))
        self.assertNotEqual(self.panel._base_caption.cget("text"), "none yet")

    def test_taking_a_base_is_refused_while_the_box_is_dirty(self):
        """Taking it from disk with unsaved edits would capture the OLDER state
        and report success — found out on the day it was needed."""
        self._retype({"plays": []})
        self.panel._on_take_base()
        self.assertIn("SAVE first", self.panel._status.cget("text"))
        self.assertIsNone(euterpe.base_snapshot())

    def test_restore_is_preview_only(self):
        """The escape hatch from a bad state must not itself need one."""
        self.panel._on_take_base()
        self._retype({"plays": [dict(self._ROW, key="wandered off")]})
        self.panel._on_save()
        on_disk = euterpe.raw_text()

        self.panel._on_restore()
        self.assertEqual(json.loads(self._text()), {"plays": [self._ROW]})
        self.assertEqual(euterpe.raw_text(), on_disk, "RESTORE wrote to the ledger")
        self.assertIn("nothing is written yet", self.panel._status.cget("text"))

    def test_restore_then_save_puts_the_ledger_back(self):
        self.panel._on_take_base()
        self._retype({"plays": [dict(self._ROW, key="wandered off")]})
        self.panel._on_save()
        self.assertEqual(euterpe.entries()[0]["key"], "wandered off")

        self.panel._on_restore()
        self.panel._on_save()
        self.assertEqual(euterpe.entries(), [self._ROW])

    def test_restore_does_not_re_take_the_staleness_stamp(self):
        """Restoring drops every play since the base was taken, so it is the
        most destructive thing this tab commits. A song landing underneath must
        still stop the SAVE rather than be waved through."""
        self.panel._on_take_base()
        euterpe.record("Other - Track", duration=240, source="dj")
        landed = euterpe.raw_text()
        self.panel._on_restore()
        self.panel._on_save()
        self.assertEqual(euterpe.raw_text(), landed)
        self.assertIn("RELOAD", self.panel._status.cget("text"))

    def test_a_save_does_not_disturb_the_base(self):
        """The difference from .bak, through the widget."""
        self.panel._on_take_base()
        taken = euterpe.base_snapshot()["text"]
        self._retype({"plays": [dict(self._ROW, source="user")]})
        self.panel._on_save()
        self.assertEqual(euterpe.base_snapshot()["text"], taken)

    def test_a_corrupt_base_is_offered_for_repair_not_hidden(self):
        with open(euterpe._LEDGER_PATH + euterpe._BASE_SUFFIX, "w",
                  encoding="utf-8") as handle:
            handle.write("{ broken")
        self.panel._refresh_base_caption()
        self.assertIn("unreadable", self.panel._base_caption.cget("text"))
        self.panel._on_restore()
        self.assertEqual(self._text(), "{ broken")

    # ── copy/paste ────────────────────────────────────────────────────────────

    def test_select_all_then_copy_puts_the_ledger_on_the_clipboard(self):
        self.panel._select_all()
        self.panel._editor.event_generate("<<Copy>>")
        self.root.update_idletasks()
        self.assertIn("artist - song", self.root.clipboard_get())

    def test_select_all_breaks_the_binding_chain(self):
        """Returning "break" is what replaces Tk's own Control-a
        (beginning-of-line) rather than running before it."""
        self.assertEqual(self.panel._select_all(), "break")

    def test_text_menu_disables_cut_copy_without_selection(self):
        self.panel._editor.tag_remove("sel", "1.0", "end")
        event = tk.Event()
        event.x_root = event.y_root = 0
        with mock.patch.object(tk.Menu, "tk_popup"), \
                mock.patch.object(tk.Menu, "grab_release"), \
                mock.patch.object(tk.Menu, "add_command") as add_cmd:
            self.panel._show_text_menu(event)
        states = {call.kwargs["label"]: call.kwargs.get("state")
                  for call in add_cmd.call_args_list}
        self.assertEqual(states["Cut"], "disabled")
        self.assertEqual(states["Copy"], "disabled")
        self.assertIsNone(states["Paste"])        # always enabled, no explicit state
        self.assertIsNone(states["Select All"])   # always enabled

    def test_text_menu_enables_cut_copy_with_a_selection(self):
        self.panel._select_all()
        event = tk.Event()
        event.x_root = event.y_root = 0
        with mock.patch.object(tk.Menu, "tk_popup"), \
                mock.patch.object(tk.Menu, "grab_release"), \
                mock.patch.object(tk.Menu, "add_command") as add_cmd:
            self.panel._show_text_menu(event)
        states = {call.kwargs["label"]: call.kwargs.get("state")
                  for call in add_cmd.call_args_list}
        self.assertEqual(states["Cut"], "normal")
        self.assertEqual(states["Copy"], "normal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
