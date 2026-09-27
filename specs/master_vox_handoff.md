# Master Vox — discussion handoff

**Status:** proposal. **F1 and F2 were fixed on 2026-09-07** (ducking — see §3
and §5); F3 and F4 are still open and the Master Vox question in §1 is still
unanswered. This is a *discussion* brief, not a spec.
**Auto-DJ landed on 2026-09-16 and adds a third writer to §2** — read the note
below before designing against that section.
**Written:** 2026-08-20, after an audio-stack audit and the `ac52404` Morpheus fix.
**Revised:** 2026-09-01, after the Hestia redesign moved the surfaces it inventories.
**Audience:** a fresh session with no memory of that audit.

Read this end to end before proposing a design. It exists so the next
conversation starts from evidence instead of re-deriving it — every finding and
every line reference below was re-verified against the running code on the
revision date, and, where marked, against real mpv.

### What changed under this document on 2026-09-01

The audit's four findings are **all still open** — nothing in the redesign
touched the Calliope/Harmonia/Morpheus call path. What moved is the UI this
document proposes mounting into, and two of those moves matter to the proposal
rather than merely renumbering it:

- `panels/narrator_panel.py` is **retired**. Its auto-speak toggle is now
  *Vox Response* in `panels/vox_controls.py`, and Home's Stop is *Vox End* in
  the same module. §4 and §5 are updated accordingly.
- **Home's Refresh silences speech too** — `_on_refresh` calls
  `calliope.stop()`. It was missing from the original inventory, which
  undercounted. The table below now has nine rows, not eight.
- **Part of §5's "what is making noise" lamp already exists.** `HomePanel._tick_face`
  derives speech-vs-idle from `harmonia.is_playing()` every 60 ms to drive the
  wireframe face. The *speech* arm of that state is built and running; the
  *music* arm is the piece still missing. Read that method before designing a
  new state source — do not build a second one.

### What changed under this document on 2026-09-16

Auto-DJ (`awen.py`) shipped. Two things in §2 are now out of date, and one of
them was already stale before this feature.

- **§2's code block is wrong about the coupling.** It shows
  `morpheus.stop()   # before buffering ANY audio` at `harmonia.py:231`. That
  became `morpheus.duck()` when F1/F2 were fixed on 2026-09-07 — the header
  above says so, but the snippet was never updated. It is now
  `harmonia.py:323`, and the comment beside it reads "lower the music — do NOT
  kill it". Anyone quoting §2 as evidence should quote the code instead.
- **There is now a THIRD writer, and it is the first one no user asked for.**
  `awen.py` writes the music path (`morpheus.play(..., from_start=True)`) and
  produces the text that goes down the speech path, on its own Kairos tick.
  Everything §2 inventories starts from a click or a typed question; this does
  not. Update the diagram in your head accordingly:

  ```
  mpv.exe        <-- Morpheus  <-- MorpheusPanel | chat Pythia | Awen
  OutputStream   <-- Harmonia  <-- Calliope <-- HomePanel (answers AND dj lines)
                               <-- Orpheus
  ```

- **The one-direction claim still holds, and Awen adds a second yield going the
  same way.** Harmonia can still silence Morpheus and Morpheus still never
  learns Harmonia exists. Awen reads `harmonia.is_playing()` and declines to
  take a turn while anything is audible — a voluntary yield, not a new coupling,
  and in the same direction as the existing one. Nothing was added to Morpheus.
- **F3's ergonomics gap narrowed twice.** `resume_music` became
  `control_music(action="replay")` on 2026-09-15, and `next_track()` actually
  works now (it sent `playlist-next weak`, which does nothing on a single loaded
  track — fixed 2026-09-16). There is still no resume BUTTON, so F3 stands as
  written.
- F4 is untouched.

### What changed under this document on 2026-09-17

Vox Array grew a fourth tab, **TUNES** (`panels/euterpe_panel.py`) — Euterpe's
song ledger as editable JSON. **§4's inventory is unchanged at nine surfaces on
purpose:** it plays nothing, stops nothing and ducks nothing, so there is no
tenth meaning of "stop" in it. It is in this host because it is the history of
what this host played. Nothing in §5 moves either — the tab is playback
*history*, not playback *selection*, and the "Master Vox must not grow a second
way to start audio" line still stands.

---

## 1. The question on the table

Felhaven's audio grew organically into three subsystems and nine control
surfaces (eight when this was written — the ninth was always there, just
missed). The user's framing, verbatim:

