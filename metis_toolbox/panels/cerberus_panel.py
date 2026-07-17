"""
Cerberus panel — the Guardian's face inside Moderati (the CERBERUS tab).
=======================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Cerberus's UI: a PIN gate, and once unlocked, three
             collapsible sections — Vault (masked secrets with reveal-on-
             demand, plus a generic add/update form for writing new or
             changed secrets), Custody (manifest-driven config list that
             hands editing to the OS), and Ledger (access log, newest-first).
             All logic — PIN verify, encryption, ledger, manifest — lives in
             cerberus.py; this module never computes a hash, derives a key,
             or touches the attempt counter directly. It only draws and
             hands off.

Placement:   A bare tk.Frame tab body inside ModeratiPanel (the CERBERUS tab),
             the same shape as ArgusPanel / EmanonPanel. NOT a Card, NOT
             Kairos-registered — Cerberus has no worker and never ticks; it is
             purely request-driven (the user unlocks, reveals, opens). So there
             is no update(data) here and no after()/thread.

Masking:     Secret values render masked (••••) by default; a per-row reveal
             toggle decrypts on demand (which the Ledger records). This is the
             full extent of visual protection — no screenshot-blocking, by
             design (see the Cerberus brief's threat model).

Custody:     Rows come straight from cerberus.manifest_configs(); clicking a
             present file logs the access and opens it in the OS default editor
             (os.startfile on Windows, open/xdg-open elsewhere). An Open Folder
             button opens the whole config directory.

Upstream:    panels/moderati_panel.py (hosts this as the fourth tab)
Downstream:  cerberus.py (all logic), theme.py (colors/fonts)

Requires:    tkinter, os, platform, subprocess (stdlib). No crypto here — that
             is cerberus.py's job, and cerberus.py's alone.
"""

import os
import platform
import subprocess
import tkinter as tk

from theme import C, FONTS, PhosphorScroll

import cerberus

# Console-window suppression for the OS-editor handoff on the non-Windows
# fallback paths; referenced defensively so an off-Windows import (CI) is fine.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_MASK = "•" * 10


