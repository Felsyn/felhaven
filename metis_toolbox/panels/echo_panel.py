"""
Echo panel — paste text, get an audio file.

EchoPanel is a bare tk.Frame tab body inside VoxArrayPanel (the ECHO tab), the
same shape as the Cogitator tab bodies. It is NOT registered with Kairos.

Top to bottom inside self:
    Text box       — paste the Markdown / prose to narrate (multi-line).
    Filename row    — the output name; ".opus" is appended by Echo if omitted.
    Send button     — inert (dim, unbound) until BOTH fields are non-empty after
                      echo.sanitize_filename; runs the conversion off the UI thread.
    Status line     — normal-theme path on success, LOUD RED on any error.

Both the text box and the filename entry get a themed right-click context menu
(Cut/Copy/Paste/Select All) — the home_panel.py (Pythia) precedent, ported
verbatim. The text box is editable (unlike Pythia's read-only transcript), so
it gets the full entry-style menu rather than the transcript's Copy-only one.

─────────────────────────────────────────────────────────────────────────────
CONVERSION THREADING + DRAIN — a documented, house-rule-aware pattern.

  echo.text_to_audio() chunks + synthesizes a whole document and shells out to
  ffmpeg; that can take real time, so it runs on a daemon thread (its ONLY shared
  touch is self._result_q.put(result) — it never touches a Tk object).

  Echo is NOT a Kairos worker (it is request-driven, not polled), so there is no
  existing tick to drain the result on. Instead a BOUNDED self.after() chain is
  started on Send and stops itself once the result is delivered — the EmanonPanel
  one-shot-chain precedent, not a periodic loop competing with Kairos. It is
  winfo_exists()-guarded and cancelled on destroy so a conversion finishing
  during app close can never fire into a dead widget.

  A single-flight guard (self._converting) makes a second click while a
  conversion is in flight a no-op, not a queued second job.
─────────────────────────────────────────────────────────────────────────────
"""

import queue
import threading
import tkinter as tk
from typing import Any

from theme import C, FONTS

from tools import echo

# How often the drain chain polls the result queue while a conversion runs.
_DRAIN_MS = 150

# Human-readable status text per Echo error code (anything unknown falls back to
# the raw code so a new code is still surfaced, never swallowed).
_ERROR_TEXT = {
    "empty_filename": "enter a filename",
    "empty_text": "paste some text first",
    "ffmpeg_unavailable": "ffmpeg not found — drop ffmpeg.exe in bin\\ or install to PATH",
    "synthesis_failed": "synthesis failed — is the kokoro model installed?",
    "ffmpeg_encode_failed": "ffmpeg could not encode Opus (is it built with libopus?)",
    "echo_failed": "conversion failed — check the log",
}


