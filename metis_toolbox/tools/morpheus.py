"""
morpheus.py — Dream Audio / YouTube Audio Player
=================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Play audio from YouTube — search, play, pause, and skip tracks.
             No video, no thumbnails, no downloads, no transcoding, no play
             history. "I want to listen to this playlist today" — the whole brief.

Contract:    A hybrid flavor, new to the stack — document it here so future-you
             doesn't expect the usual surface:
               - fetch()  : Kairos-polled status read (now-playing, paused,
                            position). READ-ONLY — never mutates playback, and
                            never raises ("morpheus not playing" is a normal
                            state, not a fault — deliberate deviation from the
                            raise-on-failure norm the other fetch()es follow).
               - mutations: play / toggle_pause / next_track / prev_track /
                            stop fire only from deliberate UI action in
                            MorpheusPanel.
             TOOL_DEFINITIONS (plural — see CONVENTIONS §3/§8) exposes two
             tools: play_music — "play this song" searches YouTube's top hit
             and plays it (it mutates audio, not records, so it's a safe LLM
             tool) — and resume_music — bring back whatever play_music (or
             the panel) last started, after it was interrupted. Other
             playback controls (pause/skip) stay panel-driven for now; a
             companion tool can be added later if wanted.

Source:      Two external binaries, zero new pip packages, stdlib-only Python.
               - mpv     : headless audio engine, driven over its JSON IPC
                           named pipe. mpv invokes yt-dlp internally to resolve
                           YouTube URLs.
               - yt-dlp  : keyless YouTube search (ytsearch:).
             Resolution order (anchored to the app root, midas precedent):
               1. <app root>/bin/mpv.exe and <app root>/bin/yt-dlp.exe
                  (flash-drive-portable)
               2. shutil.which("mpv") / shutil.which("yt-dlp") (on PATH)
             If either is missing, available() reports it and the panel shows a
             placeholder — we never crash and never raise at import time.

Playlists:   morpheus_playlists.json at the app root (config-over-code, same
             pattern as pheme_rumormill.json). Adding a playlist is a JSON edit,
             never a code change.

Resume:      mpv saves the playback position on quit (--save-position-on-quit)
             into morpheus_watch_later/. The dir is passed explicitly because
             --no-config disables mpv's default watch-later location; without it
             resume would silently do nothing. Replaying a URL then picks up
             where you left off — long lore videos survive an app restart.
             stop() and play() also force a checkpoint (write-watch-later-config)
             so a mid-session stop or a switch to another video saves your place
             too, not only app close.

Upstream:    kairos.py (calls fetch), panels/morpheus_panel.py (UI mutations),
             felhaven.py (calls shutdown() on close)
Downstream:  mpv.exe (audio engine) + yt-dlp.exe (search / URL resolution)

Requires:    mpv + yt-dlp binaries (bin/ or PATH).
             json, logging, os, shutil, subprocess, time (stdlib).
"""

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any

log = logging.getLogger("METIS.morpheus")

# ── Config / paths ──────────────────────────────────────────────────────────
# App root = one dir up from tools/ (next to felhaven.py). The bin/ dir and the
# playlist config both live here. Anchored to __file__, so cwd never matters.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN_DIR = os.path.join(_APP_ROOT, "bin")
_PLAYLISTS_PATH = os.path.join(_APP_ROOT, "config", "morpheus_playlists.json")

# Where mpv saves playback positions for resume. Explicit because --no-config
# disables mpv's default watch-later location — see _ensure_mpv.
_WATCH_LATER_DIR = os.path.join(_APP_ROOT, "morpheus_watch_later")

# mpv's JSON IPC server. A Windows named pipe — this module is Windows-only by
# design (named pipes, CREATE_NO_WINDOW). Felhaven is a Windows dashboard.
_PIPE = r"\\.\pipe\mpv-felhaven"

