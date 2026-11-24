"""
panels/machine_spirit_panel.py — the MACHINE SPIRIT face inside Moderati
==========================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia's settings tab and hand each change to the module
             that owns it. The settings are her model, narration voice,
             thinking, temperature and system prompt; nothing is persisted
             here.

Prompt:      The editor is a multiline text box pre-filled from
             machine_spirit.effective_prompt(), a Save button, and a
             "Revert to Default" button; edits become
             machine_spirit.save() calls — all
             persistence and the default text itself live in
             machine_spirit.py; this module never reads or writes
             machine_spirit_config.json directly.

Failures:    A save that fails (any of the five) says "Not saved" in red on
             the status line, and nothing is saved or made live. The model
             and answer rows go back to what is saved; the voice dropdown
             keeps showing the pick (VoiceBar has no way to set it back),
             under the red line. A settings or voice error's own message is
             shown; an OSError's is not, since it carries a file path (§2),
             only its kind.

Model:       First, the MODEL row: Ollama's installed models that can use
             tools (a model without them would lose every tool, silently), from
             Talos's 5 s poll (update(data) — Kairos "talos"). A pick goes to
             themis.save_pythia_model() and starts pythia.switch_model(): unload
             the model Felhaven was using, warm the new one, on pythia's thread.
             The status line under it says loaded / loading / not loaded / not
             installed / Ollama not reachable. THINKING greys out for a model
             that can't think, and the TEMPERATURE stepper starts from the
             model's own default.

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
             SPIRIT tab), the same shape as ThemisPanel. Kairos-registered under
             "talos" for the MODEL row only (update(data) handles None);
             everything else is request-driven. No after(), and no thread of
             its own — the model switch runs on pythia's.

Revert:      "Revert to Default" only REPOPULATES the editor with
             DEFAULT_SYSTEM_PROMPT — a preview. It does not touch the saved
             override by itself; the user must then press Save (which, on
             the now-default text, clears the override) to commit it. This
             makes the escape hatch from a bad edit non-destructive: you can
             preview the default and still back out before committing.

Upstream:    panels/moderati_panel.py (hosts this as the MACHINE SPIRIT tab);
             felhaven.py (registers it with Kairos under "talos")
Downstream:  machine_spirit.py (all persistence + the default text),
             themis.py (Pythia's model, thinking and temperature),
             pythia.py (current_model, switch_model, is_warming),
             voices.py (the roster + the saved narration voice),
             calliope.py (set_voice — live narration),
             panels/voice_bar_panel.py (the dropdown),
             theme.py (colors/fonts/PhosphorScroll/the right-click menu),
             panels/virgil_tooltip.py + tools/virgil.py (the tips)

Requires:    tkinter (stdlib). No JSON handling here — that is
             machine_spirit.py's and voices.py's job.

2026-07-24:  walkthrough audit — the preview-only Revert, the delegate-to-
             machine_spirit persistence boundary, and the no-update()/no-thread
             request-driven shape all verified honest; no code change, only this
             marker. (Save handles no I/O error, but neither does themis_panel,
             its cited precedent — that try/except is for input validation,
             which this editor has none of.)

V0.03:       Walked through 2026-11-19 (the V0.03 walkthrough's S18). REVERT
             TO DEFAULT then SAVE stored the default as an override; it now
             clears it, as this docstring always said (the fix is in
             machine_spirit.save()). SAVE and the voice pick let a failed
             write escape the callback, so nothing on screen said so; all
             five saves now say "Not saved", and none shows an OSError's
             path. The model status no
             longer promises "about a minute" (a load is 20 to 80 s; the
             owner's HEPHAESTUS call). The editor stops repeating the theme's
             cursor colour (§7). Job, Upstream and Downstream catch up; the
             Job's first sentence is the gloss Daedalus shows the model, so
             the routing probe ran: 49/56, the same verdict on every row as
             the same evening's run without it.
"""

import tkinter as tk

import calliope
import machine_spirit
import pythia
import themis
import voices
from panels import virgil_tooltip
from panels.voice_bar_panel import VoiceBar
from theme import C, FONTS, PhosphorScroll, popup_menu, themed_menu
from tools import virgil

