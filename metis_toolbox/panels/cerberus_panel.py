"""
Cerberus panel — the Guardian's face inside Moderati (the CERBERUS tab).
=======================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Cerberus's UI: a PIN gate, then the Vault, Custody and Ledger.
             Once unlocked, those are three collapsible sections — Vault (one
             row per key, masked, with reveal and REPLACE, plus a generic add
             form), Custody (the manifest's config files, handed to the OS
             editor) and Ledger (the access log, newest first). Above both
             faces, a red banner for any
             tamper alarm. All logic — the PIN check, DPAPI, the ledger, the
             manifest — lives in cerberus.py; this module only draws and hands
             off.

Placement:   A bare tk.Frame tab body inside ModeratiPanel (the CERBERUS tab).
             Kairos's `cerberus` worker (cerberus.fetch, every 2 s) calls
             update(data), so the tab reseals the moment the session closes
             anywhere — idle, Windows lock, sleep, or another tab — and the
             banner follows the alarms. Resealing destroys the Vault rows, so a
             revealed key doesn't linger in the widget tree.

Vault rows:  One per key in cerberus.KNOWN_KEYS, set or not, then any other
             key in the vault. Set: masked, with reveal and REPLACE. Not set:
             SET. Unreadable (a Windows password reset, a rebuilt profile, a
             tampered file): "re-enter the key" and REPLACE. REPLACE opens a
             masked entry in the row; the old key is never shown.

Retiring:    An old PIN-encrypted (v1) or unparseable vault file is refused by
             cerberus.py. The banner offers RETIRE OLD VAULT, a two-click
             confirm (the Morpheus idiom) that renames the file aside. Nothing
             here deletes a file.

Custody:     Rows come from cerberus.manifest_configs(); a click goes through
             cerberus.custody_open(), which refuses anything that isn't a
             listed .json inside config/. Refused rows can't be clicked.

Lamp:        CerberusLamp is the Home status row's at-a-glance state (dim
             sealed, amber open, red alarm). It lives here, with the rest of
             Cerberus's face; panels/home_panel.py places it and felhaven.py
             registers it on the `cerberus` worker.

Upstream:    panels/moderati_panel.py (hosts this as a tab), kairos.py
             (`cerberus` worker → update), panels/home_panel.py (the lamp),
             panels/midas_panel.py (LOCK_REASON_TEXT)
Downstream:  cerberus.py (all logic), theme.py (colors/fonts)

Requires:    tkinter, os, platform, subprocess (stdlib). No crypto here.

V0.02:       Audited 2026-07-24 — reads cerberus.py's now-thread-safe Ledger on
             the main thread (safe against Midas's write pool via cerberus's
             atomic writes). Dropped the unused _NO_WINDOW constant. Draw-only
             contract verified.
"""

import os
import platform
import subprocess
import tkinter as tk
from typing import Any

from theme import C, FONTS, PhosphorScroll

import cerberus

_MASK = "•" * 10
_RETIRE_CONFIRM_MS = 4000

# Why the session closed, in words. Shared with the Dynastic Vault's gate.
LOCK_REASON_TEXT = {
    cerberus.LOCK_MANUAL:  "Sealed from the CERBERUS tab.",
    cerberus.LOCK_IDLE:    "Sealed after an hour with no one at the keyboard.",
    cerberus.LOCK_WINDOWS: "Sealed when Windows was locked.",
    cerberus.LOCK_SLEEP:   "Sealed when the PC went to sleep.",
    cerberus.LOCK_EXIT:    "Sealed.",
}

