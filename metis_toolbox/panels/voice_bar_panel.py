"""
panels/voice_bar_panel.py — the voice-selection bar
===================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Let the user pick one kokoro voice from voices.CATALOG and hand the
             id to a callback. Nothing else — VoiceBar does not synthesize, does
             not persist, and does not know which section it is controlling.

Contract:    VoiceBar(parent, *, initial, on_change, caption="VOICE")
                 current() -> str        — the selected voice id
             on_change(voice_id) fires on every pick, including a re-pick of the
             same value (tkinter's OptionMenu offers no "changed" signal, and a
             cheap idempotent callback is simpler than tracking the last value).

Placement:   A bare tk.Frame sub-widget, mounted TWICE — the HestiaBar precedent
             (panels/hestia_panel.py): parent first, callbacks keyword-only.
               - Moderati -> MACHINE SPIRIT, caption "NARRATION VOICE": the
                 voice Pythia's answers are read aloud in. Persisted.
               - Vox Array -> ECHO, caption "VOICE": the voice ONE text->.opus
                 conversion is rendered in. Session-only.
             Each instance controls only its own section. That independence is
             the whole reason calliope.synthesize() takes a voice parameter —
             see calliope.py's "Voice" section.

Labels:     The menu shows voices.label(id) ("Nicole — American female"), but
            on_change and current() speak in IDS. The mapping is one-way and
            lives entirely in here, so no caller ever has to parse a label back.
            Display + one action, no state beyond the StringVar.

Upstream:    panels/machine_spirit_panel.py, panels/echo_panel.py
Downstream:  voices.py (the roster + labels), theme.py (colors/fonts)

Requires:    tkinter (stdlib). No persistence here — that is voices.py's job,
             and Echo's pick is deliberately not persisted at all.
"""

import tkinter as tk
from typing import Callable

import voices
from theme import C, FONTS


class VoiceBar(tk.Frame):
    """A caption + a themed voice dropdown. Mounted in both the MACHINE SPIRIT
    and ECHO tabs; each instance controls only its own section."""

    def __init__(self, parent: tk.Widget, *, initial: str,
                 on_change: Callable[[str], None],
                 caption: str = "VOICE") -> None:
        super().__init__(parent, bg=C["card"])

        self._on_change = on_change
        # label -> id, so the menu can read like prose while the callback still
        # speaks kokoro's language.
        self._ids = {voices.label(v): v for v in voices.CATALOG}

        if initial not in voices.CATALOG:
            initial = voices.CATALOG[0]
        self._voice = initial

        tk.Label(self, text=caption, font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(side="left", padx=(0, 6))

        # tk.OptionMenu styling copied verbatim from the watchlist picker in
        # panels/midas_panel.py — the app's only other dropdown.
        self._var = tk.StringVar(value=voices.label(initial))
        menu = tk.OptionMenu(self, self._var, *self._ids.keys(),
                             command=self._on_pick)
        menu.config(font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
                    activebackground=C["border"], activeforeground=C["text1"],
                    highlightthickness=0, bd=0, width=26, anchor="w")
        menu["menu"].config(bg=C["bar_bg"], fg=C["text1"])
        menu.pack(side="left")

    def current(self) -> str:
        """The selected voice id (never a label)."""
        return self._voice

    def _on_pick(self, chosen_label: str) -> None:
        self._voice = self._ids.get(chosen_label, self._voice)
        self._on_change(self._voice)
