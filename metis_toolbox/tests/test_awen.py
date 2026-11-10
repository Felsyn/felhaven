"""
test_awen.py — the Auto-DJ.

Awen is the first module in this stack that ACTS without being asked, so most of
what matters here is when it must NOT act. A chat tool that misfires wastes a
round trip; this one plays music over a conversation, or spins all night on a
broken player.

What earns a test:

  * EVERY NEVER-FIRE STATE. A pause, a deliberate stop, a failed load, a load
    still resolving, the flag off, narration audible, a chat turn generating.
    Each is a separate way to be wrong out loud.
  * THE END OF A SONG IS INTENT, NOT A PROPERTY. Measured 2026-09-16: a natural
    end, a skip and a failed load leave byte-identical mpv properties, and only
    a deliberate stop differs. These tests use those measured shapes, so a
    future "simplify the trigger" that reads playlist-count fails here.
  * THE GUARDS RUN IN CODE. The model proposed an already-played song six times
    across ten measured turns while looking at the do-not-repeat list. The
    prompt is a hint; the guard is the rule.
  * A TURN THAT PLAYED NOTHING SAYS NOTHING. Measured: when gemma4:e2b drops out
    of tool-calling mode it TYPES 'play_music{query:...}' as content. Speaking
    that would announce a song that is not playing.

Hermetic: pythia.ask, morpheus and harmonia are all patched, and Euterpe's files
are redirected into a temp dir. No Ollama, no mpv, no network, no audio, no Tk.

Run from the package root:
    python -X utf8 -m unittest tests.test_awen
"""

import json
import os
import sys
import tempfile
import time
import unicodedata
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import awen
import pythia
from tools import euterpe

# ── Measured mpv snapshot shapes, as morpheus.status() reports them ──────────
# These are the shapes verify/measurement runs produced against mpv v0.41 on
# 2026-09-16, not invented ones. Note that ENDED and FAILED differ ONLY by the
# error slug, and STOPPED only by pl_n — that is the whole reason the trigger
# tracks intent instead of reading properties.
# The URL the DEFAULT playing snapshot is loaded from — i.e. the LISTENER's
# song, because a snapshot with nothing pending is what a listener's play looks
# like. Its video id is deliberately NOT _FRESH's, and that is load-bearing
# rather than tidy:
#
#   record() stores a video_id since 2026-09-20, and was_recently_played()
#   checks the id branch FIRST. If these two shared an id, every test that
#   ticks _PLAYING to set the scene would put _FRESH's id in the ledger — and
#   TestAnnouncing._turn(), which does exactly that and then searches and gets
#   _FRESH back, would have its own pick refused as already-played. The whole
#   announcing suite would collapse, and nothing in the failure would name the
#   cause. Do not "tidy" these two back together.
_THEIRS = "https://www.youtube.com/watch?v=Lllllllllll"

_PLAYING = {"running": True, "title": "Artist - Song", "paused": False,
            "pos": 12.0, "dur": 240.0, "pl_pos": 0, "pl_n": 1,
            "volume": 100.0, "idle": False, "loading": False,
            "path": _THEIRS}
# path IS set while yt-dlp is still resolving — it means "mpv accepted this
# URL", not "the load worked" (morpheus, "Load failures").
_LOADING = {"running": True, "title": "watch?v=abc", "paused": False,
            "pos": None, "dur": None, "pl_pos": 0, "pl_n": 1,
            "volume": 100.0, "idle": False, "loading": True,
            "path": _THEIRS}
_ENDED = {"running": True, "title": None, "paused": False,
          "pos": None, "dur": None, "pl_pos": -1, "pl_n": 1,
          "volume": 100.0, "idle": True, "loading": False,
          "path": None}
_STOPPED = {**_ENDED, "pl_n": 0}
_FAILED = {**_ENDED, "error": "load_failed"}
_PAUSED = {**_PLAYING, "paused": True}
_IDLE = {"running": False}

# A search result the guards will accept, for tests that need a pick to LAND.
_FRESH = {"title": "Newcomer - Fresh Track", "channel": "Newcomer",
          "duration": 240, "url": "https://youtu.be/aaaaaaaaaaa"}


def _ours(title, url, **over):
    """A PLAYING snapshot for a song mpv says it loaded FROM `url`.

    What "this is the DJ's pick" now means, and why it is a helper rather than
    a dict literal: a test that wants a song to be the DJ's has to SAY so, by
    handing mpv a path that matches the pick. Before 2026-09-20 it was enough
    to repeat one title string on both sides of the comparison, which is
    exactly why the divergence that broke this in production was untestable.
    """
    return {**_PLAYING, "title": title, "path": url, **over}


class _AwenCase(unittest.TestCase):
    """Reset every module global and redirect Euterpe's files. One process runs
    the whole suite, so a leaked _enabled would have a later file's tests
    silently taking DJ turns."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved_paths = (euterpe._LEDGER_PATH, euterpe._CONFIG_PATH)
        euterpe._LEDGER_PATH = os.path.join(self._tmp.name, "ledger.json")
        euterpe._CONFIG_PATH = os.path.join(self._tmp.name, "config.json")

        # _session_plays is in here for the same reason as the rest, and it is
        # the easiest one to forget: it is the DJ's no-repeat memory, so a
        # leaked entry makes a LATER test's pick get refused as "already played"
        # by a song a different test played.
        # _queue is in here for the same reason as _session_plays, and it is
        # the same trap: a leaked entry makes a LATER test's pick get refused as
        # "already queued", or plays a song that test never chose.
        self._saved = (awen._enabled, awen._playing_title, awen._want_pick,
                       awen._pending_pick, awen._failures, awen._picking,
                       list(awen._session_plays), awen._last_played_key,
                       list(awen._queue), awen._last_fill_at,
                       awen._tapered_title, awen._queue_revision,
                       awen._next_entry_id, list(awen._declined))
        awen._enabled = False
        awen._playing_title = None
        awen._want_pick = False
        awen._pending_pick = None
        awen._failures = 0
        awen._picking = False
        awen._session_plays = []
        awen._last_played_key = None
        awen._queue = []
        # The cooldown starts RUNNING, so a top-up does not fire inside tests
        # written to pin the pick path — nearly all of which tick a PLAYING
        # snapshot to set the scene. This is the real mechanism rather than a
        # test-only flag, and the top-up tests opt back in by clearing it.
        awen._last_fill_at = time.monotonic()
        awen._tapered_title = None
        awen._queue_revision = 0
        awen._next_entry_id = 0
        awen._declined = []
        # The taper reaches a real mpv through morpheus. Stubbed for the same
        # reason describe() is: a test must not touch the player.
        self.taper = self._patch("awen.morpheus.taper")
        self.release_taper = self._patch("awen.morpheus.release_taper")
        self.addCleanup(self._restore)

        # Quiet by default: nothing audible, no chat turn in flight.
        for target, value in (("awen.harmonia.is_playing", False),
                              ("awen.pythia.is_busy", False)):
            self._patch(target, return_value=value)

        # No metadata by default, which is BOTH a safety net and the honest
        # default. Safety: describe() shells out to yt-dlp, and an unmocked
        # suite would hit YouTube once per candidate. Honest: "no evidence
        # either way" is the common case for a great deal of real music, so
        # every test that does not say otherwise is exercising the fallback —
        # the path that must keep behaving exactly as it did before the deep
        # check existed.
        self.describe = self._patch("awen.morpheus.describe",
                                    return_value={"error": "describe_failed"})

    def _patch(self, target, **kwargs):
        """Start a patch and stop THAT patch on cleanup.

        Not mock.patch.stopall: cleanups run LIFO, so a subclass registering
        stopall would tear down this base class's patches before its own
        patcher.stop() cleanups ran, and stopping an already-stopped patcher
        raises."""
        patcher = mock.patch(target, **kwargs)
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def _restore(self):
        (awen._enabled, awen._playing_title, awen._want_pick,
         awen._pending_pick, awen._failures, awen._picking,
         awen._session_plays, awen._last_played_key,
         awen._queue, awen._last_fill_at,
         awen._tapered_title, awen._queue_revision,
         awen._next_entry_id, awen._declined) = self._saved
        euterpe._LEDGER_PATH, euterpe._CONFIG_PATH = self._saved_paths

    # ── helpers ─────────────────────────────────────────────────────────────
    def _tick(self, snapshot):
        """One fetch() against a given mpv snapshot."""
        with mock.patch("awen.morpheus.last_snapshot", return_value=dict(snapshot)):
            return awen.fetch()

    def _seed(self, *titles, source="user"):
        for title in titles:
            euterpe.record(title, duration=240, source=source)

    def _play_then_end(self, title="Artist - Song"):
        """Get Awen into the state where that song has played and just ended."""
        self._tick({**_PLAYING, "title": title})
        return self._tick(_ENDED)


class TestTheFlag(_AwenCase):
    def test_off_at_launch(self):
        """Not persisted, by design: a job that starts playing music by itself
        because of something you did last week is not a feature."""
        self.assertFalse(awen.enabled())

    def test_toggle_flips_it(self):
        awen.toggle()
        self.assertTrue(awen.enabled())
        awen.toggle()
        self.assertFalse(awen.enabled())

    def test_switching_on_with_nothing_playing_arms_a_pick(self):
        self._seed("Artist - Song")
        awen.set_enabled(True)
        self.assertTrue(awen._want_pick)

    def test_switching_on_with_an_empty_ledger_says_so_and_arms_nothing(self):
        line = awen.set_enabled(True)
        self.assertEqual(line, awen._NO_HISTORY_LINE)
        self.assertFalse(awen._want_pick)

    def test_switching_on_mid_song_waits_for_it_to_finish(self):
        self._seed("Artist - Song")
        self._tick(_PLAYING)
        awen.set_enabled(True)
        self.assertFalse(awen._want_pick)

    def test_switching_off_disarms_the_pending_pick(self):
        self._seed("Artist - Song")
        awen.set_enabled(True)
        self.assertTrue(awen._want_pick)
        awen.set_enabled(False)
        self.assertFalse(awen._want_pick)

    def test_switching_off_does_not_stop_anything(self):
        """"The song finishes; no more picks; nothing stops." Asserted against
        morpheus itself so a future convenience call is caught."""
        with mock.patch("awen.morpheus.stop") as stop, \
                mock.patch("awen.morpheus.play") as play:
            awen.set_enabled(True)
            awen.set_enabled(False)
        stop.assert_not_called()
        play.assert_not_called()

    def test_switching_on_clears_the_breaker(self):
        awen._failures = 2
        self._seed("Artist - Song")
        awen.set_enabled(True)
        self.assertEqual(awen._failures, 0)


class TestObservation(_AwenCase):
    """Whose song it is decides where it goes.

    The listener's plays reach the ledger on disk, whether or not Auto-DJ is on
    — otherwise the first time it is switched on there are no priors at all. The
    DJ's own picks reach _session_plays and NOTHING else: the ledger is the list
    the listener keeps, and a job that writes to the list it reads from is the
    feedback loop that already happened once (see the anchor test below).
    """

    def test_a_play_is_recorded_with_the_flag_off(self):
        self.assertFalse(awen.enabled())
        self._tick(_PLAYING)
        self.assertEqual(len(euterpe.entries()), 1)
        self.assertEqual(euterpe.entries()[0]["source"], "user")

    def test_the_same_song_is_recorded_once_not_every_tick(self):
        for _ in range(5):
            self._tick(_PLAYING)
        self.assertEqual(len(euterpe.entries()), 1)

    def test_a_new_song_is_recorded_separately(self):
        self._tick(_PLAYING)
        self._tick({**_PLAYING, "title": "Other - Track"})
        self.assertEqual(len(euterpe.entries()), 2)

    def test_a_loading_track_is_not_recorded(self):
        """No media yet — recording the URL fragment as a song would put
        'watch?v=abc' in the taste anchor."""
        self._tick(_LOADING)
        self.assertEqual(euterpe.entries(), [])

    def test_an_idle_player_records_nothing(self):
        self._tick(_IDLE)
        self._tick(_ENDED)
        self.assertEqual(euterpe.entries(), [])

    def test_a_dj_pick_NEVER_reaches_the_ledger_file(self):
        """The listener's list stays the listener's. This is the whole change
        of 2026-09-17: the DJ reads that file and does not add to it."""
        awen._pending_pick = {"key": euterpe.normalize("Artist - Song"),
                              "title": "Artist - Song", "channel": "",
                              "url": "https://youtu.be/aaaaaaaaaaa"}
        self._tick(_ours("Artist - Song", "https://youtu.be/aaaaaaaaaaa"))
        self.assertEqual(euterpe.entries(), [])

    def test_a_dj_pick_goes_to_session_memory_with_its_id(self):
        """It has to land SOMEWHERE, or the guard goes blind. The video id is
        carried so was_recently_played() can match the exact upload, not only
        the normalized title."""
        awen._pending_pick = {"key": euterpe.normalize("Artist - Song"),
                              "title": "Artist - Song", "channel": "",
                              "url": "https://youtu.be/aaaaaaaaaaa"}
        self._tick(_ours("Artist - Song", "https://youtu.be/aaaaaaaaaaa"))
        self.assertEqual(len(awen._session_plays), 1)
        row = awen._session_plays[0]
        self.assertEqual(row["key"], "artist - song")
        self.assertEqual(row["video_id"], "aaaaaaaaaaa")
        self.assertEqual(row["source"], "dj",
                         "a session row must not read as the listener's taste")

    def test_session_memory_is_never_written_to_disk(self):
        """In this process only (CONVENTIONS §12). Nothing beside the ledger
        either — a second state file is exactly what was not wanted."""
        awen._pending_pick = {"key": euterpe.normalize("Artist - Song"),
                              "title": "Artist - Song", "channel": "", "url": ""}
        self._tick(_PLAYING)
        self.assertTrue(awen._session_plays)
        written = [n for n in os.listdir(self._tmp.name) if n != "config.json"]
        self.assertEqual(written, [], f"a DJ pick wrote {written} to disk")

    def test_a_pick_that_did_not_land_does_not_mislabel_the_next_song(self):
        """A DJ pick can fail to load and Matthew can then play something
        himself. That song is his, and must feed the anchor.

        The verdict is now PROVEN rather than defaulted, and that is the whole
        change of 2026-09-20. It used to hold because "user" was what the code
        fell back to when it could not tell — so this passed for the same
        reason the production bug happened. Now mpv says it loaded _THEIRS and
        our pick names a different video: two ids, both present, different, so
        the song is his as a matter of fact."""
        awen._pending_pick = {"key": "dj - choice", "title": "DJ - Choice",
                              "channel": "",
                              "url": "https://youtu.be/aaaaaaaaaaa"}
        self._tick(_ours("Matthew - Own Pick", _THEIRS))
        self.assertEqual(euterpe.entries()[0]["source"], "user")

    def test_a_dj_pick_is_recorded_ONCE_across_many_ticks(self):
        """The 2026-09-16 ledger bug, and the shape that hid it.

        Identity used to be the NORMALIZED key, which depends on the channel —
        and the channel is only known on the tick that consumes _pending_pick.
        The tick after, channel was "", the key came out different, the song
        looked new, and it was recorded a SECOND time as a `user` pick.

        It only bit when normalize() applied its channel prefix, i.e. when the
        title has no "artist - track" dash of its own. Every test here used
        "Artist - Song", which has one — so the whole suite was green while a
        real ledger collected eight duplicates.
        """
        # A dashless title that IS a song — "Moss Echo User Manual" was the
        # real one, and it is now refused as not-music, which would make this
        # test pass for the wrong reason.
        awen._pending_pick = {
            "key": euterpe.normalize("Bohemian Rhapsody", "Queen Official"),
            "title": "Bohemian Rhapsody", "channel": "Queen Official",
            "url": "https://youtu.be/fQbT44awL3s"}
        snapshot = _ours("Bohemian Rhapsody", "https://youtu.be/fQbT44awL3s")
        for _ in range(6):
            self._tick(snapshot)
        # The scar moved with the picks: a DJ pick now lands in session memory
        # rather than the ledger, so ONCE is asserted there. The duplicate would
        # still be a duplicate, and the second one would still be labelled as
        # the listener's — it would just be in a different list.
        self.assertEqual([r["key"] for r in awen._session_plays],
                         [euterpe.normalize("Bohemian Rhapsody", "Queen Official")])
        self.assertEqual(euterpe.entries(), [],
                         "the second sighting leaked into the listener's list")

    def test_a_dj_pick_NEVER_reaches_the_taste_anchor(self):
        """The consequence that actually mattered. A DJ choice recorded as
        `user` becomes "what Matthew likes", so the next pick matches it — one
        bad choice then feeds the next. A real ledger had 'moss echo user
        manual' and 'an in-depth review of moss air' sitting in the anchor as
        the listener's own taste."""
        self._seed("Real - Listener Pick")
        awen._pending_pick = {
            "key": euterpe.normalize("Bohemian Rhapsody", "Queen Official"),
            "title": "Bohemian Rhapsody", "channel": "Queen Official", "url": ""}
        for _ in range(4):
            self._tick({**_PLAYING, "title": "Bohemian Rhapsody"})
        self.assertEqual(euterpe.taste_anchor(), ["real - listener pick"])

    def test_a_title_with_no_artist_dash_still_records_once_when_NOT_ours(self):
        """The same shape from the other side: a play Matthew started himself,
        whose title has no dash. One row, source user."""
        for _ in range(5):
            self._tick({**_PLAYING, "title": "Bohemian Rhapsody"})
        rows = euterpe.entries()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "user")

    def test_the_pending_pick_is_consumed_not_sticky(self):
        """One pick labels one song. The second goes to the listener's list
        because nothing is pending any more — if the pick were sticky, every
        song after a DJ turn would be filed as the DJ's and vanish."""
        awen._pending_pick = {"key": euterpe.normalize("Artist - Song"),
                              "title": "Artist - Song", "channel": "", "url": ""}
        self._tick(_PLAYING)
        self._tick({**_PLAYING, "title": "Second - Song"})
        self.assertEqual([r["key"] for r in awen._session_plays],
                         ["artist - song"])
        self.assertEqual([r["key"] for r in euterpe.entries()],
                         ["second - song"])

    def test_a_pick_mpv_SPELLS_DIFFERENTLY_is_still_ours(self):
        """The two sides of the identity test come from DIFFERENT SOURCES, and
        every fixture in this class used to build both from ONE string — so the
        divergence this guards was structurally untestable, the same blind-spot
        shape as the 2026-09-16 dashed-title scar.

        Measured 2026-09-20 on the live ledger: mpv's media-title arrived in
        NFD ("u" plus a combining diaeresis) where yt-dlp's search result was
        NFC ("ü" as one codepoint). One upload, one video id, and same_song()
        said no, because casefold() folds case and not Unicode form. The DJ's
        own pick was then filed as the listener's taste.

        Two things now stop that, and this test would pass on either alone —
        which is deliberate. The id comparison makes spelling irrelevant, and
        normalize()'s NFC fold makes THIS spelling match anyway. Belt and
        braces, because the id is absent for local files and playlists and the
        fold cannot help when two producers disagree about more than form.
        """
        nfc = "Altın Gün - Goca Dünya"
        nfd = unicodedata.normalize("NFD", "Altın Gün - Goca Dünya (Live on KEXP)")
        self.assertNotEqual(nfc, nfd[:len(nfc)])          # genuinely different bytes
        awen._pending_pick = {
            "key":   euterpe.normalize(nfc, "Altın Gün"),
            "title": nfc, "channel": "Altın Gün",
            "url":   "https://youtu.be/s3a4OQR-10M",
            "intro": "Here is Altın Gün."}
        payload = self._tick(_ours(nfd,
                                   "https://www.youtube.com/watch?v=s3a4OQR-10M"))
        self.assertEqual(euterpe.entries(), [],
                         "the DJ's own pick reached the listener's ledger")
        self.assertEqual([r["source"] for r in awen._session_plays], ["dj"])
        self.assertEqual(payload["say"], "Here is Altın Gün.")

    def test_the_video_id_settles_it_when_the_TITLE_cannot(self):
        """The id half on its own, with a divergence no folding can rescue:
        yt-dlp returned the topic-channel spelling and mpv the uploader's.
        Pinned separately so nobody deletes the id comparison on the grounds
        that normalize() already handles the Unicode case."""
        awen._pending_pick = {
            "key":   euterpe.normalize("Some Band - The Song"),
            "title": "Some Band - The Song", "channel": "Some Band",
            "url":   "https://youtu.be/bbbbbbbbbbb",
            "intro": "Here is Some Band."}
        self.assertFalse(euterpe.same_song(
            euterpe.normalize("Some Band - The Song"),
            euterpe.normalize("THE SONG (live, 1998) — Some Band & Friends")),
            "fixture is no longer a divergence — pick a harder one")
        self._tick(_ours("THE SONG (live, 1998) — Some Band & Friends",
                         "https://www.youtube.com/watch?v=bbbbbbbbbbb"))
        self.assertEqual(euterpe.entries(), [])
        self.assertEqual([r["source"] for r in awen._session_plays], ["dj"])

    def test_a_song_we_cannot_prove_is_not_filed_as_taste(self):
        """No id on either side (a local file, a playlist entry, an mpv too old
        to answer `path`) and a title that does not match the pick.

        The old code resolved that doubt towards "user", which is the direction
        that poisons the anchor: a wrong `dj` costs one missed taste signal for
        one run, a wrong `user` is a permanent row in the list the DJ reads to
        decide what to play next. It resolves to `dj` now, and the held line is
        NOT spoken — we cannot name a song we cannot identify."""
        awen._pending_pick = {"key": "dj - choice", "title": "DJ - Choice",
                              "channel": "", "url": "",
                              "intro": "Now playing: DJ - Choice."}
        payload = self._tick(_ours("Something - Else", None))
        self.assertEqual(euterpe.entries(), [],
                         "an unprovable song reached the listener's ledger")
        self.assertEqual([r["source"] for r in awen._session_plays], ["dj"])
        self.assertNotIn("say", payload)

    def test_an_unprovable_attribution_is_logged_above_DEBUG(self):
        """Invisible is how this ran in production: every Awen line is DEBUG,
        the app logs at INFO, and the record() branch had no line at all — so
        the log could not answer "was that row mine?". The routine lines stay
        at DEBUG (Emanon's 40-line tail); THIS one does not, because it is the
        module failing to establish a fact it needs."""
        awen._pending_pick = {"key": "dj - choice", "title": "DJ - Choice",
                              "channel": "", "url": ""}
        with self.assertLogs("METIS.awen", level="WARNING") as caught:
            self._tick(_ours("Something - Else", None))
        self.assertTrue(any("cannot prove" in line for line in caught.output),
                        f"no unprovable-attribution warning in {caught.output}")

    def test_a_listener_play_records_its_video_id(self):
        """Every row on the live ledger had "video_id": null, because
        _note_playing passed url=None — so the id branch of
        was_recently_played() was dead against the file it was written for."""
        self._tick(_PLAYING)
        self.assertEqual(euterpe.entries()[0]["video_id"], "Lllllllllll")

    def test_a_local_file_still_records_as_the_listeners(self):
        """No id on either side and nothing pending. The listener's ordinary
        play is what this whole change must not break."""
        self._tick({**_PLAYING, "path": r"D:\music\track.mp3"})
        self.assertEqual(euterpe.entries()[0]["source"], "user")