# §2: panels map slugs to text, and an unmapped slug shows as itself.
_ERROR_TEXT = {
    cerberus.ERR_LOCKED:            "sealed — unlock first.",
    cerberus.ERR_NO_ENTRY:          "no such key.",
    cerberus.ERR_UNREADABLE:        "that key can't be decrypted on this Windows account — re-enter it.",
    cerberus.ERR_V1_VAULT:          "the old vault file is refused — retire it first (see above).",
    cerberus.ERR_CORRUPT:           "the vault file is unreadable — set it aside first (see above).",
    cerberus.ERR_IO:                "the vault file can't be read right now — try again.",
    cerberus.ERR_DPAPI_UNAVAILABLE: "the vault needs Windows (DPAPI).",
    cerberus.ERR_PROTECT_FAILED:    "Windows refused to encrypt the key.",
    cerberus.ERR_WRITE_FAILED:      "couldn't write the vault file.",
    cerberus.ERR_BAD_INPUT:         "a key needs a name and a value.",
    cerberus.ERR_NOTHING_TO_RETIRE: "there's no old vault to retire.",
    cerberus.ERR_RETIRE_FAILED:     "couldn't rename the old vault file — is it open elsewhere?",
    cerberus.ERR_NOT_LISTED:        "it isn't in the Custody manifest.",
    cerberus.ERR_NO_CONFIG_DIR:     "the manifest's config folder is unset or outside config/.",
    cerberus.ERR_OUTSIDE_CONFIG:    "it's outside config/, so Custody won't open it.",
    cerberus.ERR_NOT_JSON:          "it isn't a .json file, so Custody won't open it.",
    cerberus.ERR_MISSING:           "the file is missing.",
}

_VAULT_REFUSED_TEXT = {
    "v1":       "Refused: this is the old PIN-encrypted vault — see the warning above.",
    "corrupt":  "Refused: the vault file can't be read — see the warning above.",
    "io_error": "The vault file can't be read right now — refresh to try again.",
}

# Ledger actions that are warnings, drawn in the alarm red.
_ALARM_ACTIONS = {"wrong_pin", "unreadable", "v1_found", "corrupt",
                  "pin_reset", "refused"}


def error_text(slug: str) -> str:
    return _ERROR_TEXT.get(slug, slug)


def _alarm_text(alarm: str) -> str:
    if alarm == cerberus.ALARM_V1_VAULT:
        return ("⚠ An old PIN-encrypted vault file is in place. Anyone who knows "
                "the PIN can read its keys, so Cerberus refuses it.")
    if alarm == cerberus.ALARM_CORRUPT:
        return "⚠ The vault file can't be read, so Cerberus refuses it."
    if alarm == cerberus.ALARM_PIN_RESET:
        return ("⚠ The PIN file was recreated while the vault held keys — "
                "someone may have reset the PIN to get past the lock.")
    if alarm.startswith(cerberus.ALARM_UNREADABLE):
        name = alarm[len(cerberus.ALARM_UNREADABLE):]
        return f"⚠ {name} can't be decrypted on this Windows account — REPLACE it below."
    return f"⚠ {alarm}"


def _open_path(path: str) -> str | None:
    """Open a file or folder in the OS default handler. Returns an error string
    on failure, or None on success. Windows uses os.startfile; macOS/Linux fall
    back to open/xdg-open (R1). Only cerberus.custody_open()'s paths and the
    contained config folder ever reach it."""
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


def _link(parent: tk.Widget, text: str, command, fg: str | None = None,
          font: str = "tiny") -> tk.Label:
    """A clickable label with the house hover (amber)."""
    color = fg or C["purple"]
    lbl = tk.Label(parent, text=text, font=FONTS[font], fg=color, bg=C["card"],
                   cursor="hand2")
    lbl.bind("<Button-1>", lambda e: command())
    lbl.bind("<Enter>", lambda e: lbl.config(fg=C["amber"]))
    lbl.bind("<Leave>", lambda e: lbl.config(fg=color))
    return lbl


# ─────────────────────────────────────────────────────────────────────────────
#  CerberusLamp — Home's at-a-glance state
# ─────────────────────────────────────────────────────────────────────────────

class CerberusLamp(tk.Label):
    """Dim when sealed, amber when open, red while any tamper alarm is up. Driven
    by Kairos's `cerberus` worker; it reads nothing itself."""

    def __init__(self, parent: tk.Widget, bg: str) -> None:
        super().__init__(parent, text="● Cerberus", font=FONTS["small"],
                         fg=C["text3"], bg=bg)
        self.state = "sealed"

    def update(self, data: Any) -> None:   # Kairos contract
        """cerberus.fetch()'s report. Survives None (§6)."""
        if not isinstance(data, dict):
            return
        if data.get("alarms"):
            self.state, fg = "alarm", C["red"]
        elif data.get("unlocked"):
            self.state, fg = "open", C["amber"]
        else:
            self.state, fg = "sealed", C["text3"]
        self.config(fg=fg)


# ─────────────────────────────────────────────────────────────────────────────
#  CerberusPanel
# ─────────────────────────────────────────────────────────────────────────────