class EchoPanel(tk.Frame):
    """Paste-and-convert UI wrapper around echo.text_to_audio()."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])

        self._result_q: queue.Queue = queue.Queue()
        self._converting = False
        self._drain_after_id = None

        # ── Text box ─────────────────────────────────────────────────────────
        tk.Label(self, text="TEXT", font=FONTS["card_header"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(fill="x", pady=(6, 2))

        self._text = tk.Text(
            self, height=8, wrap="word", font=FONTS["small"],
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightthickness=1, highlightbackground=C["border"], bd=0,
            padx=6, pady=4,
        )
        self._text.pack(fill="both", expand=True)
        self._text.bind("<KeyRelease>", lambda e: self._refresh_gate())
        self._text.bind("<Button-3>", self._show_text_menu)

        # ── Filename row ─────────────────────────────────────────────────────
        row = tk.Frame(self, bg=C["card"])
        row.pack(fill="x", pady=(8, 0))

        tk.Label(row, text="filename", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"], anchor="w").pack(side="left", padx=(0, 6))

        # Send button — packed BEFORE the expanding entry so it lands on the right.
        self._send_btn = tk.Label(row, text="Send to Echo", font=FONTS["small"],
                                   fg=C["text3"], bg=C["card"], padx=10, pady=2)
        self._send_btn.pack(side="right")

        self._fname_var = tk.StringVar()
        self._fname_var.trace_add("write", lambda *a: self._refresh_gate())
        self._fname_entry = tk.Entry(
            row, textvariable=self._fname_var, font=FONTS["small"],
            bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
            highlightthickness=1, highlightbackground=C["border"], bd=0,
        )
        self._fname_entry.pack(fill="x", expand=True, side="left")
        self._fname_entry.bind("<Return>", lambda e: self._on_send())
        self._fname_entry.bind("<Button-3>", self._show_entry_menu)

        # ── Status line ──────────────────────────────────────────────────────
        self._status = tk.Label(self, text="", font=FONTS["small"], fg=C["text3"],
                                bg=C["card"], anchor="w", justify="left",
                                wraplength=560)
        self._status.pack(fill="x", pady=(8, 2))

        self._refresh_gate()

    # ── Button gating ───────────────────────────────────────────────────────

    def _fields_ready(self) -> bool:
        """Both fields usable: text non-blank AND filename survives sanitisation
        (echo.sanitize_filename is the single source of truth — decision #10)."""
        has_text = bool(self._text.get("1.0", "end").strip())
        has_name = bool(echo.sanitize_filename(self._fname_var.get()))
        return has_text and has_name

    def _refresh_gate(self) -> None:
        self._set_button_enabled(self._fields_ready() and not self._converting)

    def _set_button_enabled(self, enabled: bool) -> None:
        if enabled:
            self._send_btn.config(fg=C["text1"], cursor="hand2")
            self._send_btn.bind("<Button-1>", lambda e: self._on_send())
            self._send_btn.bind("<Enter>", lambda e: self._send_btn.config(fg=C["purple"]))
            self._send_btn.bind("<Leave>", lambda e: self._send_btn.config(fg=C["text1"]))
        else:
            self._send_btn.config(fg=C["text3"], cursor="arrow")
            self._send_btn.unbind("<Button-1>")
            self._send_btn.unbind("<Enter>")
            self._send_btn.unbind("<Leave>")

    # ── Right-click context menus (Pythia home_panel precedent) ─────────────
    # The text box is editable (unlike Pythia's read-only transcript), so it
    # gets the full Cut/Copy/Paste/Select All set, same as the filename entry.

    def _themed_menu(self) -> tk.Menu:
        """One in-theme popup menu — built fresh per right-click so its item
        states (e.g. Cut/Copy disabled with no selection) always reflect the
        current selection rather than a stale snapshot."""
        return tk.Menu(self, tearoff=0, bg=C["card"], fg=C["text1"],
                       activebackground=C["border"], activeforeground=C["text1"],
                       font=FONTS["small"], borderwidth=0)

    @staticmethod
    def _popup(menu: tk.Menu, event: "tk.Event[Any]") -> None:
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_text_menu(self, event: "tk.Event[Any]") -> None:
        has_selection = bool(self._text.tag_ranges("sel"))
        menu = self._themed_menu()
        menu.add_command(label="Cut", command=lambda: self._text.event_generate("<<Cut>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Copy", command=lambda: self._text.event_generate("<<Copy>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Paste", command=lambda: self._text.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=self._select_all_text)
        self._popup(menu, event)

    def _select_all_text(self) -> None:
        self._text.tag_add("sel", "1.0", "end")

    def _show_entry_menu(self, event: "tk.Event[Any]") -> None:
        has_selection = self._fname_entry.selection_present()
        menu = self._themed_menu()
        menu.add_command(label="Cut", command=lambda: self._fname_entry.event_generate("<<Cut>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Copy", command=lambda: self._fname_entry.event_generate("<<Copy>>"),
                         state="normal" if has_selection else "disabled")
        menu.add_command(label="Paste", command=lambda: self._fname_entry.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self._fname_entry.select_range(0, "end"))
        self._popup(menu, event)

    # ── Status helpers ──────────────────────────────────────────────────────

    def _set_status(self, text: str, *, error: bool = False, working: bool = False) -> None:
        if error:
            color = C["red"]          # loud + red — decision #16
        elif working:
            color = C["text3"]
        else:
            color = C["green"]
        self._status.config(text=text, fg=color)

    # ── Conversion (daemon thread + bounded drain chain) ────────────────────

    def _on_send(self) -> None:
        # Single-flight: a click while a conversion is in flight is a no-op.
        if self._converting or not self._fields_ready():
            return
        text = self._text.get("1.0", "end")
        filename = self._fname_var.get()

        self._converting = True
        self._set_button_enabled(False)
        self._set_status("converting…", working=True)

        # The worker's only shared touch is self._result_q.put(...) — no Tk.
        threading.Thread(
            target=lambda: self._result_q.put(echo.text_to_audio(text, filename)),
            daemon=True,
        ).start()
        self._drain_after_id = self.after(_DRAIN_MS, self._drain)

    def _drain(self) -> None:
        """Bounded, self-terminating poll of the result queue. Reschedules only
        while empty; stops the chain the moment a result arrives. Guarded against
        firing into a destroyed widget on app shutdown (EmanonPanel precedent)."""
        if not self.winfo_exists():
            return
        try:
            result = self._result_q.get_nowait()
        except queue.Empty:
            self._drain_after_id = self.after(_DRAIN_MS, self._drain)
            return
        self._drain_after_id = None
        self._deliver(result)

    def _deliver(self, result: dict) -> None:
        self._converting = False
        if "path" in result:
            self._set_status(f"saved → {result['path']}")
            # Clear both fields on success so the next paste starts fresh —
            # otherwise every conversion needs a manual delete first. Left
            # alone on error: a failed attempt should stay editable for retry.
            self._text.delete("1.0", "end")
            self._fname_var.set("")
        else:
            code = result.get("error", "echo_failed")
            self._set_status(_ERROR_TEXT.get(code, f"error: {code}"), error=True)
        self._refresh_gate()

    def destroy(self) -> None:
        """Cancel any pending drain callback before teardown so it can't fire
        into a dead widget (belt-and-suspenders with the winfo_exists guard)."""
        if self._drain_after_id is not None:
            self.after_cancel(self._drain_after_id)
            self._drain_after_id = None
        super().destroy()