class TestNeverFires(_AwenCase):
    """Each of these is a separate way to be wrong out loud."""

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.ask = self._patch("awen.pythia.ask")

    def test_not_while_a_song_is_playing(self):
        self._tick(_PLAYING)
        self.ask.assert_not_called()

    def test_not_while_paused(self):
        self._tick(_PAUSED)
        self.ask.assert_not_called()

    def test_not_while_a_load_is_still_resolving(self):
        self._tick(_PLAYING)
        self._tick(_LOADING)
        self.ask.assert_not_called()

    def test_not_on_a_deliberate_stop(self):
        """The one idle state a property separates (pl_n 0). Silence is what was
        asked for, so Auto-DJ stays ON and waits for the next song."""
        self._tick(_PLAYING)
        self._tick(_STOPPED)
        self.ask.assert_not_called()
        self.assertTrue(awen.enabled())
        self.assertFalse(awen._want_pick)

    def test_not_on_a_load_failure(self):
        """Firing a fresh pick into a broken player is how a stale yt-dlp spins
        all night — it feeds the breaker instead."""
        self._tick(_PLAYING)
        self._tick(_FAILED)
        self.ask.assert_not_called()
        self.assertEqual(awen._failures, 1)

    def test_a_VANISHED_player_is_not_a_song_ending(self):
        """{"running": False} means no mpv at all, which is not the same as a
        track finishing — and arming a pick here would be actively harmful.
        play() STARTS mpv, and felhaven._on_close calls morpheus.shutdown()
        BEFORE kairos.stop(), so a worker thread already inside fetch() at close
        would spawn a fresh mpv.exe with nothing left to shut it down. That is
        exactly the orphan process that shutdown() call exists to prevent.

        It is unknowable anyway: mpv being gone could be the app closing, a
        crash, or play() recycling it because its log grew too large."""
        self._tick(_PLAYING)
        self.assertIsNotNone(awen._playing_title)
        self._tick(_IDLE)
        self.ask.assert_not_called()
        self.assertFalse(awen._want_pick)

    def test_a_vanished_player_still_forgets_the_song(self):
        """Cleared, not remembered — otherwise mpv coming back and playing
        something else would look like the OLD song ending."""
        self._tick(_PLAYING)
        self._tick(_IDLE)
        self.assertIsNone(awen._playing_title)

    def test_a_vanished_player_never_reaches_morpheus(self):
        """The assertion that actually pins the orphan bug: nothing in this path
        may call play(), because play() is what spawns mpv."""
        with mock.patch("awen.morpheus.play") as play:
            self._tick(_PLAYING)
            self._tick(_IDLE)
            self._tick(_IDLE)
        play.assert_not_called()

    def test_not_at_launch_with_an_idle_player(self):
        self._tick(_IDLE)
        self.ask.assert_not_called()

    def test_not_while_the_flag_is_off(self):
        awen._enabled = False
        self._play_then_end()
        self.ask.assert_not_called()

    def test_an_armed_pick_still_does_not_fire_with_the_flag_off(self):
        """Defence in depth, and deliberately NOT reachable today — which is why
        it needs saying. _observe only arms a pick while the flag is on, so
        breaking fetch()'s own check changes no behaviour and a red-check on it
        stays green. This asserts the composite property against a DIRECTLY
        armed pick, so a future caller that arms one from somewhere else — a
        "play something now" button, say — cannot bypass the flag without
        failing here."""
        awen._enabled = False
        awen._want_pick = True
        self._tick(_IDLE)
        self.ask.assert_not_called()

    def test_not_while_narration_is_audible(self):
        """Conversation wins. The pick stays owed and fires once it is quiet —
        the DJ never barges in, unlike AmmitAlarm, which is right for a timer
        and wrong for a song."""
        with mock.patch("awen.harmonia.is_playing", return_value=True):
            self._play_then_end()
            self.ask.assert_not_called()
        self.assertTrue(awen._want_pick)

    def test_not_while_a_chat_turn_is_generating(self):
        with mock.patch("awen.pythia.is_busy", return_value=True):
            self._play_then_end()
            self.ask.assert_not_called()
        self.assertTrue(awen._want_pick)

    def test_the_owed_pick_fires_once_it_goes_quiet(self):
        with mock.patch("awen.harmonia.is_playing", return_value=True):
            self._play_then_end()
        self.ask.return_value = ""
        self._tick(_ENDED)
        self.ask.assert_called_once()

    def test_the_dj_s_own_loading_pick_does_not_re_fire_it(self):
        """The pick is playing; that is not a song ending."""
        self.ask.return_value = ""
        self._play_then_end()
        self.ask.reset_mock()
        self._tick(_LOADING)
        self._tick(_PLAYING)
        self.ask.assert_not_called()