class CerberusPanel(tk.Frame):
    """PIN gate + Vault / Custody / Ledger, under an alarm banner. Updated by
    Kairos's `cerberus` worker; everything else is request-driven."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._revealed: dict[str, bool] = {}       # vault name -> shown?
        self._editing: str | None = None           # the row with REPLACE open
        self._editor: tk.Entry | None = None
        self._rows: dict[str, dict[str, tk.Label]] = {}
        self._sections: dict[str, _Section] = {}
        self._unlocked_built = False
        self._showing_unlocked = False
        self._alarms: tuple[str, ...] = tuple(cerberus.alarms())
        self._retire_armed = False
        self._retire_after_id: str | None = None
        self._retire_btn: tk.Label | None = None

        # The banner sits above whichever face is showing.
        self._banner = tk.Frame(self, bg=C["card"])
        self._gate = tk.Frame(self, bg=C["card"])
        self._unlocked = tk.Frame(self, bg=C["card"])

        self._build_gate(self._gate)
        self._gate.pack(fill="both", expand=True)
        self._render_banner()

    def destroy(self) -> None:
        self._disarm_retire()
        super().destroy()

    # ── Kairos ───────────────────────────────────────────────────────────────

    def update(self, data: Any) -> None:   # Kairos contract
        """cerberus.fetch()'s report: reseal if the session closed anywhere,
        and follow the alarms. Survives None (§6)."""
        if not isinstance(data, dict):
            return
        if self._showing_unlocked and not data.get("unlocked"):
            self._reseal(data.get("reason"))
        alarms = tuple(data.get("alarms") or ())
        if alarms != self._alarms:
            self._alarms = alarms
            self._render_banner()
            if self._showing_unlocked and self._editing is None:
                self._refresh_if_open("vault")

    # ── Banner ───────────────────────────────────────────────────────────────

    def _render_banner(self) -> None:
        self._disarm_retire()
        for w in self._banner.winfo_children():
            w.destroy()
        self._retire_btn = None
        if not self._alarms:
            self._banner.pack_forget()
            return
        tk.Frame(self._banner, bg=C["red"], height=1).pack(fill="x", pady=(6, 4))
        for alarm in self._alarms:
            tk.Label(self._banner, text=_alarm_text(alarm), font=FONTS["tiny"],
                     fg=C["red"], bg=C["card"], anchor="w", wraplength=360,
                     justify="left").pack(fill="x")
            if alarm in (cerberus.ALARM_V1_VAULT, cerberus.ALARM_CORRUPT):
                if self._showing_unlocked:
                    label = ("RETIRE OLD VAULT" if alarm == cerberus.ALARM_V1_VAULT
                             else "SET THE FILE ASIDE")
                    self._retire_btn = _link(self._banner, label, self._on_retire_click,
                                             fg=C["red"], font="small_bold")
                    self._retire_btn.pack(anchor="w", pady=(2, 4))
                else:
                    tk.Label(self._banner, text="Unlock to set it aside.",
                             font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                             anchor="w").pack(fill="x", pady=(0, 4))
            elif alarm == cerberus.ALARM_PIN_RESET and self._showing_unlocked:
                _link(self._banner, "dismiss", self._on_dismiss_reset,
                      fg=C["red"]).pack(anchor="w", pady=(2, 4))
        tk.Frame(self._banner, bg=C["red"], height=1).pack(fill="x", pady=(4, 0))
        face = self._unlocked if self._showing_unlocked else self._gate
        self._banner.pack(fill="x", before=face)

    def _on_retire_click(self) -> None:
        if not self._retire_armed:
            self._retire_armed = True
            if self._retire_btn is not None:
                self._retire_btn.config(text="CONFIRM — RENAME THE OLD FILE ASIDE?")
            self._retire_after_id = self.after(_RETIRE_CONFIRM_MS, self._disarm_retire_and_relabel)
            return
        self._disarm_retire()
        try:
            new_name = cerberus.retire_vault()
        except cerberus.VaultError as e:
            self._status.config(text=error_text(e.slug), fg=C["red"])
            return
        self._status.config(
            text=f"Old vault renamed to {new_name}. Enter your keys below, and "
                 f"delete that file once they read back.", fg=C["text3"])
        self._after_alarm_change()

    def _disarm_retire(self) -> None:
        if self._retire_after_id is not None:
            self.after_cancel(self._retire_after_id)
            self._retire_after_id = None
        self._retire_armed = False

    def _disarm_retire_and_relabel(self) -> None:
        self._retire_after_id = None
        self._retire_armed = False
        self._render_banner()

    def _on_dismiss_reset(self) -> None:
        try:
            cerberus.dismiss_alarm(cerberus.ALARM_PIN_RESET)
        except cerberus.VaultError as e:
            self._status.config(text=error_text(e.slug), fg=C["red"])
            return
        self._status.config(text="PIN-reset warning dismissed (logged).", fg=C["text3"])
        self._after_alarm_change()

    def _after_alarm_change(self) -> None:
        self._alarms = tuple(cerberus.alarms())
        self._render_banner()
        self._refresh_if_open("vault")
        self._refresh_if_open("ledger")

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

        # No PIN file yet (a fresh clone, or a deleted one): offer first-run
        # setup here rather than failing closed or punting to the CLI.
        self._first_run = False
        try:
            cerberus.preflight()
        except cerberus.HashFileError:
            self._enter_first_run_mode()

    def _enter_first_run_mode(self) -> None:
        """Turn the gate into a 'choose your PIN' setup: reveal the confirm row,
        relabel the button CREATE, and route submit to _create_pin. If the vault
        already holds keys, say up front that this will be logged as a reset."""
        self._first_run = True
        self._confirm_frame.pack(fill="x", pady=(6, 0), before=self._gate_status)
        self._enter_btn.config(text="CREATE")
        if cerberus.vault_names():
            self._gate_status.config(
                text="The PIN file is gone, but the vault holds keys. A new PIN "
                     "will work, and it will be logged as a PIN reset.",
                fg=C["red"])
        else:
            self._gate_status.config(
                text="No PIN yet — choose one. It's a child lock: it decides when "
                     "keys may be used, and decrypts nothing. Use one you use "
                     "nowhere else.",
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
            self._gate_status.config(text="Cerberus's PIN file vanished.", fg=C["red"])
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
        cerberus.set_pin, then unlock straight into the vault."""
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
            self._gate_status.config(text=f"Could not save the PIN: {e}", fg=C["red"])
            return
        self._first_run = False
        self._confirm_frame.pack_forget()
        self._confirm_entry.delete(0, "end")
        self._entry.delete(0, "end")
        self._enter_btn.config(text="UNLOCK")
        self._alarms = tuple(cerberus.alarms())
        if cerberus.unlock(pin):
            self._show_unlocked()

    # ── Unlocked body ──────────────────────────────────────────────────────────

    def _show_unlocked(self) -> None:
        self._gate.pack_forget()
        if not self._unlocked_built:
            self._build_unlocked(self._unlocked)
            self._unlocked_built = True
        self._showing_unlocked = True
        self._unlocked.pack(fill="both", expand=True)
        self._render_banner()
        for key in self._sections:
            self._render_section(key)

    def _build_unlocked(self, parent: tk.Frame) -> None:
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
            ("vault",   "VAULT — keys",  False),
            ("custody", "CUSTODY — configs", True),
            ("ledger",  "LEDGER — access log", True),
        ):
            sec = _Section(body, title, collapsed=collapsed,
                           on_show=lambda k=key: self._render_section(k))
            sec.pack(fill="x")
            self._sections[key] = sec

    def _lock(self) -> None:
        cerberus.lock(cerberus.LOCK_MANUAL)
        self._reseal(None)
        self._gate_status.config(text="Sealed again. The guardian sleeps.", fg=C["text3"])

    def _reseal(self, reason: str | None) -> None:
        """Back to the gate. Destroys every section's rows first, so no revealed
        key or half-typed REPLACE stays in the widget tree."""
        self._showing_unlocked = False
        self._revealed.clear()
        self._editing = None
        self._editor = None
        self._rows.clear()
        for sec in self._sections.values():
            for w in sec.content.winfo_children():
                w.destroy()
        self._unlocked.pack_forget()
        self._entry.config(state="normal")
        self._gate_status.config(
            text=LOCK_REASON_TEXT.get(reason or "", "Sealed by Cerberus."), fg=C["text3"])
        self._gate.pack(fill="both", expand=True)
        self._render_banner()

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
        self._rows.clear()
        self._editor = None
        state = cerberus.vault_status()
        if state in _VAULT_REFUSED_TEXT:
            self._empty(parent, _VAULT_REFUSED_TEXT[state])
            return
        try:
            health = cerberus.vault_health()
        except cerberus.VaultError as e:
            self._empty(parent, error_text(e.slug))
            health = {}
        names = list(cerberus.KNOWN_KEYS) + [n for n in health
                                             if n not in cerberus.KNOWN_KEYS]
        for name in names:
            self._vault_row(parent, name, health.get(name))
        self._build_vault_form(parent)
        self._note(parent, "keys show only on reveal · REPLACE never shows the "
                           "old key · each reveal and save is logged.")

    def _vault_row(self, parent: tk.Frame, name: str, status: str | None) -> None:
        row = tk.Frame(parent, bg=C["card"])
        row.pack(fill="x", pady=(4, 0))
        top = tk.Frame(row, bg=C["card"])
        top.pack(fill="x")
        tk.Label(top, text=name, font=FONTS["small"], fg=C["text2"],
                 bg=C["card"], anchor="w").pack(side="left")
        use = cerberus.KNOWN_KEYS.get(name)
        if use:
            tk.Label(top, text=f"· {use}", font=FONTS["tiny"], fg=C["text3"],
                     bg=C["card"], anchor="w").pack(side="left", padx=(6, 0))

        line = tk.Frame(row, bg=C["card"])
        line.pack(fill="x")
        if self._editing == name:
            self._build_editor(line, name)
            return

        widgets: dict[str, tk.Label] = {}
        if status is None:
            text, fg, action = "not set", C["text3"], "set"
        elif status == "unreadable":
            text, fg, action = "⚠ unreadable — re-enter the key", C["red"], "replace"
        else:
            text, fg, action = _MASK, C["text3"], "replace"
        val_lbl = tk.Label(line, text=text, font=FONTS["small"], fg=fg,
                           bg=C["card"], anchor="w")
        val_lbl.pack(side="left", fill="x", expand=True, padx=(0, 6))
        widgets["value"] = val_lbl

        widgets["replace"] = _link(line, action, lambda n=name: self._start_replace(n))
        widgets["replace"].pack(side="right")
        if status == "ok":
            shown = self._revealed.get(name, False)
            reveal = tk.Label(line, text="hide" if shown else "reveal",
                              font=FONTS["tiny"], fg=C["purple"], bg=C["card"],
                              cursor="hand2")
            reveal.pack(side="right", padx=(0, 10))
            reveal.bind("<Button-1>",
                        lambda e, n=name, v=val_lbl, b=reveal: self._toggle_reveal(n, v, b))
            reveal.bind("<Enter>", lambda e, b=reveal: b.config(fg=C["amber"]))
            reveal.bind("<Leave>", lambda e, b=reveal: b.config(fg=C["purple"]))
            widgets["reveal"] = reveal
            if shown:                       # keep a revealed row shown across a refresh
                self._show_value(name, val_lbl)
        self._rows[name] = widgets

    def _build_editor(self, line: tk.Frame, name: str) -> None:
        """REPLACE: a masked entry in place of the value. The old key is never
        decrypted for this."""
        entry = tk.Entry(
            line, show="•", font=FONTS["small"],
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightbackground=C["border"], highlightcolor=C["purple"],
            highlightthickness=1, borderwidth=0,
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        _link(line, "✕", self._cancel_replace).pack(side="right")
        _link(line, "SAVE", lambda: self._save_replace(name, entry),
              font="small_bold").pack(side="right", padx=(0, 8))
        entry.bind("<Return>", lambda e: self._save_replace(name, entry))
        entry.bind("<Escape>", lambda e: self._cancel_replace())
        entry.focus_set()
        self._editor = entry

    def _start_replace(self, name: str) -> None:
        self._editing = name
        self._revealed[name] = False
        self._render_section("vault")
        self._status.config(text=f"paste the new {name}, then SAVE (or Enter).",
                            fg=C["text3"])

    def _cancel_replace(self) -> None:
        self._editing = None
        self._render_section("vault")
        self._status.config(text="nothing changed.", fg=C["text3"])

    def _save_replace(self, name: str, entry: tk.Entry) -> None:
        value = entry.get().strip()
        entry.delete(0, "end")
        if not value:
            self._status.config(text="paste the new key first.", fg=C["red"])
            return
        try:
            cerberus.vault_set(name, value)
        except cerberus.VaultError as e:
            self._status.config(text=error_text(e.slug), fg=C["red"])
            return
        self._editing = None
        self._status.config(text=f"saved {name}.", fg=C["text3"])
        self._alarms = tuple(cerberus.alarms())
        self._render_banner()
        self._render_section("vault")
        self._refresh_if_open("ledger")

    def _build_vault_form(self, parent: tk.Frame) -> None:
        """Generic add form for any other key: a name, a masked value, SAVE.
        Saving an existing name overwrites it, like REPLACE."""
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(8, 6))
        tk.Label(parent, text="another key:", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(fill="x")

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

        save_btn = _link(value_row, "SAVE",
                         lambda: self._on_vault_submit(name_entry, value_entry),
                         font="small_bold")
        save_btn.pack(side="left")
        value_entry.bind("<Return>", lambda e: self._on_vault_submit(name_entry, value_entry))
        name_entry.bind("<Return>", lambda e: value_entry.focus_set())

    def _on_vault_submit(self, name_entry: tk.Entry, value_entry: tk.Entry) -> None:
        name = name_entry.get().strip()
        value = value_entry.get().strip()
        if not name:
            self._status.config(text="name a key first.", fg=C["red"])
            return
        if not value:
            self._status.config(text="value can't be empty.", fg=C["red"])
            return
        value_entry.delete(0, "end")
        try:
            cerberus.vault_set(name, value)
        except cerberus.VaultError as e:
            self._status.config(text=error_text(e.slug), fg=C["red"])
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
            val_lbl.config(text=f"⚠ {error_text(e.slug)}", fg=C["red"])
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
            fname, exists, refused = r["file"], r.get("exists", False), r.get("refused")
            if refused:
                text, fg = f"{fname}  (refused: {error_text(refused)})", C["red"]
            elif not exists:
                text, fg = f"{fname}  (missing)", C["text3"]
            else:
                text, fg = fname, C["text1"]
            clickable = exists and not refused
            name_lbl = tk.Label(item, text=text, font=FONTS["small_bold"], fg=fg,
                                bg=C["card"], anchor="w", wraplength=360,
                                justify="left", cursor="hand2" if clickable else "arrow")
            name_lbl.pack(fill="x")
            if clickable:
                name_lbl.bind("<Button-1>", lambda e, f=fname: self._open_config(f))
                name_lbl.bind("<Enter>", lambda e, w=name_lbl: w.config(fg=C["amber"]))
                name_lbl.bind("<Leave>", lambda e, w=name_lbl: w.config(fg=C["text1"]))
            tk.Label(item, text=r.get("desc", ""), font=FONTS["tiny"],
                     fg=C["text3"], bg=C["card"], anchor="w",
                     wraplength=360, justify="left").pack(fill="x")

        if cfg_dir:
            tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", pady=(6, 4))
            _link(parent, "📂 Open Folder", self._open_folder).pack(anchor="w")

    def _open_config(self, fname: str) -> None:
        try:
            path = cerberus.custody_open(fname)
        except cerberus.CustodyError as e:
            self._status.config(text=f"won't open {fname}: {error_text(e.slug)}",
                                fg=C["red"])
            self._refresh_if_open("ledger")
            return
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
            head, action = e.get("head", "?"), e.get("action", "?")
            if action in _ALARM_ACTIONS:
                fg = C["red"]
            elif head == "vault":
                fg = C["text2"]
            else:
                fg = C["text3"]
            tk.Label(row, text=f"{head}·{action}", font=FONTS["tiny"], fg=fg,
                     bg=C["card"], width=18, anchor="w").pack(side="left")
            count = e.get("count", 1)
            suffix = f"  ×{count}" if count and count > 1 else ""
            tk.Label(row, text=f"{e.get('target', '')}{suffix}", font=FONTS["tiny"],
                     fg=C["text1"], bg=C["card"], anchor="w").pack(
                         side="left", fill="x", expand=True)
        self._note(parent, "vault reads dedupe per unlock session · everything "
                           "else is logged in full.")

    # ── small shared row helpers ─────────────────────────────────────────────

    def _empty(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, font=FONTS["small_italic"], fg=C["text3"],
                 bg=C["card"], anchor="w", wraplength=360, justify="left").pack(
                     anchor="w", pady=(2, 0))

    def _note(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, font=FONTS["tiny"], fg=C["text3"], bg=C["card"],
                 anchor="w", wraplength=360, justify="left").pack(anchor="w", pady=(4, 0))
