"""
test_virgil_tooltip_smoke.py — the hover tooltip: when it shows, what it draws,
when it hides, and that it never fights a widget's own bindings.

The root is mapped but invisible (alpha 0, no frame), because a withdrawn
window is never viewable and the tooltip refuses to show over a widget that
isn't — and because the tip is transient to the root, and a transient window
stays withdrawn while its master is. The tip window is made invisible too, so
nothing flashes on screen. The pointer is never moved: virgil_tooltip._pointer_xy is
patched to put it inside or outside the widget. Events go through
event_generate, so the real bindtag handlers run; the 500 ms timer is not
waited out, _fire() is called the way the timer would call it.

Run from the package root:
    python -X utf8 -m unittest tests.test_virgil_tooltip_smoke
"""

import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import theme
from theme import C, FONTS
from panels import virgil_tooltip
from tools import virgil

CPU = virgil.term("cpu")
HEPHAESTUS = virgil.module("hephaestus")


class _TooltipCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.attributes("-alpha", 0.0)
        cls.root.overrideredirect(True)
        cls.root.geometry("320x200+40+40")
        theme._init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.host = tk.Frame(self.root, width=320, height=200)
        self.host.pack_propagate(False)
        self.host.pack()
        # A composite widget, like a SidebarRow: a frame holding a label.
        self.row = tk.Frame(self.host, width=200, height=60)
        self.row.pack_propagate(False)
        self.row.pack(padx=20, pady=20)
        self.child = tk.Label(self.row, text="Moderati")
        self.child.pack(padx=30, pady=15)
        self.host.update()
        if not self.row.winfo_viewable():
            self.skipTest("no mapped window on this display")
        self.addCleanup(self.host.destroy)

    def tip(self):
        shared = virgil_tooltip._shared(self.root)
        shared.attributes("-alpha", 0.0)
        return shared

    def attach(self, widget=None, tip=CPU):
        virgil_tooltip.attach(widget or self.row, tip)
        return self.tip()

    def pointer(self, inside=True, widget=None):
        """Patch the pointer to the middle of the widget, or well outside it."""
        w = widget or self.row
        if inside:
            xy = (w.winfo_rootx() + w.winfo_width() // 2,
                  w.winfo_rooty() + w.winfo_height() // 2)
        else:
            xy = (w.winfo_rootx() + w.winfo_width() + 50,
                  w.winfo_rooty() + w.winfo_height() + 50)
        patch = mock.patch.object(virgil_tooltip, "_pointer_xy", return_value=xy)
        patch.start()
        self.addCleanup(patch.stop)

    def pending(self):
        return set(self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def show(self, widget=None, tip=CPU):
        shared = self.attach(widget, tip)
        self.pointer(widget=widget)
        (widget or self.row).event_generate("<Enter>")
        shared._fire()
        return shared


class TestShowing(_TooltipCase):

    def test_a_hover_arms_exactly_one_pending_show(self):
        shared = self.attach()
        self.pointer()
        baseline = self.pending()
        self.row.event_generate("<Enter>")
        self.assertEqual(len(self.pending() - baseline), 1)
        self.assertIs(shared._owner, self.row)
        self.row.event_generate("<Enter>")            # back from its own child
        self.assertEqual(len(self.pending() - baseline), 1)
        shared._hide()
        self.assertEqual(self.pending() - baseline, set())

    def test_firing_early_leaves_nothing_pending(self):
        """§6: a callback can be entered without its timer having fired (as
        every test here does); it must take that timer back, not orphan it."""
        shared = self.attach()
        self.pointer()
        baseline = self.pending()
        self.row.event_generate("<Enter>")
        shared._fire()
        self.assertTrue(shared._shown)
        self.assertEqual(self.pending() - baseline, set())

    def test_a_term_draws_its_label_and_text_in_glossary_colours(self):
        shared = self.show()
        entry = virgil.glossary()["cpu"]
        self.assertTrue(shared._shown)
        self.assertEqual(shared.state(), "normal")
        self.assertEqual(shared._title.cget("text"), entry["label"])
        self.assertEqual(shared._title.cget("fg"), C["text1"])
        self.assertEqual(str(shared._title.cget("font")), str(FONTS["small_bold"]))
        self.assertEqual(shared._body.cget("text"), entry["text"])
        self.assertEqual(shared._body.cget("fg"), C["text2"])
        self.assertEqual(str(shared._body.cget("font")), str(FONTS["small"]))

    def test_a_module_gloss_is_drawn_verbatim(self):
        shared = self.show(tip=HEPHAESTUS)
        words = virgil.text_for(HEPHAESTUS)
        self.assertEqual(shared._title.cget("text"), "Hephaestus")
        self.assertEqual(shared._body.cget("text"), words.body)
        self.assertEqual(shared._body.cget("fg"), C["verbatim"])

    def test_a_tip_with_no_words_never_shows(self):
        with mock.patch.object(virgil, "text_for", return_value=None):
            shared = self.show()
        self.assertFalse(shared._shown)
        self.assertEqual(shared.state(), "withdrawn")
        self.assertIsNone(shared._owner)

    def test_the_pointer_gone_before_the_delay_means_no_show(self):
        shared = self.attach()
        self.pointer(inside=False)
        self.row.event_generate("<Enter>")       # a <Leave> that never arrived
        shared._fire()
        self.assertFalse(shared._shown)

    def test_a_view_switched_away_during_the_delay_never_shows(self):
        """felhaven packs away the whole VIEW. On Windows the widget inside
        still hears <Unmap> (measured 2026-11-09), so the handler cancels the
        show; _fire()'s viewable re-check is the backstop for an <Unmap> that
        never arrives, so this case drops that event on purpose."""
        shared = self.attach()
        self.pointer()
        self.row.event_generate("<Enter>")
        with mock.patch.object(shared, "_hide_for"):      # the <Unmap> is lost
            self.host.pack_forget()                       # an ancestor: the view
            self.root.update_idletasks()
        self.assertIs(shared._owner, self.row)
        self.assertFalse(self.row.winfo_viewable())
        shared._fire()
        self.assertFalse(shared._shown)

    def test_a_view_switch_hides_through_unmap(self):
        shared = self.show()
        self.host.pack_forget()
        self.root.update_idletasks()
        self.assertFalse(shared._shown)

    def test_one_window_serves_every_widget_on_a_root(self):
        other = tk.Label(self.host, text="RAM")
        other.pack()
        first = self.attach()
        second = self.attach(other, virgil.term("ram"))
        self.assertIs(first, second)
        tips = [w for w in self.root.winfo_children()
                if isinstance(w, virgil_tooltip._Tip)]
        self.assertEqual(len(tips), 1)

    def test_the_tip_is_borderless_and_transient(self):
        shared = self.tip()
        self.assertTrue(shared.overrideredirect())
        self.assertEqual(str(shared.transient()), str(self.root))


class TestHiding(_TooltipCase):

    def test_leave_with_the_pointer_outside_hides(self):
        shared = self.show()
        self.pointer(inside=False)
        self.row.event_generate("<Leave>")
        self.assertFalse(shared._shown)
        self.assertEqual(shared.state(), "withdrawn")

    def test_leave_onto_its_own_child_keeps_the_tip(self):
        shared = self.show()                     # the pointer stays inside
        self.row.event_generate("<Leave>")
        self.assertTrue(shared._shown)

    def test_a_click_hides_on_the_widget_or_its_child(self):
        for target in (self.row, self.child):
            with self.subTest(clicked=target.winfo_class()):
                shared = self.show()
                target.event_generate("<ButtonPress-1>")
                self.assertFalse(shared._shown)
                self.pointer(inside=False)
                self.row.event_generate("<Leave>")   # clear the quiet spell

    def test_a_click_hides_even_where_the_widget_binds_button_1(self):
        """Every SidebarRow and tab label binds <Button-1> on each of its
        widgets. The row's click and the tip's must both run."""
        clicked = []
        for w in (self.row, self.child):
            w.bind("<Button-1>", lambda _e: clicked.append("row"))
        for target in (self.row, self.child):
            with self.subTest(clicked=target.winfo_class()):
                clicked.clear()
                shared = self.show()
                target.event_generate("<ButtonPress-1>")
                self.assertEqual(clicked, ["row"], "the row's own click was lost")
                self.assertFalse(shared._shown, "the click did not hide the tip")
                self.pointer(inside=False)
                self.row.event_generate("<Leave>")   # clear the quiet spell

    def test_after_a_click_it_stays_quiet_until_the_pointer_leaves(self):
        shared = self.show()
        self.child.event_generate("<ButtonPress-1>")
        baseline = self.pending()
        self.row.event_generate("<Enter>")       # back from the label
        self.assertEqual(self.pending() - baseline, set())
        self.pointer(inside=False)
        self.row.event_generate("<Leave>")
        self.pointer()
        self.row.event_generate("<Enter>")
        self.assertEqual(len(self.pending() - baseline), 1)
        shared._hide()

    def test_unmap_hides(self):
        shared = self.show()
        self.row.pack_forget()
        self.host.update()
        self.assertFalse(shared._shown)

    def test_destroying_the_widget_hides_and_cancels(self):
        shared = self.attach()
        self.pointer()
        baseline = self.pending()
        self.row.event_generate("<Enter>")
        self.assertTrue(self.pending() - baseline)
        self.row.destroy()
        self.assertEqual(self.pending() - baseline, set())
        self.assertIsNone(shared._owner)
        self.assertNotIn(str(self.row), shared._tips)


class TestBindings(_TooltipCase):
    """The handlers hang on bindtags, never on the widget, so nothing a panel
    does to its own bindings can reach them."""

    def test_attaching_twice_tags_once_and_replaces_the_tip(self):
        self.attach(tip=CPU)
        shared = self.attach(tip=virgil.term("ram"))
        self.assertEqual(self.row.bindtags().count(virgil_tooltip._TAG), 1)
        for w in (self.row, self.child):
            self.assertEqual(w.bindtags().count(virgil_tooltip._PRESS_TAG), 1)
        self.assertEqual(self.row.bindtags()[0], virgil_tooltip._PRESS_TAG)
        self.pointer()
        self.row.event_generate("<Enter>")
        shared._fire()
        self.assertEqual(shared._title.cget("text"), "RAM")

    def test_attach_binds_nothing_on_the_widget(self):
        sequences = ("<Enter>", "<Leave>", "<Unmap>", "<Destroy>",
                     *virgil_tooltip._PRESSES)
        before = {s: self.row.bind(s) for s in sequences}
        self.attach()
        self.assertEqual({s: self.row.bind(s) for s in sequences}, before)

    def test_an_existing_enter_handler_still_fires(self):
        seen = []
        self.row.bind("<Enter>", lambda _e: seen.append("enter"))
        self.row.bind("<Leave>", lambda _e: seen.append("leave"))
        shared = self.attach()
        self.pointer()
        self.row.event_generate("<Enter>")
        self.row.event_generate("<Leave>")
        self.assertEqual(seen, ["enter", "leave"])
        self.assertIs(shared._owner, self.row)   # and the tip armed too
        shared._hide()

    def test_a_widget_that_rebinds_or_unbinds_keeps_its_tip(self):
        """Orpheus's stop button and Echo's send button bind and unbind
        <Enter>, <Leave> and <Button-1> each time they light or dim, with no
        add="+". Neither may take the tip with it."""
        for label, churn in (
            ("bind", lambda w: [w.bind(s, lambda _e: None)
                                for s in ("<Enter>", "<Leave>", "<Button-1>")]),
            ("unbind", lambda w: [w.unbind(s)
                                  for s in ("<Enter>", "<Leave>", "<Button-1>")]),
        ):
            with self.subTest(after=label):
                shared = self.attach()
                churn(self.row)
                churn(self.child)
                self.pointer()
                self.row.event_generate("<Enter>")
                shared._fire()
                self.assertTrue(shared._shown, "the tip was lost")
                self.child.event_generate("<ButtonPress-1>")
                self.assertFalse(shared._shown, "the click no longer hides it")
                self.pointer(inside=False)
                self.row.event_generate("<Leave>")

    def test_a_handler_that_breaks_cannot_starve_the_tip(self):
        for w in (self.row, self.child):
            for s in ("<Enter>", "<Leave>", "<Button-1>"):
                w.bind(s, lambda _e: "break")
        shared = self.show()
        self.assertTrue(shared._shown)
        self.child.event_generate("<ButtonPress-1>")
        self.assertFalse(shared._shown)

    def test_a_click_goes_to_the_nearest_attached_widget(self):
        """A tipped control inside a tipped box: the click quiets the control,
        and the box can still show its own tip afterwards."""
        shared = self.attach()
        virgil_tooltip.attach(self.child, virgil.term("ram"))
        self.pointer(widget=self.child)
        self.child.event_generate("<Enter>")
        self.child.event_generate("<ButtonPress-1>")
        self.assertIs(shared._quiet, self.child)

    def test_attach_never_raises(self):
        gone = tk.Label(self.host)
        gone.destroy()
        with self.assertLogs("METIS.virgil_tooltip", "WARNING"):
            virgil_tooltip.attach(gone, CPU)
            virgil_tooltip.attach(None, CPU)
            virgil_tooltip.attach_tag(None, "speak", CPU)


class TestWhen(_TooltipCase):
    """attach(..., when=): asked at show time, so one widget can have a tip
    only while it has something to explain."""

    def test_a_falsy_when_means_no_tip_and_a_truthy_one_shows(self):
        answer = [False]
        virgil_tooltip.attach(self.row, CPU, when=lambda: answer[0])
        shared = self.tip()
        self.pointer()
        self.row.event_generate("<Enter>")
        shared._fire()
        self.assertFalse(shared._shown)
        answer[0] = True
        self.row.event_generate("<Enter>")
        shared._fire()
        self.assertTrue(shared._shown)

    def test_a_when_that_raises_means_no_tip(self):
        virgil_tooltip.attach(self.row, CPU, when=lambda: 1 / 0)
        shared = self.tip()
        self.pointer()
        self.row.event_generate("<Enter>")
        shared._fire()
        self.assertFalse(shared._shown)

    def test_attaching_again_without_when_drops_it(self):
        virgil_tooltip.attach(self.row, CPU, when=lambda: False)
        shared = self.show()
        self.assertTrue(shared._shown)


class TestSpans(_TooltipCase):
    """attach_tag(): a tip on a tagged span of a Text. Tag events are raised
    by the real pointer, which is never moved here, so the handlers are called
    the way Tk would call them; _pointer_xy is patched over the span as it is
    for a widget."""

    def setUp(self):
        super().setUp()
        self.text = tk.Text(self.host, width=30, height=3)
        self.text.pack()
        self.text.tag_configure("speak")
        self.text.insert("end", "an answer\n")
        self.text.insert("end", "speak aloud", ("speak", "speak1"))
        self.text.insert("end", "\nmore\n")
        self.host.update()
        virgil_tooltip.attach_tag(self.text, "speak", CPU)
        self.shared = self.tip()
        self.span = self.shared._spans[(str(self.text), "speak")]

    def over(self, index):
        x, y, w, h = self.text.bbox(index)
        xy = (self.text.winfo_rootx() + x + w // 2,
              self.text.winfo_rooty() + y + h // 2)
        patch = mock.patch.object(virgil_tooltip, "_pointer_xy", return_value=xy)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_hover_over_the_span_shows_its_tip(self):
        self.over("2.3")
        self.shared._on_enter(self.span)
        self.shared._fire()
        self.assertTrue(self.shared._shown)
        self.assertEqual(self.shared._title.cget("text"), "CPU")

    def test_the_pointer_off_the_span_means_no_show(self):
        self.over("1.2")                 # the same Text, an untagged line
        self.shared._on_enter(self.span)
        self.shared._fire()
        self.assertFalse(self.shared._shown)

    def test_leaving_the_span_hides(self):
        self.over("2.3")
        self.shared._on_enter(self.span)
        self.shared._fire()
        self.over("1.2")
        self.shared._on_leave(self.span)
        self.assertFalse(self.shared._shown)

    def test_a_click_hides_and_quiets_until_the_pointer_leaves(self):
        self.over("2.3")
        self.shared._on_enter(self.span)
        self.shared._fire()
        self.shared._on_click(self.span)
        self.assertFalse(self.shared._shown)
        baseline = self.pending()
        self.shared._on_enter(self.span)
        self.assertEqual(self.pending() - baseline, set())

    def test_attaching_twice_binds_once(self):
        before = self.text.tag_bind("speak", "<Enter>", None)
        virgil_tooltip.attach_tag(self.text, "speak", virgil.term("ram"))
        self.assertEqual(self.text.tag_bind("speak", "<Enter>", None), before)
        self.assertEqual(self.shared._tips[(str(self.text), "speak")],
                         virgil.term("ram"))

    def test_a_per_span_tag_bound_without_add_leaves_the_tip(self):
        """Home binds each answer's own tag for its click and hover. Those
        are other tags; the shared one keeps the tip's handlers."""
        before = self.text.tag_bind("speak", "<Enter>", None)
        for s in ("<Enter>", "<Leave>", "<Button-1>"):
            self.text.tag_bind("speak1", s, lambda _e: None)
        self.assertEqual(self.text.tag_bind("speak", "<Enter>", None), before)

    def test_unmapping_or_destroying_the_text_hides_and_forgets(self):
        self.over("2.3")
        self.shared._on_enter(self.span)
        self.shared._fire()
        self.text.pack_forget()
        self.host.update()
        self.assertFalse(self.shared._shown)
        self.text.destroy()
        self.assertNotIn((str(self.text), "speak"), self.shared._tips)
        self.assertNotIn((str(self.text), "speak"), self.shared._spans)


class TestSecondaryWindows(_TooltipCase):

    def test_a_widget_in_a_toplevel_uses_the_roots_one_tip(self):
        """The Rite Log and the queue box are Toplevels of the main root."""
        window = tk.Toplevel(self.root)
        window.attributes("-alpha", 0.0)
        self.addCleanup(window.destroy)
        label = tk.Label(window, text="Rites", width=20, height=3)
        label.pack()
        window.update()
        if not label.winfo_viewable():
            self.skipTest("no mapped window on this display")
        shared = self.show(widget=label)
        self.assertIs(shared, virgil_tooltip._shared(self.root))
        self.assertTrue(shared._shown)


class TestPlacement(_TooltipCase):

    def _place_at(self, x, y):
        shared = self.show()
        with mock.patch.object(virgil_tooltip, "_pointer_xy", return_value=(x, y)):
            shared._place()
        shared.update_idletasks()
        return shared, shared.winfo_reqwidth(), shared.winfo_reqheight()

    def _origin(self, shared):
        """(x, y) from "WxH+X+Y"; Tk writes a negative offset as "+-10"."""
        x, y = re.findall(r"[+-]-?\d+", shared.geometry())
        return tuple(int(v[1:]) if v[0] == "+" else int(v) for v in (x, y))

    def test_below_right_and_never_under_the_pointer(self):
        shared, w, h = self._place_at(100, 100)
        x, y = self._origin(shared)
        self.assertGreater(x, 100)
        self.assertGreater(y, 100)

    def test_flips_left_and_up_near_the_far_edges(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        shared, w, h = self._place_at(sw - 5, sh - 5)
        x, y = self._origin(shared)
        self.assertLess(x + w, sw - 5)           # wholly left of the pointer
        self.assertLess(y + h, sh - 5)           # wholly above it
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_the_wrap_follows_the_live_font(self):
        font = FONTS["small"]
        size = font.cget("size")
        self.addCleanup(font.configure, size=size)
        shared = self.show()
        narrow = int(shared._body.cget("wraplength"))
        self.assertEqual(narrow, font.measure("0") * virgil_tooltip._WRAP_CHARS)
        font.configure(size=size * 2)
        shared._hide()
        self.row.event_generate("<Enter>")
        shared._fire()
        self.assertGreater(int(shared._body.cget("wraplength")), narrow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