class TestFires(_AwenCase):
    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.ask = self._patch("awen.pythia.ask")
        self.ask.return_value = ""

    def test_a_natural_end_yields_exactly_one_turn(self):
        self._play_then_end()
        self.ask.assert_called_once()

    def test_a_skip_fires_too(self):
        """Measured identical to a natural end, and it means the same thing
        here: that song is over, play another. No DJ-specific seam in Morpheus."""
        self._tick(_PLAYING)
        self._tick(_ENDED)          # what `playlist-next force` leaves
        self.ask.assert_called_once()

    def test_the_turn_is_not_repeated_on_the_next_tick(self):
        """One ended song, one turn — for a turn that PLAYED something. A turn
        that played nothing never discharged the debt and does try again; that
        is TestABadTurnIsNotTheEndOfTheNight, and it is why this test has to
        land a song rather than let ask() return an empty string."""
        self.ask.side_effect = lambda message, **kw: (
            kw["tool_overrides"]["play_music"](_FRESH["title"]),
            "Here is Newcomer with Fresh Track.")[1]
        with mock.patch("awen.morpheus.search", return_value=[_FRESH]), \
                mock.patch("awen.morpheus.play"):
            self._play_then_end()
            self._tick(_ENDED)
            self._tick(_ENDED)
        self.ask.assert_called_once()

    def test_the_turn_carries_the_dj_prompt_and_only_play_music(self):
        self._play_then_end()
        kwargs = self.ask.call_args.kwargs
        self.assertEqual(kwargs["system"], awen.SYSTEM_PROMPT)
        self.assertEqual(kwargs["allow_tools"], ["play_music"])
        self.assertIn("play_music", kwargs["tool_overrides"])

    def test_the_turn_never_thinks(self):
        """A pick runs in the gap between songs. The owner's thinking setting is
        for her answers; here it would stretch the silence by 5-20 s."""
        self._play_then_end()
        self.assertIs(self.ask.call_args.kwargs["think"], False)

    def test_the_turn_carries_no_chat_history(self):
        """Stateless by design — see the module's *Stateless* section."""
        self._play_then_end()
        self.assertIsNone(self.ask.call_args.kwargs.get("history"))
        self.assertEqual(len(self.ask.call_args.args), 1)

    def test_the_turn_message_carries_the_ledger_views(self):
        self._seed("Anchor - Song")
        self._seed("Robot - Song", source="dj")
        self._play_then_end()
        message = self.ask.call_args.args[0]
        self.assertIn("anchor - song", message)
        self.assertNotIn("Robot - Song", message)   # raw titles never go in
        self.assertIn("Do not choose any of these", message)

    def test_the_do_not_choose_list_carries_the_DJs_own_picks_too(self):
        """The list is what the model reads. If the guard refuses a song the
        message never mentioned, the model burns a whole turn on a refusal it
        was not warned about."""
        awen._session_plays = [{"title": "Robot - Choice", "key": "robot - choice",
                                "video_id": None, "source": "dj"}]
        self._play_then_end()
        message = self.ask.call_args.args[0]
        window = message.split("Do not choose any of these:")[1]
        self.assertIn("robot - choice", window)

    def test_just_finished_names_the_song_that_actually_ended(self):
        """It reads _last_played_key, not the newest ledger row. The ledger no
        longer holds the DJ's picks, so the newest row there can be a song from
        days ago — handed to a model told to match its energy."""
        self._seed("Old - Listener Pick")
        awen._pending_pick = {"key": euterpe.normalize("Robot - Choice"),
                              "title": "Robot - Choice", "channel": "", "url": ""}
        self._tick({**_PLAYING, "title": "Robot - Choice"})
        self._tick(_ENDED)
        finished = self.ask.call_args.args[0].splitlines()[0]
        self.assertIn("robot - choice", finished)
        self.assertNotIn("old - listener pick", finished)

    def test_just_finished_follows_a_LISTENER_song_played_after_a_DJ_one(self):
        """The ordering case, and the reason "just finished" cannot be the tail
        of the combined list. The listener's song goes to the ledger and the
        DJ's to session memory, and _known_plays() concatenates the second after
        the first — so the newest row of that list is the DJ's pick even when
        the listener has played something since."""
        awen._pending_pick = {"key": euterpe.normalize("Robot - Choice"),
                              "title": "Robot - Choice", "channel": "", "url": ""}
        self._tick({**_PLAYING, "title": "Robot - Choice"})
        self._tick({**_PLAYING, "title": "Matthew - Own Pick"})
        self._tick(_ENDED)
        finished = self.ask.call_args.args[0].splitlines()[0]
        self.assertIn("matthew - own pick", finished)
        self.assertNotIn("robot - choice", finished)

    def test_a_session_pick_never_reaches_the_taste_anchor(self):
        """The 2026-09-16 failure, in its new home. A session row is written
        with source "dj" precisely so the anchor's existing rule skips it when
        the combined list is handed over; label it "user" and the DJ's own
        choices become "what Matthew likes" all over again, just from memory
        instead of from a file.

        Driven through _note_playing rather than by presetting _session_plays —
        the label is written by that function, so a test that sets the row
        itself would pass no matter what the code writes.
        """
        self._seed("Anchor - Song")
        awen._pending_pick = {"key": euterpe.normalize("Robot - Choice"),
                              "title": "Robot - Choice", "channel": "", "url": ""}
        self._tick({**_PLAYING, "title": "Robot - Choice"})
        self._tick(_ENDED)
        anchor = self.ask.call_args.args[0].splitlines()[1]
        self.assertIn("anchor - song", anchor)
        self.assertNotIn("robot - choice", anchor)

    def test_the_turn_message_uses_normalized_titles(self):
        euterpe.record("Artist - Song (Official Music Video) [4K]", duration=240)
        self._play_then_end()
        message = self.ask.call_args.args[0]
        self.assertIn("artist - song", message)
        self.assertNotIn("Official Music Video", message)


class TestGuards(_AwenCase):
    """The DJ's own play_music: search, CHECK, then play."""

    def setUp(self):
        super().setUp()
        self.play = self._patch("awen.morpheus.play")

    def _search(self, **row):
        base = {"title": "Artist - Song", "channel": "Artist", "duration": 240,
                "url": "https://youtu.be/aaaaaaaaaaa"}
        return mock.patch("awen.morpheus.search", return_value=[{**base, **row}])

    def test_a_good_pick_is_QUEUED_not_played(self):
        """Since the queue, the handler banks a song and the POP plays it. That
        split is the whole feature: banking can happen while a song is still
        going, which is what removes the gap."""
        with self._search():
            result = awen._guarded_play_music("Artist - Song")
        self.assertEqual(result["now_playing"], "Artist - Song")
        self.assertEqual([e["title"] for e in awen._queue], ["Artist - Song"])
        self.play.assert_not_called()

    def test_every_queued_entry_carries_a_speakable_line(self):
        """Built at enqueue, so an entry that reaches the speakers always has
        something true to say even if the model's own line never arrives."""
        with self._search():
            awen._guarded_play_music("Artist - Song")
        self.assertEqual(awen._queue[0]["intro"], "Now playing: Artist - Song.")

    def test_the_queue_refuses_to_grow_without_end(self):
        """A turn that keeps calling the tool must not bank the whole evening."""
        for n in range(awen._QUEUE_MAX):
            awen._queue.append({"key": f"k{n}", "title": f"T{n}", "channel": "",
                                "url": f"https://youtu.be/{n:011d}",
                                "duration": 240, "intro": "x"})
        with self._search():
            result = awen._guarded_play_music("Artist - Song")
        self.assertEqual(result["error"], "queue_full")
        self.assertEqual(len(awen._queue), awen._QUEUE_MAX)

    def test_a_pick_starts_from_zero_not_a_watch_later_position(self):
        """Measured: a track skipped halfway and re-picked later reopens
        halfway. A DJ pick must not open mid-song. The assertion lives on the
        POP now, because that is where play() moved to."""
        with self._search():
            awen._guarded_play_music("Artist - Song")
        awen._play_from_queue()
        self.assertIs(self.play.call_args.kwargs["from_start"], True)

    def test_a_song_the_DJ_ITSELF_played_is_refused_on_the_next_turn(self):
        """The one that did not exist before 2026-09-17, and the one this whole
        change rests on.

        Until then the DJ's picks went into the ledger, so this worked by
        accident of where they were stored — and every no-repeat test seeded the
        ledger with source="user", so the suite would have stayed green while
        the DJ looped one song forever. The prompt does not cover for it either:
        measured over ten real turns, the model proposed a song already on the
        do-not-repeat list it had just been handed six times.

        Played through the real path, not by presetting state: pick, hear it,
        then offer it again.
        """
        with self._search():
            awen._guarded_play_music("Artist - Song")
        awen._play_from_queue()          # the pop is part of the real path now
        self._tick(_ours("Artist - Song", "https://youtu.be/aaaaaaaaaaa"))
        self._tick(_ENDED)
        self.assertEqual(euterpe.entries(), [], "it reached the listener's list")

        self.play.reset_mock()
        with self._search():
            result = awen._guarded_play_music("Artist - Song")
        self.assertIn("already played recently", result["error"])
        self.play.assert_not_called()

    def test_a_different_upload_of_the_DJs_own_pick_is_also_refused(self):
        """Session memory carries the normalized key, so YouTube shipping the
        same track under a second upload does not get a free replay."""
        with self._search():
            awen._guarded_play_music("Artist - Song")
        self._tick({**_PLAYING, "title": "Artist - Song"})

        with self._search(title="Artist - Song (Official Video) [4K]",
                          url="https://youtu.be/bbbbbbbbbbb"):
            result = awen._guarded_play_music("Artist - Song")
        self.assertIn("already played recently", result["error"])

    def test_session_memory_survives_the_flag_going_off_and_on(self):
        """Off-then-on is not a request to start repeating. The pending pick is
        disarmed by set_enabled; the memory of what played is not."""
        with self._search():
            awen._guarded_play_music("Artist - Song")
        self._tick({**_PLAYING, "title": "Artist - Song"})
        awen.set_enabled(False)
        self._seed("Anchor - Song")     # so switching on has a taste to match
        awen.set_enabled(True)

        with self._search():
            result = awen._guarded_play_music("Artist - Song")
        self.assertIn("already played recently", result["error"])

    def test_the_same_song_uploaded_the_other_way_round_is_refused(self):
        """The actual repeat, from a real mpv log on 2026-09-17. The DJ played
        'Linkin Park - In The End (Official Music Video)' and then
        'In The End [Official HD Music Video] - Linkin Park' — one song, two
        keys, and equality on the keys said they were different."""
        with self._search(title="Linkin Park - In The End (Official Music Video)",
                          channel="Linkin Park"):
            awen._guarded_play_music("Linkin Park In The End")
        self._tick({**_PLAYING,
                    "title": "Linkin Park - In The End (Official Music Video)"})

        with self._search(title="In The End [Official HD Music Video] - Linkin Park",
                          channel="Linkin Park",
                          url="https://youtu.be/bbbbbbbbbbb"):
            result = awen._guarded_play_music("Linkin Park In The End")
        self.assertIn("already played recently", result["error"])

    def test_a_song_only_the_LISTENER_played_is_still_refused(self):
        """The ledger half of the same guard. Both lists, one answer."""
        self._tick({**_PLAYING, "title": "Artist - Song"})
        with self._search():
            result = awen._guarded_play_music("Artist - Song")
        self.assertIn("already played recently", result["error"])

    def test_an_already_played_song_is_refused_and_plays_nothing(self):
        euterpe.record("Artist - Song", channel="Artist", duration=240,
                       url="https://youtu.be/aaaaaaaaaaa")
        with self._search():
            result = awen._guarded_play_music("Artist - Song")
        self.assertIn("already played recently", result["error"])
        self.play.assert_not_called()

    def test_a_different_upload_of_a_played_song_is_also_refused(self):
        """The id will not match; the normalized title must."""
        euterpe.record("Artist - Song (Official Audio)", channel="Artist",
                       duration=240, url="https://youtu.be/bbbbbbbbbbb")
        with self._search(title="Artist - Song (Lyrics)",
                          url="https://youtu.be/ccccccccccc"):
            result = awen._guarded_play_music("Artist - Song")
        self.assertIn("already played recently", result["error"])
        self.play.assert_not_called()

    def test_an_over_long_result_is_refused(self):
        cap = euterpe.settings()["max_song_seconds"]
        with self._search(duration=cap + 1):
            result = awen._guarded_play_music("Artist - Greatest Hits")
        self.assertIn("too long to be a song", result["error"])
        self.play.assert_not_called()

    def test_a_live_stream_is_refused(self):
        """Measured: yt-dlp reports duration None for a live stream, and a
        stream never ends — so the trigger would never fire again."""
        with self._search(duration=None):
            result = awen._guarded_play_music("lofi hip hop radio")
        self.assertIn("a live stream", result["error"])
        self.play.assert_not_called()

    def test_it_takes_the_first_RESULT_that_passes_not_just_the_top_hit(self):
        """Looking further down the same result set turns a rejection into a
        selection. The top hit for an invented band name is routinely a gear
        manual; the track two rows down is a song."""
        rows = [{"title": "Moss Echo User Manual", "channel": "Mosslab",
                 "duration": 282, "url": "https://youtu.be/aaaaaaaaaaa"},
                {"title": "Long Flutter Echo in Big Hall", "channel": "x",
                 "duration": 26, "url": "https://youtu.be/bbbbbbbbbbb"},
                {"title": "Radiohead - Creep", "channel": "Radiohead",
                 "duration": 237, "url": "https://youtu.be/ccccccccccc"}]
        with mock.patch("awen.morpheus.search", return_value=rows):
            result = awen._guarded_play_music("moss echo")
        self.assertEqual(result["now_playing"], "Radiohead - Creep")
        self.assertEqual([e["title"] for e in awen._queue], ["Radiohead - Creep"])

    def test_it_searches_deeper_than_one_result(self):
        with mock.patch("awen.morpheus.search", return_value=[]) as search:
            awen._guarded_play_music("anything")
        self.assertEqual(search.call_args.kwargs["limit"], awen._SEARCH_DEPTH)

    def test_it_skips_a_result_already_played(self):
        euterpe.record("Radiohead - Creep", channel="Radiohead", duration=237,
                       url="https://youtu.be/ccccccccccc")
        rows = [{"title": "Radiohead - Creep", "channel": "Radiohead",
                 "duration": 237, "url": "https://youtu.be/ccccccccccc"},
                {"title": "Pink Floyd - Money", "channel": "Pink Floyd",
                 "duration": 284, "url": "https://youtu.be/ddddddddddd"}]
        with mock.patch("awen.morpheus.search", return_value=rows):
            result = awen._guarded_play_music("radiohead")
        self.assertEqual(result["now_playing"], "Pink Floyd - Money")

    def test_every_refusal_carries_one_verifiable_slug(self):
        """§2 wants the slug BEFORE any interpolation so it stays greppable, and
        tests/test_tool_output_contract.py parses the AST — it cannot resolve a
        slug read out of a variable. So the guards share one literal slug and
        put WHICH guard refused into the human half."""
        rows = [{"title": "Moss Echo User Manual", "channel": "Mosslab",
                 "duration": 282, "url": "https://youtu.be/aaaaaaaaaaa"}]
        with mock.patch("awen.morpheus.search", return_value=rows):
            result = awen._guarded_play_music("moss echo")
        slug = result["error"].split(":", 1)[0]
        self.assertRegex(slug, r"^[a-z][a-z0-9_]*$")
        self.assertEqual(slug, "no_usable_result")

    def test_when_NOTHING_passes_it_says_which_reason(self):
        rows = [{"title": "Moss Echo User Manual", "channel": "Mosslab",
                 "duration": 282, "url": "https://youtu.be/aaaaaaaaaaa"}]
        with mock.patch("awen.morpheus.search", return_value=rows):
            result = awen._guarded_play_music("moss echo")
        self.assertIn("not music", result["error"])
        self.assertIn("song title", result["error"])
        self.play.assert_not_called()

    def test_a_too_short_result_is_refused(self):
        with self._search(title="Long Flutter Echo in Big Hall", duration=26):
            result = awen._guarded_play_music("echo")
        self.assertIn("too short to be a song", result["error"])
        self.play.assert_not_called()

    def test_the_guard_and_the_ledger_agree(self):
        """They must not be able to disagree — one judgment, one place. If the
        DJ may play it, the ledger must be willing to remember it."""
        for title, duration in (("Radiohead - Creep", 237),
                                ("Moss Echo User Manual", 282),
                                ("Long Flutter Echo in Big Hall", 26),
                                ("lofi radio", None)):
            with self.subTest(title=title[:30]):
                with self._search(title=title, duration=duration):
                    result = awen._guarded_play_music(title)
                playable = "now_playing" in result
                rememberable = euterpe.is_a_song(title, duration) is None
                self.assertEqual(playable, rememberable)

    def test_no_results_is_refused(self):
        with mock.patch("awen.morpheus.search", return_value=[]):
            result = awen._guarded_play_music("asdkjhasd")
        self.assertEqual(result["error"], "no_results")
        self.play.assert_not_called()

    def test_a_failed_search_is_refused(self):
        with mock.patch("awen.morpheus.search",
                        return_value=[{"error": "search_failed"}]):
            result = awen._guarded_play_music("anything")
        self.assertEqual(result["error"], "no_results")
        self.play.assert_not_called()

    def test_an_empty_query_is_refused(self):
        result = awen._guarded_play_music("")
        self.assertEqual(result["error"], "no_query")
        self.play.assert_not_called()

    def test_every_refusal_tells_the_model_to_name_an_artist_AND_a_title(self):
        """Measured: after any guard error the model degrades to a bare artist
        name, whose top YouTube hit is a greatest-hits compilation that then
        trips too_long — five times across ten turns. The detail is aimed at
        exactly that, so it is asserted rather than left to drift."""
        cases = []
        with self._search(duration=99999):
            cases.append(awen._guarded_play_music("Pink Floyd"))
        with self._search(duration=None):
            cases.append(awen._guarded_play_music("Pink Floyd"))
        with mock.patch("awen.morpheus.search", return_value=[]):
            cases.append(awen._guarded_play_music("Pink Floyd"))
        for result in cases:
            with self.subTest(error=result["error"][:40]):
                said = result.get("detail", "") + result["error"]
                self.assertIn("song title", said)

    def test_every_refusal_is_a_slug(self):
        for result in (awen._guarded_play_music(""),):
            self.assertRegex(result["error"], r"^[a-z][a-z0-9_]*$")

    def test_chat_s_play_music_is_untouched(self):
        """The guards are the DJ's preconditions and belong to the DJ. Morpheus's
        own handler must not have learned any of them.

        available() is mocked because play_music() gates on the BINARIES before
        it reaches anything this test is about, and `bin/` is gitignored — it
        holds mpv.exe and yt-dlp.exe, which exist on a developer's machine and
        never on a fresh clone. Without this the test asserts "the real handler
        works", which is true here and false in CI, and it was: red on every run
        of this branch while passing locally every time.
        """
        from tools import morpheus
        euterpe.record("Artist - Song", channel="Artist", duration=240)
        with mock.patch("tools.morpheus.available",
                        return_value={"mpv": "mpv.exe", "ytdlp": "yt-dlp.exe"}), \
                mock.patch("tools.morpheus.search",
                           return_value=[{"title": "Artist - Song",
                                          "channel": "Artist", "duration": 240,
                                          "url": "https://youtu.be/aaaaaaaaaaa"}]), \
                mock.patch("tools.morpheus.play") as play:
            result = morpheus.play_music("Artist - Song")
        self.assertIn("now_playing", result)
        play.assert_called_once()


