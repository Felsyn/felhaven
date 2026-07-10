"""
Midas panel — market prices (PRICES tab) + holdings ledger (LEDGER tab).

Cerberus gate: the whole panel is sealed behind the Cerberus PIN (Midas is the
guardian's first real consumer — see the Cerberus brief, locked decision #2).
Until the PIN is entered the body shows an alarm-red-on-black gate; the tabs are
built lazily on unlock and Kairos ticks are buffered until then. This is a
UI-only wrapper — midas.py (the tool) is untouched, and the gate uses
cerberus.verify() (a PIN check), not the Vault's encryption.

MidasPanel hosts two tabs:
    PRICES — one row per ticker in midas_watchlist.json, current price + daily %
             change (green/red). Driven by the `midas` Kairos worker via
             update(). (Was the whole panel; now a tab.)
    LEDGER — Plutus: manual buy/sell entry + derived totals + scrolling record.
             No network, no Kairos worker — pure local CRUD. Rebuilt from the
             ledger file on each entry.

Tab pattern mirrors PhemePanel: tk.Label tabs with a 1px underline indicator.

Plutus ledger UI:
    Top:    total shares held · total cost invested   ('0 / $0.00' when empty)
    Entry:  ticker dropdown (from watchlist) · shares · price · BUY/SELL · Add
    Below:  scrolling record, newest first —
            date · ticker · action · shares · cost-of-this-event
            BUY rows green, SELL rows red.
"""

import tkinter as tk
from datetime import datetime

from theme import C, FONTS, Card, PhosphorScroll

import cerberus
from tools import midas, plutus