# Opening the pipe can transiently fail right after mpv starts (or while it's
# serving another client): Windows ERROR_PIPE_BUSY surfaces as errno 22. Retry
# the open a few times with a short backoff before giving up — bounded so a
# genuinely dead pipe still fails fast.
_OPEN_RETRIES = 5
_OPEN_BACKOFF = 0.05   # seconds between open attempts

# Hide the console window mpv / yt-dlp would otherwise flash. Defined via getattr
# so importing this module on a non-Windows box (e.g. running a unit test) can't
# AttributeError — the value is only meaningful on Windows anyway.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ── Module state ──────────────────────────────────────────────────────────────
_proc: "subprocess.Popen[bytes] | None" = None   # the headless mpv process, or None
_request_id = 0                            # IPC request id counter (see _ipc)
_last_url: "str | None" = None             # last URL play() actually loaded (resume_music)


# ── Binary resolution ─────────────────────────────────────────────────────────

def _resolve(name: str) -> "str | None":
    """bin/<name>.exe wins over PATH so the portable copy beats a stale install."""
    local = os.path.join(_BIN_DIR, f"{name}.exe")
    if os.path.isfile(local):
        return local
    return shutil.which(name)


def available() -> dict[str, Any]:
    """
    Report which binaries we can find. The panel reads this once at build time:
    if either is None it shows a placeholder and disables the controls.
    Returns {"mpv": path_or_None, "ytdlp": path_or_None}.
    """
    return {"mpv": _resolve("mpv"), "ytdlp": _resolve("yt-dlp")}


# ── mpv lifecycle ─────────────────────────────────────────────────────────────

def _ensure_mpv() -> bool:
    """
    Guarantee a live mpv with its IPC pipe ready. Idempotent: returns True fast
    if mpv is already running. Spawns it otherwise and waits for the named pipe
    to appear (it does not exist until mpv finishes starting up).

    Returns False (logging one ERROR) if a binary is missing or the pipe never
    appears — callers (play) simply do nothing in that case.
    """
    global _proc
    if _proc is not None and _proc.poll() is None:
        return True

    bins = available()
    if not bins["mpv"] or not bins["ytdlp"]:
        log.error("cannot start mpv — missing binary (mpv=%s, yt-dlp=%s)",
                  bins["mpv"], bins["ytdlp"])
        return False

    # Ensure the resume store exists before mpv writes to it. mpv won't reliably
    # create --watch-later-dir itself, and --no-config (below) disables its
    # default location, so we own this directory explicitly.
    try:
        os.makedirs(_WATCH_LATER_DIR, exist_ok=True)
    except OSError as e:
        log.warning("could not create watch-later dir: %s", e)

    args = [
        bins["mpv"],
        # Ignore any user mpv.conf / scripts on the host machine, so Morpheus
        # behaves identically on every PC — the portability rule (CONVENTIONS
        # §11), applied to mpv's own config instead of Python's cwd. Without it,
        # someone who uses mpv normally could have settings that change us.
        "--no-config",
        "--no-video",
        # Never create a video output window, even idle or when a stream carries
        # a video track. --no-video alone isn't enough: a host's force-window=yes
        # (or some ytdl format picks) still pops a black window. CREATE_NO_WINDOW
        # only hides the *console*, not this — hence both are needed.
        "--force-window=no",
        "--idle=yes",
        "--no-terminal",
        "--ytdl-format=bestaudio",
        # Resume long videos across restarts. --save-position-on-quit writes the
        # current position when mpv quits (i.e. on app close via shutdown());
        # replaying the same URL then resumes from there. The dir is explicit
        # because --no-config above disables mpv's default watch-later location,
        # so without it resume would silently do nothing.
        "--save-position-on-quit",
        f"--watch-later-dir={_WATCH_LATER_DIR}",
        f"--input-ipc-server={_PIPE}",
        # Pass our resolved yt-dlp explicitly so the bin/ copy wins over any
        # stale yt-dlp already on PATH.
        f"--script-opts=ytdl_hook-ytdl_path={bins['ytdlp']}",
    ]
    try:
        _proc = subprocess.Popen(args, creationflags=_NO_WINDOW)
    except Exception as e:
        log.error("failed to spawn mpv: %s", e)
        _proc = None
        return False

    # The pipe is created asynchronously as mpv starts; poll for it before the
    # first command. 10 × 0.2 s == up to 2 s, which is plenty on a cold start.
    for _ in range(10):
        try:
            with open(_PIPE, "r+b", buffering=0):
                return True
        except OSError:
            time.sleep(0.2)

    log.error("mpv started but its IPC pipe never appeared")
    return False