class TestTheIntro(_AwenCase):
    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True

    # The song the DJ picks must NOT be the one that just ended: observing
    # that play puts it in the ledger, so picking it again is correctly
    # refused by the recently_played guard. (It was, the first time these
    # were written — the guard was right and the test was wrong.)
    PICK = "Newcomer - Fresh Track"

    def _turn(self, answer, played=True):
        """Run one turn where the model answers `answer` and (optionally) the
        tool override lands a song. Returns the TURN's payload, which since
        2026-09-18 carries no line — see _said()."""
        def fake_ask(message, **kwargs):
            if played:
                kwargs["tool_overrides"]["play_music"](self.PICK)
            return answer

        with mock.patch("awen.morpheus.search",
                        return_value=[{"title": self.PICK,
                                       "channel": "Newcomer", "duration": 240,
                                       "url": "https://youtu.be/aaaaaaaaaaa"}]), \
                mock.patch("awen.morpheus.play"), \
                mock.patch("awen.pythia.ask", side_effect=fake_ask):
            return self._play_then_end()

    def _said(self, answer):
        """One turn, then the tick on which that song becomes audible — which
        is when the held line is released (TestTheIntroWaitsForTheSong). These
        tests are about WHAT is said; that it waits is tested there."""
        self._turn(answer)
        # _ours, not _PLAYING: mpv has to report loading the URL the pick named,
        # or the song is the listener's and no held line is released.
        return self._tick(_ours(self.PICK, _FRESH["url"]))

    def test_a_good_intro_is_handed_back_to_be_spoken(self):
        payload = self._said("Here's Newcomer with Fresh Track.")
        self.assertEqual(payload["say"], "Here's Newcomer with Fresh Track.")
        self.assertEqual(payload["kind"], "intro")
        self.assertEqual(payload["now_playing"], self.PICK)

    def test_a_song_with_no_intro_gets_a_line_built_in_code(self):
        """Measured 1 turn in 10: guard re-picks spend the round budget and the
        song plays with nothing said. The intro is the feature, and a line built
        from the resolved title cannot be wrong."""
        payload = self._said("")
        self.assertEqual(payload["say"], "Now playing: " + self.PICK + ".")
        self.assertEqual(payload["kind"], "intro")

    def test_an_intro_that_names_a_DIFFERENT_SONG_is_replaced(self):
        """Measured 2026-09-17, and the reason this guard exists: handed the
        resolved title in the tool's own result, gemma4:e2b announced a
        different Linkin Park song. Naming the right artist is not enough —
        that is exactly the failure."""
        payload = self._said("Here's Newcomer with Some Other Song!")
        self.assertEqual(payload["say"], "Now playing: " + self.PICK + ".")

    def test_an_intro_that_names_the_right_song_any_way_round_is_kept(self):
        """A substring test, not a template. The model is free to write the
        sentence however it likes as long as it has said the words."""
        payload = self._said("Fresh Track, that's Newcomer, coming up now.")
        self.assertEqual(payload["say"],
                         "Fresh Track, that's Newcomer, coming up now.")

    def test_the_check_ignores_case(self):
        payload = self._said("here's NEWCOMER with fresh track")
        self.assertEqual(payload["say"], "here's NEWCOMER with fresh track")

    def test_the_built_line_uses_the_CLEANED_title(self):
        """The name spoken aloud comes from euterpe.pretty(), so an upload
        titled with a pile of bracketed junk does not get read out in full."""
        self.PICK = "In The End [Official HD Music Video] - Linkin Park"
        payload = self._said("")
        self.assertEqual(payload["say"],
                         "Now playing: In The End - Linkin Park.")

    def test_a_typed_out_tool_call_never_becomes_speech(self):
        """Measured: when gemma4:e2b drops out of tool-calling mode it writes
        'play_music{query:<|"|>...}' as content. Calliope would read that aloud."""
        payload = self._said('play_music{query:<|"|>Newcomer - Fresh Track<|"|>}')
        self.assertEqual(payload["say"], "Now playing: " + self.PICK + ".")

    def test_a_DEGRADED_answer_never_becomes_an_intro(self):
        """The bug from the 2026-09-16 transcript, exactly: a turn PLAYED a song
        and then ran out of tool rounds, so ask() returned "The oracle got stuck
        consulting its tools. Try rephrasing." — prose that is neither blank nor
        a typed tool call. It sailed through and was read aloud as the song's
        introduction, telling a listener to rephrase a question they never
        asked. The song really was playing, so silence would be wrong too."""
        payload = self._said(pythia._STUCK_ANSWER)
        self.assertEqual(payload["say"], "Now playing: " + self.PICK + ".")

    def test_every_degraded_answer_is_rejected(self):
        """Not just the one from the transcript — the whole family, so a new
        member added to pythia cannot quietly start being spoken."""
        for answer in (pythia._STUCK_ANSWER, pythia._SILENT_ANSWER,
                       pythia._TIMEOUT_ANSWER,
                       "The oracle is unreachable — no Ollama server at x.",
                       "The oracle faltered (RuntimeError)."):
            with self.subTest(answer=answer[:34]):
                awen._playing_title = None
                # The previous pass PLAYED this song, so it is in the DJ's own
                # no-repeat memory now that the intro waits for it to be
                # audible. Each answer is meant to be judged on its own.
                awen._session_plays = []
                payload = self._said(answer)
                self.assertEqual(payload["say"],
                                 "Now playing: " + self.PICK + ".")

    def test_a_turn_that_played_NOTHING_says_nothing(self):
        """The dangerous case. The model's text here is an apology or a typed
        tool call, and reading either aloud announces a song that is not on."""
        payload = self._turn("I am now playing Stairway to Heaven.", played=False)
        self.assertNotIn("say", payload)

    def test_a_turn_that_played_nothing_counts_a_failure(self):
        self._turn("anything", played=False)
        self.assertEqual(awen._failures, 1)

    def test_a_turn_that_played_something_clears_the_failure_count(self):
        """"Played" means AUDIBLE, not "play() was called" — the breaker counts
        turns that end with no song playing, and _turn() mocks morpheus.play(),
        so the song only lands once a PLAYING snapshot carries it."""
        awen._failures = 2
        self._said("Here it is.")
        self.assertEqual(awen._failures, 0)

    def test_calling_play_is_not_yet_a_song_playing(self):
        """The other half, and the one the 2026-08-20 shape turned on: if the
        pick never loads, the count must still be standing when the observer
        comes to add to it. See TestAPickThatNeverLoads."""
        awen._failures = 2
        self._turn("Here it is.")
        self.assertEqual(awen._failures, 2)


class TestTheDeepCheck(_AwenCase):
    """It once played a video about sharpening chainsaws and announced it as
    Linkin Park.

    Not a model failure. euterpe.is_a_song() received a title and a duration
    and nothing else, because morpheus.search() runs --flat-playlist and keeps
    four fields — so YouTube's own Music category and the track/artist metadata
    were never fetched at all. A chainsaw title hits none of the fifteen
    denylist phrases and four minutes sits inside the 90-900s band, so it passed
    every check there was. The naming guard did its job, incidentally: the
    Linkin Park claim would have been replaced by the built line. The PICK was
    wrong, not the sentence about it.

    The deep pass costs a full yt-dlp extraction per candidate, which is
    affordable only because the queue fills during playback. That is why this
    landed second and not first.
    """

    CHAINSAW = {"title": "Sharpening a chainsaw chain the right way",
                "channel": "Chainsaw Dan", "duration": 240,
                "url": "https://youtu.be/chainsaw111"}
    PLAIN = {"title": "Someone - A Song", "channel": "Someone",
             "duration": 240, "url": "https://youtu.be/plain111111"}
    TRACK = {"title": "Linkin Park - In The End", "channel": "Linkin Park",
             "duration": 216, "url": "https://youtu.be/track111111"}

    _HOWTO = {"categories": ["Howto & Style"], "channel": "Chainsaw Dan"}
    _NONE = {"categories": ["Entertainment"], "channel": "Someone"}
    _MUSIC = {"categories": ["Music"], "track": "In The End",
              "artist": "Linkin Park", "channel": "Linkin Park"}
    _FAILED = {"error": "describe_failed"}

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.play = self._patch("awen.morpheus.play")

    def _pick(self, rows, described):
        """One guarded pick over `rows`, where describe() answers per URL."""
        self.describe.side_effect = lambda url: described.get(url, self._FAILED)
        with mock.patch("awen.morpheus.search", return_value=rows):
            return awen._guarded_play_music("anything")

    # ── the defect ──────────────────────────────────────────────────────────
    def test_the_CHAINSAW_is_refused(self):
        """The named bug. Nothing is queued and the model is told why."""
        result = self._pick([self.CHAINSAW],
                            {self.CHAINSAW["url"]: self._HOWTO})
        self.assertIn("YouTube files it under something else", result["error"])
        self.assertEqual(awen._queue, [])

    def test_the_chainsaw_still_passes_the_CHEAP_checks(self):
        """Asserted so the fix has something to be a fix of: if this ever goes
        green the denylist grew a phrase and the deep check is being credited
        for work a title already did."""
        self.assertIsNone(euterpe.is_a_song(self.CHAINSAW["title"],
                                            self.CHAINSAW["duration"]))

    # ── select, don't reject ────────────────────────────────────────────────
    def test_an_evidenced_result_BEATS_an_unevidenced_one_above_it(self):
        result = self._pick([self.PLAIN, self.TRACK],
                            {self.PLAIN["url"]: self._NONE,
                             self.TRACK["url"]: self._MUSIC})
        self.assertEqual(result["now_playing"], self.TRACK["title"])

    def test_with_NO_evidence_anywhere_the_old_rules_still_choose(self):
        """Live sets, covers and indie uploads carry no metadata. A hard gate
        would starve the queue of exactly the music worth finding."""
        result = self._pick([self.PLAIN, self.TRACK],
                            {self.PLAIN["url"]: self._NONE,
                             self.TRACK["url"]: self._NONE})
        self.assertEqual(result["now_playing"], self.PLAIN["title"])

    def test_a_chainsaw_ABOVE_a_real_track_loses_to_it(self):
        """The whole thing end to end: the bad result is rejected outright and
        the good one is selected, in one pass."""
        result = self._pick([self.CHAINSAW, self.TRACK],
                            {self.CHAINSAW["url"]: self._HOWTO,
                             self.TRACK["url"]: self._MUSIC})
        self.assertEqual(result["now_playing"], self.TRACK["title"])

    # ── degradation ─────────────────────────────────────────────────────────
    def test_every_describe_failing_degrades_to_the_OLD_rules(self):
        """One yt-dlp outage must not take Auto-DJ off the air. Without this the
        DJ would refuse everything the moment the network wobbled."""
        result = self._pick([self.PLAIN, self.TRACK], {})
        self.assertEqual(result["now_playing"], self.PLAIN["title"])

    def test_a_describe_that_RAISES_does_not_escape_the_guard(self):
        self.describe.side_effect = OSError("boom")
        with mock.patch("awen.morpheus.search", return_value=[self.PLAIN]):
            with self.assertRaises(OSError):
                awen._guarded_play_music("anything")
        # ...but a tick around it still survives, which is the contract that
        # actually matters (fetch never raises).
        self.assertIsInstance(awen.fetch(), dict)

    # ── the budget ──────────────────────────────────────────────────────────
    def test_it_stops_at_the_first_evidenced_result(self):
        """The common case: the top hit for "Artist - Song" IS the track, and
        that must cost exactly one extraction."""
        self._pick([self.TRACK, self.PLAIN],
                   {self.TRACK["url"]: self._MUSIC})
        self.assertEqual(self.describe.call_count, 1)

    def test_it_never_describes_more_than_the_budget(self):
        rows = [{**self.PLAIN, "title": "Someone - Song %d" % n,
                 "url": "https://youtu.be/plain%07d" % n} for n in range(5)]
        self._pick(rows, {})
        self.assertEqual(self.describe.call_count, awen._DEEP_CHECK_MAX)

    def test_a_result_the_CHEAP_checks_refused_is_never_described(self):
        """The expensive pass runs on survivors only. Describing a result the
        duration band already refused is a subprocess spent on nothing."""
        short = {**self.PLAIN, "duration": 5}
        self._pick([short], {})
        self.describe.assert_not_called()

    def test_when_every_deep_checked_result_is_refused_the_pick_is_refused(self):
        """Three instructional videos at the top of a result set is a QUERY
        problem, and the model re-picking is a better answer than this module
        digging further down a bad list. It costs nothing: a top-up refusal
        happens while music is still playing."""
        rows = [{**self.CHAINSAW, "title": "Sharpening tips %d" % n,
                 "url": "https://youtu.be/saw%08d" % n} for n in range(4)]
        result = self._pick(rows, {r["url"]: self._HOWTO for r in rows})
        self.assertIn("no_usable_result", result["error"])
        self.assertEqual(awen._queue, [])