class MidasPanel(Card):
    """PRICES driven by Kairos via update(); LEDGER is local Plutus CRUD."""

    # Card header follows the active tab, so it never says "market prices" while
    # the ledger is showing. Card uppercases on set, so we do too here.
    _HEADERS = {
        "prices": "DYNASTIC VAULT — MARKET PRICES",
        "ledger": "DYNASTIC VAULT — HOLDINGS LEDGER",
    }

    def __init__(self, parent):
        super().__init__(parent, "Dynastic Vault — market prices", C["amber"])

        self._watchlist = midas._load_watchlist()

        # ── Cerberus PIN gate ─────────────────────────────────────────────────
        # The Vault is sealed until the Cerberus PIN is entered (Cerberus is the
        # first real consumer of the guardian). The gate is rendered in the
        # alarm red on black, deliberately louder than the panel's amber. The
        # real content is built lazily on unlock; update() buffers ticks until
        # then. midas.py (the tool) is untouched — this is a UI-only wrapper.
        self._unlocked = False
        self._pending: dict | None = None      # last tick received while sealed
        self._content_built = False

        self._gate = tk.Frame(self.body, bg=C["bg"])
        self._build_gate(self._gate)
        self._gate.pack(fill="both", expand=True)

        self._content = tk.Frame(self.body, bg=C["card"])

    # ─────────────────────────────────────────────────────────────────────────
    #  Cerberus gate (alarm red on black) — the only new surface; verify() only.
    # ─────────────────────────────────────────────────────────────────────────

    def _build_gate(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="DYNASTIC VAULT — SEALED", font=FONTS["card_header"],
                 fg=C["red"], bg=C["bg"], anchor="w").pack(fill="x", pady=(10, 2))
        tk.Label(parent,
                 text="Cerberus guards this vault. Enter the guardian's PIN.",
                 font=FONTS["small_italic"], fg=C["red"], bg=C["bg"], anchor="w",
                 wraplength=360, justify="left").pack(fill="x", pady=(0, 10))

        prompt = tk.Frame(parent, bg=C["bg"])
        prompt.pack(fill="x")
        tk.Label(prompt, text="PIN ▸", font=FONTS["body"], fg=C["red"],
                 bg=C["bg"]).pack(side="left", padx=(0, 6))
        self._gate_entry = tk.Entry(
            prompt, show="*", font=FONTS["body"], width=14,
            bg=C["bg"], fg=C["red"], insertbackground=C["red"],
            highlightbackground=C["red"], highlightcolor=C["red"],
            highlightthickness=1, borderwidth=0,
        )
        self._gate_entry.pack(side="left")
        self._gate_entry.bind("<Return>", self._on_gate_submit)

        btn = tk.Label(prompt, text="UNLOCK", font=FONTS["small_bold"],
                       fg=C["red"], bg=C["bg"], cursor="hand2")
        btn.pack(side="left", padx=(8, 0))
        btn.bind("<Button-1>", self._on_gate_submit)

        self._gate_status = tk.Label(parent, text="", font=FONTS["tiny"],
                                     fg=C["red"], bg=C["bg"], anchor="w",
                                     wraplength=360, justify="left")
        self._gate_status.pack(fill="x", pady=(10, 0))

        # Fail closed if Cerberus has no PIN set — a calm placeholder, not a crash.
        try:
            cerberus.preflight()
        except cerberus.HashFileError:
            self._gate_entry.config(state="disabled")
            self._gate_status.config(
                text="Cerberus has no PIN set — run "
                     "`python cerberus.py setpin <pin>` first.")

    def _on_gate_submit(self, _event=None) -> None:
        pin = self._gate_entry.get()
        self._gate_entry.delete(0, "end")
        try:
            ok = cerberus.verify(pin)
        except cerberus.HashFileError:
            self._gate_status.config(text="Cerberus's hash file vanished.")
            return
        if ok:
            self._unlock()
            return
        remaining = cerberus.attempts_left()
        if remaining > 0:
            noun = "attempt" if remaining == 1 else "attempts"
            self._gate_status.config(text=f"Wrong PIN. {remaining} {noun} remain.")
            self._gate_entry.focus_set()
        else:
            self._gate_entry.config(state="disabled")
            self._gate_status.config(
                text="Sealed — too many attempts. Relaunch to try again.")

    def _unlock(self) -> None:
        self._unlocked = True
        self._build_content()
        self._gate.pack_forget()
        self._content.pack(fill="both", expand=True)
        if self._pending is not None:      # apply the tick that arrived while sealed
            self.update(self._pending)
            self._pending = None

    def _build_content(self) -> None:
        if self._content_built:
            return

        # ── Tab bar ───────────────────────────────────────────────────────────
        tab_row = tk.Frame(self._content, bg=C["card"])
        tab_row.pack(fill="x", pady=(6, 0))

        self._tabs:  dict[str, tk.Label] = {}
        self._lines: dict[str, tk.Frame] = {}
        for key, text in (("prices", "PRICES"), ("ledger", "LEDGER")):
            wrap = tk.Frame(tab_row, bg=C["card"])
            wrap.pack(side="left", padx=(0, 14))
            lbl = tk.Label(wrap, text=text, font=FONTS["card_header"],
                           fg=C["text3"], bg=C["card"], cursor="hand2")
            lbl.pack()
            line = tk.Frame(wrap, height=1, bg=C["border"])
            line.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tabs[key]  = lbl
            self._lines[key] = line

        # ── Content frames ────────────────────────────────────────────────────
        self._prices_frame = tk.Frame(self._content, bg=C["card"])
        self._ledger_frame = tk.Frame(self._content, bg=C["card"])

        self._build_prices(self._prices_frame)
        self._build_ledger(self._ledger_frame)

        # ── Activate PRICES ───────────────────────────────────────────────────
        self._active = "prices"
        self._prices_frame.pack(fill="both", expand=True)
        self._tabs["prices"].config(fg=C["text1"])
        self._lines["prices"].config(bg=C["amber"])
        self._content_built = True

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _show_tab(self, key: str) -> None:
        if key == self._active:
            return
        self._prices_frame.pack_forget()
        self._ledger_frame.pack_forget()
        self._tabs[self._active].config(fg=C["text3"])
        self._lines[self._active].config(bg=C["border"])

        self._active = key
        frame = self._prices_frame if key == "prices" else self._ledger_frame
        frame.pack(fill="both", expand=True)
        self._tabs[key].config(fg=C["text1"])
        self._lines[key].config(bg=C["amber"])
        self._header_lbl.config(text=self._HEADERS[key])

        if key == "ledger":
            self._refresh_ledger()   # show latest on entry

    # ─────────────────────────────────────────────────────────────────────────
    #  PRICES tab
    # ─────────────────────────────────────────────────────────────────────────

    def _build_prices(self, parent: tk.Frame) -> None:
        self._rows_frame = tk.Frame(parent, bg=C["card"])
        self._rows_frame.pack(fill="both", expand=True, pady=(6, 0))

        self._last_lbl = tk.Label(parent, text="", font=FONTS["card_header"],
                                  fg=C["text3"], bg=C["card"], anchor="e")
        self._last_lbl.pack(fill="x", pady=(4, 0))

        self._row_widgets = {}
        for sym in self._watchlist:
            self._build_price_row(sym)

    def _build_price_row(self, symbol: str) -> None:
        row = tk.Frame(self._rows_frame, bg=C["card"])
        row.pack(fill="x", pady=1)

        tk.Label(row, text=symbol, font=FONTS["small"],
                 fg=C["text2"], bg=C["card"], anchor="w", width=8).pack(side="left")

        price_lbl = tk.Label(row, text="—", font=FONTS["small_bold"],
                             fg=C["text1"], bg=C["card"], anchor="e")
        price_lbl.pack(side="right", padx=(8, 0))

        pct_lbl = tk.Label(row, text="", font=FONTS["small"],
                           fg=C["text3"], bg=C["card"], anchor="e")
        pct_lbl.pack(side="right")

        self._row_widgets[symbol] = (price_lbl, pct_lbl)

    def update(self, data: dict) -> None:
        """Called by the `midas` Kairos worker (60 s) on the main thread."""
        if data is None:
            return
        if not self._unlocked:
            # Vault still sealed: remember the latest tick and apply it on unlock
            # so prices are current the instant the gate opens.
            self._pending = data
            return
        tickers = data.get("tickers", [])
        for r in tickers:
            sym = r["symbol"]
            if sym not in self._row_widgets:
                continue
            price_lbl, pct_lbl = self._row_widgets[sym]
            if "error" in r:
                price_lbl.config(text="—", fg=C["text3"])
                pct_lbl.config(text="err", fg=C["text3"])
            else:
                price_lbl.config(text=r["price_fmt"], fg=C["text1"])
                color = C["green"] if r["direction"] == "up" else C["red"]
                pct_lbl.config(text=r["pct_fmt"], fg=color)
        self._last_lbl.config(text=f"updated {datetime.now().strftime('%H:%M')}")

    # ─────────────────────────────────────────────────────────────────────────
    #  LEDGER tab (Plutus)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ledger(self, parent: tk.Frame) -> None:
        # ── Totals header ─────────────────────────────────────────────────────
        self._totals_lbl = tk.Label(
            parent, text="0 shares  ·  $0.00 invested",
            font=FONTS["small_bold"], fg=C["text1"], bg=C["card"], anchor="w",
        )
        self._totals_lbl.pack(fill="x", pady=(8, 6))

        # ── Entry row ─────────────────────────────────────────────────────────
        entry = tk.Frame(parent, bg=C["card"])
        entry.pack(fill="x", pady=(0, 6))

        # Ticker dropdown (from watchlist)
        self._sel_ticker = tk.StringVar(value=self._watchlist[0] if self._watchlist else "")
        opts = self._watchlist or [""]
        ticker_menu = tk.OptionMenu(entry, self._sel_ticker, *opts)
        ticker_menu.config(font=FONTS["small"], bg=C["bar_bg"], fg=C["text1"],
                           activebackground=C["border"], activeforeground=C["text1"],
                           highlightthickness=0, bd=0, width=6)
        ticker_menu["menu"].config(bg=C["bar_bg"], fg=C["text1"])
        ticker_menu.pack(side="left", padx=(0, 4))

        # Shares
        self._sh_var = tk.StringVar()
        tk.Entry(entry, textvariable=self._sh_var, font=FONTS["small"], width=6,
                 bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
                 highlightthickness=1, highlightbackground=C["border"], bd=0
                 ).pack(side="left", padx=(0, 4))
        tk.Label(entry, text="sh", font=FONTS["tiny"], fg=C["text3"],
                 bg=C["card"]).pack(side="left", padx=(0, 6))

        # Price
        tk.Label(entry, text="$", font=FONTS["small"], fg=C["text3"],
                 bg=C["card"]).pack(side="left")
        self._pr_var = tk.StringVar()
        tk.Entry(entry, textvariable=self._pr_var, font=FONTS["small"], width=8,
                 bg=C["bar_bg"], fg=C["text1"], insertbackground=C["text1"],
                 highlightthickness=1, highlightbackground=C["border"], bd=0
                 ).pack(side="left", padx=(0, 6))

        # Buy/Sell toggle (default BUY — we stack, we don't sell)
        self._action = tk.StringVar(value="buy")
        self._action_btn = tk.Label(
            entry, text="BUY", font=FONTS["small_bold"], width=5,
            fg=C["green"], bg=C["bar_bg"], cursor="hand2",
        )
        self._action_btn.pack(side="left", padx=(0, 6))
        self._action_btn.bind("<Button-1>", lambda e: self._toggle_action())

        # Add
        add_btn = tk.Label(entry, text="+ ADD", font=FONTS["small_bold"],
                           fg=C["amber"], bg=C["card"], cursor="hand2")
        add_btn.pack(side="left")
        add_btn.bind("<Button-1>", lambda e: self._on_add())

        # ── Inline error / status line ────────────────────────────────────────
        self._ledger_msg = tk.Label(parent, text="", font=FONTS["tiny"],
                                    fg=C["text3"], bg=C["card"], anchor="w")
        self._ledger_msg.pack(fill="x")

        # ── Scrolling record ──────────────────────────────────────────────────
        self._record = _LedgerScroll(parent)
        self._record.pack(fill="both", expand=True, pady=(4, 0))

    def _toggle_action(self) -> None:
        if self._action.get() == "buy":
            self._action.set("sell")
            self._action_btn.config(text="SELL", fg=C["red"])
        else:
            self._action.set("buy")
            self._action_btn.config(text="BUY", fg=C["green"])

    def _on_add(self) -> None:
        ticker = self._sel_ticker.get()
        action = self._action.get()
        try:
            shares = float(self._sh_var.get())
            price  = float(self._pr_var.get())
        except ValueError:
            self._ledger_msg.config(text="shares and price must be numbers", fg=C["red"])
            return
        try:
            plutus.add_event(ticker, action, shares, price)
        except ValueError as e:
            self._ledger_msg.config(text=str(e), fg=C["red"])
            return

        self._sh_var.set("")
        self._pr_var.set("")
        self._ledger_msg.config(
            text=f"recorded {action} {shares:g} {ticker} @ ${price:,.2f}", fg=C["text3"])
        self._refresh_ledger()

    def _refresh_ledger(self) -> None:
        """Recompute totals + rebuild the record from the ledger file."""
        t = plutus.totals()
        self._totals_lbl.config(
            text=f"{t['shares']:g} shares  ·  ${t['cost']:,.2f} invested")
        self._record.set_rows(plutus.history())


