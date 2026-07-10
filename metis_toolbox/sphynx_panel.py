"""
sphynx_panel.py — Boot Litany + Riddle Gate UI
=================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw the Sphynx boot/riddle sequence, take PIN input, and hand
             off to felhaven.py on success. Owns tkinter and subprocess; all
             hash/attempt logic lives in sphynx.py — this module never
             computes a hash or touches the attempt counter directly.

First run:   When sphynx_data.json is absent (a fresh clone — the file is
             per-user now, no longer shipped), the gate can't verify anything,
             so instead of failing closed this panel shows a SETUP screen: the
             user writes their own riddle + PIN (or skips the gate entirely).
             sphynx.create() persists the choice; later launches gate normally
             against the stored riddle, or bypass if the user skipped.

Entry point: launched by Felhaven.bat in place of felhaven.py. On a correct
             PIN (or a skipped/just-created gate) it spawns felhaven.py as its
             own process and exits 0. On 3 wrong PINs its own window closes and
             it exits 1 — Felhaven is never spawned.

Root-level, standalone: owns its own tk.Tk(). Not under panels/, not
Kairos-registered — it runs once per launch and exits, it doesn't tick.

Upstream:    Felhaven.bat
Downstream:  sphynx.py (verification + create), felhaven.py (spawned on
             success), theme.py (colors/fonts)

Requires:    tkinter (stdlib), subprocess, sys, os.
"""

import os
import subprocess
import sys
import tkinter as tk

import sphynx
from theme import C, FONTS, _init_fonts

# felhaven.py lives next to this file — anchor to __file__ (CONVENTIONS §11),
# so this resolves correctly regardless of cwd.
_APP_ROOT        = os.path.dirname(os.path.abspath(__file__))
_FELHAVEN_SCRIPT = os.path.join(_APP_ROOT, "felhaven.py")
_NO_WINDOW       = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The fixed flavor lines shown before the riddle; the riddle itself is loaded
# per-launch from sphynx_data.json (sphynx.riddle()) and appended at _start.
_LITANY_PREFIX = [
    "...",
    ">> SPHYNX v1.0 — threshold guardian",
    ">> waking core..............[ OK ]",
    ">> calibrating phosphor array...[ OK ]",
    ">> loading riddle............[ OK ]",
    "",
    "I am Sphynx. I guard the gate to Felhaven.",
    "None may pass who cannot answer my riddle.",
    "",
]

_LINE_DELAY_MS = 450