class TestTheIntroWaitsForTheSong(_AwenCase):
    """Measured 2026-09-17: the stream failed to open, mpv logged "finished
    playback, no audio or video data played", and the introduction had already
    been read out. A listener was told what they were about to hear, and then
    heard silence.

    morpheus.play() sends a loadfile and returns. It does not wait, and cannot:
    whether a URL will open is not known until yt-dlp has finished with it,
    seconds later, which is the same fact _judge_pending_load is built around.
    So a turn cannot both play a song and truthfully announce it.

    The intro is therefore HELD on _pending_pick and released by _note_playing,
    on the tick the song becomes audible. The record that already decides "is
    this song ours" is the one that decides whether its introduction was ever
    true — no second piece of state, and no timer.
    """

    PICK = "Newcomer - Fresh Track"
    INTRO = "Here is Newcomer with Fresh Track."

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.play = self._patch("awen.morpheus.play")

    def _turn(self):
        """A tick whose turn picks a song and calls play(). Returns its
        payload. The song is NOT audible yet — nothing has confirmed it."""
        def fake_ask(message, **kwargs):
            kwargs["tool_overrides"]["play_music"](self.PICK)
            return self.INTRO

        with mock.patch("awen.morpheus.search", return_value=[_FRESH]), \
                mock.patch("awen.pythia.ask", side_effect=fake_ask):
            return self._play_then_end()

    def _hear(self, title=None, path=None):
        """The tick on which that song becomes audible.

        `path` is what mpv says it LOADED, and it defaults to the pick's own
        URL because the usual case here is our song arriving. A test about the
        listener starting something instead must pass a different one — saying
        so explicitly is the point, since a snapshot naming our URL while
        reporting their title is a state mpv cannot produce."""
        return self._tick(_ours(title or self.PICK, path or _FRESH["url"]))

    def test_the_pick_says_nothing_yet(self):
        """The turn's own payload carries no line. Nothing has confirmed the
        song exists, so there is nothing true to say about it."""
        payload = self._turn()
        self.assertNotIn("say", payload)

    def test_the_intro_is_spoken_once_the_song_is_audible(self):
        self._turn()
        payload = self._hear()
        self.assertEqual(payload["say"], self.INTRO)
        self.assertEqual(payload["kind"], "intro")
        self.assertEqual(payload["now_playing"], self.PICK)

    def test_a_pick_that_never_loads_is_NEVER_announced(self):
        """The 2026-09-17 failure itself. Every payload from here on, and there
        must not be a line in any of them."""
        said = [self._turn().get("say")]
        awen._observe({**_LOADING, "title": "watch?v=aaaaaaaaaaa"})
        for snapshot in (_FAILED, _FAILED, _FAILED):
            said.append(self._tick(dict(snapshot)).get("say"))
        # The breaker giving up is allowed to speak; the intro is not.
        self.assertEqual([s for s in said if s and s != awen._BREAKER_LINE], [])

    def test_the_intro_is_spoken_exactly_once(self):
        """_note_playing already returns early for a song it has seen. The held
        line goes with the record it was held on, so it cannot be read twice."""
        self._turn()
        self._hear()
        again = self._hear()
        self.assertNotIn("say", again)

    def test_a_song_the_LISTENER_started_instead_is_not_announced(self):
        """They reached for something else while ours was still resolving. That
        song is not ours, our pick never played, and our line described a
        different song entirely.

        The path is passed EXPLICITLY, and it matters. Left to default, the
        snapshot would say mpv loaded our URL while reporting their title —
        a state mpv cannot produce — and the test would pass while describing
        something impossible. It asserts "a different video is loaded" now,
        not "a different string is in the title field"."""
        self._turn()
        payload = self._hear(title="Someone Else - Their Choice", path=_THEIRS)
        self.assertNotIn("say", payload)

    def test_a_vanished_player_drops_the_held_line(self):
        """mpv gone is not a song starting, and the line was about a song that
        now certainly is not playing."""
        self._turn()
        self._tick(_IDLE)
        payload = self._hear()
        self.assertNotIn("say", payload)

    def test_the_held_line_is_still_the_one_the_guards_approved(self):
        """Holding it must not smuggle the model's text past the four checks in
        _take_a_turn — a typed-out tool call held for two seconds is still a
        typed-out tool call."""
        self.INTRO = 'play_music{query:<|"|>Newcomer - Fresh Track<|"|>}'
        self._turn()
        payload = self._hear()
        self.assertEqual(payload["say"], "Now playing: " + self.PICK + ".")


class TestBreaker(_AwenCase):
    """On 2026-08-20 a stale yt-dlp made every play a silent no-op. A DJ would
    have spun on that all night."""

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True

    def _failing_turn(self):
        with mock.patch("awen.pythia.ask", return_value="sorry"):
            return self._play_then_end()

    def test_three_failures_turn_it_off(self):
        self._failing_turn()
        self._failing_turn()
        self.assertTrue(awen.enabled())
        payload = self._failing_turn()
        self.assertFalse(awen.enabled())
        self.assertEqual(payload["say"], awen._BREAKER_LINE)
        self.assertEqual(payload["kind"], "notice")

    def test_it_logs_exactly_one_warning(self):
        self._failing_turn()
        self._failing_turn()
        with self.assertLogs("METIS.awen", level="WARNING") as caught:
            self._failing_turn()
        warnings = [line for line in caught.output if "WARNING" in line]
        self.assertEqual(len(warnings), 1)

    def test_one_good_song_resets_the_count(self):
        """CONSECUTIVE means consecutive. Guard hits are normal — eleven across
        ten measured turns, all of which still played."""
        self._failing_turn()
        self._failing_turn()
        awen._failures = 0          # stand-in for a successful turn
        self._failing_turn()
        self.assertTrue(awen.enabled())

    def test_repeated_load_failures_also_trip_it(self):
        """The 2026-08-20 shape exactly: the turn picks a song, the player
        reports load_failed, nothing ever plays."""
        for _ in range(3):
            self._tick(_PLAYING)
            self._tick(_FAILED)
        self.assertFalse(awen.enabled())

    def test_once_tripped_it_stops_firing(self):
        for _ in range(3):
            self._failing_turn()
        with mock.patch("awen.pythia.ask") as ask:
            self._play_then_end()
        ask.assert_not_called()


class TestABadTurnIsNotTheEndOfTheNight(_AwenCase):
    """The stall that took Auto-DJ off the air at 19:07 on 2026-09-16 and left
    it looking ON, with nothing playing, until the app was restarted.

    fetch() clears _want_pick BEFORE the turn, which is right — it is what stops
    one ended song firing two turns. But a turn that played nothing never
    discharged that debt, and nothing put it back. The song whose ending armed
    the pick has already ended, so _observe's trigger cannot fire a second time,
    and the breaker — which needs three turns IN A ROW — never gets its second
    turn. TestBreaker only reaches three by starting a fresh song by hand
    between each failure, which is the listener rescuing it.

    Eleven good turns, then one bad one, then silence: no music, no spoken
    notice, an amber lamp still saying Auto-DJ is on, and not one line above
    DEBUG to say otherwise.
    """

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True

    def _dud_tick(self, snapshot=_ENDED):
        """One tick whose turn, if it fires, answers without playing anything —
        the measured 19:07 shape, where the model spent its rounds on re-picks
        and returned _STUCK_ANSWER. Returns (payload, the ask mock)."""
        with mock.patch("awen.pythia.ask",
                        return_value=pythia._STUCK_ANSWER) as ask:
            return self._tick(snapshot), ask

    def test_a_failed_turn_leaves_the_pick_still_owed(self):
        self._tick(_PLAYING)
        self._dud_tick()
        self.assertTrue(awen._want_pick)

    def test_it_takes_another_turn_on_the_next_tick(self):
        """The whole bug in one assertion. Nothing else is playing and nothing
        else will start, so a tick that does not fire here never fires again."""
        self._tick(_PLAYING)
        self._dud_tick()
        _, ask = self._dud_tick()
        ask.assert_called_once()

    def test_the_status_line_still_says_it_is_working(self):
        """A stalled DJ and a busy one looked identical on screen: status_line()
        reads _want_pick, so the stall showed nothing at all."""
        self._tick(_PLAYING)
        self._dud_tick()
        self.assertTrue(awen.status_line())

    def test_three_bad_turns_in_a_row_trip_the_breaker_unaided(self):
        """Three turns with no listener rescuing it in between — the shape the
        breaker was written for and could not reach."""
        self._tick(_PLAYING)
        for _ in range(2):
            self._dud_tick()
            self.assertTrue(awen.enabled())
        payload, _ = self._dud_tick()
        self.assertFalse(awen.enabled())
        self.assertEqual(payload["say"], awen._BREAKER_LINE)

    def test_the_tripped_breaker_leaves_nothing_owed(self):
        """Giving up has to be quiet, or the retry outlives the flag that was
        switched off to stop it."""
        self._tick(_PLAYING)
        for _ in range(3):
            self._dud_tick()
        _, ask = self._dud_tick()
        ask.assert_not_called()
        self.assertFalse(awen._want_pick)


class TestAPickThatNeverLoads(_AwenCase):
    """The 2026-08-20 outage — a stale yt-dlp making every play a silent no-op —
    in the shape it actually arrives in: the DJ's OWN pick fails to load.

    By then the previous song has ended, so _playing_title is None and _observe
    returns at `was is None` before it ever looks at the error slug. The failure
    is not counted, no pick is re-armed, and Auto-DJ is off the air. TestBreaker's
    version ticks _PLAYING first, which is a LISTENER's song failing, not the
    DJ's, and is the one shape this path never takes.
    """

    PICK = "Newcomer - Fresh Track"

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.play = self._patch("awen.morpheus.play")

    def _fail_the_load(self):
        """The player's verdict on our pick, as it actually arrives: at least
        one clean snapshot first (morpheus's cache catching up with the play()
        that cleared the last slug), then the error.

        Not decoration — without the clean tick the DJ is being handed the
        PREVIOUS pick's sticky slug, and a test that skips it is testing the
        race rather than the failure."""
        awen._observe({**_LOADING, "title": "watch?v=aaaaaaaaaaa"})
        awen._observe(dict(_FAILED))

    def _pick_tick(self, snapshot=_ENDED):
        """A tick whose turn picks a song and calls play(). The song does NOT
        become audible — that takes a _PLAYING snapshot, and its absence is the
        whole point here."""
        def fake_ask(message, **kwargs):
            kwargs["tool_overrides"]["play_music"](self.PICK)
            return f"Here is {self.PICK}."

        with mock.patch("awen.morpheus.search",
                        return_value=[{"title": self.PICK,
                                       "channel": "Newcomer", "duration": 240,
                                       "url": "https://youtu.be/aaaaaaaaaaa"}]), \
                mock.patch("awen.pythia.ask", side_effect=fake_ask):
            return self._tick(snapshot)

    def test_a_pick_that_never_loads_counts_a_failure(self):
        self._tick(_PLAYING)
        self._pick_tick()
        self.assertIsNotNone(awen._pending_pick)
        self._fail_the_load()
        self.assertEqual(awen._failures, 1)

    def test_a_pick_that_never_loads_leaves_a_pick_owed(self):
        self._tick(_PLAYING)
        self._pick_tick()
        self._fail_the_load()
        self.assertTrue(awen._want_pick)

    def test_the_sticky_error_slug_is_counted_ONCE_not_every_tick(self):
        """morpheus keeps _load_error set until the next play(), so the slug
        rides EVERY following snapshot. Counting per tick would trip the breaker
        in six seconds and blame the wrong turn."""
        self._tick(_PLAYING)
        self._pick_tick()
        self._fail_the_load()
        for _ in range(3):
            awen._observe(dict(_FAILED))
        self.assertEqual(awen._failures, 1)

    def test_a_pick_still_resolving_is_not_a_failure(self):
        """loading=True is between tracks, not the end of one — the same rule
        the trigger below it lives by."""
        self._tick(_PLAYING)
        self._pick_tick()
        awen._observe({**_LOADING, "error": "load_failed"})
        self.assertEqual(awen._failures, 0)
        self.assertIsNotNone(awen._pending_pick)

    def test_the_PREVIOUS_picks_sticky_error_is_not_blamed_on_this_one(self):
        """morpheus's cache can be a tick stale while play() has already cleared
        the slug in morpheus's own state, so the tick right after a pick can
        carry the LAST pick's verdict. Counting it would have the retry path
        this fix adds manufacture its own second failure."""
        self._tick(_PLAYING)
        self._pick_tick()
        awen._observe(dict(_FAILED))        # stale: cache has not caught up
        self.assertEqual(awen._failures, 0)
        self.assertIsNotNone(awen._pending_pick)

    def test_calling_play_is_not_the_same_as_a_song_playing(self):
        """Resetting on a play() CALL is what let the 2026-08-20 shape run all
        night: pick, reset, fail, count, pick, reset — the count oscillates
        0-1-0-1 and three in a row never happens. The breaker's own contract
        says turns that end with no song PLAYING."""
        self._tick(_PLAYING)
        awen._failures = 2
        self._pick_tick()
        self.assertEqual(awen._failures, 2)

    def test_the_song_becoming_audible_is_what_clears_the_count(self):
        self._tick(_PLAYING)
        awen._failures = 2
        self._pick_tick()
        self._tick(_ours(self.PICK, _FRESH["url"]))
        self.assertEqual(awen._failures, 0)

    def test_three_picks_that_never_load_trip_the_breaker(self):
        """The outage the breaker exists for, end to end and unaided."""
        self._tick(_PLAYING)
        self._pick_tick()
        for n in range(1, 4):
            self._fail_the_load()
            if awen.enabled():
                self.PICK = f"Newcomer - Track {n}"
                self._pick_tick(_FAILED)
        self.assertFalse(awen.enabled())