def shutdown() -> None:
    """
    Best-effort clean stop. Safe to call when mpv was never started.
    Asks mpv to quit over IPC, then terminates the process if it lingers.
    Hard requirement: no orphan mpv.exe after the app closes.
    """
    global _proc
    if _proc is None:
        return
    if _proc.poll() is None:
        _ipc(["quit"])                       # polite ask
        try:
            _proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _proc.terminate()                # it didn't listen — make it
            try:
                _proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _proc.kill()                  # still there — belt-and-suspenders
                try:
                    _proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    log.warning("mpv did not exit after terminate()+kill()")
    _proc = None


# ── IPC client (stdlib only) ──────────────────────────────────────────────────

def _ipc(command: list[Any], expect_reply: bool = False, timeout: float = 2.0) -> dict[str, Any] | None:
    """
    Send one command to mpv over the IPC pipe. Opens / writes / (reads) / closes
    each call — simple and boring, and at the 2 s status cadence it is cheap and
    avoids stale-handle bookkeeping.

    If expect_reply, read lines until a JSON object whose request_id matches
    ours, skipping mpv's async event objects (multiplexed onto the same pipe).
    The timeout bounds the wait BETWEEN received lines — it is only checked
    after each readline() returns, so it cannot interrupt a single readline()
    that is already blocking. In practice mpv replies fast or streams events,
    so a truly hung read is rare; this is an accepted stdlib tradeoff (fixing
    it would need overlapped I/O, out of scope for this module).

    NEVER raises to callers — a dead pipe is a normal state (mpv idle or gone),
    not an emergency. Returns the reply dict (expect_reply) or None.
    """
    global _request_id
    _request_id += 1
    rid = _request_id
    payload = (json.dumps({"command": command, "request_id": rid}) + "\n").encode("utf-8")

    # Acquire the pipe, retrying the transient "busy" window (errno 22) right
    # after mpv starts. FileNotFoundError is different — the pipe doesn't exist
    # at all (mpv isn't running), so fail fast without retrying.
    pipe = None
    last_err = None
    for _ in range(_OPEN_RETRIES):
        try:
            pipe = open(_PIPE, "r+b", buffering=0)
            break
        except FileNotFoundError:
            return None
        except OSError as e:
            last_err = e
            time.sleep(_OPEN_BACKOFF)
    if pipe is None:
        log.warning("IPC %s: pipe unavailable: %s",
                    command[0] if command else "?", last_err)
        return None

    try:
        with pipe:
            pipe.write(payload)
            if not expect_reply:
                return None

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                line = pipe.readline()
                if not line:
                    break                    # pipe closed / EOF
                try:
                    msg: dict[str, Any] = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "event" in msg:
                    continue                 # async event, not our reply
                if msg.get("request_id") == rid:
                    return msg
            return None
    except (OSError, ValueError) as e:
        # FileNotFoundError (pipe gone) is the common, expected case. ValueError
        # guards an odd decode/IO edge. WARNING, not ERROR — this is routine.
        log.warning("IPC %s failed: %s", command[0] if command else "?", e)
        return None


def _get_prop(name: str) -> Any:
    """Read one mpv property. Returns its value, or None if unavailable."""
    reply = _ipc(["get_property", name], expect_reply=True)
    if reply is None:
        return None
    return reply.get("data")


