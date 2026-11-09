"""
panels/pythia_face.py — Pythia's wireframe face
===============================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Draw Pythia as a wireframe face, animated for idle, thinking or
             speaking. A pure indicator: it reads no
             state of its own, holds no audio handle, and calls nothing back.
             HestiaBar pushes a state name in; that is the entire input.

Contract:    PythiaFace(parent, width=140, height=120, fps=20, rng=None)
                 set_state(name)   — idle | playing | thinking | speaking
                 set_level(value)  — 0..1, or None to hand control back
                 start() / stop()  — manual control; normally unnecessary
                 advance()         — draw one frame, schedule nothing (the
                                     loop's body; Clio steps it per video frame)
             `rng` is a random.Random for every random draw; pass a seeded
             one and the same calls draw the same face.

Design:      Framed at the neckline: face and neck only, nothing below. There
             are no eyes, no mouth, no blink and no text anywhere in the
             widget, by design. The signal IS the face, so the states are told
             apart by amplitude, speed, jitter and spatial frequency alone.

Colours:     theme.C only (text1, amber, bg, and burnout for the hottest step
             of each glow ramp). Applied as chassis vs signal —
                 chassis (rim, head contours, neck) is ALWAYS C["text1"]: it is
                     the physical form, and does not have moods
                 signal (face waveform, halo, throat cabling) carries the state
                     colour, gliding text1 <-> amber over about a second rather
                     than cutting
             tkinter has no per-item transparency, so every glow here is a
             colour blended toward the canvas background. That only works
             because the background is a known flat colour — _BG must match the
             frame this is packed into (C["bg"]), or the widget renders as a
             darker rectangle with mis-blended glows.

§6:          This is the documented exception for an animation loop — a 24 fps
             redraw cannot ride Kairos's 500 ms tick. Bounded by visibility
             (<Map> starts it, <Unmap> stops it, so a face on a hidden view
             costs nothing) and cancelled in destroy(). See CONVENTIONS §6.

Threading:   set_level() is safe from a worker thread (it only assigns a
             float). set_state() is NOT — it touches tk.

Upstream:    panels/hestia_panel.py (mounts it), panels/home_panel.py (drives
             the state from its existing 60 ms drain chain), panels/clio_face.py
             (steps it off-screen to render Clio's videos)
Downstream:  theme.py (colors)

Requires:    tkinter, math, random (stdlib). No Pillow, no numpy, no images.

Demo:        python -X utf8 -m panels.pythia_face   (run from metis_toolbox/)
             Keys: 1 idle  2 playing  3 thinking  4 speaking  q quit
"""

import math
import random
import tkinter as tk

from theme import C, rgb

# --------------------------------------------------------------------------
# palette — derived from theme.C, never hardcoded (§7). The ramps blend in
# RGB, so every colour in this module enters through theme's rgb().
# --------------------------------------------------------------------------

# The frame this widget is packed into. The fake-alpha ramps blend toward it,
# so it MUST match its parent's background or the face sits in a visible box.
BG = C["bg"]

GREEN = rgb(C["text1"])
AMBER = rgb(C["amber"])
BURNOUT = rgb(C["burnout"])   # what the hottest step of every ramp reaches for

RAMP_STEPS = 24


