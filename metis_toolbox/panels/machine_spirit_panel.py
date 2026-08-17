"""
panels/machine_spirit_panel.py — the MACHINE SPIRIT face inside Moderati
==========================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia's system-prompt editor: a multiline text box
             pre-filled from machine_spirit.effective_prompt(), a Save
             button, and a "Revert to Default" button. Its ONE job is to
             turn edits into machine_spirit.save()/revert() calls — all
             persistence and the default text itself live in
             machine_spirit.py; this module never reads or writes
             machine_spirit_config.json directly.

Voice:       The tab also carries the NARRATION VOICE bar — a shared VoiceBar
             (panels/voice_bar_panel.py) also mounted in the ECHO tab, where it
             controls that tab alone. Same delegation rule as the prompt: the
             pick goes to voices.save_pythia_voice() (persistence) and
             calliope.set_voice() (live effect), and no JSON is touched here.
             It sits ABOVE the editor and applies immediately — it is not a
             field SAVE commits, and the "applies immediately" hint says so.

Placement:   A bare tk.Frame tab body inside ModeratiPanel (the MACHINE
             SPIRIT tab), the same shape as ThemisPanel. NOT Kairos-
             registered — purely request-driven (the user edits and saves),
             so there is no update(data) here and no after()/thread.

Revert:      "Revert to Default" only REPOPULATES the editor with
             DEFAULT_SYSTEM_PROMPT — a preview. It does not touch the saved
             override by itself; the user must then press Save (which, on
             the now-default text, clears the override) to commit it. This
             makes the escape hatch from a bad edit non-destructive: you can
             preview the default and still back out before committing.

Upstream:    panels/moderati_panel.py (hosts this as the MACHINE SPIRIT tab)
Downstream:  machine_spirit.py (all persistence + the default text),
             voices.py (the roster + the saved narration voice),
             calliope.py (set_voice — live narration),
             panels/voice_bar_panel.py (the dropdown),
             theme.py (colors/fonts/PhosphorScroll)

Requires:    tkinter (stdlib). No JSON handling here — that is
             machine_spirit.py's and voices.py's job.

V0.02:       Audited 2026-07-24 — the preview-only Revert, the delegate-to-
             machine_spirit persistence boundary, and the no-update()/no-thread
             request-driven shape all verified honest; no code change, only this
             marker. (Save handles no I/O error, but neither does themis_panel,
             its cited precedent — that try/except is for input validation,
             which this editor has none of.)
"""

import tkinter as tk

import calliope
import machine_spirit
import voices
from panels.voice_bar_panel import VoiceBar
from theme import C, FONTS, PhosphorScroll


class MachineSpiritPanel(tk.Frame):
    """Pythia's system-prompt editor. A bare Frame tab body inside
    ModeratiPanel; no Kairos worker, no update() — purely request-driven."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=C["card"])

        tk.Label(self, text="MACHINE SPIRIT — Pythia's system prompt",
                 font=FONTS["card_header"], fg=C["text2"], bg=C["card"],
                 anchor="w").pack(fill="x", pady=(8, 2))
        tk.Label(self,
                 text="This is the instruction Pythia reads before every "
                      "answer. Edits take effect on the next question — no "
                      "restart needed.",
                 font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                 anchor="w", wraplength=380, justify="left").pack(fill="x", pady=(0, 10))

        # ── Narration voice ──────────────────────────────────────────────────
        # Above the editor, with its own "applies immediately" hint: this is a
        # different concern from the prompt, and must not read as a field that
        # SAVE commits. Persistence is voices.py's — this panel only delegates.
        voice_row = tk.Frame(self, bg=C["card"])
        voice_row.pack(fill="x", pady=(0, 10))
        VoiceBar(voice_row, initial=voices.pythia_voice(),
                 on_change=self._on_voice,
                 caption="NARRATION VOICE").pack(side="left")
        tk.Label(voice_row, text="applies immediately", font=FONTS["tiny"],
                 fg=C["text3"], bg=C["card"]).pack(side="left", padx=(10, 0))

        editor_row = tk.Frame(self, bg=C["card"])
        editor_row.pack(fill="both", expand=True)
        self._editor = tk.Text(
            editor_row, font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
            insertbackground=C["text1"], highlightbackground=C["border"],
            highlightcolor=C["amber"], highlightthickness=1, borderwidth=0,
            wrap="word", height=10, padx=6, pady=4,
        )
        self._editor.pack(side="left", fill="both", expand=True)
        scroll = PhosphorScroll(editor_row, command=self._editor.yview)
        scroll.pack(side="right", fill="y")
        self._editor.configure(yscrollcommand=scroll.set)
        self._editor.insert("1.0", machine_spirit.effective_prompt())

        btn_row = tk.Frame(self, bg=C["card"])
        btn_row.pack(fill="x", pady=(8, 0))

        save = tk.Label(btn_row, text="SAVE", font=FONTS["small_bold"],
                        fg=C["text1"], bg=C["card"], cursor="hand2")
        save.pack(side="left")
        save.bind("<Button-1>", lambda e: self._on_save())
        save.bind("<Enter>", lambda e: save.config(fg=C["amber"]))
        save.bind("<Leave>", lambda e: save.config(fg=C["text1"]))

        revert = tk.Label(btn_row, text="REVERT TO DEFAULT", font=FONTS["small_bold"],
                          fg=C["text2"], bg=C["card"], cursor="hand2")
        revert.pack(side="left", padx=(16, 0))
        revert.bind("<Button-1>", lambda e: self._on_revert())
        revert.bind("<Enter>", lambda e: revert.config(fg=C["amber"]))
        revert.bind("<Leave>", lambda e: revert.config(fg=C["text2"]))

        self._status = tk.Label(self, text="", font=FONTS["tiny"], fg=C["text3"],
                                bg=C["card"], anchor="w", wraplength=380,
                                justify="left")
        self._status.pack(fill="x", pady=(8, 0))

    # ── actions ────────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        text = self._editor.get("1.0", "end")
        machine_spirit.save(text)
        self._status.config(text="Saved ✓ — takes effect on the next question.",
                            fg=C["text1"])

    def _on_voice(self, voice: str) -> None:
        """Persist the pick, then point live narration at it. Both halves are
        needed: voices.py makes it survive a restart, calliope.set_voice() makes
        it take effect on the next spoken line."""
        voices.save_pythia_voice(voice)
        calliope.set_voice(voice)
        self._status.config(
            text=f"Narration voice: {voices.label(voice)}.", fg=C["text1"])

    def _on_revert(self) -> None:
        """Preview only — repopulate the editor with the default text. The
        override isn't cleared until the user presses Save."""
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", machine_spirit.DEFAULT_SYSTEM_PROMPT)
        self._status.config(
            text="Reverted to default in the editor — press Save to commit it.",
            fg=C["text2"])