class SphynxPanel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SPHYNX")
        self.root.configure(bg=C["bg"])
        self.root.geometry("640x440")
        self.root.minsize(480, 360)

        _init_fonts(self.root)

        self._success = False
        self._riddle_line = ""

        tk.Label(self.root, text="SPHYNX", font=FONTS["title"],
                 fg=C["text1"], bg=C["bg"]).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Frame(self.root, bg=C["border"], height=2).pack(fill="x", padx=16)

        # height=14 keeps the Text widget's own size request well under the
        # window's 440px — Tk's packer carves cavity in packing order, so an
        # unconstrained Text (default 24 rows) would claim nearly the whole
        # window before the input rows (packed later) ever get a share.
        self._log = tk.Text(
            self.root, bg=C["bg"], fg=C["text1"], font=FONTS["body"],
            borderwidth=0, highlightthickness=0, wrap="word", state="disabled",
            height=14,
        )
        self._log.pack(fill="both", expand=True, padx=16, pady=(10, 6))

        # ── Normal-gate PIN row (built now, packed only when the riddle shows) ──
        self._entry_frame = tk.Frame(self.root, bg=C["bg"])
        tk.Label(self._entry_frame, text=">", font=FONTS["body"],
                 fg=C["text1"], bg=C["bg"]).pack(side="left", padx=(16, 4))
        self._entry = tk.Entry(
            self._entry_frame, show="*", font=FONTS["body"],
            bg=C["card"], fg=C["text1"], insertbackground=C["text1"],
            highlightbackground=C["border"], highlightcolor=C["text1"],
            highlightthickness=1, borderwidth=0,
        )
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 16))
        self._entry.bind("<Return>", self._on_submit)

        # ── First-run SETUP form (built now, packed only on a fresh clone) ─────
        self._setup_frame = self._build_setup_form()

        self.root.after(300, self._start)

    # ── sequencing ──────────────────────────────────────────────────────

    def _print(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        self._log.configure(state="disabled")
        self._log.see("end")

    def _start(self) -> None:
        # 1) Gate explicitly skipped on a prior first run — stand aside.
        if sphynx.is_disabled():
            self._print("Sphynx stands aside — the gate is open.")
            self._success = True
            self.root.after(500, self._launch_felhaven)
            return
        # 2) No PIN-bearing file at all — a fresh clone. Offer first-run setup
        #    instead of failing closed (the old behavior).
        try:
            sphynx.preflight()
        except sphynx.HashFileError:
            self._begin_first_run()
            return
        # 3) Normal launch — pose the user's stored riddle.
        self._riddle_line = sphynx.riddle()
        self._reveal(_LITANY_PREFIX + [self._riddle_line], 0)

    def _reveal(self, litany: list, i: int) -> None:
        if i >= len(litany):
            return
        line = litany[i]
        self._print(line)
        if line == self._riddle_line and self._riddle_line:
            # side="bottom" so the packer carves this row from the bottom of the
            # window regardless of the log Text's expand=True request.
            self._entry_frame.pack(side="bottom", fill="x", pady=(0, 14))
            self._entry.focus_set()
            return
        self.root.after(_LINE_DELAY_MS, self._reveal, litany, i + 1)

    # ── normal-gate input handling ──────────────────────────────────────

    def _on_submit(self, _event=None) -> None:
        pin = self._entry.get()
        self._entry.delete(0, "end")
        if sphynx.verify(pin):
            self._print("Correct... Summoning the Pantheon...")
            self._entry_frame.pack_forget()
            self._success = True
            self.root.after(500, self._launch_felhaven)
            return
        remaining = sphynx.attempts_left()
        if remaining > 0:
            suffix = "attempt" if remaining == 1 else "attempts"
            self._print(f"Wrong. The gate does not yield to that. {remaining} {suffix} remain.")
            self._entry.focus_set()
        else:
            self._print("Wrong. The gate does not yield to that.")
            self._print("The Sphynx falls silent — the riddle keeps its secret tonight.")
            self._entry_frame.pack_forget()
            self.root.after(1500, self.root.destroy)

    # ── first-run setup ─────────────────────────────────────────────────

    def _build_setup_form(self) -> tk.Frame:
        """A three-field setup form (riddle, PIN, confirm) plus Set-gate and
        Skip-gate actions. Built once, packed only on a fresh clone."""
        frame = tk.Frame(self.root, bg=C["bg"])

        def _row(label: str, show: str = "") -> tk.Entry:
            row = tk.Frame(frame, bg=C["bg"])
            row.pack(fill="x", padx=16, pady=2)
            tk.Label(row, text=label, font=FONTS["small"], fg=C["text2"],
                     bg=C["bg"], anchor="w", width=16).pack(side="left")
            entry = tk.Entry(
                row, show=show, font=FONTS["body"], bg=C["card"], fg=C["text1"],
                insertbackground=C["text1"], highlightbackground=C["border"],
                highlightcolor=C["text1"], highlightthickness=1, borderwidth=0,
            )
            entry.pack(side="left", fill="x", expand=True)
            return entry

        self._riddle_entry  = _row("Riddle / statement")
        self._pin_entry     = _row("Choose a PIN", show="*")
        self._confirm_entry = _row("Confirm PIN", show="*")
        self._pin_entry.bind("<Return>", lambda e: self._confirm_entry.focus_set())
        self._confirm_entry.bind("<Return>", lambda e: self._on_create())

        actions = tk.Frame(frame, bg=C["bg"])
        actions.pack(fill="x", padx=16, pady=(6, 12))
        set_btn = tk.Label(actions, text="SET GATE", font=FONTS["small_bold"],
                           fg=C["text1"], bg=C["bg"], cursor="hand2")
        set_btn.pack(side="left")
        set_btn.bind("<Button-1>", lambda e: self._on_create())
        set_btn.bind("<Enter>", lambda e: set_btn.config(fg=C["amber"]))
        set_btn.bind("<Leave>", lambda e: set_btn.config(fg=C["text1"]))

        skip_btn = tk.Label(actions, text="skip the gate", font=FONTS["small"],
                            fg=C["text3"], bg=C["bg"], cursor="hand2")
        skip_btn.pack(side="right")
        skip_btn.bind("<Button-1>", lambda e: self._on_skip())
        skip_btn.bind("<Enter>", lambda e: skip_btn.config(fg=C["amber"]))
        skip_btn.bind("<Leave>", lambda e: skip_btn.config(fg=C["text3"]))

        self._setup_status = tk.Label(frame, text="", font=FONTS["tiny"],
                                      fg=C["red"], bg=C["bg"], anchor="w",
                                      wraplength=580, justify="left")
        self._setup_status.pack(fill="x", padx=16, pady=(0, 8))
        return frame

    def _begin_first_run(self) -> None:
        self._print("No gate is set — this looks like a fresh install.")
        self._print("Write a riddle or statement and choose a PIN to guard the")
        self._print("gate, or skip it (it's soft 'family-misclick' theater, not")
        self._print("real security — a solo user may not want it).")
        self._print("")
        self._setup_frame.pack(side="bottom", fill="x")
        self._riddle_entry.focus_set()

    def _on_create(self) -> None:
        riddle = self._riddle_entry.get().strip()
        pin = self._pin_entry.get()
        confirm = self._confirm_entry.get()
        if not riddle:
            self._setup_status.config(text="Write a riddle or statement first.")
            return
        if not pin.strip():
            self._setup_status.config(text="Choose a PIN.")
            return
        if pin != confirm:
            self._setup_status.config(text="The two PINs don't match.")
            self._confirm_entry.delete(0, "end")
            return
        try:
            sphynx.create(pin, riddle)
        except OSError as e:
            self._setup_status.config(text=f"Could not write the gate: {e}")
            return
        self._setup_frame.pack_forget()
        self._print("")
        self._print("Gate set. It's yours now. Entering Felhaven...")
        self._success = True
        self.root.after(700, self._launch_felhaven)

    def _on_skip(self) -> None:
        try:
            sphynx.create("", disabled=True)
        except OSError as e:
            self._setup_status.config(text=f"Could not save your choice: {e}")
            return
        self._setup_frame.pack_forget()
        self._print("")
        self._print("Gate skipped — Sphynx will stand aside from now on.")
        self._success = True
        self.root.after(700, self._launch_felhaven)

    # ── handoff ─────────────────────────────────────────────────────────

    def _launch_felhaven(self) -> None:
        subprocess.Popen(
            [sys.executable, _FELHAVEN_SCRIPT],
            cwd=_APP_ROOT,
            creationflags=_NO_WINDOW,
        )
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0 if self._success else 1


if __name__ == "__main__":
    sys.exit(SphynxPanel().run())
