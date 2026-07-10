"""
theme.py — Visual Identity
===========================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Own the visual language of the Felhaven stack.
             One source of truth for colors, fonts, and shared widgets.
             Every GUI module imports from here — nothing duplicates here.

Exports:
    C               — color palette dict
    FONTS           — live tkfont.Font registry (populated by _init_fonts)
    _init_fonts(root)
                    — must be called once after tk.Tk() exists, before widgets
    rescale_fonts(width, height, base_w, base_h)
                    — call on <Configure> to keep fonts proportional;
                      pass each app's own baseline geometry
    Card            — base card widget used by all panels
    PhosphorScroll  — in-theme vertical scrollbar (drop-in tk.Scrollbar replacement)

Upstream:    felhaven.py, scribe.py
Downstream:  none
"""

import tkinter as tk
import tkinter.font as tkfont


# ─────────────────────────────────────────────────────────────────────────────
#  Color palette
# ─────────────────────────────────────────────────────────────────────────────

C = {
    "bg":        "#0A0E0A",   # black — faintest green cast (set to #000000 for pure black)
    "card":      "#0C120C",   # panel fill — one hair above bg
    "border":    "#2E7D32",   # phosphor border (rendered at thickness 2 now)
    "text1":     "#7CFC7C",   # bright phosphor — primary / active text
    "text2":     "#4FB04F",   # mid phosphor — labels
    "text3":     "#2F7A2F",   # dim phosphor — subtitles / inactive
    "amber":     "#D6A11F",   # THE uniform nav dot
    "green":     "#7CFC7C",   # active nav dot (== text1, so the active item "lights up")
    # ── decorative accents, folded into the phosphor ramp so the theme reads monochrome ──
    "teal":      "#4FB04F",
    "blue":      "#4FB04F",
    "purple":    "#4FB04F",
    "coral":     "#D6A11F",
    "gray":      "#2F7A2F",
    # ── genuinely semantic — kept distinct ──
    "red":       "#E24B4A",   # alarm / expired timer ONLY
    # ── badges & bars ──
    "badge_bg":  "#0F2A0F",
    "badge_fg":  "#9DFF9D",
    "season_bg": "#0C240C",
    "season_fg": "#7CFC7C",
    "bar_bg":    "#0E160E",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Font registry
#  Full set used across the stack. Consumers use what they need.
#  name -> (base_size, family, weight, slant, extra_kwargs)
# ─────────────────────────────────────────────────────────────────────────────

_FONT_SPECS = {
    "card_header":   (8,  "Consolas", "normal", "roman",  {}),
    "tiny":          (9,  "Consolas", "normal", "roman",  {}),
    "small":         (10, "Consolas", "normal", "roman",  {}),
    "small_bold":    (10, "Consolas", "bold",   "roman",  {}),
    "small_italic":  (10, "Consolas", "normal", "italic", {}),
    "body":          (11, "Consolas", "normal", "roman",  {}),
    "body_strike":   (11, "Consolas", "normal", "roman",  {"overstrike": 1}),
    "medium":        (12, "Consolas", "normal", "roman",  {}),
    "large_bold":    (14, "Consolas", "bold",   "roman",  {}),
    "xlarge_bold":   (24, "Consolas", "bold",   "roman",  {}),
    "title":         (18, "Georgia",  "bold",   "roman",  {}),
    "subtitle":      (11, "Georgia",  "normal", "italic", {}),
}

FONTS    = {}   # name -> tkfont.Font (populated after root exists)
_FONT_BASE = {} # name -> base size (for rescaling)


def _init_fonts(root):
    """
    Create shared Font objects. Must be called once after tk.Tk() exists
    and before any widget references FONTS.
    Safe to call again on a new root — replaces the existing registry.
    """
    for name, (size, family, weight, slant, extra) in _FONT_SPECS.items():
        FONTS[name] = tkfont.Font(root=root, family=family, size=size,
                                  weight=weight, slant=slant, **extra)
        _FONT_BASE[name] = size


def rescale_fonts(width: int, height: int, base_w: int, base_h: int) -> float:
    """
    Recompute every font's size proportionally to the current window dimensions.

    base_w / base_h are the app's baseline geometry (the size at scale=1.0).
    Felhaven passes 960, 620. Scribe passes 540, 500.
    Only grows — never shrinks below the base size.
    Capped at 3x to prevent 4K ballooning.

    Returns the scale factor so callers can size other proportional elements
    (e.g. a fixed-width sidebar) in lockstep with the fonts.
    """
    scale = max(1.0, min(width / base_w, height / base_h))
    scale = min(scale, 3.0)
    for name, base in _FONT_BASE.items():
        FONTS[name].configure(size=max(1, int(round(base * scale))))
    return scale


# ─────────────────────────────────────────────────────────────────────────────
#  Shared widgets
# ─────────────────────────────────────────────────────────────────────────────

class Card(tk.Frame):
    """
    Bordered card with a colored dot and uppercase header label.
    Base class for all dashboard panels.
    """
    def __init__(self, parent, label_text: str, dot_color: str, **kw):
        super().__init__(parent, bg=C["card"], highlightbackground=C["border"],
                         highlightthickness=2, padx=14, pady=12, **kw)
        header = tk.Frame(self, bg=C["card"])
        header.pack(fill="x", anchor="w")
        dot = tk.Canvas(header, width=8, height=8, bg=C["card"], highlightthickness=0)
        dot.create_oval(1, 1, 7, 7, fill=dot_color, outline="")
        dot.pack(side="left", padx=(0, 6))
        self._header_lbl = tk.Label(
            header, text=label_text.upper(), font=FONTS["card_header"],
            fg=C["text3"], bg=C["card"], anchor="w"
        )
        self._header_lbl.pack(side="left")

        self.body = tk.Frame(self, bg=C["card"])
        self.body.pack(fill="both", expand=True)

    def set_header(self, text: str) -> None:
        """Update the card's header label (upcased, like the constructor). Lets a
        panel reflect runtime-changed state — e.g. Aura re-titling to the live
        weather location after a Settings edit."""
        self._header_lbl.config(text=text.upper())


class PhosphorScroll(tk.Canvas):
    """
    Drop-in replacement for a vertical tk.Scrollbar, drawn in-theme.

    Contract (identical to tk.Scrollbar):
        bar = PhosphorScroll(parent, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)

    Why this exists: on Windows, tk.Scrollbar is rendered by the native
    theme engine and silently ignores bg/troughcolor/activebackground
    (CONVENTIONS §12). This Canvas subclass owns every pixel.

    Visuals: 8px lane, 1px hairline rail (text3), 6px square thumb.
    Thumb: border idle -> text2 hover -> text1 while dragging.
    When content fits (last - first >= 1) nothing is drawn; the lane
    keeps its packed width so layout never jumps.
    """

    LANE       = 8    # widget width
    THUMB_W    = 6    # thumb width, centered in the lane
    MIN_THUMB  = 24   # px — stays grabbable on very long lists
    RAIL_INSET = 4    # px trimmed off top/bottom of the hairline rail

    def __init__(self, parent, command=None, **kw):
        kw.setdefault("bg", C["card"])
        super().__init__(parent, width=self.LANE,
                         highlightthickness=0, bd=0, **kw)
        self._command = command
        self._first, self._last = 0.0, 1.0
        self._drag_anchor = None   # y-offset into the thumb while dragging
        self._hover = False

        self.bind("<Configure>",       lambda e: self._redraw())
        self.bind("<Button-1>",        self._press)
        self.bind("<B1-Motion>",       self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>",           self._enter)
        self.bind("<Leave>",           self._leave)

    # ── tk.Scrollbar contract ────────────────────────────────────────

    def set(self, first, last):
        """Called by the scrolled widget via yscrollcommand (str args)."""
        self._first, self._last = float(first), float(last)
        self._redraw()

    # ── geometry ─────────────────────────────────────────────────────

    def _thumb_box(self):
        """(y0, y1) of the thumb, or None when content fits / no height."""
        h = self.winfo_height()
        span = self._last - self._first
        if span >= 1.0 or span <= 0.0 or h <= 0:
            return None
        th = max(self.MIN_THUMB, span * h)
        travel = h - th
        # first ranges over [0, 1-span]; map it onto [0, travel] so a
        # MIN_THUMB-clamped thumb still reaches both ends of the lane.
        y0 = (self._first / (1.0 - span)) * travel if travel > 0 else 0.0
        return y0, y0 + th

    # ── drawing ──────────────────────────────────────────────────────

    def _redraw(self):
        self.delete("all")
        box = self._thumb_box()
        if box is None:
            return
        h  = self.winfo_height()
        cx = self.LANE // 2
        self.create_line(cx, self.RAIL_INSET, cx, h - self.RAIL_INSET,
                         fill=C["text3"], width=1)
        if self._drag_anchor is not None:
            color = C["text1"]
        elif self._hover:
            color = C["text2"]
        else:
            color = C["border"]
        x0 = cx - self.THUMB_W // 2
        self.create_rectangle(x0, box[0], x0 + self.THUMB_W, box[1],
                              fill=color, outline="")

    # ── interaction ──────────────────────────────────────────────────

    def _press(self, event):
        box = self._thumb_box()
        if box is None or self._command is None:
            return
        if box[0] <= event.y <= box[1]:
            self._drag_anchor = event.y - box[0]
            self._redraw()
        else:
            self._command("scroll", 1 if event.y > box[1] else -1, "pages")

    def _drag(self, event):
        if self._drag_anchor is None or self._command is None:
            return
        box = self._thumb_box()
        if box is None:
            return
        h, th = self.winfo_height(), box[1] - box[0]
        travel = h - th
        if travel <= 0:
            return
        y0 = min(max(event.y - self._drag_anchor, 0), travel)
        span = self._last - self._first
        self._command("moveto", str((y0 / travel) * (1.0 - span)))

    def _release(self, _event):
        self._drag_anchor = None
        self._redraw()

    def _enter(self, _event):
        self._hover = True
        self._redraw()

    def _leave(self, _event):
        self._hover = False
        self._redraw()