def _get_props(names: list[str], timeout: float = 2.0) -> dict[str, Any]:
    """
    Read several mpv properties over ONE pipe connection — one open/write/read/
    close cycle instead of one per property, so a Kairos tick pays a single
    reply timeout worst-case instead of len(names) of them.

    Returns {name: value_or_None}. Missing/failed reads are None — never
    raises (same contract as _ipc). On any pipe failure returns
    {name: None for name in names}.

    Like _ipc(), the timeout is a single shared deadline checked BETWEEN
    received lines — it does not interrupt a single readline() that is
    already blocking (see _ipc()'s docstring).
    """
    global _request_id
    results = {name: None for name in names}

    # Same open-with-retry logic as _ipc(): retry the transient "busy" window,
    # fail fast if the pipe doesn't exist at all (mpv isn't running).
    pipe = None
    last_err = None
    for _ in range(_OPEN_RETRIES):
        try:
            pipe = open(_PIPE, "r+b", buffering=0)
            break
        except FileNotFoundError:
            return results
        except OSError as e:
            last_err = e
            time.sleep(_OPEN_BACKOFF)
    if pipe is None:
        log.warning("IPC get_props: pipe unavailable: %s", last_err)
        return results

    try:
        with pipe:
            rid_to_name = {}
            for name in names:
                _request_id += 1
                rid = _request_id
                rid_to_name[rid] = name
                payload = (json.dumps({"command": ["get_property", name],
                                       "request_id": rid}) + "\n").encode("utf-8")
                pipe.write(payload)

            pending = set(rid_to_name)
            deadline = time.monotonic() + timeout
            while pending and time.monotonic() < deadline:
                line = pipe.readline()
                if not line:
                    break                    # pipe closed / EOF
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "event" in msg:
                    continue                 # async event, not a reply
                rid = msg.get("request_id")
                if rid in pending:
                    results[rid_to_name[rid]] = msg.get("data")
                    pending.discard(rid)
            return results
    except (OSError, ValueError) as e:
        # Same rationale as _ipc(): FileNotFoundError (pipe gone) is the
        # common, expected case. WARNING, not ERROR — this is routine.
        log.warning("IPC get_props failed: %s", e)
        return {name: None for name in names}


# ── Public API — mutations (UI-driven only) ───────────────────────────────────

def _checkpoint() -> None:
    """Force mpv to write the current playback position to its watch-later file
    *without* quitting (write-watch-later-config), so resume survives a
    mid-session stop or a switch to another video — not only app close. No-op
    when nothing is loaded; never raises."""
    _ipc(["write-watch-later-config"])


def play(url: str) -> None:
    """Load (and start) a URL, replacing whatever was playing. Starts mpv if
    needed. mpv natively queues a full playlist URL; next/prev then walk it.
    Bookmarks the outgoing track first so switching videos keeps its place.
    Remembers `url` as the resume target (resume_music) once actually loaded —
    not on an early return, so a failed mpv start never overwrites a good one."""
    global _last_url
    if not _ensure_mpv():
        return
    _checkpoint()                      # bookmark the outgoing track, if any
    _ipc(["loadfile", url, "replace"])
    _last_url = url


def toggle_pause() -> None:
    _ipc(["cycle", "pause"])


def next_track() -> None:
    _ipc(["playlist-next", "weak"])


def prev_track() -> None:
    _ipc(["playlist-prev", "weak"])


def stop() -> None:
    """Stop playback. mpv stays idle and alive, ready for the next play().
    Bookmarks the current position first, so resume works after a mid-session
    stop and not only on app close."""
    _checkpoint()
    _ipc(["stop"])


# ── Public API — keyless search (BLOCKING — caller threads it) ────────────────

