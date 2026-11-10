"""
panels/machine_spirit_panel.py — the MACHINE SPIRIT face inside Moderati
==========================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia's system-prompt editor and turn its edits into save
             and revert calls. The editor is a multiline text box pre-filled
             from machine_spirit.effective_prompt(), a Save button, and a
             "Revert to Default" button; edits become
             machine_spirit.save()/revert() calls — all
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

Editing:     The editor has what every editable box has (euterpe_panel's
             precedent): a right-click Cut / Copy / Paste / Select All,
             Control-a for select all, and undo (Revert is one step). Its
             PhosphorScroll is packed before it, so a narrow window can't
             squeeze the scrollbar out.

Answering:   Below the voice, THINKING (off/on) and TEMPERATURE (a 0.1 stepper,
             or DEFAULT for the model's own). Same rule again: each click goes
             to themis.save_pythia(), pythia.py reads both at the next
             question, and no JSON is touched here.

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
             themis.py (Pythia's thinking and temperature),
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
import themis
import voices
from panels.voice_bar_panel import VoiceBar
from theme import C, FONTS, PhosphorScroll, popup_menu, themed_menu

# Where the temperature stepper starts while no temperature is set: gemma4:e2b's
# own default (its /api/show parameters, 2026-11-09), so the first step moves
# from what she is actually using.
_MODEL_TEMPERATURE = 1.0
_TEMPERATURE_STEP = 0.1


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

        # ── How she answers ──────────────────────────────────────────────────
        # Thinking and temperature. Like the voice, each click is saved at once
        # (themis.save_pythia) and read by pythia at the next question, so they
        # are not fields SAVE commits.
        settings = themis.load()
        self._thinking: bool = bool(settings["pythia_thinking"])
        self._temperature = settings["pythia_temperature"]   # None: the model's own

        think_row = tk.Frame(self, bg=C["card"])
        think_row.pack(fill="x")
        tk.Label(think_row, text="THINKING", font=FONTS["small"], fg=C["text2"],
                 bg=C["card"], anchor="w", width=12).pack(side="left")
        self._think_labels: dict[bool, tk.Label] = {}
        for value, text in ((False, "OFF"), (True, "ON")):
            lbl = tk.Label(think_row, text=text, font=FONTS["small_bold"],
                           bg=C["card"], cursor="hand2", padx=6)
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda _e, v=value: self._on_thinking(v))
            self._think_labels[value] = lbl

        temp_row = tk.Frame(self, bg=C["card"])
        temp_row.pack(fill="x", pady=(2, 0))
        tk.Label(temp_row, text="TEMPERATURE", font=FONTS["small"], fg=C["text2"],
                 bg=C["card"], anchor="w", width=12).pack(side="left")
        self._temp_down = self._link(temp_row, "−", lambda: self._on_step(-1))
        self._temp_value = tk.Label(temp_row, font=FONTS["small_bold"],
                                    bg=C["card"], width=8)
        self._temp_value.pack(side="left")
        self._temp_up = self._link(temp_row, "+", lambda: self._on_step(+1))
        self._temp_reset = self._link(temp_row, "DEFAULT",
                                      lambda: self._save_answer(self._thinking, None))
        self._temp_reset.pack_configure(padx=(10, 0))

        tk.Label(self,
                 text="Thinking: slower, more careful answers (her reasoning "
                      "is never shown). Temperature: lower is steadier, higher "
                      "more varied.",
                 font=FONTS["tiny"], fg=C["text3"], bg=C["card"], anchor="w",
                 wraplength=380, justify="left").pack(fill="x", pady=(4, 10))
        self._restyle_answer()

        editor_row = tk.Frame(self, bg=C["card"])
        editor_row.pack(fill="both", expand=True)
        # height is a token request, not the editor's size: it grows to fill
        # the tab, and at the 500 px minimum window the rows above would push
        # SAVE and its status line off the bottom if it asked for ten lines
        # (CONVENTIONS §7; measured in a 760 x 500 screenshot, 2026-11-09).
        self._editor = tk.Text(
            editor_row, font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
            insertbackground=C["text1"], highlightbackground=C["border"],
            highlightcolor=C["amber"], highlightthickness=1, borderwidth=0,
            wrap="word", height=3, padx=6, pady=4, undo=True,
        )
        # The scroll lane is packed FIRST. A tk.Text asks for 80 characters
        # (574 px at the base font); packed first, it took the whole row of a
        # narrow window and pack unmapped the scrollbar, at exactly the size
        # where the prompt overflows (measured 2026-11-09, CONVENTIONS §7).
        self._scroll = PhosphorScroll(editor_row, command=self._editor.yview)
        self._scroll.pack(side="right", fill="y")
        self._editor.pack(side="left", fill="both", expand=True)
        self._editor.configure(yscrollcommand=self._scroll.set)
        self._editor.insert("1.0", machine_spirit.effective_prompt())
        self._editor.edit_reset()       # the prefill is not an undoable edit

        # The right-click menu and Control-a every other editable box has
        # (euterpe_panel's precedent). Control-c/v/x already work, and
        # Control-z does with undo=True.
        self._editor.bind("<Button-3>", self._show_text_menu)
        self._editor.bind("<Control-a>", self._select_all)
        self._editor.bind("<Control-A>", self._select_all)

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

    # ── right-click menu (the euterpe_panel precedent) ─────────────────────────

    def _show_text_menu(self, event) -> None:
        """Cut and Copy only with a selection; Paste and Select All always.
        Built fresh per click, so the states match the live selection."""
        has_selection = bool(self._editor.tag_ranges("sel"))
        menu = themed_menu(self)
        menu.add_command(label="Cut", command=lambda: self._editor.event_generate("<<Cut>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Copy", command=lambda: self._editor.event_generate("<<Copy>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Paste", command=lambda: self._editor.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=self._select_all)
        popup_menu(menu, event)

    def _select_all(self, event=None) -> str:
        """Select the whole prompt. Returns "break" so Control-a REPLACES Tk's
        own binding (beginning of line) rather than running before it."""
        self._editor.tag_add("sel", "1.0", "end")
        return "break"

    # ── how she answers ────────────────────────────────────────────────────────

    def _link(self, parent: tk.Widget, text: str, action) -> tk.Label:
        """A clickable word (§7: hand cursor, amber on hover)."""
        lbl = tk.Label(parent, text=text, font=FONTS["small_bold"], fg=C["text2"],
                       bg=C["card"], cursor="hand2", padx=6)
        lbl.pack(side="left")
        lbl.bind("<Button-1>", lambda _e: action())
        lbl.bind("<Enter>", lambda _e: lbl.config(fg=C["amber"]))
        lbl.bind("<Leave>", lambda _e: self._restyle_answer())
        return lbl

    def _on_thinking(self, value: bool) -> None:
        if value != self._thinking:
            self._save_answer(value, self._temperature)

    def _on_step(self, direction: int) -> None:
        """One step from the current temperature, or from the model's own when
        none is set, kept inside themis's range."""
        start = _MODEL_TEMPERATURE if self._temperature is None else self._temperature
        value = start + direction * _TEMPERATURE_STEP
        value = min(themis.TEMPERATURE_MAX, max(themis.TEMPERATURE_MIN, value))
        self._save_answer(self._thinking, value)    # themis rounds it

    def _save_answer(self, thinking: bool, temperature) -> None:
        """Persist both through themis, then show them. themis validates; this
        panel never writes the settings file itself."""
        try:
            saved = themis.save_pythia(thinking=thinking, temperature=temperature)
        except (themis.SettingsError, OSError) as e:
            self._status.config(text=f"Not saved — {e}", fg=C["red"])
            return
        changed_thinking = thinking != self._thinking
        # What themis wrote, not what was asked: it rounds the temperature, and
        # a 0.1 step taken from 1.0 seven times is otherwise 0.30000000000000004.
        self._thinking = bool(saved["pythia_thinking"])
        self._temperature = saved["pythia_temperature"]
        temperature = self._temperature
        self._restyle_answer()
        if changed_thinking:
            text = ("Thinking on — slower, more careful answers from the next "
                    "question." if thinking else "Thinking off.")
        elif temperature is None:
            text = "Temperature: the model's own default, from the next question."
        else:
            text = f"Temperature {self._shown(temperature)}, from the next question."
        self._status.config(text=text, fg=C["text1"])

    @staticmethod
    def _shown(temperature: float) -> str:
        """0.35 -> "0.35", 0.7 -> "0.7", 1.0 -> "1.0"."""
        text = f"{temperature:.2f}"
        return text[:-1] if text.endswith("0") else text

    def _restyle_answer(self) -> None:
        for value, lbl in self._think_labels.items():
            lbl.config(fg=C["text1"] if value == self._thinking else C["text3"])
        for lbl in (self._temp_down, self._temp_up):
            lbl.config(fg=C["text2"])
        if self._temperature is None:
            self._temp_value.config(text="default", fg=C["text2"])
            self._temp_reset.config(fg=C["text3"])          # already there
        else:
            self._temp_value.config(text=self._shown(self._temperature),
                                    fg=C["text1"])
            self._temp_reset.config(fg=C["text2"])

    def _on_revert(self) -> None:
        """Preview only — repopulate the editor with the default text. The
        override isn't cleared until the user presses Save. One undoable step,
        so Control-z brings the edited text back."""
        # Tk puts a separator wherever an edit switches from deleting to
        # inserting, which would make this two undo steps (the first leaving an
        # empty box). Off for the swap, then back on.
        self._editor.configure(autoseparators=False)
        self._editor.edit_separator()
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", machine_spirit.DEFAULT_SYSTEM_PROMPT)
        self._editor.edit_separator()
        self._editor.configure(autoseparators=True)
        self._status.config(
            text="Reverted to default in the editor — press Save to commit it.",
            fg=C["text2"])