> "either the lock is too solid, or its clashing somehow, or theres simply *too
> many panels trying to play anything*. A 'Master Vox' panel maybe? One set of
> controls?"

The audit answered the first part: **there is no lock and nothing is clashing.**
The architecture is clean. What's actually wrong is narrower and more specific —
three findings below — and a Master Vox panel is a plausible answer to two of
them, but not to all four, and it is *not* the highest-value work available.

Do not treat "build Master Vox" as settled. The user asked a question.

---

## 2. Architecture — who owns what

Two fully independent output paths. This matters: they do not contend for the
device, and no amount of panel-tidying changes that.

```
mpv.exe (separate process, own device handle)  <-- Morpheus      [music]
sounddevice OutputStream (exactly one, owned)  <-- Harmonia  <-- Calliope [speech]
                                                            <-- Orpheus  [briefing playback]
```

- **`harmonia.py`** is the device authority. It owns ONE `sd.OutputStream`,
  opened lazily and kept for the app's life; `stop()` is a buffer flush, not a
  device call. This design is deliberate and correct — it replaced a
  shared-global-stream race that used to segfault PortAudio on barge-in
  (CONVENTIONS §12, 2026-07-26). **Do not propose reverting it.**
- **`calliope.py`** is output-only TTS (kokoro-onnx). Voice *input* was retired;
  there is no Metis/Apollo listener any more.
- **`tools/morpheus.py`** drives mpv over a Windows named pipe, one
  open/write/close per call.
- **`tools/orpheus.py`** decodes a local file with ffmpeg and hands PCM to
  Harmonia.
- **`tools/echo.py`** renders text to an `.opus` file. It never plays anything.

**The one coupling between the two paths** is a single line —
`metis_toolbox/harmonia.py:231`:

```python
morpheus.stop()   # before buffering ANY audio
```

One direction only. Harmonia can silence Morpheus; Morpheus never learns
Harmonia exists. That is a good design. The problems are in its *granularity*
and its *irreversibility*, not its direction.

---

## 3. Findings — the actual defects

All four were confirmed by reading the code. None is speculative.

### F1 — the yield fires per audio chunk, but it is a per-turn concern

> **CLOSED 2026-09-07 — and it was worse than this section knew.** The yield did
> not merely cost latency; it *killed music the user had just asked for*. mpv's
> `loadfile` is fire-and-forget, so `play_music` returned success while yt-dlp
> was still resolving, and the answer's first speech chunk stopped the track
> 2.36 s later. Fixed by ducking rather than by moving the yield: `play()` now
> calls `morpheus.duck()` and the new `harmonia.tick_yield()` restores. The
> per-chunk cadence stayed, and stopped mattering — the duck latches, so a
> ten-chunk answer costs 3 pipe transactions and 0 watch-later writes instead of
> 20 and 10.

`calliope.py:438` (the release worker) calls `harmonia.play()` **once per
synthesized chunk**. Every one of those calls `morpheus.stop()`
(`harmonia.py:231`), which is `_checkpoint()` + `stop`
(`tools/morpheus.py:564`) — **two named-pipe open/write/close cycles plus a
watch-later disk write, per chunk.**

A ten-chunk answer re-stops an already-stopped mpv ten times and issues twenty
pipe transactions **from the thread that feeds the audio buffer**. Each pipe
open can retry 5 × 50 ms. That is a latency and stutter source in the narration
pipeline, and it writes junk resume checkpoints.

The yield belongs at turn start, not per buffer append.

### F2 — `_checkpoint()` on a music-killing stop is actively harmful

> **CLOSED 2026-09-07.** Dissolved by the same change, exactly as §5 predicted:
> narration no longer calls `stop()`, so it no longer checkpoints. A new trap
> replaced it and is guarded — mpv persists *changed options*, and `volume` is
> one, so `--watch-later-options=start` now pins those files to the position
> alone.

`tools/morpheus.py:576` bookmarks the position on every `stop()`, and `play()`
resumes from that bookmark. So narration interrupting a track near its end saves
a near-end position; replaying that URL later resumes at the end and stops
instantly — indistinguishable from "Morpheus won't play."

`morpheus_watch_later/` currently holds 22 entries, several with `start=` over
4000 s. A checkpoint written by a *yield* is not the same event as one written
by a *user stop*, and the code cannot tell them apart.

### F3 — nothing in the UI can bring music back