class TestTheQueue(_AwenCase):
    """Picking measured 4.2s typically and 17.1s at worst, and every second of
    it was silence somebody sat through — because nothing knew what came next
    until the current song was already over.

    The queue moves that work under the music. What it does NOT change is who
    decides: every entry passed the same guards, and passes them again at the
    pop, because an entry can be minutes old by the time it plays.
    """

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.play = self._patch("awen.morpheus.play")
        self._urls = 0

    def _bank(self, title, duration=240, channel=""):
        """Put an entry in the queue directly, the way a turn would have."""
        self._urls += 1
        entry = {"key": euterpe.normalize(title, channel), "title": title,
                 "channel": channel, "duration": duration,
                 "url": "https://youtu.be/q%09d" % self._urls,
                 "intro": "Now playing: " + title + "."}
        awen._queue.append(entry)
        return entry

    def _fill_now(self):
        """Let a top-up through: clear the cooldown the harness starts."""
        awen._last_fill_at = 0.0

    # ── the headline ────────────────────────────────────────────────────────
    def test_a_song_ending_with_a_warm_queue_takes_NO_model_turn(self):
        """The whole feature in one assertion. No turn, no search, no gap."""
        entry = self._bank("Newcomer - Fresh Track")
        with mock.patch("awen.pythia.ask") as ask:
            self._tick(_PLAYING)
            self._tick(_ENDED)
        ask.assert_not_called()
        self.play.assert_called_once()
        self.assertEqual(self.play.call_args.args[0], entry["url"])

    def test_an_empty_queue_still_uses_the_old_immediate_pick(self):
        """The queue is an optimisation whose empty state is the pre-queue code
        path. If that fallback is lost, a cold start is a dead DJ."""
        with mock.patch("awen.pythia.ask", return_value="") as ask:
            self._tick(_PLAYING)
            self._tick(_ENDED)
        ask.assert_called_once()

    # ── the queue is part of "already chosen" ───────────────────────────────
    def test_a_queued_song_is_refused_by_the_guard(self):
        self._bank("Newcomer - Fresh Track", channel="Newcomer")
        rows = [{"title": "Newcomer - Fresh Track", "channel": "Newcomer",
                 "duration": 240, "url": "https://youtu.be/aaaaaaaaaaa"}]
        with mock.patch("awen.morpheus.search", return_value=rows):
            result = awen._guarded_play_music("Newcomer - Fresh Track")
        self.assertIn("already played recently", result["error"])

    def test_a_queued_song_is_named_in_the_turn_message(self):
        """Asserted beside the guard on purpose: if these two ever came from
        different places, the model would burn a whole turn on a refusal it was
        never warned about."""
        self._bank("Newcomer - Fresh Track", channel="Newcomer")
        self.assertIn("newcomer - fresh track", awen._turn_message())

    def test_a_queued_song_NEVER_reaches_the_taste_anchor(self):
        """Labelling a queued row "user" would be the 2026-09-16 feedback loop
        again, one stage further upstream."""
        self._bank("Newcomer - Fresh Track")
        self.assertEqual(euterpe.taste_anchor(plays=awen._known_plays()),
                         ["artist - prior"])

    def test_the_queue_never_reaches_the_ledger_file(self):
        self._bank("Newcomer - Fresh Track")
        self.assertEqual([r["title"] for r in euterpe.entries()],
                         ["Artist - Prior"])

    # ── revalidation at the pop ─────────────────────────────────────────────
    def test_a_stale_entry_is_dropped_and_the_next_one_plays(self):
        """Minutes can pass between choosing and playing, and the listener may
        have played that song themselves in between."""
        self._bank("Artist - Prior")                  # already in the ledger
        fresh = self._bank("Newcomer - Fresh Track")
        played = awen._play_from_queue()
        self.assertEqual(played["title"], fresh["title"])
        self.play.assert_called_once()

    def test_an_entry_the_config_now_refuses_is_dropped(self):
        """euterpe's config is re-read on every call, so the duration band can
        move under an entry that was fine when it was chosen."""
        self._bank("Newcomer - Too Short", duration=5)
        fresh = self._bank("Newcomer - Fresh Track")
        self.assertEqual(awen._play_from_queue()["title"], fresh["title"])

    def test_an_entirely_stale_queue_falls_back_to_a_live_turn(self):
        self._bank("Artist - Prior")
        with mock.patch("awen.pythia.ask", return_value="") as ask:
            self._tick(_PLAYING)
            self._tick(_ENDED)
        ask.assert_called_once()

    def test_a_dropped_entry_counts_NO_failure(self):
        """A discard is a guard hit, and guard hits are normal."""
        self._bank("Artist - Prior")
        self._bank("Newcomer - Fresh Track")
        awen._play_from_queue()
        self.assertEqual(awen._failures, 0)

    # ── the top-up ──────────────────────────────────────────────────────────
    def test_a_top_up_runs_while_a_song_plays(self):
        self._fill_now()
        with mock.patch("awen.pythia.ask", return_value="") as ask:
            self._tick(_PLAYING)
        ask.assert_called_once()

    def test_a_top_up_NEVER_runs_on_an_idle_player(self):
        """Auto-DJ left on overnight with nothing playing must not spend the
        model all night for a queue nobody is draining."""
        self._fill_now()
        with mock.patch("awen.pythia.ask") as ask:
            self._tick(_ENDED)
            self._fill_now()
            self._tick(_IDLE)
        ask.assert_not_called()

    def test_a_top_up_NEVER_runs_while_paused(self):
        self._fill_now()
        with mock.patch("awen.pythia.ask") as ask:
            self._tick(_PAUSED)
        ask.assert_not_called()

    def test_a_top_up_stops_once_the_queue_is_deep_enough(self):
        for n in range(awen._QUEUE_TARGET):
            self._bank("Newcomer - Track %d" % n)
        self._fill_now()
        with mock.patch("awen.pythia.ask") as ask:
            self._tick(_PLAYING)
        ask.assert_not_called()

    def test_the_cooldown_paces_top_ups(self):
        """Without it, a failing search plus a short queue is a yt-dlp process
        every 2s for the length of every song."""
        self._fill_now()
        with mock.patch("awen.pythia.ask", return_value="") as ask:
            self._tick(_PLAYING)
            self._tick({**_PLAYING, "pos": 14.0})
            self._tick({**_PLAYING, "pos": 16.0})
        ask.assert_called_once()

    def test_a_top_up_says_nothing(self):
        self._fill_now()
        with mock.patch("awen.pythia.ask", return_value="Here is a song."):
            payload = self._tick(_PLAYING)
        self.assertNotIn("say", payload)

    def test_a_top_up_does_not_wear_the_picking_label(self):
        """"Choosing the next song…" is the label that explains a SILENCE. Shown
        over music it would explain nothing and mean nothing."""
        self._fill_now()
        seen = []

        def fake_ask(message, **kwargs):
            seen.append(awen._picking)
            return ""

        with mock.patch("awen.pythia.ask", side_effect=fake_ask):
            self._tick(_PLAYING)
        self.assertEqual(seen, [False])

    # ── the breaker must not trip over a good evening ───────────────────────
    def test_top_ups_that_queue_NOTHING_never_trip_the_breaker(self):
        """THE test of this change. The breaker means "attempts to fill a
        SILENCE that produced no song"; during a top-up a song is playing and
        nothing the listener can hear has failed. Counting here would switch
        Auto-DJ off, out loud, in the middle of a perfectly good song."""
        with mock.patch("awen.pythia.ask", return_value="sorry"):
            for pos in (10.0, 20.0, 30.0, 40.0):
                self._fill_now()
                self._tick({**_PLAYING, "pos": pos})
        self.assertEqual(awen._failures, 0)
        self.assertTrue(awen.enabled())

    def test_a_failed_top_up_does_not_arm_a_pick_over_a_playing_song(self):
        """An unconditional _owe_a_pick() here would have the DJ start a second
        song over the one already going."""
        self._fill_now()
        with mock.patch("awen.pythia.ask", return_value="sorry"):
            self._tick(_PLAYING)
        self.assertFalse(awen._want_pick)
        self.play.assert_not_called()

    def test_a_silence_the_queue_cannot_fill_STILL_counts(self):
        """The other half: when the song ends and nothing is playable, that is
        exactly the failure the breaker was written for."""
        with mock.patch("awen.pythia.ask", return_value="sorry"):
            self._tick(_PLAYING)
            self._tick(_ENDED)
        self.assertEqual(awen._failures, 1)

    # ── the held introduction survives the wait ─────────────────────────────
    def test_the_held_line_survives_minutes_in_the_queue(self):
        entry = self._bank("Newcomer - Fresh Track")
        entry["intro"] = "Here is Newcomer with Fresh Track."
        self._tick(_PLAYING)
        self._tick(_ENDED)
        payload = self._tick({**_PLAYING, "title": "Newcomer - Fresh Track"})
        self.assertEqual(payload["say"], "Here is Newcomer with Fresh Track.")

    def test_a_dropped_entry_takes_its_line_with_it(self):
        """The invariant the hold stands on, one stage earlier: the sentence
        lives on the record for the pick it describes."""
        self._bank("Artist - Prior")                  # dropped as stale
        self._bank("Newcomer - Fresh Track")
        awen._play_from_queue()
        self.assertEqual(awen._pending_pick["title"], "Newcomer - Fresh Track")


class TestTheHandoverTaper(_AwenCase):
    """The seam between two songs was a cut. With a queue there is finally
    something to fade TO, so the DJ dips the outgoing song, swaps under the
    floor, and brings the new one up with its introduction over the top.

    The predictive half reads `dur - pos`, which §12 is emphatic about: whether
    a track ENDED is decided in Python and never read off mpv. This does not
    touch that. It arms a GAIN and nothing else — no latch, no clearing, no
    return value — so every one of these tests is really the same assertion from
    a different angle: the taper may be wrong without anything else being wrong.
    """

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.play = self._patch("awen.morpheus.play")
        self._urls = 0

    def _bank(self, title="Newcomer - Fresh Track"):
        self._urls += 1
        awen._queue.append({"key": euterpe.normalize(title), "title": title,
                            "channel": "", "duration": 240,
                            "url": "https://youtu.be/t%09d" % self._urls,
                            "intro": "Now playing: " + title + "."})

    def _near_end(self, pos=237.0, **over):
        return {**_PLAYING, "pos": pos, "dur": 240.0, **over}

    # ── the §12 property ────────────────────────────────────────────────────
    def test_arming_the_taper_decides_NOTHING_about_the_song_ending(self):
        """The test this whole design rests on. A gain is not a verdict: the
        trigger for the next song is still media that existed and then stopped
        existing, tracked in Python."""
        self._bank()
        self._tick(_PLAYING)
        before = (awen._want_pick, awen._playing_title)
        self._tick(self._near_end())
        self.taper.assert_called_once()
        self.assertEqual((awen._want_pick, awen._playing_title), before)

    def test_the_song_still_ends_the_way_it_always_did(self):
        """A taper must not become the thing that moves to the next track."""
        self._bank()
        self._tick(_PLAYING)
        self._tick(self._near_end())
        self.play.assert_not_called()          # the dip alone plays nothing
        self._tick(_ENDED)
        self.play.assert_called_once()

    # ── when it fires ───────────────────────────────────────────────────────
    def test_it_fires_once_per_song_however_many_ticks(self):
        """A bad `dur` could otherwise produce dip, swell, dip, swell for the
        rest of the track."""
        self._bank()
        self._tick(_PLAYING)
        for pos in (236.5, 237.0, 238.0, 239.0):
            self._tick(self._near_end(pos=pos))
        self.taper.assert_called_once()

    def test_a_MISPREDICTED_end_still_only_dips_once(self):
        """The degradation that matters: `dur` was a lie and the song plays on.
        One dip-and-swell, then nothing — the taper is bounded and expires on
        its own, so there is nothing here that needs cancelling."""
        self._bank()
        self._tick(_PLAYING)
        self._tick(self._near_end())
        for pos in (241.0, 260.0, 300.0):
            self._tick({**_PLAYING, "pos": pos, "dur": 240.0})
        self.taper.assert_called_once()

    def test_mid_song_is_far_too_early(self):
        self._bank()
        self._tick({**_PLAYING, "pos": 30.0, "dur": 240.0})
        self.taper.assert_not_called()

    # ── when it must not ────────────────────────────────────────────────────
    def test_never_with_an_EMPTY_queue(self):
        """Dipping before a gap we cannot fill is worse than a clean cut."""
        self._tick(_PLAYING)
        self._tick(self._near_end())
        self.taper.assert_not_called()

    def test_never_with_the_flag_off(self):
        self._bank()
        awen._enabled = False
        self._tick(self._near_end())
        self.taper.assert_not_called()

    def test_never_while_paused(self):
        """Paused near the end is not about to end — it is stopped."""
        self._bank()
        self._tick(self._near_end(paused=True))
        self.taper.assert_not_called()

    def test_never_with_pos_or_dur_MISSING(self):
        """Both are nullable through the whole load-resolve window, and
        `dur - pos` on a None is a TypeError inside _observe every 2 s.

        Called directly rather than through a tick: a snapshot with BOTH missing
        is not a song nearly over, it is no media at all, and _observe reads it
        as the song ENDING — at which point the pop tapers, correctly, and the
        assertion below would be measuring the wrong thing."""
        self._bank()
        for over in ({"pos": None}, {"dur": None}, {"pos": None, "dur": None}):
            with self.subTest(**over):
                awen._tapered_title = None
                awen._maybe_taper(self._near_end(**over), "Artist - Song")
        self.taper.assert_not_called()

    def test_never_on_a_LIVE_STREAM(self):
        """euterpe.is_a_song is the authority here too, so a stream that never
        ends cannot have a "nearly over" — and the guard, the ledger and this
        cannot disagree about what a song is."""
        self._bank()
        self._tick({**_PLAYING, "title": "lofi hip hop radio",
                    "pos": 30000.0, "dur": 30003.0})
        self.taper.assert_not_called()

    def test_never_while_a_load_is_still_resolving(self):
        self._bank()
        self._tick(self._near_end(loading=True))
        self.taper.assert_not_called()

    # ── the half that needs no prediction ───────────────────────────────────
    def test_the_POP_arms_one_by_itself(self):
        """The valuable half, and it has no §12 exposure at all: the dip is
        driven by an event we caused. The down-ramp lands on an idle player, the
        hold covers yt-dlp resolving, and the up-ramp catches the opening bars."""
        self._bank()
        awen._play_from_queue()
        self.taper.assert_called_once()

    def test_one_dip_spans_the_WHOLE_handover(self):
        """The early taper and the pop's both fire, and morpheus ignores the
        second — so the song falls away, the swap happens under the floor, and
        the new one rises, as one movement rather than two fighting."""
        self._bank()
        self._tick(_PLAYING)
        self._tick(self._near_end())
        self._tick(_ENDED)
        self.assertEqual(self.taper.call_count, 2)   # armed twice, dips once

    # ── the release, since 2026-09-19 ───────────────────────────────────────
    def test_release_does_not_fire_while_still_waiting(self):
        """The hold is conditional now: nothing ends it while the new song has
        not actually arrived. _ENDED carries no media, so this tick pops the
        queue and plays, but nothing is audible yet."""
        self._bank()
        self._tick(_PLAYING)
        self._tick(self._near_end())
        self.release_taper.reset_mock()      # the first tick's own new-song fire
        self._tick(_ENDED)
        self.release_taper.assert_not_called()

    def test_release_fires_the_instant_the_new_song_is_audible(self):
        """The whole point of the split: release_taper() rides the SAME event
        that releases the held introduction (see TestIsThisSongOurs), so the
        up-ramp and the DJ's voice start together rather than the ramp running
        out on morpheus's own backstop clock."""
        self._bank()
        self._tick(_PLAYING)
        self._tick(self._near_end())
        self._tick(_ENDED)
        self.release_taper.reset_mock()
        self._tick({**_PLAYING, "title": "Newcomer - Fresh Track"})
        self.release_taper.assert_called_once()