def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Keyless YouTube search via yt-dlp's ytsearch. BLOCKS for seconds —
    MorpheusPanel calls this on a daemon thread, never the main thread.

    Returns up to `limit` rows: {"title", "channel", "duration", "url"}.
    On any failure returns [{"error": "search failed"}] so the panel can render
    a single error row.
    """
    ytdlp = _resolve("yt-dlp")
    if not ytdlp:
        log.warning("search: yt-dlp not found")
        return [{"error": "search failed"}]

    args = [ytdlp, f"ytsearch{limit}:{query}", "--flat-playlist", "-J", "--no-warnings"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=20,
                              creationflags=_NO_WINDOW)
        if proc.returncode != 0:
            log.warning("search: yt-dlp exit %s: %s",
                        proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
            return [{"error": "search failed"}]
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log.warning("search failed: %s", e)
        return [{"error": "search failed"}]

    rows = []
    for entry in data.get("entries") or []:
        vid = entry.get("id")
        rows.append({
            "title":    entry.get("title") or "(untitled)",
            "channel":  entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration"),      # seconds, may be None
            "url":      entry.get("url") or (f"https://www.youtube.com/watch?v={vid}"
                                         if vid else ""),
        })
    return rows


# ── Public API — status / Kairos entry point ──────────────────────────────────

def status() -> dict[str, Any]:
    """
    Current playback snapshot. {"running": False} when mpv isn't up — and we
    return that WITHOUT touching the pipe, so an idle dashboard generates no IPC
    churn and no Emanon noise (idle is the normal state).
    """
    if _proc is None or _proc.poll() is not None:
        return {"running": False}

    props = _get_props(["media-title", "pause", "time-pos", "duration",
                        "playlist-pos", "playlist-count"])

    # Liveness check: if EVERY property came back None, the pipe is dead or
    # unresponsive even though the process is alive (rare) — fall back to
    # idle. A None title alone (stream still resolving, everything else live)
    # is not a liveness failure — the panel renders that as "(loading…)".
    if all(v is None for v in props.values()):
        return {"running": False}

    return {
        "running": True,
        "title":  props["media-title"],         # str | None
        "paused": props["pause"],                # bool | None
        "pos":    props["time-pos"],             # float seconds | None
        "dur":    props["duration"],             # float seconds | None
        "pl_pos": props["playlist-pos"],         # int | None
        "pl_n":   props["playlist-count"],       # int | None
    }


def fetch() -> dict[str, Any]:
    """Kairos entry point — just status(). Never raises (see module docstring)."""
    return status()


# ── Playlist config ───────────────────────────────────────────────────────────

def _save_playlists(data: dict[str, Any]) -> None:
    """Write the full playlists dict. Raises on failure so the caller can
    catch it. Temp-then-replace so a crash mid-write can't truncate it."""
    tmp = _PLAYLISTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _PLAYLISTS_PATH)