`resume_music` exists only as an LLM tool (`tools/morpheus.py:919`). If
auto-speak is on and you ask Pythia anything, narration takes the speaker.
There is still no resume button anywhere — though since narration now ducks
rather than stops (2026-09-07), and the filler that used to fire first is gone
entirely, this is an ergonomics gap rather than the lost-music trap it was.

### F4 — Orpheus appends rather than replaces

`tools/orpheus.py:356` hands a whole decoded briefing to `harmonia.play()`,
which *appends*. `panels/orpheus_panel.py:257` never stops first. So:

- clicking two briefings concatenates them;
- clicking one mid-narration queues it behind the rest of the answer, while the
  label immediately claims `playing: <name>`.

Harmonia has no mixer — two sources serialise, they do not blend.

---

## 4. Control-surface inventory

Nine surfaces, **three incompatible meanings of "stop"**, and none of them
stops everything:

| # | Surface | Action | Stop scope |
|---|---|---|---|
| 1 | Hestia box → *Vox Response* (`panels/vox_controls.py`) | toggle auto-speak | — |
| 2 | Home → `▶ speak aloud` (`panels/home_panel.py:336` → `_speak`, `:413`) | `calliope.stop()` + `speak()` | speech only (barge-in) |
| 3 | Hestia box → *Vox End* (`panels/home_panel.py:506`, `_on_stop`) | `calliope.stop()` | speech only |
| 4 | Hestia box → *Reset Session* (`panels/home_panel.py:516`, `_on_refresh`) | `calliope.stop()` + clears the chat | speech only |
| 5 | Vox Array → MORPHEUS transport (`panels/morpheus_panel.py:251-254`) | prev/pause/next/stop | music only |
| 6 | Vox Array → MORPHEUS rows | `morpheus.play(url)` | — |
| 7 | Vox Array → ORPHEUS rows / stop (`panels/orpheus_panel.py:257`, `:194`) | play file / `harmonia.stop()` | speech + briefings, **not** music |
| 8 | Vox Array → ECHO `VoiceBar` | session-only voice | — |
| 9 | Moderati → MACHINE SPIRIT `VoiceBar` | persisted narration voice | — |

Also true, and relevant:

- **Rows 1, 3 and 4 now sit in a single box** (`VoxControls`, top-right of Home).
  That is new as of 2026-09-01 and it cuts both ways: the three speech controls
  finally read as one instrument, which is most of what §5 wanted — but it also
  means the box is where a user will look for a master stop and a resume, and
  neither is there.
- **No volume control anywhere in the stack.** Verified by grep — no gain, no
  ducking, no `volume` property use. Music and narration both run at whatever
  the OS mixer says.
- The two `VoiceBar` instances being independent is **correct and deliberate**
  (Echo renders a file in one voice without changing how Pythia is read aloud).
  Do not "unify" them.

---

## 5. The proposal to discuss

**Master Vox as a status-and-transport strip, not a fourth tab.**

Mount it in Hestia's command box beside the vox controls. The precedent is now
`panels/vox_controls.py` itself, which is *already* the shape this section asked
for — display plus a small number of actions, no state of its own, reading
Calliope's flag rather than caching it. A master stop and a resume-music control
are two more `VoxToggle` rows in that box; the "what is making noise" readout is
the thing that does not fit the row shape and needs its own decision.

Note the box is deliberately **not** a general dumping ground: it inverts §7's
colour convention (amber = active) precisely because it sits beside the face, so
anything mounted there inherits that vocabulary and should mean the same thing
by it.

It should own the three things currently unowned:

1. **One master stop** — `calliope.stop()` + `harmonia.stop()` +
   `morpheus.stop()`. The only control that means "silence, everything, now."
   Answers the three-meanings-of-stop problem.
2. **A resume-music button** — surfaces `morpheus.resume_music()` to the UI.
   Closes **F3**.
3. **A "what is making noise" lamp** — music / speech / briefing / silent. You
   currently cannot tell without guessing, which is most of why this felt like a
   clash.

**Explicitly out of scope for it:** playback *selection*. Morpheus's search and
playlists, Orpheus's file list, and Echo's converter are correctly scoped
one-job panels. Master Vox must not grow a second way to start audio.

**What it does NOT fix:** F1 and F2 are inside the Calliope/Harmonia/Morpheus
call path and need fixing there regardless of what the UI looks like.

### The larger alternative — duck instead of kill