# ─────────────────────────────────────────────────────────────────────────────
#  Scrollable ledger record
# ─────────────────────────────────────────────────────────────────────────────

class _LedgerScroll(tk.Frame):
    """Vertically-scrollable container for ledger rows (Canvas + Scrollbar)."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["card"])
        self._canvas = tk.Canvas(self, bg=C["card"], highlightthickness=0, bd=0)
        self._scroll = PhosphorScroll(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._scroll.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self._canvas, bg=C["card"])
        self._win = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(self._win, width=e.width))
        self._canvas.bind("<Enter>", self._bind_wheel)
        self._canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e):
        self._canvas.bind_all("<MouseWheel>", self._wheel)
        self._canvas.bind_all("<Button-4>", self._wheel)
        self._canvas.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _e):
        self._canvas.unbind_all("<MouseWheel>")
        self._canvas.unbind_all("<Button-4>")
        self._canvas.unbind_all("<Button-5>")

    def _wheel(self, event):
        if event.num == 5 or event.delta < 0:
            self._canvas.yview_scroll(1, "units")
        elif event.num == 4 or event.delta > 0:
            self._canvas.yview_scroll(-1, "units")

    def set_rows(self, rows: list) -> None:
        for w in self.inner.winfo_children():
            w.destroy()

        if not rows:
            tk.Label(self.inner, text="no transactions yet",
                     font=FONTS["small_italic"], fg=C["text3"],
                     bg=C["card"]).pack(anchor="w", pady=(8, 0))
            return

        for r in rows:
            color = C["green"] if r["action"] == "buy" else C["red"]
            line = tk.Frame(self.inner, bg=C["card"])
            line.pack(fill="x", pady=1)

            tk.Label(line, text=r["date"], font=FONTS["tiny"], fg=C["text3"],
                     bg=C["card"], width=10, anchor="w").pack(side="left")
            tk.Label(line, text=r["ticker"], font=FONTS["small"], fg=C["text2"],
                     bg=C["card"], width=6, anchor="w").pack(side="left")
            tk.Label(line, text=r["action"].upper(), font=FONTS["small_bold"],
                     fg=color, bg=C["card"], width=5, anchor="w").pack(side="left")
            tk.Label(line, text=f"{r['shares']:g} sh", font=FONTS["small"],
                     fg=C["text1"], bg=C["card"], width=9, anchor="w").pack(side="left")
            tk.Label(line, text=f"${r['cost']:,.2f}", font=FONTS["small"],
                     fg=color, bg=C["card"], anchor="e").pack(side="right")

            tk.Frame(self.inner, bg=C["border"], height=1).pack(fill="x")