class TestTheTaperReleaseTracksAudibilityNotOwnership(_AwenCase):
    """release_taper() ends the hold on ANY song becoming audible, exactly like
    the owed-pick debt it sits beside (_note_playing) — whoever started it, a
    thing making sound is the end of the silence the hold exists to bridge."""

    def setUp(self):
        super().setUp()
        awen._enabled = True

    def test_fires_on_the_first_song_of_a_session(self):
        self._tick(_PLAYING)
        self.release_taper.assert_called_once()

    def test_does_not_fire_again_for_a_song_already_noted(self):
        self._tick(_PLAYING)
        self.release_taper.reset_mock()
        self._tick(_PLAYING)
        self.release_taper.assert_not_called()

    def test_fires_for_a_song_the_LISTENER_started_too(self):
        """A held taper does not know or care whose pick ends it — only that
        something is now audible."""
        self._tick({**_PLAYING, "title": "Someone Else - Track"})
        self.release_taper.assert_called_once()


class TestIsThisSongOurs(_AwenCase):
    """_note_playing decides whether the song now audible is the DJ's own pick
    or something the listener started, and everything downstream hangs off it:
    ours goes to session memory, theirs goes to the LEDGER and becomes taste.

    It compared two keys that are derived from different sources — the pending
    key from the search RESULT's title and channel, the live one from mpv's
    `media-title` — with a bare `==`. Any of the ways one song can have two
    keys therefore filed the DJ's own pick as the listener's, which is the
    2026-09-16 feedback loop reached through a comparison instead of a label.
    """

    PICK = "Queen - Bohemian Rhapsody"

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.play = self._patch("awen.morpheus.play")

    def _pick_then_hear(self, mpv_title, path="https://youtu.be/aaaaaaaaaaa"):
        """Fire a pick, then have mpv report the song under `mpv_title`.

        `path` defaults to the pick's own URL, so these tests go on exercising
        the TITLE comparison they were written for — pass a different one to
        test the identity rather than the spelling."""
        awen._queue.append({"key": euterpe.normalize(self.PICK, "Queen"),
                            "title": self.PICK, "channel": "Queen",
                            "duration": 240,
                            "url": "https://youtu.be/aaaaaaaaaaa",
                            "intro": "Now playing: " + self.PICK + "."})
        awen._play_from_queue()
        self._tick(_ours(mpv_title, path))

    def test_the_same_title_is_ours(self):
        """The easy case, asserted so the hard ones below have a baseline."""
        self._pick_then_hear(self.PICK)
        self.assertEqual(euterpe.entries()[-1]["title"], "Artist - Prior",
                         "the DJ's own pick reached the listener's list")

    def test_mpv_reporting_the_song_the_OTHER_WAY_ROUND_is_still_ours(self):
        """YouTube ships both orders and mpv reports whichever the upload used.
        With `==` this was a different song, so the DJ's pick was recorded as
        the listener's taste — and the taste anchor then steered toward it."""
        self._pick_then_hear("Bohemian Rhapsody - Queen")
        self.assertEqual(euterpe.entries()[-1]["title"], "Artist - Prior",
                         "the DJ's own pick reached the listener's list")

    def test_a_song_the_LISTENER_started_is_still_theirs(self):
        """The guard on the guard. A comparison that says yes to everything
        would pass both tests above and quietly stop the ledger recording
        anything at all.

        A DIFFERENT path as well as a different title, because that is what
        actually happened: they started their own song, so mpv loaded their
        URL. Leaving the default would have mpv holding our video and calling
        it theirs, and the assertion would then be testing an impossible
        snapshot."""
        self._pick_then_hear("Someone Else - A Different Song", path=_THEIRS)
        self.assertEqual(euterpe.entries()[-1]["title"],
                         "Someone Else - A Different Song")


class TestTheQueueIsVisible(_AwenCase):
    """The queue decided what music played for hours and nothing could see it:
    no UI read it, status_line() said nothing, and every line about it was DEBUG
    while logging runs at INFO. The verification step written for it — "confirm
    the queue fills during playback" — could not actually be performed.

    This is the seam the box sits on. No Tk here.
    """

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.play = self._patch("awen.morpheus.play")

    def _bank(self, title="Newcomer - Fresh Track", **extra):
        awen._next_entry_id += 1
        entry = {"id": awen._next_entry_id, "key": euterpe.normalize(title),
                 "title": title, "channel": "", "duration": 240,
                 "url": "https://youtu.be/q%09d" % awen._next_entry_id,
                 "artist": "", "track": "",
                 "intro": "Now playing: " + title + "."}
        entry.update(extra)
        awen._queue.append(entry)
        awen._queue_revision += 1
        return entry

    # ── the count on the label ──────────────────────────────────────────────
    def test_the_count_reaches_the_status_line(self):
        for _ in range(3):
            self._bank()
        self.assertEqual(awen.status_line(), "Auto-DJ: 3 queued")

    def test_an_empty_queue_says_nothing(self):
        self.assertEqual(awen.status_line(), "")

    def test_the_flag_being_off_says_nothing(self):
        self._bank()
        awen._enabled = False
        self.assertEqual(awen.status_line(), "")

    def test_a_SILENCE_outranks_the_count(self):
        """The first two lines explain a gap the listener is sitting through.
        A queue depth is ambient, and ambient loses."""
        self._bank()
        awen._picking = True
        self.assertIn("choosing", awen.status_line())
        awen._picking = False
        awen._want_pick = True
        self.assertIn("waiting", awen.status_line())

    def test_the_count_is_still_FREE_to_ask(self):
        """It runs on a 60 ms chain. Reading the ledger or taking the lock here
        would put a file read at 16 Hz behind the operator's own typing."""
        self._bank()
        with mock.patch("awen.euterpe.entries",
                        side_effect=AssertionError("read the ledger")), \
                mock.patch("awen.morpheus.last_snapshot",
                           side_effect=AssertionError("touched the player")):
            awen.status_line()

    # ── the view ────────────────────────────────────────────────────────────
    def test_the_view_COPIES_before_it_leaves_the_lock(self):
        """The naming loop runs outside _lock, so it must walk a copy. Walking
        the live list is a real "list changed size during iteration" the moment
        the worker pops mid-draw — which it does, once a song.

        Driven by making the worker's write happen DURING the loop, because a
        test that merely reads the result cannot tell the two apart: the rows
        are freshly built dicts either way, so nothing about the return value
        reveals which list was walked."""
        for n in range(4):
            self._bank("Newcomer - Track %d" % n)
        real = euterpe.pretty

        def pop_mid_draw(title, channel=""):
            if awen._queue:
                awen._queue.pop(0)          # the worker, mid-iteration
            return real(title, channel)

        with mock.patch("awen.euterpe.pretty", side_effect=pop_mid_draw):
            rows = awen.queue_view()
        # Four in, four out. A list does NOT raise when it is mutated during
        # iteration — it silently skips — so the symptom to assert is the
        # COUNT, not an exception. Walking the live list here yields two.
        self.assertEqual(len(rows), 4)

    def test_the_rows_cannot_reach_back_into_the_queue(self):
        self._bank()
        rows = awen.queue_view()
        rows.append({"id": 99, "name": "not a real song"})
        rows[0]["name"] = "vandalised"
        self.assertEqual(len(awen._queue), 1)
        self.assertEqual(awen.queue_view()[0]["name"], "Newcomer - Fresh Track")

    def test_it_carries_the_id_and_the_name_and_nothing_else(self):
        """A list has no business with a URL."""
        self._bank()
        self.assertEqual(set(awen.queue_view()[0]), {"id", "name"})

    def test_it_keeps_PLAY_ORDER(self):
        for n in range(3):
            self._bank("Newcomer - Track %d" % n)
        self.assertEqual([r["name"] for r in awen.queue_view()],
                         ["Newcomer - Track 0", "Newcomer - Track 1",
                          "Newcomer - Track 2"])

    def test_the_metadata_artist_and_track_NAME_the_row(self):
        """yt-dlp is the one source that knows which half is which — a title
        has to be guessed at from a dash."""
        self._bank("In The End [Official HD Music Video] - Linkin Park",
                   artist="Linkin Park", track="In The End")
        self.assertEqual(awen.queue_view()[0]["name"], "Linkin Park - In The End")

    def test_one_without_the_other_is_not_a_name(self):
        """Both or neither, the same rule has_music_evidence() applies to the
        same two fields."""
        for extra in ({"artist": "Linkin Park"}, {"track": "In The End"},
                      {"artist": "  ", "track": "In The End"}):
            with self.subTest(**extra):
                awen._queue = []
                self._bank("Linkin Park - Numb", **extra)
                self.assertEqual(awen.queue_view()[0]["name"],
                                 "Linkin Park - Numb")

    def test_a_row_with_no_metadata_falls_back_to_pretty(self):
        self._bank("Newcomer - Fresh Track [Official Video]")
        self.assertEqual(awen.queue_view()[0]["name"], "Newcomer - Fresh Track")

    # ── removal ─────────────────────────────────────────────────────────────
    def test_removing_by_id_drops_exactly_that_entry(self):
        first, second, third = (self._bank("Newcomer - Track %d" % n)
                                for n in range(3))
        self.assertTrue(awen.remove_queued(second["id"]))
        self.assertEqual([e["title"] for e in awen._queue],
                         [first["title"], third["title"]])

    def test_the_INDEX_is_not_the_identity(self):
        """The worker pops from the head while the box is open, so row n is a
        different song by the time a click lands. Popping the head and then
        removing what the user clicked must still take the right one."""
        first, second = self._bank("A - One"), self._bank("B - Two")
        awen._play_from_queue()                  # the worker takes `first`
        self.assertTrue(awen.remove_queued(second["id"]))
        self.assertEqual(awen._queue, [])
        self.assertEqual(first["id"], 1)         # and it was NOT index 0 twice

    def test_removing_an_ALREADY_POPPED_entry_is_False_not_a_crash(self):
        """Between the pop and morpheus.play() the entry belongs to no list.
        Removal is scoped to songs that have not started."""
        entry = self._bank()
        awen._play_from_queue()
        self.assertFalse(awen.remove_queued(entry["id"]))

    def test_removing_an_id_that_never_existed_is_False(self):
        self.assertFalse(awen.remove_queued(4321))

    def test_serials_are_NEVER_reused(self):
        """`id = len(_queue)` would hand a new entry the serial of one just
        removed, and a click held over from the old list would drop it."""
        first = self._bank()
        awen.remove_queued(first["id"])
        self.assertNotEqual(self._bank()["id"], first["id"])

    # ── removed means DECLINED ──────────────────────────────────────────────
    def test_a_removed_song_is_not_offered_again(self):
        """Without this the button looks broken: the entry leaves the
        do-not-choose list at the same moment it leaves the queue, so the next
        top-up can bank it again inside twenty seconds."""
        entry = self._bank("Newcomer - Fresh Track")
        awen.remove_queued(entry["id"])
        rows = [{"title": "Newcomer - Fresh Track", "channel": "",
                 "duration": 240, "url": "https://youtu.be/bbbbbbbbbbb"}]
        with mock.patch("awen.morpheus.search", return_value=rows):
            result = awen._guarded_play_music("Newcomer - Fresh Track")
        self.assertIn("already played recently", result["error"])

    def test_a_declined_song_is_named_in_the_turn_message(self):
        entry = self._bank("Newcomer - Fresh Track")
        awen.remove_queued(entry["id"])
        self.assertIn("newcomer - fresh track", awen._turn_message())

    def test_a_declined_song_NEVER_reaches_the_taste_anchor(self):
        """It was never heard, let alone chosen by the listener. Sourced "dj"
        so taste_anchor() skips it by the rule it already has."""
        entry = self._bank("Newcomer - Fresh Track")
        awen.remove_queued(entry["id"])
        self.assertEqual(euterpe.taste_anchor(plays=awen._known_plays()),
                         ["artist - prior"])

    def test_a_declined_song_is_not_counted_as_PLAYED(self):
        """_played_rows() is documented as songs that have actually been HEARD.
        Putting a never-played song there would make that a lie, and the pop's
        revalidation reads it."""
        entry = self._bank("Newcomer - Fresh Track")
        awen.remove_queued(entry["id"])
        self.assertNotIn("newcomer - fresh track",
                         [r.get("key") for r in awen._played_rows()])

    # ── the revision ────────────────────────────────────────────────────────
    def test_the_revision_moves_on_append_pop_and_remove(self):
        """The UI redraws on a change of this int alone, so a write that forgets
        to bump it is a box that silently goes stale.

        Each leg is measured from the revision IMMEDIATELY before it. Taking one
        reading at the start and one at the end proves nothing — any single bump
        satisfies it, and the first version of this test passed with the removal
        bump deleted."""
        before = awen.queue_revision()
        first = self._bank()
        self.assertGreater(awen.queue_revision(), before, "append")

        second = self._bank()
        before = awen.queue_revision()
        awen.remove_queued(second["id"])
        self.assertGreater(awen.queue_revision(), before, "remove")

        before = awen.queue_revision()
        awen._play_from_queue()
        self.assertGreater(awen.queue_revision(), before, "pop")
        self.assertEqual(first["id"], 1)

    def test_a_failed_removal_does_not_move_it(self):
        self._bank()
        before = awen.queue_revision()
        awen.remove_queued(9999)
        self.assertEqual(awen.queue_revision(), before)