def _open_path(path: str) -> str | None:
    """Open a file or folder in the OS default handler. Returns an error string
    on failure, or None on success. Windows uses os.startfile; macOS/Linux fall
    back to open/xdg-open (R1)."""
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]  # Windows-only
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as e:
        return str(e)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  _ScrollFrame — vertically scrollable container (copied per §7's one-per-panel
#  house rule; PhosphorScroll inside, never tk.Scrollbar — see §12).
# ─────────────────────────────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0)
        scroll = PhosphorScroll(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)

        scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=C["card"])
        self._win = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_config)
        self._canvas.bind("<Configure>", self._on_canvas_config)
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_config(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_config(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, _event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")


# ─────────────────────────────────────────────────────────────────────────────
#  _Section — collapsible ▶/▼ section (the Helios/Selene/Argus precedent).
# ─────────────────────────────────────────────────────────────────────────────

class _Section(tk.Frame):
    def __init__(self, parent, title: str, collapsed: bool = True, on_show=None):
        super().__init__(parent, bg=C["card"])
        self._on_show = on_show
        self.collapsed = collapsed

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", pady=(6, 4))

        header = tk.Frame(self, bg=C["card"])
        header.pack(fill="x")
        self._title = tk.Label(header, text=title, font=FONTS["card_header"],
                               fg=C["text3"], bg=C["card"], anchor="w", cursor="hand2")
        self._title.pack(side="left")
        self._tog = tk.Label(header, text="▶" if collapsed else "▼",
                             font=FONTS["card_header"], fg=C["text3"],
                             bg=C["card"], cursor="hand2")
        self._tog.pack(side="right")

        self.content = tk.Frame(self, bg=C["card"])
        if not collapsed:
            self.content.pack(fill="x", pady=(2, 0))

        for w in (self._title, self._tog):
            w.bind("<Button-1>", lambda e: self.toggle())
        self._tog.bind("<Enter>", lambda e: self._tog.config(fg=C["text1"]))
        self._tog.bind("<Leave>", lambda e: self._tog.config(fg=C["text3"]))

    def set_title(self, text: str) -> None:
        self._title.config(text=text)

    def toggle(self) -> None:
        if self.collapsed:
            self.content.pack(fill="x", pady=(2, 0))
            self._tog.config(text="▼")
            self.collapsed = False
            if self._on_show:
                self._on_show()
        else:
            self.content.pack_forget()
            self._tog.config(text="▶")
            self.collapsed = True


# ─────────────────────────────────────────────────────────────────────────────
#  CerberusPanel
# ─────────────────────────────────────────────────────────────────────────────

class CerberusPanel(tk.Frame):
    """PIN gate + Vault / Custody / Ledger. A bare Frame tab body inside
    ModeratiPanel; no Kairos worker, no update() — purely request-driven."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._revealed: dict[str, bool] = {}       # vault name -> shown?
        self._sections: dict[str, _Section] = {}
        self._unlocked_built = False

        # Two swappable faces: the gate, and the unlocked body.
        self._gate = tk.Frame(self, bg=C["card"])
        self._unlocked = tk.Frame(self, bg=C["card"])

        self._build_gate(self._gate)
        self._gate.pack(fill="both", expand=True)

    # ── Gate ─────────────────────────────────────────────────────────────────

    def _build_gate(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="CERBERUS — sealed", font=FONTS["card_header"],
                 fg=C["text2"], bg=C["card"], anchor="w").pack(fill="x", pady=(8, 2))
        tk.Label(parent,
                 text="Three heads guard the vault, the configs, and the log. "
                      "Speak the word.",
                 font=FONTS["small_italic"], fg=C["text3"], bg=C["card"],
                 anchor="w", wraplength=360, justify="left").pack(fill="x", pady=(0, 10))

        prompt = tk.Frame(parent, bg=C["card"])
        prompt.pack(fill="x")
        tk.Label(prompt, text="PIN ▸", font=FONTS["body"], fg=C["text1"],
                 bg=C["card"]).pack(side="left", padx=(0, 6))
        self._entry = tk.Entry(
            prompt, show="*", font=FONTS["body"], width=14,
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightbackground=C["border"], highlightcolor=C["purple"],
            highlightthickness=1, borderwidth=0,
        )
        self._entry.pack(side="left")
        self._entry.bind("<Return>", self._on_submit)

        self._enter_btn = tk.Label(prompt, text="UNLOCK", font=FONTS["small_bold"],
                                   fg=C["purple"], bg=C["card"], cursor="hand2")
        self._enter_btn.pack(side="left", padx=(8, 0))
        self._enter_btn.bind("<Button-1>", self._on_submit)
        self._enter_btn.bind("<Enter>", lambda e: self._enter_btn.config(fg=C["amber"]))
        self._enter_btn.bind("<Leave>", lambda e: self._enter_btn.config(fg=C["purple"]))

        # Confirm-PIN row — built now, packed only in first-run setup (when no
        # cerberus_data.json exists yet). before=status keeps it above the note.
        self._confirm_frame = tk.Frame(parent, bg=C["card"])
        tk.Label(self._confirm_frame, text="again ▸", font=FONTS["body"], fg=C["text1"],
                 bg=C["card"]).pack(side="left", padx=(0, 6))
        self._confirm_entry = tk.Entry(
            self._confirm_frame, show="*", font=FONTS["body"], width=14,
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightbackground=C["border"], highlightcolor=C["purple"],
            highlightthickness=1, borderwidth=0,
        )
        self._confirm_entry.pack(side="left")
        self._confirm_entry.bind("<Return>", self._on_submit)

        self._gate_status = tk.Label(parent, text="", font=FONTS["tiny"],
                                     fg=C["text3"], bg=C["card"], anchor="w",
                                     wraplength=360, justify="left")
        self._gate_status.pack(fill="x", pady=(10, 0))

        # No data file yet (a fresh clone): offer first-run PIN setup here rather
        # than failing closed or punting to the CLI (§7 placeholder, not crash).
        self._first_run = False
        try:
            cerberus.preflight()
        except cerberus.HashFileError:
            self._enter_first_run_mode()

    def _enter_first_run_mode(self) -> None:
        """Turn the gate into a 'choose your vault PIN' setup: reveal the confirm
        row, relabel the button CREATE, and route submit to _create_pin."""
        self._first_run = True
        self._confirm_frame.pack(fill="x", pady=(6, 0), before=self._gate_status)
        self._enter_btn.config(text="CREATE")
        self._gate_status.config(
            text="No vault yet — choose a PIN to seal your own Cerberus.",
            fg=C["text3"])

    def _on_submit(self, _event=None) -> None:
        if self._first_run:
            self._create_pin()
            return
        pin = self._entry.get()
        self._entry.delete(0, "end")
        try:
            ok = cerberus.unlock(pin)
        except cerberus.HashFileError:
            self._gate_status.config(text="Cerberus's hash file vanished.", fg=C["red"])
            return
        if ok:
            self._show_unlocked()
            return
        remaining = cerberus.attempts_left()
        if remaining > 0:
            noun = "attempt" if remaining == 1 else "attempts"
            self._gate_status.config(
                text=f"Wrong. The heads do not yield. {remaining} {noun} remain.",
                fg=C["red"])
            self._entry.focus_set()
        else:
            self._entry.config(state="disabled")
            self._gate_status.config(
                text="Cerberus falls silent — too many attempts. Relaunch to try again.",
                fg=C["red"])

    def _create_pin(self) -> None:
        """First-run: validate the chosen PIN + confirm, write it via
        cerberus.set_pin, then unlock straight into the (empty) vault."""
        pin = self._entry.get()
        confirm = self._confirm_entry.get()
        if not pin.strip():
            self._gate_status.config(text="Choose a PIN.", fg=C["red"])
            return
        if pin != confirm:
            self._gate_status.config(text="The two PINs don't match.", fg=C["red"])
            self._confirm_entry.delete(0, "end")
            return
        try:
            cerberus.set_pin(pin)
        except OSError as e:
            self._gate_status.config(text=f"Could not create the vault: {e}", fg=C["red"])
            return
        # Setup done: leave first-run mode and open the (empty) vault directly.
        self._first_run = False
        self._confirm_frame.pack_forget()
        self._confirm_entry.delete(0, "end")
        self._entry.delete(0, "end")
        self._enter_btn.config(text="UNLOCK")
        if cerberus.unlock(pin):
            self._show_unlocked()

    # ── Unlocked body ──────────────────────────────────────────────────────────

    def _show_unlocked(self) -> None:
        self._gate.pack_forget()
        if not self._unlocked_built:
            self._build_unlocked(self._unlocked)
            self._unlocked_built = True
        self._unlocked.pack(fill="both", expand=True)
        for key in self._sections:
            self._render_section(key)

    def _build_unlocked(self, parent: tk.Frame) -> None:
        # Header row: identity + refresh + lock.
        head = tk.Frame(parent, bg=C["card"])
        head.pack(fill="x", pady=(8, 2))
        tk.Label(head, text="CERBERUS — unlocked", font=FONTS["card_header"],
                 fg=C["text1"], bg=C["card"], anchor="w").pack(side="left")

        lock_btn = tk.Label(head, text="🔒 lock", font=FONTS["tiny"],
                            fg=C["text3"], bg=C["card"], cursor="hand2")
        lock_btn.pack(side="right")
        lock_btn.bind("<Button-1>", lambda e: self._lock())
        lock_btn.bind("<Enter>", lambda e: lock_btn.config(fg=C["amber"]))
        lock_btn.bind("<Leave>", lambda e: lock_btn.config(fg=C["text3"]))

        refresh_btn = tk.Label(head, text="↻ refresh", font=FONTS["tiny"],
                               fg=C["text3"], bg=C["card"], cursor="hand2")
        refresh_btn.pack(side="right", padx=(0, 12))
        refresh_btn.bind("<Button-1>", lambda e: self._refresh_open())
        refresh_btn.bind("<Enter>", lambda e: refresh_btn.config(fg=C["amber"]))
        refresh_btn.bind("<Leave>", lambda e: refresh_btn.config(fg=C["text3"]))

        self._status = tk.Label(parent, text="", font=FONTS["tiny"], fg=C["text3"],
                                bg=C["card"], anchor="w", wraplength=360, justify="left")
        self._status.pack(fill="x", pady=(0, 2))

        # Scrollable body of the three heads. Vault open by default (the point);
        # Custody and Ledger opt-in so the tab opens glanceable.
        scroll = _ScrollFrame(parent)
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        body = scroll.inner
        for key, title, collapsed in (
            ("vault",   "VAULT — secrets",  False),
            ("custody", "CUSTODY — configs", True),
            ("ledger",  "LEDGER — access log", True),
        ):
            sec = _Section(body, title, collapsed=collapsed,
                           on_show=lambda k=key: self._render_section(k))
            sec.pack(fill="x")
            self._sections[key] = sec

    def _lock(self) -> None:
        cerberus.lock()
        self._revealed.clear()
        self._unlocked.pack_forget()
        self._entry.config(state="normal")
        self._gate_status.config(text="Sealed again. The guardian sleeps.", fg=C["text3"])
        self._gate.pack(fill="both", expand=True)
        self._entry.focus_set()

    def _refresh_open(self) -> None:
        for key, sec in self._sections.items():
            if not sec.collapsed:
                self._render_section(key)
        self._status.config(text="refreshed.", fg=C["text3"])

    def _refresh_if_open(self, key: str) -> None:
        if key in self._sections and not self._sections[key].collapsed:
            self._render_section(key)

    def _render_section(self, key: str) -> None:
        sec = self._sections[key]
        for w in sec.content.winfo_children():
            w.destroy()
        getattr(self, f"_render_{key}")(sec.content)

    # ── Vault ────────────────────────────────────────────────────────────────

    def _render_vault(self, parent: tk.Frame) -> None:
        names = cerberus.vault_names()
        if not names:
            self._empty(parent, "vault empty — add one below.")
        for name in names:
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name, font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], anchor="w", width=16).pack(side="left")

            shown = self._revealed.get(name, False)
            val_lbl = tk.Label(
                row, text=_MASK, font=FONTS["small"],
                fg=C["text3"], bg=C["card"], anchor="w")
            val_lbl.pack(side="left", fill="x", expand=True, padx=(6, 6))

            btn = tk.Label(row, text="hide" if shown else "reveal",
                           font=FONTS["tiny"], fg=C["purple"], bg=C["card"],
                           cursor="hand2", width=6, anchor="e")
            btn.pack(side="right")
            btn.bind("<Button-1>",
                     lambda e, n=name, v=val_lbl, b=btn: self._toggle_reveal(n, v, b))
            btn.bind("<Enter>", lambda e, b=btn: b.config(fg=C["amber"]))
            btn.bind("<Leave>", lambda e, b=btn: b.config(fg=C["purple"]))

            if shown:                       # keep a revealed row shown across a refresh
                self._show_value(name, val_lbl)

        self._build_vault_form(parent)
        self._note(parent, "values decrypt only on reveal — each reveal and "
                            "each save is logged.")

    def _build_vault_form(self, parent: tk.Frame) -> None:
        """Generic add/update form: one name field, one masked value field, one
        submit action. Works for any key — present or new, Finnhub, Brave, or
        anything else — not a per-row inline edit. Overwriting an existing
        name is silent (matches the CLI `set` command's own behavior)."""
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))

        name_row = tk.Frame(parent, bg=C["card"])
        name_row.pack(fill="x", pady=(0, 4))
        tk.Label(name_row, text="name ▸", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"]).pack(side="left", padx=(0, 4))
        name_entry = tk.Entry(
            name_row, font=FONTS["small"],
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightbackground=C["border"], highlightcolor=C["purple"],
            highlightthickness=1, borderwidth=0,
        )
        name_entry.pack(side="left", fill="x", expand=True)

        value_row = tk.Frame(parent, bg=C["card"])
        value_row.pack(fill="x")
        tk.Label(value_row, text="value ▸", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"]).pack(side="left", padx=(0, 4))
        value_entry = tk.Entry(
            value_row, show="•", font=FONTS["small"],
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightbackground=C["border"], highlightcolor=C["purple"],
            highlightthickness=1, borderwidth=0,
        )
        value_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        save_btn = tk.Label(value_row, text="SAVE", font=FONTS["small_bold"],
                            fg=C["purple"], bg=C["card"], cursor="hand2")
        save_btn.pack(side="left")
        save_btn.bind("<Enter>", lambda e: save_btn.config(fg=C["amber"]))
        save_btn.bind("<Leave>", lambda e: save_btn.config(fg=C["purple"]))

        def _submit(_event=None) -> None:
            self._on_vault_submit(name_entry, value_entry)

        save_btn.bind("<Button-1>", _submit)
        value_entry.bind("<Return>", _submit)
        name_entry.bind("<Return>", lambda e: value_entry.focus_set())

    def _on_vault_submit(self, name_entry: tk.Entry, value_entry: tk.Entry) -> None:
        name = name_entry.get().strip()
        value = value_entry.get()
        if not name:
            self._status.config(text="name a secret first.", fg=C["red"])
            return
        if not value:
            self._status.config(text="value can't be empty.", fg=C["red"])
            return
        try:
            cerberus.vault_set(name, value)
        except cerberus.VaultError as e:
            self._status.config(text=str(e), fg=C["red"])
            return
        self._status.config(text=f"saved {name!r} to the vault.", fg=C["text3"])
        self._render_section("vault")     # the new/updated row shows immediately
        self._refresh_if_open("ledger")

    def _toggle_reveal(self, name: str, val_lbl: tk.Label, btn: tk.Label) -> None:
        if self._revealed.get(name):
            val_lbl.config(text=_MASK, fg=C["text3"])
            btn.config(text="reveal")
            self._revealed[name] = False
        else:
            if self._show_value(name, val_lbl):
                btn.config(text="hide")
                self._revealed[name] = True
                self._refresh_if_open("ledger")

    def _show_value(self, name: str, val_lbl: tk.Label) -> bool:
        try:
            val_lbl.config(text=cerberus.vault_get(name), fg=C["text1"])
            return True
        except cerberus.VaultError as e:
            val_lbl.config(text=f"⚠ {e}", fg=C["red"])
            return False

    # ── Custody ──────────────────────────────────────────────────────────────

    def _render_custody(self, parent: tk.Frame) -> None:
        rows = cerberus.manifest_configs()
        cfg_dir = cerberus.manifest_config_dir()
        if not rows:
            self._empty(parent, "no config manifest — see cerberus_manifest.json")
        for r in rows:
            item = tk.Frame(parent, bg=C["card"])
            item.pack(fill="x", pady=(3, 0))
            exists = r.get("exists", False)
            fname = r["file"]
            name_lbl = tk.Label(
                item, text=fname if exists else f"{fname}  (missing)",
                font=FONTS["small_bold"],
                fg=C["text1"] if exists else C["text3"], bg=C["card"],
                anchor="w", cursor="hand2" if exists else "arrow")
            name_lbl.pack(fill="x")
            if exists:
                name_lbl.bind("<Button-1>", lambda e, f=fname: self._open_config(f))
                name_lbl.bind("<Enter>", lambda e, w=name_lbl: w.config(fg=C["amber"]))
                name_lbl.bind("<Leave>", lambda e, w=name_lbl: w.config(fg=C["text1"]))
            tk.Label(item, text=r.get("desc", ""), font=FONTS["tiny"],
                     fg=C["text3"], bg=C["card"], anchor="w",
                     wraplength=360, justify="left").pack(fill="x")

        if cfg_dir:
            tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(6, 4))
            folder_btn = tk.Label(parent, text="📂 Open Folder", font=FONTS["tiny"],
                                  fg=C["purple"], bg=C["card"], cursor="hand2", anchor="w")
            folder_btn.pack(anchor="w")
            folder_btn.bind("<Button-1>", lambda e: self._open_folder())
            folder_btn.bind("<Enter>", lambda e: folder_btn.config(fg=C["amber"]))
            folder_btn.bind("<Leave>", lambda e: folder_btn.config(fg=C["purple"]))

    def _open_config(self, fname: str) -> None:
        cfg_dir = cerberus.manifest_config_dir()
        path = os.path.join(cfg_dir, fname) if cfg_dir else fname
        cerberus.ledger_log_custody(fname, "open")
        err = _open_path(path)
        self._status.config(
            text=f"could not open {fname}: {err}" if err else f"opened {fname} in your editor.",
            fg=C["red"] if err else C["text3"])
        self._refresh_if_open("ledger")

    def _open_folder(self) -> None:
        cfg_dir = cerberus.manifest_config_dir()
        if not cfg_dir:
            return
        err = _open_path(cfg_dir)
        self._status.config(text=f"could not open folder: {err}" if err else "opened config folder.",
                            fg=C["red"] if err else C["text3"])

    # ── Ledger ───────────────────────────────────────────────────────────────

    def _render_ledger(self, parent: tk.Frame) -> None:
        entries = cerberus.ledger_entries()
        if not entries:
            self._empty(parent, "no access recorded yet")
            return
        for e in entries:
            row = tk.Frame(parent, bg=C["card"])
            row.pack(fill="x", pady=1)
            ts = e.get("ts", "").replace("T", " ")
            tk.Label(row, text=ts, font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                     width=17, anchor="w").pack(side="left")
            head = e.get("head", "?")
            tk.Label(row, text=f"{head}·{e.get('action', '?')}", font=FONTS["tiny"],
                     fg=C["text2"] if head == "vault" else C["text3"], bg=C["card"],
                     width=13, anchor="w").pack(side="left")
            count = e.get("count", 1)
            suffix = f"  ×{count}" if count and count > 1 else ""
            tk.Label(row, text=f"{e.get('target', '')}{suffix}", font=FONTS["tiny"],
                     fg=C["text1"], bg=C["card"], anchor="w").pack(
                         side="left", fill="x", expand=True)
        self._note(parent, "vault reads dedupe per unlock session · custody opens logged in full.")

    # ── small shared row helpers ─────────────────────────────────────────────

    def _empty(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, font=FONTS["small_italic"], fg=C["text3"],
                 bg=C["card"], anchor="w", wraplength=360, justify="left").pack(
                     anchor="w", pady=(2, 0))

    def _note(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                 anchor="w", wraplength=360, justify="left").pack(anchor="w", pady=(4, 0))