> **BUILT 2026-09-07**, and it turned out to be the *first* step rather than the
> second: the "who is speaking" state this section expected to need from Master
> Vox was already there in `harmonia.is_playing()`, so the restore needed no new
> state source at all. The open sub-questions below were settled as: duck to 30;
> restore 0.75 s after the buffer goes quiet (hysteresis — it empties mid-answer
> between one synthesized chunk and the next); and a track started from the
> panel during a duck begins quiet and comes up with everything else, because
> clearing the latch on `play()` would defeat the yield on the one turn the user
> is most likely listening to.

Rather than `morpheus.stop()`, narration could drop mpv's volume and restore it.
**Verified against the user's mpv build (v0.41.0, 2026-08-20)**: the `volume`
property is readable and settable over the existing IPC pipe —
`100.0 → 30.0 → 100.0`, confirmed. Use `volume` (mpv's own output), **not**
`ao-volume`, which is the system mixer.

This would replace both the blunt `morpheus.stop()` **and** the resume button,
and would dissolve F2 (no stop, so no spurious checkpoint). It needs the
"who is speaking" state Master Vox would already track — so it is naturally the
*second* step, not the first.

Open sub-questions: what duck level; whether to restore on barge-in vs. natural
end; what happens if the user hits play during a duck.

---

## 6. Constraints any design must respect

Read `CONVENTIONS.md` before proposing. The ones that bite here:

- **§0 Anti-Legion — one job per module.** Master Vox displays and acts; it does
  not synthesize, decode, or select.
- **§6 Kairos owns all timing.** No panel spawns threads or self-re-arming
  `after()` chains. A single one-shot `after()` for transient UI feedback is
  permitted by name, and must be cancelled in a `destroy()` override. The
  exception list is short and each entry is justified in code — `PythiaFace`'s
  animation loop was added on 2026-09-01, bounded by visibility. **A
  "who is making noise" readout must not become a new exception**: HomePanel
  already runs a standing 60 ms drain chain and derives face state on it
  (`_tick_face`). Ride that, the way `_tick_warmup` does.
- **§2 error vocabulary.** Bare slug `^[a-z][a-z0-9_]*$`, or `slug: detail`.
  Never raw external text — `tests/test_tool_output_contract.py` enforces this by
  AST, and its allowlist is **empty**, so any new violation fails CI.
- **§7 Panels** map slugs to human text via an `_ERROR_TEXT` dict with a
  raw-slug fallback.
- **Harmonia has no channels, by design.** A "stop only the briefing" control is
  not implementable without giving Harmonia channels — which is a real design
  change with real cost, not a small feature. Decide deliberately.
- Tests are **stdlib `unittest`, no pytest**, run with `-X utf8`.
  `mypy --strict` on `tools/` is **blocking in CI and currently at zero**.

---

## 7. Already done — do not redo

Commit `ac52404` (2026-08-20) fixed a *different* problem found during the same
audit: Morpheus could fail totally and log nothing. A stale `yt-dlp`
(`2026.06.09`) resolved URLs mpv then got HTTP 403 on, so every play was a no-op
while the panel showed "(loading…)" indefinitely.

That is fixed and verified: `yt-dlp` updated to `2026.08.19`, load-failure
detection added (`status()` reports a sticky `load_failed`), mpv now runs with
`--log-file`, and the failure is distilled into `logs/mpv/last_failure.log`
(361 bytes vs 22 KB). Reasoning is in `CONVENTIONS.md` §12.

**None of that touched the coupling.** F1–F4 are all still open.

The 2026-09-01 Hestia redesign likewise touched none of it. It reshaped the
control surfaces (§4), retired `narrator_panel.py`, mounted the wireframe face,
and fixed an unrelated layout bug — `PhosphorScroll` requested a bare Canvas's
265 px of height and silently squeezed Home's chat bar to 1 px on any window
under ~1280x900. No audio code changed.

---

## 8. Suggested order of discussion

1. Decide whether Master Vox is the right shape at all, or whether F1/F2 should
   be fixed first — they are real defects; Master Vox is an ergonomics win.
2. If yes: agree its exact surface (three actions above? more? fewer?).
3. Decide kill-vs-duck. Duck is better and is now known to be *possible*; it is
   also strictly more work and more state.
4. Only then, where it mounts and what it looks like.

**A note on scope discipline for whoever picks this up:** the user is a
self-described beginner building this to learn. Distinguish clearly between "fix
now" and "watch as it grows", give the reasoning behind recommendations, and
resist bundling F1–F4 plus a new panel plus ducking into one change.