class TestNeverRaises(_AwenCase):
    """fetch() runs on a Kairos worker for a job nobody asked for. An exception
    here would deliver None, which Kairos reads as a failure and answers with
    its retry ladder — meaningless for a job waiting on a song to finish."""

    def test_a_broken_snapshot_read_does_not_raise(self):
        with mock.patch("awen.morpheus.last_snapshot",
                        side_effect=RuntimeError("pipe gone")):
            with self.assertLogs("METIS.awen", level="ERROR"):
                payload = awen.fetch()
        self.assertIsInstance(payload, dict)

    def test_a_broken_turn_does_not_raise(self):
        self._seed("Artist - Prior")
        awen._enabled = True
        with mock.patch("awen.pythia.ask", side_effect=RuntimeError("boom")):
            with self.assertLogs("METIS.awen", level="ERROR"):
                payload = self._play_then_end()
        self.assertIsInstance(payload, dict)

    def test_a_garbled_snapshot_is_survived(self):
        for snapshot in ({}, {"running": True}, {"running": True, "title": None},
                         {"running": True, "title": "x", "pos": None,
                          "dur": None}):
            with self.subTest(snapshot=snapshot):
                self.assertIsInstance(self._tick(snapshot), dict)

    def test_fetch_never_returns_None(self):
        """None is Kairos's failure signal and would start the retry ladder."""
        self.assertIsNotNone(self._tick(_IDLE))
        self.assertIsNotNone(self._tick(_PLAYING))
        self.assertIsNotNone(self._tick(_ENDED))

    def test_the_payload_always_reports_the_flag(self):
        """So a breaker trip reaches the UI without a second read."""
        self.assertIn("enabled", self._tick(_IDLE))


class TestLogHygiene(_AwenCase):
    """Auto-DJ must be SILENT at INFO, and this is the guard that keeps it so.

    Emanon folds log levels into a health verdict and shows the last 40 lines
    (emanon._TAIL_LINES). This job runs unattended for hours, so roughly three
    INFO lines a song would flush every real failure out of the one surface a
    human looks at — which is exactly the surface Auto-DJ most needs when it
    misbehaves overnight. CONVENTIONS §5 states the rule; this states the
    consequence.

    The record of what played is Euterpe's ledger, not felhaven.log. That is
    what makes the silence affordable rather than a loss.
    """

    def setUp(self):
        super().setUp()
        self._seed("Artist - Prior")
        awen._enabled = True
        self.addCleanup(mock.patch.stopall)

    def _quiet_cycle(self, pick="Newcomer - Fresh Track"):
        """A whole good turn: observe a song, see it end, pick, play, hear it
        start, and introduce it.

        The pick is a parameter because the intro is released when the song
        becomes AUDIBLE, which also files it in _session_plays — so a second
        cycle asking for the same song is correctly refused by the no-repeat
        guard, and would not be a good turn at all."""
        artist, _, track = pick.partition(" - ")

        def fake_ask(message, **kwargs):
            kwargs["tool_overrides"]["play_music"](pick)
            return f"Here's {track} by {artist}."

        with mock.patch("awen.morpheus.search",
                        return_value=[{"title": pick,
                                       "channel": artist, "duration": 240,
                                       "url": "https://youtu.be/aaaaaaaaaaa"}]), \
                mock.patch("awen.morpheus.play"), \
                mock.patch("awen.pythia.ask", side_effect=fake_ask):
            self._play_then_end()
        # The tick that releases the held line — the turn itself says nothing.
        return self._tick(_ours(pick, "https://youtu.be/aaaaaaaaaaa"))

    def test_a_whole_good_turn_writes_nothing_at_INFO_or_above(self):
        payload = self._quiet_cycle()
        self.assertEqual(payload["kind"], "intro")      # it really did work
        # Re-run under the assertion, so the check is on a turn we know succeeds
        # rather than on one that was quiet because it did nothing. A DIFFERENT
        # song, because the first is in _session_plays now that the intro waits
        # for the song to be audible, and the guard would rightly refuse it.
        awen._playing_title = None
        with self.assertNoLogs("METIS.awen", level="INFO"):
            self._quiet_cycle("Another - Second Song")

    def test_the_idle_ticks_between_songs_are_silent_too(self):
        """The common case by far: nothing playing, nothing owed, every 2s.

        pythia.ask is stubbed even though this path should never reach it — if
        the trigger ever regresses, an un-stubbed test would quietly start
        talking to the real Ollama instead of failing."""
        with mock.patch("awen.pythia.ask", return_value="") as ask:
            with self.assertNoLogs("METIS.awen", level="INFO"):
                for _ in range(10):
                    self._tick(_IDLE)
                    self._tick(_PLAYING)
                    self._tick(_PAUSED)
            ask.assert_not_called()

    def test_a_deliberate_stop_is_silent(self):
        with self.assertNoLogs("METIS.awen", level="INFO"):
            self._tick(_PLAYING)
            self._tick(_STOPPED)

    def test_a_load_failure_is_silent_HERE(self):
        """Not because it does not matter — because Morpheus already logs that
        failure at ERROR and owns it. Reporting it twice would double-count one
        fault in Emanon's verdict."""
        with self.assertNoLogs("METIS.awen", level="INFO"):
            self._tick(_PLAYING)
            self._tick(_FAILED)

    def test_a_single_failed_turn_is_silent(self):
        """One turn that played nothing is recoverable — it re-picks next time.
        The breaker is what earns a verdict, not each attempt."""
        with self.assertNoLogs("METIS.awen", level="INFO"):
            with mock.patch("awen.pythia.ask", return_value="sorry"):
                self._play_then_end()
        self.assertEqual(awen._failures, 1)

    def test_the_breaker_STILL_reaches_Emanon(self):
        """The one thing that must not be quietened: Auto-DJ giving up."""
        with mock.patch("awen.pythia.ask", return_value="sorry"):
            self._play_then_end()
            self._play_then_end()
            with self.assertLogs("METIS.awen", level="WARNING") as caught:
                self._play_then_end()
        self.assertEqual(len(caught.output), 1)
        self.assertIn("Auto-DJ is off", caught.output[0])

    def test_a_failed_tick_STILL_reaches_Emanon(self):
        with mock.patch("awen.morpheus.last_snapshot",
                        side_effect=RuntimeError("pipe gone")):
            with self.assertLogs("METIS.awen", level="ERROR"):
                awen.fetch()

    def test_the_module_holds_no_info_call_at_all(self):
        """Belt and braces on the source itself. The behavioural tests above
        only cover the paths they walk; a new INFO line on a path nobody
        exercises would slip past them and still cost Emanon a row."""
        import inspect
        source = inspect.getsource(awen)
        self.assertNotIn("log.info(", source)


class TestStatusLine(_AwenCase):
    """The label that fills the silent gap.

    A DJ turn measured 4.2s typically and 17.1s at worst, and until this the
    screen said nothing for that whole stretch — the song stopped and eventually
    something else started. That is indistinguishable from Auto-DJ being broken,
    which is the wrong conclusion to invite about a job nobody is watching.
    """

    def test_silent_when_auto_dj_is_off(self):
        self.assertEqual(awen.status_line(), "")

    def test_silent_when_merely_enabled_mid_song(self):
        """The toggle's amber lamp already says "Auto-DJ is on". A standing
        label would compete with the chat's own messages for a fact that is
        already on screen."""
        self._seed("Artist - Prior")
        awen._enabled = True
        self._tick(_PLAYING)
        self.assertEqual(awen.status_line(), "")

    def test_it_names_the_wait_when_a_pick_is_owed(self):
        """Without this, waiting for narration to finish looks exactly like
        being broken."""
        self._seed("Artist - Prior")
        awen._enabled = True
        with mock.patch("awen.harmonia.is_playing", return_value=True):
            self._play_then_end()
        self.assertIn("waiting", awen.status_line())

    def test_it_names_the_turn_while_picking(self):
        seen = []

        def fake_ask(message, **kwargs):
            seen.append(awen.status_line())
            return ""

        self._seed("Artist - Prior")
        awen._enabled = True
        with mock.patch("awen.pythia.ask", side_effect=fake_ask):
            self._play_then_end()
        self.assertEqual(len(seen), 1)
        self.assertIn("choosing", seen[0])

    def test_the_picking_label_never_outlives_the_turn(self):
        """A stuck "choosing the next song…" would read as a hang forever, so
        the flag is cleared in a finally rather than on the happy path."""
        self._seed("Artist - Prior")
        awen._enabled = True
        with mock.patch("awen.pythia.ask", side_effect=RuntimeError("boom")):
            with self.assertLogs("METIS.awen", level="ERROR"):
                self._play_then_end()
        self.assertFalse(awen._picking)
        self.assertEqual(awen.status_line(), "")

    def test_picking_outranks_waiting(self):
        """Both can be true for an instant. The turn is the specific one."""
        awen._enabled = True
        awen._want_pick = True
        awen._picking = True
        self.assertIn("choosing", awen.status_line())

    def test_it_costs_nothing_to_ask(self):
        """Called from a 60 ms chain, so it must not touch mpv, the ledger or
        the model — the morpheus.is_audible() bargain."""
        awen._enabled = True
        awen._want_pick = True
        with mock.patch("awen.morpheus.last_snapshot",
                        side_effect=AssertionError("status_line did IPC")), \
                mock.patch("awen.euterpe.entries",
                           side_effect=AssertionError("status_line read the ledger")), \
                mock.patch("awen.pythia.ask",
                           side_effect=AssertionError("status_line asked the model")):
            self.assertTrue(awen.status_line())


class TestKairosRegistration(unittest.TestCase):
    def test_awen_is_a_registered_worker(self):
        from kairos import Kairos
        names = [name for name, _, _ in Kairos.WORKERS]
        self.assertIn("awen", names)

    def test_its_dotted_path_resolves(self):
        from kairos import Kairos
        dotted = next(d for n, _, d in Kairos.WORKERS if n == "awen")
        self.assertEqual(dotted, "awen.fetch")
        import importlib
        module = importlib.import_module("awen")
        self.assertTrue(callable(getattr(module, "fetch")))

    def test_it_polls_no_faster_than_the_snapshot_it_reads(self):
        """It reads morpheus's CACHED snapshot, so polling faster than morpheus
        would only re-read the same tick twice."""
        from kairos import Kairos
        intervals = {n: i for n, i, _ in Kairos.WORKERS}
        self.assertGreaterEqual(intervals["awen"], intervals["morpheus"])


class TestTheRiderIsRegistered(unittest.TestCase):
    """Awen returns a payload; if nobody is registered under "awen", Kairos
    drops it on the floor and Auto-DJ plays songs in total silence.

    That failure has no symptom except silence, which is the shape this stack
    keeps getting bitten by (CONVENTIONS §12 on HomePanel._poll), so it gets a
    guard rather than trust.

    Asserted against felhaven.py's SOURCE rather than by building the app:
    constructing Felhaven needs a real Tk root, every panel, and the operator's
    own config. The registration is one line and reading it is honest — what
    would be dishonest is a test that only proves the line parses.
    """

    def _register_calls(self):
        import ast
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "felhaven.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register_panel"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                names.append(node.args[0].value)
        return names

    def test_felhaven_registers_something_under_awen(self):
        self.assertIn("awen", self._register_calls(),
                      "felhaven.py registers no panel under 'awen' — Kairos "
                      "would drop every DJ payload and Auto-DJ would be silent")

    def test_every_kairos_worker_has_a_consumer(self):
        """Lockstep, not a list: WORKERS and felhaven's registrations are two
        live sources. A worker nobody consumes is either a missing registration
        or a worker that should not exist."""
        from kairos import Kairos
        workers = {name for name, _, _ in Kairos.WORKERS}
        registered = set(self._register_calls())
        self.assertEqual(workers - registered, set(),
                         "these workers produce data nobody receives")

    def test_home_panel_exposes_the_rider_felhaven_reaches_for(self):
        """felhaven says `self._views["felhaven"].dj_rider`. This is the other
        half of that name."""
        from panels.home_panel import HomePanel, DjRider
        self.assertTrue(hasattr(HomePanel, "dj_says"))
        self.assertTrue(hasattr(DjRider, "update"))


class TestNotABrainTool(unittest.TestCase):
    """Auto-DJ is a job, not a tool. If it were registered, chat Pythia could
    turn it on and the model would be scheduling its own unprompted work."""

    def test_awen_exposes_no_tool_definition(self):
        self.assertFalse(hasattr(awen, "TOOL_DEFINITION"))
        self.assertFalse(hasattr(awen, "TOOL_DEFINITIONS"))

    def test_awen_is_not_in_the_registry(self):
        import pythia
        self.assertNotIn(awen, pythia._TOOL_MODULES)


if __name__ == "__main__":
    unittest.main()