def load_playlists() -> list[dict[str, Any]]:
    """
    Ordered playlist rows from morpheus_playlists.json (UI order follows file
    order). Returns [] on any failure (panel shows a hint row), logs ERROR.
    Called from MorpheusPanel._reload_playlists().
    """
    try:
        with open(_PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            playlists: list[dict[str, Any]] = json.load(f).get("playlists", [])
            return playlists
    except FileNotFoundError:
        return []
    except Exception as e:
        log.error("failed to load playlists: %s", e)
        return []


def save_playlist(label: str, url: str) -> bool:
    """
    Append one entry to morpheus_playlists.json.
    Creates the file with an empty playlists list if it does not exist.
    Returns True on success, False on any write failure (logs ERROR).
    Called from MorpheusPanel._on_save_playlist() and _save_from_search().
    """
    try:
        try:
            with open(_PLAYLISTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {"playlists": []}

        data.setdefault("playlists", []).append({
            "label": label.strip(),
            "url":   url.strip(),
        })

        _save_playlists(data)

        log.info("playlist saved: %r", label)
        return True
    except Exception as e:
        log.error("save_playlist failed: %s", e)
        return False


def remove_playlist(index: int) -> bool:
    """
    Remove the entry at `index` (0-based, in load_playlists() / file order)
    from morpheus_playlists.json. Returns True on success, False on any failure
    or out-of-range index (logs ERROR) — the file is left untouched on failure.
    Removal is by position, not by content, so duplicate labels delete exactly
    the row the caller targeted. Called from MorpheusPanel._on_remove_playlist().
    """
    try:
        with open(_PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        playlists = data.get("playlists", [])
        if not 0 <= index < len(playlists):
            log.error("remove_playlist: index %d out of range (have %d)",
                      index, len(playlists))
            return False

        removed = playlists.pop(index)

        _save_playlists(data)

        log.info("playlist removed: %r", removed.get("label"))
        return True
    except FileNotFoundError:
        log.error("remove_playlist: %s not found", _PLAYLISTS_PATH)
        return False
    except Exception as e:
        log.error("remove_playlist failed: %s", e)
        return False


# ── LLM tool contract ─────────────────────────────────────────────────────────
# TOOL_DEFINITIONS (plural — the Callimachus precedent, CONVENTIONS §3/§8):
# two tightly-coupled calls on one job (play music / bring it back), each with
# a like-named function replacing the old singular handle(). play_music
# searches the top YouTube hit and plays it. resume_music replays the last URL
# play() actually loaded — mpv's own watch-later file (written by every
# stop()/play() via _checkpoint()) resumes the position, so this is just
# "load the same URL again," nothing more. Other playback controls
# (pause/skip) stay panel-driven for now; a companion tool can be added later
# if wanted.

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": (
                "Search YouTube for a song, artist, or any audio and play the top "
                "result through the local audio engine (mpv). Call whenever the user "
                "asks to play, put on, or listen to music or a specific track."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to play — a song title, artist, or search terms.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_music",
            "description": (
                "Resume the most recently played song or audio from where it left "
                "off, after it was interrupted (e.g. by a spoken answer). Call when "
                "the user asks to resume, continue, or pick the music back up — not "
                "for starting a new song (use play_music for that)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def play_music(query: str = "") -> dict[str, Any]:
    """Toolbox entry: search + play the top hit. BLOCKS for a few seconds on the
    yt-dlp search, so the caller must thread it — Pythia already runs tool calls
    on a worker thread. Never raises: a missing binary, empty query, or no
    result degrades to an error dict the model can relay."""
    query = (query or "").strip()
    if not query:
        return {"error": "no_query", "detail": "No song was specified."}

    bins = available()
    if not bins["ytdlp"]:
        return {"error": "player_unavailable", "detail": "yt-dlp binary not found."}
    if not bins["mpv"]:
        return {"error": "player_unavailable", "detail": "mpv binary not found."}

    results = search(query, limit=1)
    if not results or "error" in results[0] or not results[0].get("url"):
        return {"error": "no_results", "detail": f"Nothing found for {query!r}."}

    top = results[0]
    play(top["url"])
    return {
        "now_playing": top["title"],
        "channel":     top.get("channel", ""),
        "url":         top["url"],
    }


def resume_music() -> dict[str, Any]:
    """Toolbox entry: replay the last URL play() actually loaded. Never
    raises: nothing played yet, or a missing mpv binary, degrades to an
    error dict the model can relay."""
    if _last_url is None:
        return {"error": "nothing_to_resume", "detail": "Nothing has been played yet."}

    bins = available()
    if not bins["mpv"]:
        return {"error": "player_unavailable", "detail": "mpv binary not found."}

    play(_last_url)
    return {"resumed_url": _last_url}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[morpheus] available():", available())
    print("[morpheus] searching 'lofi' ...")
    for r in search("lofi", limit=5):
        if "error" in r:
            print(f"  ! {r['error']}")
        else:
            mins = "" if r["duration"] is None else f"{int(r['duration'])//60}:{int(r['duration'])%60:02d}"
            print(f"  • {r['title']}  [{r['channel']} {mins}]")
