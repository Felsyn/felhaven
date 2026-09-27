"""
test_vox_controls_smoke.py — headless Tk smoke test for the Vox control row.

Replaces test_narrator_panel_smoke.py: the speaker lamp became three words (four
since Auto-DJ joined them on 2026-09-16), so
the assertions moved from canvas item types to label colours, but the property
being pinned is the same one — the row reads Calliope's real auto-speak flag and
never a cached copy of it.

Same approach as test_home_panel_smoke.py: a withdrawn tk.Tk() root driven with
update_idletasks() (no window flashes, no mainloop). Fully hermetic — Calliope's
auto-speak flag is just a module-level bool, so toggling it makes NO audio and
loads NO model. Each test resets the flag in setUp/tearDown so the module global
never leaks between tests (or into test_home_panel_smoke, which snapshots it).

The colour rule under test INVERTS §7 on purpose (active = amber, inactive =
dim, hover brightens within the state), so it is asserted rather than assumed —
that is exactly the kind of local exception that rots when nothing holds it.

    python -X utf8 -m unittest tests.test_vox_controls_smoke
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import awen
import calliope
import theme
from panels.vox_controls import VoxControls, VoxToggle


# One root for the whole module, not one per class. Six classes each building
# and destroying their own root left Tk propagating a <<ThemeChanged>> to a
# window that had already gone, which prints to stderr and passes anyway — the
# same quiet-rot shape test_panel_after_teardown exists to catch.
_ROOT = None


def setUpModule():
    global _ROOT
    _ROOT = tk.Tk()
    _ROOT.withdraw()
    theme._init_fonts(_ROOT)


def tearDownModule():
    _ROOT.destroy()


class _VoxCase(unittest.TestCase):

    @property
    def root(self) -> tk.Tk:
        return _ROOT

    def setUp(self):
        calliope.set_auto_speak(False)
        self._saved_dj = awen._enabled
        awen._enabled = False
        self.ended = []
        self.reset = []
        self.notices = []

    def tearDown(self):
        calliope.set_auto_speak(False)
        # Awen's flag is a module global and this row is its only UI mutator, so
        # a leaked True would leave a later file's tests with a live Auto-DJ.
        awen._enabled = self._saved_dj

    def _row(self) -> VoxControls:
        row = VoxControls(self.root,
                          on_end=lambda: self.ended.append(1),
                          on_reset=lambda: self.reset.append(1),
                          on_notice=self.notices.append)
        self.root.update_idletasks()
        return row


class TestTheRowIsWords(_VoxCase):

    def test_the_four_controls_are_labelled_in_words(self):
        row = self._row()
        self.assertEqual(row._response._label.cget("text"), "Vox Response")
        self.assertEqual(row._end._label.cget("text"), "Vox End")
        self.assertEqual(row._dj._label.cget("text"), "Auto-DJ")
        self.assertEqual(row._reset._label.cget("text"), "Reset Session")

    def test_auto_dj_sits_directly_above_reset_session(self):
        """Where it was asked for, and it is not arbitrary: it belongs with the
        other two STATE controls rather than beside the momentary one.

        Reads PACK order, not winfo_children(). An unpacked widget is still a
        child, so the first version of this passed with the toggle built and
        invisible — found by a red-check that stayed green."""
        row = self._row()
        inner = row.winfo_children()[0]
        order = [w._label.cget("text") for w in inner.pack_slaves()]
        self.assertEqual(order, ["Vox Response", "Vox End", "Auto-DJ",
                                 "Reset Session"])

    def test_every_control_is_actually_packed(self):
        row = self._row()
        inner = row.winfo_children()[0]
        for control in (row._response, row._end, row._dj, row._reset):
            with self.subTest(label=control._label.cget("text")):
                self.assertIn(control, inner.pack_slaves())

    def test_every_control_is_dim_at_rest(self):
        row = self._row()
        for control in (row._response, row._end, row._dj, row._reset):
            self.assertEqual(control._label.cget("fg"), theme.C["text3"])


class TestAutoDj(_VoxCase):
    """The fourth control. Same discipline as Vox Response — read awen's real
    flag, never a cached copy — with one extra reason: Auto-DJ can turn ITSELF
    off (its breaker gives up after three turns that played nothing), so a
    cached lamp would sit amber over a job that had already stopped."""

    def test_starts_dim_when_auto_dj_is_off(self):
        self.assertEqual(self._row()._dj._label.cget("fg"), theme.C["text3"])

    def test_starts_lit_when_auto_dj_is_already_on(self):
        awen._enabled = True
        self.assertEqual(self._row()._dj._label.cget("fg"), theme.C["amber"])

    def test_clicking_flips_awens_real_flag(self):
        row = self._row()
        row._toggle_dj()
        self.assertTrue(awen.enabled())
        row._toggle_dj()
        self.assertFalse(awen.enabled())

    def test_refresh_reads_the_real_flag_not_a_cached_copy(self):
        """The breaker path: nobody clicked, and the lamp still has to follow."""
        awen._enabled = True
        row = self._row()
        self.assertEqual(row._dj._label.cget("fg"), theme.C["amber"])
        awen._enabled = False                  # as the breaker would
        row.refresh()
        self.assertEqual(row._dj._label.cget("fg"), theme.C["text3"])

    def test_a_notice_is_handed_up_rather_than_spoken(self):
        """Switching on with an empty ledger answers with a line. This row owns
        styling and nothing else, so it must pass that up, not say it."""
        row = self._row()
        with mock.patch("awen.toggle", return_value="say this"):
            row._toggle_dj()
        self.assertEqual(self.notices, ["say this"])

    def test_no_notice_means_nothing_is_handed_up(self):
        row = self._row()
        with mock.patch("awen.toggle", return_value=None):
            row._toggle_dj()
        self.assertEqual(self.notices, [])

    def test_the_lamp_follows_a_toggle_that_refused_to_arm(self):
        """set_enabled() can return a line AND still leave the flag on, so the
        lamp is painted from the flag that comes back, not from the click."""
        row = self._row()
        with mock.patch("awen.toggle", side_effect=lambda: (
                setattr(awen, "_enabled", True), "no history")[1]):
            row._toggle_dj()
        self.assertEqual(row._dj._label.cget("fg"), theme.C["amber"])


class TestVoxResponse(_VoxCase):

    def test_starts_lit_when_auto_speak_is_already_on(self):
        calliope.set_auto_speak(True)
        row = self._row()
        self.assertEqual(row._response._label.cget("fg"), theme.C["amber"])

    def test_click_flips_calliopes_flag_and_repaints_each_time(self):
        row = self._row()
        self.assertEqual(row._response._label.cget("fg"), theme.C["text3"])

        row._toggle_response()                       # → on
        self.root.update_idletasks()
        self.assertTrue(calliope.auto_speak_enabled())
        self.assertEqual(row._response._label.cget("fg"), theme.C["amber"])

        row._toggle_response()                       # → off
        self.root.update_idletasks()
        self.assertFalse(calliope.auto_speak_enabled())
        self.assertEqual(row._response._label.cget("fg"), theme.C["text3"])

    def test_refresh_reads_the_real_flag_not_a_cached_copy(self):
        # Single source of truth: flip the flag underneath the row, then
        # refresh — it must reflect Calliope, never a local guess.
        row = self._row()
        self.assertEqual(row._response._label.cget("fg"), theme.C["text3"])

        calliope.set_auto_speak(True)                # changed externally
        row.refresh()
        self.root.update_idletasks()
        self.assertEqual(row._response._label.cget("fg"), theme.C["amber"])


class TestVoxEnd(_VoxCase):

    def test_set_armed_lights_and_dims_it(self):
        row = self._row()
        row.set_armed(True)
        self.assertEqual(row._end._label.cget("fg"), theme.C["amber"])
        row.set_armed(False)
        self.assertEqual(row._end._label.cget("fg"), theme.C["text3"])

    def test_click_fires_while_disarmed(self):
        # THE regression this row inherited from the old Stop glyph: narration
        # outlives generation, so the control must work when it looks dim.
        row = self._row()
        row.set_armed(False)
        row._end._on_click()
        self.assertEqual(len(self.ended), 1)


class TestResetSession(_VoxCase):

    def test_click_fires_the_callback(self):
        row = self._row()
        row._reset._on_click()
        self.assertEqual(len(self.reset), 1)

    def test_flash_lights_it_then_hands_it_back(self):
        row = self._row()
        row.flash_reset()
        self.assertEqual(row._reset._label.cget("fg"), theme.C["amber"])

        row._reset._end_flash()                      # what the timer would do
        self.root.update_idletasks()
        self.assertEqual(row._reset._label.cget("fg"), theme.C["text3"])

    def test_flash_does_not_disturb_the_active_state_underneath(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle.set_active(True)
        toggle.flash()
        toggle._end_flash()
        self.assertEqual(toggle._label.cget("fg"), theme.C["amber"])   # still active


class TestTheDotAgreesWithItsLabel(_VoxCase):
    """The dot is SidebarRow's dot with the polarity inverted: there, active is
    green and everything else is amber; here amber IS active, because the row
    sits beside the face. Getting that backwards would light a dot green at the
    exact moment its own word turned amber, so the two are asserted together —
    one computed colour, both surfaces, every state."""

    def _dot(self, toggle) -> str:
        return toggle._dot.itemcget(toggle._dot_item, "fill")

    def test_dot_and_word_are_dim_together(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        self.assertEqual(self._dot(toggle), theme.C["text3"])
        self.assertEqual(toggle._label.cget("fg"), theme.C["text3"])

    def test_dot_and_word_go_amber_together(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle.set_active(True)
        self.assertEqual(self._dot(toggle), theme.C["amber"])
        self.assertEqual(toggle._label.cget("fg"), theme.C["amber"])

    def test_dot_never_borrows_the_sidebars_green(self):
        # The specific mistake this class exists to catch.
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle.set_active(True)
        self.assertNotEqual(self._dot(toggle), theme.C["green"])

    def test_dot_brightens_with_the_word_on_hover(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle._set_hover(True)
        self.assertEqual(self._dot(toggle), theme.C["text1"])
        toggle.set_active(True)
        self.assertEqual(self._dot(toggle), theme.C["amber_bright"])

    def test_dot_and_word_flash_together(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle.flash()
        self.assertEqual(self._dot(toggle), theme.C["amber"])
        self.assertEqual(toggle._label.cget("fg"), theme.C["amber"])


class TestEverySurfaceIsClickable(_VoxCase):
    """Bind only the label and the dot becomes a dead 8 px target that still
    looks like part of the control, and the hover drops out as the pointer
    crosses from the word onto the frame. SidebarRow binds every child for the
    same reason."""

    # Tk will not deliver a synthetic <Button-1> to an unmapped widget, and
    # this suite runs on a withdrawn root — so assert the wiring rather than
    # faking the click. A missing binding is the actual failure mode; the
    # handler itself is exercised directly elsewhere in this file.

    def test_every_surface_is_bound_to_the_click(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        for name, w in (("frame", toggle), ("dot", toggle._dot),
                        ("label", toggle._label)):
            with self.subTest(surface=name):
                self.assertTrue(w.bind("<Button-1>"),
                                f"{name} has no <Button-1> binding")

    def test_every_surface_is_bound_to_the_hover_pair(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        for name, w in (("frame", toggle), ("dot", toggle._dot),
                        ("label", toggle._label)):
            with self.subTest(surface=name):
                self.assertTrue(w.bind("<Enter>"), f"{name} has no <Enter>")
                self.assertTrue(w.bind("<Leave>"), f"{name} has no <Leave>")

    def test_the_handler_the_bindings_reach_fires_once(self):
        fired = []
        toggle = VoxToggle(self.root, "x", lambda: fired.append(1))
        toggle._on_click()
        self.assertEqual(len(fired), 1)

    def test_every_surface_carries_the_hand_cursor(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        for w in (toggle, toggle._dot, toggle._label):
            self.assertEqual(str(w.cget("cursor")), "hand2")


class TestHoverBrightensWithinState(_VoxCase):
    """§7 is inverted here, so hover cannot also be amber — it has to brighten
    inside whichever state the control is already in."""

    def test_dim_control_hovers_to_bright_green_not_amber(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle._set_hover(True)
        self.assertEqual(toggle._label.cget("fg"), theme.C["text1"])
        toggle._set_hover(False)
        self.assertEqual(toggle._label.cget("fg"), theme.C["text3"])

    def test_lit_control_hovers_to_a_brighter_amber(self):
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle.set_active(True)
        toggle._set_hover(True)
        self.assertEqual(toggle._label.cget("fg"), theme.C["amber_bright"])
        toggle._set_hover(False)
        self.assertEqual(toggle._label.cget("fg"), theme.C["amber"])


class TestFlashTeardown(_VoxCase):

    def _pending(self) -> set:
        return set(self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def test_a_pending_flash_does_not_outlive_the_widget(self):
        baseline = self._pending()
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle.pack()
        toggle.flash()
        self.root.update_idletasks()
        self.assertTrue(self._pending() - baseline, "nothing was scheduled")
        toggle.destroy()
        self.assertEqual(self._pending() - baseline, set())

    def test_a_second_flash_never_orphans_the_first_id(self):
        baseline = self._pending()
        toggle = VoxToggle(self.root, "x", lambda: None)
        toggle.pack()
        toggle.flash()
        toggle.flash()                               # re-arm before the first fires
        self.root.update_idletasks()
        self.assertEqual(len(self._pending() - baseline), 1)
        toggle.destroy()
        self.assertEqual(self._pending() - baseline, set())


if __name__ == "__main__":
    unittest.main()
