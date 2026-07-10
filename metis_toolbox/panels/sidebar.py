import tkinter as tk

import themis
from theme import C, FONTS


class SidebarRow(tk.Frame):
    def __init__(self, parent, name: str, label: str, subtitle: str, on_click):
        super().__init__(parent, bg=C["bg"],
                         highlightbackground=C["border"], highlightthickness=2)

        inner = tk.Frame(self, bg=C["bg"])
        inner.pack(side="left", fill="both", expand=True, padx=(8, 8), pady=6)

        self._dot = tk.Canvas(inner, width=8, height=8, bg=C["bg"], highlightthickness=0)
        self._dot_item = self._dot.create_oval(1, 1, 7, 7, fill=C["amber"], outline="")
        self._dot.pack(side="left", padx=(0, 8))

        text_frame = tk.Frame(inner, bg=C["bg"])
        text_frame.pack(side="left", fill="both", expand=True)

        self._label_lbl = tk.Label(
            text_frame, text=label, font=FONTS["small_bold"],
            fg=C["text2"], bg=C["bg"], anchor="w",
        )
        self._label_lbl.pack(anchor="w")

        self._subtitle_lbl = tk.Label(
            text_frame, text=subtitle, font=FONTS["tiny"],
            fg=C["text3"], bg=C["bg"], anchor="w",
        )
        self._subtitle_lbl.pack(anchor="w")

        _cb = lambda e, n=name: on_click(n)
        for w in (self, inner, self._dot, text_frame,
                  self._label_lbl, self._subtitle_lbl):
            w.bind("<Button-1>", _cb)
            w.configure(cursor="hand2")

    def set_active(self, active: bool) -> None:
        if active:
            self._dot.itemconfigure(self._dot_item, fill=C["green"])
            self._label_lbl.configure(fg=C["text1"])
            self.configure(highlightbackground=C["green"])    # active border lights up too
        else:
            self._dot.itemconfigure(self._dot_item, fill=C["amber"])
            self._label_lbl.configure(fg=C["text2"])
            self.configure(highlightbackground=C["border"])

    def _set_subtitle(self, text: str) -> None:
        self._subtitle_lbl.configure(text=text)

    def update(self, data) -> None:
        pass


class Sidebar(tk.Frame):
    BASE_WIDTH = 200   # px at scale 1.0; rescale() grows it to match the fonts

    _ROWS = [
        ("felhaven",   "Felhaven",        "Home"),
        ("horai",      "Chronometry",     "Temporal Awareness"),
        ("moderati",   "Moderati",        "Vitals · Logs"),
        ("aura",       "Atmospherics",    "—°F Weather"),
        ("hypatia",    "Celestarium",     "Star Map"),
        ("midas",      "Dynastic Vault",  "Finance"),
        ("pheme",      "Scriptorium",     "Rumors"),
        ("morpheus",   "Vox Array",       "Audio"),
        ("cogitator",  "Cogitator",       "Tasks · Calc · Convert"),
    ]

    def __init__(self, parent, on_select):
        super().__init__(parent, bg=C["bg"], width=self.BASE_WIDTH)
        self.pack_propagate(False)
        self._rows: dict[str, SidebarRow] = {}

        for name, label, subtitle in self._ROWS:
            row = SidebarRow(self, name, label, subtitle, on_select)
            row.pack(fill="x", pady=3)        # pady gives the bordered cards breathing room
            self._rows[name] = row

        aura_row = self._rows["aura"]

        def _aura_update(data, _row=aura_row):
            unit = themis.temperature_unit()          # "F" or "C"
            if data is None:
                _row._set_subtitle(f"—°{unit} Weather")
            else:
                temp = data["temp_c"] if unit == "C" else data["temp_f"]
                _row._set_subtitle(f"{temp}°{unit} Weather")

        aura_row.update = _aura_update

    def rescale(self, scale: float) -> None:
        """Grow the fixed sidebar width in lockstep with the fonts so the nav
        labels never outgrow their box. Uses the same scale theme.rescale_fonts
        applied to the fonts, so the text-to-box fit stays constant at any size."""
        self.configure(width=int(round(self.BASE_WIDTH * scale)))

    def set_active(self, name: str) -> None:
        for row_name, row in self._rows.items():
            row.set_active(row_name == name)

    def row(self, name: str) -> SidebarRow:
        return self._rows[name]