# Where the temperature stepper starts while no temperature is set: gemma4:e2b's
# own default (its /api/show parameters, 2026-11-09), so the first step moves
# from what she is actually using.
_MODEL_TEMPERATURE = 1.0
_TEMPERATURE_STEP = 0.1


def _not_saved(error: Exception) -> str:
    """The status line for a save that failed. A settings or voice error's
    message is written for the reader; an OSError's carries a file path, so
    only its kind is shown (§2: never raw exception text on the screen)."""
    if isinstance(error, OSError):
        return f"Not saved — the file could not be written ({type(error).__name__})."
    return f"Not saved — {error}"


class _ModelBar(tk.Frame):
    """MODEL: a dropdown of the Ollama models that can use tools. VoiceBar's
    styling (the midas_panel precedent), with two differences: its options are
    rebuilt as Talos reports them, and a saved model that isn't listed (Ollama
    down, or not pulled yet) stays shown rather than being replaced."""

    PULL_HINT = "add more: ollama pull <name>"

    def __init__(self, parent: tk.Widget, *, current: str, on_pick) -> None:
        super().__init__(parent, bg=C["card"])
        self._on_pick = on_pick
        self._names: list[str] = []
        tk.Label(self, text="MODEL", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(side="left", padx=(0, 6))
        self._var = tk.StringVar(value=current)
        self._menu = tk.OptionMenu(self, self._var, current)
        self._menu.config(font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
                          activebackground=C["border"], activeforeground=C["text1"],
                          highlightthickness=0, bd=0, width=26, anchor="w")
        self._menu["menu"].config(bg=C["bar_bg"], fg=C["text1"])
        self._menu.pack(side="left")
        self.set_options([], current)

    def current(self) -> str:
        return self._var.get()

    def set_options(self, names: list[str], current: str) -> None:
        """Rebuild the list: `names`, plus `current` if it isn't among them,
        then the pull hint as a greyed last entry. Skipped when nothing
        changed, so a 5 s poll never rebuilds a menu someone has open."""
        listed = list(names) if current in names else [current, *names]
        if listed == self._names and self._var.get() == current:
            return
        self._names = listed
        menu = self._menu["menu"]
        menu.delete(0, "end")
        for name in listed:
            menu.add_command(label=name, command=lambda n=name: self._picked(n))
        menu.add_separator()
        menu.add_command(label=self.PULL_HINT, state="disabled")
        self._var.set(current)

    def _picked(self, name: str) -> None:
        self._var.set(name)
        self._on_pick(name)


class MachineSpiritPanel(tk.Frame):
    """Pythia's model, voice, thinking, temperature and system prompt. A bare
    Frame tab body inside ModeratiPanel. Registered with Kairos under "talos"
    for the model list and the loaded model (update(data)); everything else is
    request-driven."""

    # Hover tooltips (Virgil) for the three settings rows whose names are
    # jargon, each a glossary entry (CONVENTIONS §7). Keys name the rows in
    # self.tip_targets; a tip on a row covers its label and its controls.
    _TIPS = {
        "model": virgil.term("llm_model"),
        "thinking": virgil.term("llm_thinking"),
        "temperature": virgil.term("llm_temperature"),
    }

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=C["card"])

        tk.Label(self, text="MACHINE SPIRIT — Pythia's model and system prompt",
                 font=FONTS["card_header"], fg=C["text2"], bg=C["card"],
                 anchor="w").pack(fill="x", pady=(8, 2))
        tk.Label(self,
                 text="The model Pythia thinks with, and the instruction she "
                      "reads first. Changes apply to her next question.",
                 font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                 anchor="w", wraplength=440, justify="left").pack(fill="x", pady=(0, 8))

        # ── Model ────────────────────────────────────────────────────────────
        # A pick is saved at once (themis.save_pythia_model) and starts the
        # switch (pythia.switch_model: unload the old, warm the new). The list
        # and the status line follow Talos's 5 s poll through update().
        self._talos = None                       # the latest talos.fetch(), or None
        self._model_thinks = True                # unknown until Talos says
        self._model_temperature = _MODEL_TEMPERATURE
        model_row = tk.Frame(self, bg=C["card"])
        model_row.pack(fill="x")
        self._model_bar = _ModelBar(model_row, current=pythia.current_model(),
                                    on_pick=self._on_model)
        self._model_bar.pack(side="left")
        self._model_status = tk.Label(self, text="", font=FONTS["tiny"],
                                      fg=C["text3"], bg=C["card"], anchor="w",
                                      wraplength=440, justify="left")
        self._model_status.pack(fill="x", pady=(2, 6))

        # ── Narration voice ──────────────────────────────────────────────────
        # Above the editor, with its own "applies immediately" hint: this is a
        # different concern from the prompt, and must not read as a field that
        # SAVE commits. Persistence is voices.py's — this panel only delegates.
        voice_row = tk.Frame(self, bg=C["card"])
        voice_row.pack(fill="x", pady=(0, 6))
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
        # Shown when the chosen model can't think: the setting still saves (it
        # applies to a model that can), but this model is never asked.
        self._think_note = tk.Label(think_row, text="", font=FONTS["tiny"],
                                    fg=C["text3"], bg=C["card"])
        self._think_note.pack(side="left", padx=(10, 0))

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
                 text="Thinking: slower, more careful answers, reasoning "
                      "never shown. Temperature: lower is steadier.",
                 font=FONTS["tiny"], fg=C["text3"], bg=C["card"], anchor="w",
                 wraplength=440, justify="left").pack(fill="x", pady=(4, 6))
        self._restyle_answer()

        self.tip_targets: dict[str, tk.Misc] = {
            "model": model_row, "thinking": think_row, "temperature": temp_row}
        for key, tip in self._TIPS.items():
            virgil_tooltip.attach(self.tip_targets[key], tip)

        # The editor is packed LAST, and SAVE and the status line FIRST (see
        # below): pack hands out requested heights in pack order, so whatever
        # comes last is what shrinks, and that must be the box that scrolls
        # (CONVENTIONS §7). The MODEL row pushed SAVE off the bottom of a
        # 760 x 500 window when they came after the editor (screenshot,
        # 2026-11-09). height is a token request; the editor grows to fill.
        editor_row = tk.Frame(self, bg=C["card"])
        self._editor = tk.Text(
            editor_row, font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
            highlightbackground=C["border"],
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

        btn_row = self._btn_row = tk.Frame(self, bg=C["card"])

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
                                bg=C["card"], anchor="w", wraplength=440,
                                justify="left")
        # Pack order is priority: the status line and SAVE go to the FRONT of
        # it (before the header), against the bottom, so they are served
        # first; the editor goes last and takes what is left.
        first = self.pack_slaves()[0]
        self._status.pack(side="bottom", fill="x", pady=(6, 0), before=first)
        btn_row.pack(side="bottom", fill="x", pady=(6, 0), before=first)
        editor_row.pack(fill="both", expand=True)

    # ── actions ────────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        text = self._editor.get("1.0", "end")
        try:
            machine_spirit.save(text)
        except OSError as e:
            self._status.config(text=_not_saved(e), fg=C["red"])
            return
        self._status.config(text="Saved ✓ — takes effect on the next question.",
                            fg=C["text1"])

    def _on_voice(self, voice: str) -> None:
        """Persist the pick, then point live narration at it. Both halves are
        needed: voices.py makes it survive a restart, calliope.set_voice() makes
        it take effect on the next spoken line. A pick that fails to save is
        not made live either, so what plays is what the next start plays."""
        try:
            voices.save_pythia_voice(voice)
        except (voices.VoiceError, OSError) as e:
            self._status.config(text=_not_saved(e), fg=C["red"])
            return
        calliope.set_voice(voice)
        self._status.config(
            text=f"Narration voice: {voices.label(voice)}.", fg=C["text1"])

    # ── model ──────────────────────────────────────────────────────────────────

    def _on_model(self, name: str) -> None:
        """Save the pick, then start the switch: pythia unloads the model
        Felhaven was using and warms this one on its own thread."""
        previous = pythia.current_model()
        if name == previous:
            return
        try:
            themis.save_pythia_model(name)
        except (themis.SettingsError, OSError) as e:
            self._status.config(text=_not_saved(e), fg=C["red"])
            self._model_bar.set_options(self._tool_models(), previous)
            return
        pythia.switch_model(previous)
        # No time promised: a load ran 20 to 80 s, by model and by disk (the
        # owner's call on HEPHAESTUS, 2026-11-17). Home says when she's ready.
        self._status.config(
            text=f"Model: {name}. Loading; Home says when she's ready.",
            fg=C["text1"])
        self._apply_model_facts()
        self._refresh_model_status()

    def _tool_models(self) -> list[str]:
        """Installed models that can use tools (or whose details are unknown:
        Talos couldn't read them, so they aren't ruled out)."""
        installed = (self._talos or {}).get("installed") or []
        return sorted(m["name"] for m in installed
                      if m.get("capabilities") is None
                      or "tools" in m["capabilities"])

    def _installed(self, name: str):
        for m in (self._talos or {}).get("installed") or []:
            if m["name"] == name:
                return m
        return None

    def _apply_model_facts(self) -> None:
        """What the chosen model can do: whether it thinks, and its own
        temperature (where the stepper starts)."""
        model = self._installed(pythia.current_model())
        caps = model.get("capabilities") if model else None
        self._model_thinks = caps is None or "thinking" in caps
        found = model.get("default_temperature") if model else None
        self._model_temperature = found if found is not None else _MODEL_TEMPERATURE
        self._restyle_answer()

    def update(self, data) -> None:
        """Talos's reading, every 5 s from Kairos on the main thread; None when
        Ollama can't be reached."""
        self._talos = data
        self._model_bar.set_options(self._tool_models(), pythia.current_model())
        self._apply_model_facts()
        self._refresh_model_status()

    def _refresh_model_status(self) -> None:
        name = pythia.current_model()
        if pythia.is_warming():
            text, colour = "loading…", C["amber_bright"]
        elif self._talos is None:
            text, colour = ("Ollama not reachable — the pick is kept for when "
                            "it is", C["text3"])
        elif self._installed(name) is None:
            text, colour = (f"not installed — pull it with: ollama pull {name}",
                            C["amber_bright"])
        else:
            loaded = next((m for m in self._talos.get("loaded") or []
                           if m["name"] == name), None)
            if loaded is None:
                text, colour = ("not loaded — the next question loads it first",
                                C["text3"])
            else:
                # The share, never Ollama's GB figure: it leaves out most of
                # what gemma4 holds in GPU memory (see hephaestus_panel's
                # OllamaWidget; HEPHAESTUS's GPU memory bar is the true total).
                pct = int(loaded.get("gpu_pct") or 0)
                text = ("loaded · all of it on the GPU" if pct >= 100 else
                        f"loaded · {pct}% on the GPU, the rest on the CPU (slower)")
                colour = C["text2"] if pct >= 100 else C["amber_bright"]
        self._model_status.config(text=text, fg=colour)

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
        start = (self._model_temperature if self._temperature is None
                 else self._temperature)
        value = start + direction * _TEMPERATURE_STEP
        value = min(themis.TEMPERATURE_MAX, max(themis.TEMPERATURE_MIN, value))
        self._save_answer(self._thinking, value)    # themis rounds it

    def _save_answer(self, thinking: bool, temperature) -> None:
        """Persist both through themis, then show them. themis validates; this
        panel never writes the settings file itself."""
        try:
            saved = themis.save_pythia(thinking=thinking, temperature=temperature)
        except (themis.SettingsError, OSError) as e:
            self._status.config(text=_not_saved(e), fg=C["red"])
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
            lit = value == self._thinking and self._model_thinks
            lbl.config(fg=C["text1"] if lit else C["text3"])
        self._think_note.config(
            text="" if self._model_thinks else "this model can't think")
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