def _mix(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _hex(c: "tuple[int, int, int]") -> str:
    return "#%02x%02x%02x" % c


def _build_ramp(colour, bg_hex=BG, steps=RAMP_STEPS):
    """Fake alpha: blend from background up to the colour, then to a hot core.

    tkinter has no per-item transparency, so every 'glow' in here is really a
    colour blended toward the canvas background. Works because the background
    is a known flat colour.
    """
    bg = rgb(bg_hex)
    ramp = []
    for i in range(steps):
        t = i / (steps - 1)
        if t <= 0.82:
            ramp.append(_hex(_mix(bg, colour, t / 0.82)))
        else:
            # top of the ramp burns out toward C["burnout"], like an
            # overdriven phosphor
            ramp.append(_hex(_mix(colour, BURNOUT, (t - 0.82) / 0.18 * 0.55)))
    return ramp


# A bank of ramps stepping green -> amber. The state machine interpolates an
# index into this rather than swapping between two ramps, so a state change
# glides in colour the same way it glides in amplitude.
HOT_STEPS = 9
RAMPS = [_build_ramp(_mix(GREEN, AMBER, k / (HOT_STEPS - 1)))
         for k in range(HOT_STEPS)]

GREEN_RAMP = RAMPS[0]     # the chassis ramp: always cold, never state-tinted
AMBER_RAMP = RAMPS[-1]


def _lvl(ramp, x):
    """x in 0..1 -> a colour from the ramp."""
    i = int(x * (len(ramp) - 1))
    return ramp[0 if i < 0 else (len(ramp) - 1 if i >= len(ramp) else i)]


# --------------------------------------------------------------------------
# geometry (pure functions - no tk, so they can be unit tested)
# --------------------------------------------------------------------------

def head_half_width(y, cy, rx, ry):
    """Half-width of the skull silhouette at height y. 0 outside the head."""
    t = (y - cy) / ry
    if abs(t) >= 1.0:
        return 0.0
    w = rx * math.sqrt(1.0 - t * t)
    if t > 0.12:  # taper the jaw so it reads as a face, not an egg
        k = (t - 0.12) / 0.88
        w *= 1.0 - 0.42 * (k ** 1.35)
    if t < -0.55:  # flatten the crown very slightly
        w *= 1.0 - 0.05 * ((-t - 0.55) / 0.45)
    return w


def neck_half_width(y, chin_y, neck_bottom, rx):
    """Neck half-width, chin down to the crop.

    The flare is deliberately gentle. There are no shoulders to widen into any
    more - the frame cuts at the neckline - so the taper only hints at the
    trapezius starting before it runs off the bottom edge.
    """
    span = max(neck_bottom - chin_y, 1.0)
    t = (y - chin_y) / span
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return rx * (0.40 + 0.28 * (t ** 2.4))


# --------------------------------------------------------------------------
# widget
# --------------------------------------------------------------------------

class PythiaFace(tk.Canvas):

    STATES = {
        # There are no eyes and no mouth, so the four states have to be told
        # apart by motion and colour alone. Spatial frequency is what separates
        # them in character rather than just in loudness: thinking interferes
        # with itself, speaking swells in broad syllables.
        #
        # name:        (target level, ripple speed, jitter, hot colour, freq)
        "idle":        (0.10, 0.55, 0.02, "green", 5.0),
        "playing":     (0.30, 0.85, 0.05, "amber", 4.0),
        "thinking":    (0.42, 1.90, 0.22, "green", 9.0),
        "speaking":    (0.85, 1.35, 0.30, "amber", 3.2),
    }

    def __init__(self, master, width=140, height=120, fps=20, rng=None, **kw):
        # Every random draw (flicker, syllables, cable phase) goes through one
        # generator, so a seeded one renders the same face twice (Clio).
        self._rng = rng if rng is not None else random.Random()
        kw.setdefault("bg", BG)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(master, width=width, height=height, **kw)

        self.w = float(width)
        self.h = float(height)
        self.s = min(self.w / 380.0, self.h / 320.0)   # scale factor
        self.delay = max(int(1000 / fps), 16)

        # ---- layout ------------------------------------------------------
        # Framed at the neckline: head high, neck running off the bottom edge.
        # The fractions are chosen so the head lands at the same pixel size it
        # had in the taller bust framing - only the crop has ever changed.
        self.cx = self.w * 0.5
        self.head_cy = self.h * 0.462
        self.ry = self.h * 0.300
        self.rx = self.ry * 0.755
        self.chin_y = self.head_cy + self.ry * 0.99
        self.neck_bottom_y = self.h

        self.face_cx = self.cx
        self.face_cy = self.head_cy + self.ry * 0.04
        self.frx = self.rx * 0.80
        self.fry = self.ry * 0.60

        # ---- animation state ---------------------------------------------
        self.state = "idle"
        self.level = 0.10
        self._target = 0.10
        self._phase = 0.0
        self._syl = 0.0            # syllable envelope for speech
        self._flicker = 1.0
        self._frame = 0
        self._hot = 0.0            # 0 = green chassis mood, 1 = amber; lerped
        self._running = False
        self._job = None
        self._external_level = None
        self._colour_cache = {}    # (item, option) -> last colour actually set

        self._wave = []            # amber/green face scanlines
        self._tendrils = []        # cable lines down the neck
        self._halo = []            # concentric glow rings around the face
        self._rim = []             # skull outline layers (pulse with level)
        self._sweep = None

        self._build_neck()
        self._build_head()
        self._build_face()
        self._build_overlay()

        # The loop follows visibility rather than an explicit start() call.
        # Two reasons: forgetting start() used to leave a fully drawn but
        # frozen face, which looks like success; and a face on a hidden tab
        # must not keep burning frames. start() guards on _running, so a
        # repeated <Map> cannot double-schedule.
        self.bind("<Map>", lambda _e: self.start())
        self.bind("<Unmap>", lambda _e: self.stop())

    # -- public API --------------------------------------------------------

    def set_state(self, name: str) -> None:
        """Switch state. NOT thread-safe - this touches tk; call it on the
        tk thread only. (set_level is the one that is safe from a worker.)
        """
        if name not in self.STATES:
            raise ValueError("unknown state: %r" % name)
        if name == self.state:
            # Idempotent on purpose: HomePanel pushes the state every 60 ms off
            # its drain chain, and the _syl reset below would otherwise pin the
            # syllable envelope at zero and freeze the speaking animation.
            return
        self.state = name
        self._target = self.STATES[name][0]
        self._syl = 0.0            # restart the envelope, don't resume it

    def set_level(self, value: "float | None") -> None:
        """Drive amplitude directly (e.g. from Calliope's output buffer).

        Pass None to hand control back to the state machine.

        Safe to call from a non-tk thread: it only assigns a float and never
        touches the canvas. Note it does not decay - a caller that stops
        feeding levels without passing None leaves the face frozen at the last
        amplitude.
        """
        self._external_level = None if value is None else max(0.0, min(1.0, value))

    # -- internals ---------------------------------------------------------

    def _set_colour(self, item, colour, option="fill"):
        """itemconfigure is a full Tcl round-trip. Skip it when nothing moved.

        _lvl quantises to RAMP_STEPS, so once the level settles most frames ask
        for the colour the item already carries. option exists because the halo
        rings are ovals configured by 'outline', not 'fill'.
        """
        key = (item, option)
        if self._colour_cache.get(key) == colour:
            return
        self._colour_cache[key] = colour
        self.itemconfigure(item, **{option: colour})

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._tick()

    def stop(self) -> None:
        self._running = False
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def destroy(self) -> None:
        self.stop()
        super().destroy()

    # -- static layers -----------------------------------------------------

    def _build_neck(self):
        """Neck contours and side rails, chin to the bottom crop.

        Same treatment as the skull - dim dashed interior, bright edge stubs -
        so the neck reads as the same wireframe surface and not as an empty
        outline. Deliberately a shade dimmer than the head so the face keeps
        the eye, and fading toward the crop so the bottom edge does not read
        as a hard cut. Chassis, so it stays on the cold ramp.
        """
        step = max(3.0 * self.s, 2.0)
        span = max(self.neck_bottom_y - self.chin_y, 1.0)

        y = self.chin_y + step
        row = 0
        while y < self.neck_bottom_y:
            hw = neck_half_width(y, self.chin_y, self.neck_bottom_y, self.rx)
            jog = math.sin(row * 0.7) * 0.6 * self.s
            fade = 1.0 - 0.45 * ((y - self.chin_y) / span)
            if hw > 3.0:
                self.create_line(self.cx - hw + 2, y + jog,
                                 self.cx + hw - 2, y + jog,
                                 fill=_lvl(GREEN_RAMP, 0.13 * fade),
                                 dash=(2, 4), width=1)
                stub = min(hw * 0.34, 10 * self.s)
                for sign in (-1, 1):
                    x0 = self.cx + sign * hw
                    self.create_line(x0, y + jog, x0 - sign * stub, y + jog,
                                     fill=_lvl(GREEN_RAMP, 0.42 * fade), width=1)
            y += step
            row += 1

        # side rails last, so they close over the ends of the contours
        for sign in (-1, 1):
            pts = []
            y = self.chin_y - 6 * self.s
            while y <= self.neck_bottom_y:
                pts += [self.cx + sign * neck_half_width(
                    y, self.chin_y, self.neck_bottom_y, self.rx), y]
                y += 5 * self.s
            self.create_line(*pts, fill=_lvl(GREEN_RAMP, 0.62),
                             width=1, smooth=True)

    def _build_head(self):
        """Horizontal contour scanlines + a layered glow rim."""
        top = self.head_cy - self.ry
        bot = self.head_cy + self.ry
        step = max(3.0 * self.s, 2.0)

        y = top + step
        row = 0
        while y < bot:
            hw = head_half_width(y, self.head_cy, self.rx, self.ry)
            if hw > 2.0:
                jog = math.sin(row * 0.7) * 0.6 * self.s
                # dim interior fill, dashed so it reads as mesh not solid
                self.create_line(self.cx - hw + 3, y + jog, self.cx + hw - 3, y + jog,
                                 fill=_lvl(GREEN_RAMP, 0.16),
                                 dash=(2, 4), width=1)
                # bright edge stubs - the silhouette glows hottest
                stub = min(hw * 0.34, 16 * self.s)
                for sign in (-1, 1):
                    x0 = self.cx + sign * hw
                    self.create_line(x0, y + jog, x0 - sign * stub, y + jog,
                                     fill=_lvl(GREEN_RAMP, 0.55), width=1)
            y += step
            row += 1

        # rim: three offset outlines faking a bloom. Kept as items so the
        # brightness can pulse with the level each frame.
        for grow, bright in ((0.0, 0.95), (0.045, 0.42), (0.10, 0.20)):
            pts = []
            steps = 60
            for i in range(steps + 1):          # right side, crown to chin
                t = -0.999 + 1.998 * i / steps
                yy = self.head_cy + t * self.ry * (1 + grow)
                hw = head_half_width(yy, self.head_cy,
                                     self.rx * (1 + grow), self.ry * (1 + grow))
                pts += [self.cx + hw, yy]
            for i in range(steps + 1):          # back up the left side
                t = 0.999 - 1.998 * i / steps
                yy = self.head_cy + t * self.ry * (1 + grow)
                hw = head_half_width(yy, self.head_cy,
                                     self.rx * (1 + grow), self.ry * (1 + grow))
                pts += [self.cx - hw, yy]
            item = self.create_line(*pts, fill=_lvl(GREEN_RAMP, bright),
                                    width=2 if grow == 0 else 1, smooth=True)
            self._rim.append((item, bright))

    def _build_face(self):
        """The active core: rippling scanlines inside a face-shaped ellipse."""
        # halo rings, dimmest first
        for i, grow in enumerate((1.10, 1.30, 1.55, 1.85)):
            item = self.create_oval(self.face_cx - self.frx * grow,
                                    self.face_cy - self.fry * grow,
                                    self.face_cx + self.frx * grow,
                                    self.face_cy + self.fry * grow,
                                    outline=_lvl(GREEN_RAMP, 0.1), width=1)
            self._halo.append((item, 0.30 / (i + 1)))

        rows = max(int(26 * self.s), 14)
        self._wave_cols = 34
        for i in range(rows):
            t = (i / (rows - 1)) * 2.0 - 1.0
            fy = self.face_cy + t * self.fry * 0.97
            hw = self.frx * math.sqrt(max(0.0, 1.0 - t * t))
            if hw < 2:
                hw = 2
            pts = []
            for j in range(self._wave_cols):
                x = self.face_cx - hw + (2 * hw) * j / (self._wave_cols - 1)
                pts += [x, fy]
            item = self.create_line(*pts, fill=_lvl(GREEN_RAMP, 0.4), width=1)
            self._wave.append({"item": item, "fy": fy, "hw": hw,
                               "t": t, "row": i})

        # cabling down the throat - signal, so it follows the state ramp
        for sign, xoff in ((-1, 0.30), (1, 0.30), (0, 0.02)):
            item = self.create_line(0, 0, 0, 0, fill=_lvl(GREEN_RAMP, 0.3),
                                    width=1, smooth=True)
            self._tendrils.append({"item": item, "sign": sign, "off": xoff,
                                   "seed": self._rng.random() * 6.28})

    def _build_overlay(self):
        """CRT dressing: scanline mask and the rolling sweep. No text."""
        gap = max(int(4 * self.s), 4)
        y = 0
        while y < self.h:
            self.create_line(0, y, self.w, y, fill=BG, width=1)
            y += gap

        # A faint band off the cold ramp — the same phosphor as everything
        # else, barely above the background (§7: no hex literals here).
        self._sweep = self.create_rectangle(0, -40, self.w, -20,
                                            outline="", fill=_lvl(GREEN_RAMP, 0.09))
        self.tag_lower(self._sweep)

    # -- animation ---------------------------------------------------------

    def _tick(self):
        if not self._running:
            return
        self.advance()
        self._job = self.after(self.delay, self._tick)

    def advance(self) -> None:
        """Draw exactly one frame and schedule nothing.

        The <Map> loop calls it on its own clock. Clio (panels/clio_face.py)
        calls it on a video's clock instead, one call per frame, so a rendered
        face moves exactly as the app's does without a timer in the way.
        """
        self._frame += 1
        _, speed, jitter, hot, freq = self.STATES[self.state]

        # Colour glides the way the level does. Lerping an index into RAMPS
        # rather than swapping ramps means green -> amber takes about a second
        # instead of cutting on a single frame.
        self._hot += ((1.0 if hot == "amber" else 0.0) - self._hot) * 0.12
        ramp = RAMPS[int(self._hot * (HOT_STEPS - 1) + 0.5)]

        # ---- level -------------------------------------------------------
        if self._external_level is not None:
            target = self._external_level
        else:
            target = self._target
            if self.state == "speaking":
                # crude syllable envelope so it looks like speech, not a hum
                self._syl += 0.16 + self._rng.random() * 0.18
                target *= 0.42 + 0.58 * abs(math.sin(self._syl))
            elif self.state == "playing":
                target *= 0.75 + 0.25 * math.sin(self._frame * 0.06)
            elif self.state == "thinking":
                target *= 0.55 + 0.45 * abs(math.sin(self._frame * 0.13))
            else:
                target *= 0.85 + 0.15 * math.sin(self._frame * 0.035)
        self.level += (target - self.level) * 0.28
        lv = self.level

        self._phase += 0.13 * speed
        self._flicker = 1.0 - jitter * 0.25 * self._rng.random()

        # ---- face scanlines ----------------------------------------------
        amp = (2.0 + lv * 12.0) * self.s
        cols = self._wave_cols
        for wl in self._wave:
            hw = wl["hw"]
            fy = wl["fy"]
            rowenv = math.sqrt(max(0.0, 1.0 - wl["t"] * wl["t"]))
            a = amp * rowenv
            ph = self._phase + wl["row"] * 0.52
            pts = []
            for j in range(cols):
                u = j / (cols - 1) * 2.0 - 1.0            # -1 .. 1
                x = self.face_cx + u * hw
                env = math.cos(u * math.pi * 0.5)          # pinned at the edges
                y = fy + a * env * math.sin(u * freq + ph)
                pts += [x, y]
            self.coords(wl["item"], *pts)
            b = (0.28 + 0.62 * rowenv) * (0.45 + 0.75 * lv) * self._flicker
            self._set_colour(wl["item"], _lvl(ramp, b))

        # ---- halo, rim, tendrils (cheap, every other frame) --------------
        if self._frame % 2 == 0:
            for item, base in self._halo:
                self._set_colour(item, _lvl(ramp, base * (0.3 + lv)), "outline")
            # The rim is chassis, not signal: the skull is the physical form
            # and does not have moods, so it stays on the cold ramp whatever
            # the state. Only what glows through it changes colour.
            for item, base in self._rim:
                self._set_colour(
                    item, _lvl(GREEN_RAMP, base * (0.72 + 0.28 * lv)))

        for td in self._tendrils:
            pts = []
            y = self.chin_y - 4 * self.s
            while y <= self.neck_bottom_y:
                k = (y - self.chin_y) / max(self.neck_bottom_y - self.chin_y, 1)
                nw = neck_half_width(y, self.chin_y, self.neck_bottom_y, self.rx)
                x = self.cx + td["sign"] * nw * td["off"] * (0.4 + k)
                x += math.sin(y * 0.05 + self._phase * 0.6 + td["seed"]) * 3.0 * self.s
                pts += [x, y]
                y += 6 * self.s
            self.coords(td["item"], *pts)
            self._set_colour(td["item"], _lvl(ramp, 0.18 + 0.55 * lv))

        # ---- rolling sweep -----------------------------------------------
        y0 = ((self._frame * 2.2) % (self.h + 120)) - 60
        self.coords(self._sweep, 0, y0, self.w, y0 + 26 * self.s)


# --------------------------------------------------------------------------
# demo
# --------------------------------------------------------------------------

def _demo() -> None:
    root = tk.Tk()
    root.title("Pythia")
    root.configure(bg=BG)

    # The demo runs at the size the geometry was tuned for; the header mounts
    # it much smaller (every constant is a fraction of w/h, so it scales).
    face = PythiaFace(root, width=380, height=320, fps=24)
    face.pack()

    keys = {"1": "idle", "2": "playing", "3": "thinking", "4": "speaking"}
    root.bind("<Key>", lambda e: (root.destroy() if e.char == "q"
                                  else face.set_state(keys.get(e.char, face.state))))

    root.mainloop()      # no start(): the <Map> binding runs the loop


if __name__ == "__main__":
    _demo()
