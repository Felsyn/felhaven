"""
test_euterpe.py — the song ledger.

Euterpe exists to answer two questions for a job nobody is watching: "have we
heard this recently?" and "what does the listener actually like?". Both are
guards against a small model, so both have to hold when the model is wrong, not
only when it is right.

What earns a test here beyond the CRUD:

  * NORMALIZATION, because it is the whole no-repeat rule. YouTube ships one
    song under many uploads — a single measured search for "Fleetwood Mac
    Dreams" returned it five times under five different titles — and an
    exact-title check passes every one of them. The cases below are those real
    titles, not invented ones.
  * The DURATION rule, because it is the difference between a song ledger and a
    log of every hour-long lore video Morpheus has ever played.
  * DEGRADED reads (§10): a missing file, a corrupt file, a file whose root is
    the wrong type, a row that is not an object. This is written from a Kairos
    worker nobody is looking at, so every one of those must read as an empty
    history and never raise.
  * The config/code LOCKSTEP (§10): the shipped JSON and _DEFAULTS are two live
    sources and are asserted against each other, never a hardcoded number.

Fully hermetic: _LEDGER_PATH and _CONFIG_PATH are redirected into a temp dir for
every case, so nothing here can read or write the running app's real ledger.

Run from the package root:
    python -X utf8 -m unittest tests.test_euterpe
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import euterpe

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHIPPED_CONFIG = os.path.join(_APP_ROOT, "config", "euterpe_config.json")

# Real YouTube titles for ONE song, captured 2026-09-16 from a single
# `yt-dlp ytsearch5:"Fleetwood Mac Dreams"` — the exact case normalization
# exists for. Captured rather than invented, per §10: a hand-written set would
# agree with whatever the regex happens to do.
_DREAMS = [
    ("Fleetwood Mac - Dreams (Official Music Video) [4K]", "Fleetwood Mac", 264),
    ("Dreams (2004 Remaster)", "Fleetwood Mac", 258),
    ("Fleetwood Mac - Dreams (Official Audio)", "Fleetwood Mac", 258),
    ("Fleetwood Mac - Dreams (Lyrics)", "7clouds", 258),
    ("Fleetwood Mac - Dreams (Official Vinyl Video)", "RHINO", 268),
]


class _EuterpeCase(unittest.TestCase):
    """Redirect both files into a temp dir. Every case, not just the writing
    ones: settings() reads config on nearly every call, and a test that quietly
    read the operator's real tuning would pass or fail on their JSON."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = (euterpe._LEDGER_PATH, euterpe._CONFIG_PATH)
        euterpe._LEDGER_PATH = os.path.join(self._tmp.name, "euterpe_ledger.json")
        euterpe._CONFIG_PATH = os.path.join(self._tmp.name, "euterpe_config.json")
        self.addCleanup(self._restore)

    def _restore(self):
        euterpe._LEDGER_PATH, euterpe._CONFIG_PATH = self._saved

    def _write_config(self, **values):
        with open(euterpe._CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(values, handle)

    def _write_raw_ledger(self, text):
        with open(euterpe._LEDGER_PATH, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _play(self, title, **kwargs):
        kwargs.setdefault("duration", 240)
        return euterpe.record(title, **kwargs)


class TestNormalize(_EuterpeCase):
    """One song, one key, whatever upload it arrived as."""

    def test_all_five_real_uploads_of_one_song_share_a_key(self):
        keys = {euterpe.normalize(title, channel) for title, channel, _ in _DREAMS}
        self.assertEqual(len(keys), 1, f"expected one key, got {keys}")
        self.assertEqual(keys.pop(), "fleetwood mac - dreams")

    def test_the_artist_comes_from_the_channel_when_the_title_omits_it(self):
        """The measured case that made this rule necessary: one upload of five
        was titled just "Dreams (2004 Remaster)". Without the channel it keys as
        "dreams", matches none of its siblings, and the song plays twice."""
        self.assertEqual(euterpe.normalize("Dreams (2004 Remaster)"), "dreams")
        self.assertEqual(
            euterpe.normalize("Dreams (2004 Remaster)", "Fleetwood Mac"),
            "fleetwood mac - dreams")

    def test_a_channel_never_overwrites_an_artist_the_title_already_has(self):
        """7clouds is a lyrics channel, not the artist. The title already says
        who made the song, so the channel must stay out of the key."""
        self.assertEqual(
            euterpe.normalize("Fleetwood Mac - Dreams (Lyrics)", "7clouds"),
            "fleetwood mac - dreams")

    def test_topic_and_vevo_channel_furniture_is_dropped(self):
        for channel in ("Fleetwood Mac - Topic", "FleetwoodMacVEVO",
                        "Fleetwood Mac Official"):
            with self.subTest(channel=channel):
                self.assertTrue(
                    euterpe.normalize("Dreams", channel).endswith("dreams"))

    def test_an_artist_already_leading_the_title_is_not_doubled(self):
        self.assertEqual(euterpe.normalize("Queen Bohemian Rhapsody", "Queen"),
                         "queen - bohemian rhapsody")

    def test_square_brackets_go_wholesale(self):
        self.assertEqual(euterpe.normalize("Song [4K] [Official Video]"), "song")

    def test_parentheses_survive_unless_they_hold_junk(self):
        """The guard against over-stripping: a title whose own name uses
        brackets must come through intact."""
        self.assertEqual(
            euterpe.normalize("(I Can't Get No) Satisfaction"),
            "(i can't get no) satisfaction")

    def test_a_remix_or_version_is_deliberately_a_different_song(self):
        """Conservative on purpose — an acoustic version is a different
        recording, not a different upload. Over-stripping hides a song from the
        DJ forever, which is worse than an occasional missed repeat."""
        self.assertNotEqual(euterpe.normalize("Dreams (Acoustic Version)"),
                            euterpe.normalize("Dreams"))
        self.assertNotEqual(euterpe.normalize("Dreams (Kygo Remix)"),
                            euterpe.normalize("Dreams"))

    def test_dash_variants_all_fold_together(self):
        keys = {euterpe.normalize(f"Artist {dash} Song")
                for dash in ("-", "–", "—", "|", "·")}
        self.assertEqual(len(keys), 1, keys)

    def test_a_title_that_is_entirely_junk_keys_as_nothing(self):
        """"" must read as "no key", never as a key that matches everything."""
        self.assertEqual(euterpe.normalize("[Official Video]"), "")

    def test_it_never_raises_on_junk_input(self):
        for value in ("", None, "   ", "((("):
            with self.subTest(value=value):
                self.assertIsInstance(euterpe.normalize(value), str)


class TestOneIdentityPerSong(_EuterpeCase):
    """same_song() moved the comparison off the stored key in 2026-09-17, and
    duplicates kept arriving anyway. These are the four ways one song still had
    two identities, all found by looking at the live ledger rather than by
    reasoning about titles.

    The one that was actually on disk is case, not order. Both rows below were
    in `euterpe_ledger.json` on 2026-09-18, four hours apart, and both reached
    the model's do-not-repeat list as separate songs.
    """

    def _seed_rows(self, rows):
        """Write rows with EXACT keys straight to the ledger file.

        Not _play(): record() folds the key through normalize(), so it cannot
        produce the capitalised key that is actually on disk. That key got there
        by being typed into TUNES, and replace_all() does not re-derive keys —
        which is the whole reason the duplicate survived. Reproducing it needs
        the same door it came through."""
        existing = euterpe.entries()
        for row in rows:
            existing.append({"title": row["title"], "key": row["key"],
                             "video_id": None, "url": "", "channel": "",
                             "duration": 240, "source": "user",
                             "at": "2026-09-17T10:17:19"})
        self._write_raw_ledger(json.dumps({"plays": existing}))

    # ── case ────────────────────────────────────────────────────────────────
    def test_the_same_song_in_two_CASES_is_one_song(self):
        """The live duplicate. _halves() sorted and compared raw strings, so a
        capitalised key and its own folded form were two songs.

        record() cannot produce a capitalised key — normalize() folds. It was
        typed into TUNES, and replace_all() deliberately does not re-derive
        keys, so the file kept it and _validate() accepted it."""
        self.assertTrue(euterpe.same_song(
            "Cage The Elephant - ain't no rest for the wicked",
            "cage the elephant - ain't no rest for the wicked"))

    def test_case_and_ORDER_together_are_still_one_song(self):
        self.assertTrue(euterpe.same_song("Linkin Park - In The End",
                                          "in the end - linkin park"))

    def test_two_genuinely_different_songs_are_still_two(self):
        """The fold must not collapse anything real. This is the guard on the
        guard: a comparison that says yes to everything would pass every other
        test in this class."""
        self.assertFalse(euterpe.same_song("queen - bohemian rhapsody",
                                           "queen - radio ga ga"))

    # ── the two `not in` holes ──────────────────────────────────────────────
    def _duplicate_pair(self):
        self._seed_rows([
            {"title": "Cage The Elephant - Ain't No Rest for the Wicked",
             "key": "Cage The Elephant - ain't no rest for the wicked"},
            {"title": "Cage The Elephant - Ain't No Rest For The Wicked (Official Video)",
             "key": "cage the elephant - ain't no rest for the wicked"},
        ])

    def test_the_do_not_repeat_list_names_a_song_ONCE(self):
        """recent_titles() de-duped with `key not in seen` — plain list
        membership, bypassing same_song() entirely. This is the list the model
        reads, and it is how the duplicate reached the user."""
        self._duplicate_pair()
        titles = euterpe.recent_titles()
        self.assertEqual(len(titles), 1, titles)

    def test_the_taste_anchor_does_not_spend_two_slots_on_one_song(self):
        """Same hole, second site. Five slots decide what the DJ matches
        against; a duplicate pair narrows the taste by 20% for free."""
        self._seed_rows([{"title": "Artist %d - Song" % n,
                          "key": "artist %d - song" % n} for n in range(4)])
        self._duplicate_pair()
        anchor = euterpe.taste_anchor()
        self.assertEqual(len(anchor), len(set(anchor)), anchor)
        self.assertEqual(sum(1 for a in anchor if "elephant" in a.casefold()), 1,
                         anchor)

    # ── the dash suffix, which is the Topic-channel form ────────────────────
    def test_a_REMASTERED_dash_suffix_does_not_become_half_the_song(self):
        """_JUNK_WORDS has named `remaster` since the beginning, but only
        _PAREN_JUNK consumes it — so it fires inside parentheses and nowhere
        else. YouTube Music's "- Topic" channels title things with a DASH, and
        that form is doubly damaging: the dash also makes _clean() return
        before the channel-derived artist is applied.

        This matters more since 2026-09-18, because the DJ now PREFERS
        Topic-channel results as music evidence — so the picks the new
        validation favours were the ones keying worst."""
        self.assertEqual(
            euterpe.normalize("Bohemian Rhapsody - Remastered 2011", "Queen"),
            "queen - bohemian rhapsody")

    def test_a_LIVE_dash_suffix_goes_the_same_way(self):
        self.assertEqual(euterpe.normalize("Bother - Live at Rock in Rio",
                                           "Stone Sour"),
                         "stone sour - bother")

    def test_the_remastered_upload_is_the_same_song_as_the_plain_one(self):
        """The point of all of it: two uploads, one song."""
        self.assertTrue(euterpe.same_song(
            euterpe.normalize("Bohemian Rhapsody - Remastered 2011", "Queen"),
            euterpe.normalize("Queen - Bohemian Rhapsody")))

    def test_a_real_dash_in_a_TITLE_is_left_alone(self):
        """The cost of the rule, stated: only the named junk words are stripped
        from a dash tail. A song whose title genuinely has three parts keeps
        them."""
        self.assertEqual(euterpe.normalize("Godspeed You - Black Emperor - Storm"),
                         "godspeed you - black emperor - storm")

    def test_a_VERSION_is_still_a_different_recording(self):
        """The line this nearly crossed. _JUNK_WORDS excludes `version` and
        `remix` on purpose — an acoustic version is a different RECORDING, not
        another upload of the same one — so the dash list must not strip what
        the parenthesis list is documented to keep.

        For one afternoon it did, and only for the dash spelling, which
        recreated the exact asymmetry _FEAT_TAIL exists to remove."""
        plain = euterpe.normalize("Dreams")
        for title in ("Dreams - Acoustic Version", "Dreams - Radio Edit",
                      "Dreams - Single Version", "Dreams - Extended Mix"):
            with self.subTest(title=title):
                self.assertNotEqual(euterpe.normalize(title), plain)

    def test_both_spellings_of_a_version_agree_with_each_other(self):
        """Whatever the rule is, the dash form and the paren form must reach
        the same verdict. Disagreeing is the bug, in either direction."""
        self.assertEqual(
            euterpe.same_song(euterpe.normalize("Dreams - Acoustic Version"),
                              euterpe.normalize("Dreams")),
            euterpe.same_song(euterpe.normalize("Dreams (Acoustic Version)"),
                              euterpe.normalize("Dreams")))

    def test_a_key_stored_BEFORE_this_rule_does_not_match_after_it(self):
        """The cost of not re-keying, made explicit. A Topic upload keyed
        before 2026-09-19 reads "bohemian rhapsody - remastered 2011"; the same
        upload keys as "queen - bohemian rhapsody" now, and the two do not
        match. That song can be played once more and leaves a stale twin for
        the operator to delete.

        Asserted so nobody "fixes" it by loosening same_song() into an overlap
        or subset test, which would start matching songs that share one word."""
        self.assertFalse(euterpe.same_song("bohemian rhapsody - remastered 2011",
                                           "queen - bohemian rhapsody"))

    # ── feat., whichever bracket YouTube reached for ────────────────────────
    def test_every_FEAT_form_gives_one_key(self):
        """[feat. X] was stripped wholesale by _BRACKET_JUNK while (feat. X)
        and a bare ft. X survived — so the same song had two identities decided
        by which bracket the uploader used."""
        plain = euterpe.normalize("Linkin Park - Numb")
        for title in ("Linkin Park - Numb [feat. Jay-Z]",
                      "Linkin Park - Numb (feat. Jay-Z)",
                      "Linkin Park - Numb ft. Jay-Z",
                      "Linkin Park - Numb featuring Jay-Z"):
            with self.subTest(title=title):
                self.assertEqual(euterpe.normalize(title), plain)


class TestSameSong(_EuterpeCase):
    """Order across the dash is not identity.

    YouTube ships one song both ways round and normalize() cannot tell which
    half is the artist. Measured 2026-09-17 from a real mpv log: the DJ played
    'Linkin Park - In The End (Official Music Video)' and then 'In The End
    [Official HD Music Video] - Linkin Park'. Equality on the keys said those
    were two different songs, so the guard let the second one through.
    """

    _REVERSED = [
        ("Linkin Park - In The End (Official Music Video)",
         "In The End [Official HD Music Video] - Linkin Park", "Linkin Park"),
        ("Radiohead - Creep", "Creep - Radiohead", "Radiohead"),
        ("Soundgarden - Black Hole Sun", "Black Hole Sun - Soundgarden",
         "Soundgarden"),
    ]

    def test_the_same_song_either_way_round_is_one_song(self):
        for canonical, reversed_, channel in self._REVERSED:
            with self.subTest(song=canonical):
                self.assertTrue(euterpe.same_song(
                    euterpe.normalize(canonical, channel),
                    euterpe.normalize(reversed_, channel)))

    def test_the_stored_keys_still_differ__the_COMPARISON_moved(self):
        """Deliberate: keys keep the order they arrived in, so TUNES and the
        model's do-not-repeat list read like titles, and nothing on disk needed
        re-keying."""
        self.assertNotEqual(euterpe.normalize("Radiohead - Creep"),
                            euterpe.normalize("Creep - Radiohead"))

    def test_two_different_songs_by_one_artist_are_not_the_same(self):
        self.assertFalse(euterpe.same_song("linkin park - numb",
                                           "linkin park - faint"))

    def test_two_unrelated_songs_are_not_the_same(self):
        self.assertFalse(euterpe.same_song("radiohead - creep",
                                           "soundgarden - black hole sun"))

    def test_a_key_with_no_dash_still_compares(self):
        self.assertTrue(euterpe.same_song("505", "505"))
        self.assertFalse(euterpe.same_song("505", "creep"))

    def test_an_empty_key_matches_nothing(self):
        """A blank key must never be a match-everything — that would refuse the
        DJ every song it proposed."""
        self.assertFalse(euterpe.same_song("", "radiohead - creep"))
        self.assertFalse(euterpe.same_song("radiohead - creep", ""))
        self.assertFalse(euterpe.same_song("", ""))

    def test_the_guard_refuses_the_reversed_upload(self):
        self._play("Linkin Park - In The End (Official Music Video)",
                   channel="Linkin Park")
        self.assertTrue(euterpe.was_recently_played(
            "In The End [Official HD Music Video] - Linkin Park",
            channel="Linkin Park"))

    def test_record_moves_the_reversed_upload_rather_than_adding_it(self):
        self._play("Radiohead - Creep", channel="Radiohead")
        self._play("Creep - Radiohead", channel="Radiohead")
        self.assertEqual(len(euterpe.entries()), 1)

    def test_replace_all_refuses_the_reversed_duplicate_quoting_both(self):
        """Quoted as TYPED, because that is what has to be found in the box."""
        error = euterpe.replace_all(json.dumps({"plays": [
            {"title": "Radiohead - Creep", "key": "radiohead - creep",
             "video_id": None, "url": "", "channel": "", "duration": 240.0,
             "source": "user", "at": "2026-09-17T09:00:00"},
            {"title": "Creep - Radiohead", "key": "creep - radiohead",
             "video_id": None, "url": "", "channel": "", "duration": 240.0,
             "source": "user", "at": "2026-09-17T09:01:00"},
        ]}))
        self.assertEqual(error, "duplicate_row: plays 1 and 2 are "
                                "'radiohead - creep' and 'creep - radiohead'")


class TestPretty(_EuterpeCase):
    """The display name: normalize()'s rules, the title's own case."""

    def test_it_strips_the_junk_and_keeps_the_case(self):
        self.assertEqual(
            euterpe.pretty("In The End [Official HD Music Video] - Linkin Park"),
            "In The End - Linkin Park")

    def test_it_borrows_the_artist_from_the_channel_like_normalize(self):
        self.assertEqual(euterpe.pretty("Dreams (2004 Remaster)", "Fleetwood Mac"),
                         "Fleetwood Mac - Dreams")

    def test_it_agrees_with_normalize_on_every_measured_title(self):
        """The two share _clean() so they cannot drift — a display name that
        stripped differently from the key would show one song and file
        another. This asserts the property rather than the plumbing."""
        measured = [(title, channel) for title, channel, _ in _DREAMS]
        # The 2026-09-19 forms too. _DREAMS predates the dash-tail and feat.
        # rules and exercises neither, so without these the agreement could
        # break on exactly the titles those rules were written for — and the
        # tempting "fix" for a spoken name is to special-case it OUTSIDE
        # _clean(), which is the drift this test exists to forbid.
        measured += [("Bohemian Rhapsody - Remastered 2011", "Queen"),
                     ("Bother - Live at Rock in Rio", "Stone Sour"),
                     ("Linkin Park - Numb ft. Jay-Z", ""),
                     ("Linkin Park - Numb (feat. Jay-Z)", ""),
                     ("Dreams - Acoustic Version", "Fleetwood Mac")]
        for title, channel in measured:
            with self.subTest(title=title):
                self.assertEqual(euterpe.pretty(title, channel).casefold(),
                                 euterpe.normalize(title, channel))

    def test_a_title_that_is_all_junk_is_empty_either_way(self):
        self.assertEqual(euterpe.pretty("(Official Video)"), "")
        self.assertEqual(euterpe.normalize("(Official Video)"), "")


class TestVideoId(_EuterpeCase):
    def test_every_youtube_url_shape_yields_the_id(self):
        for url in ("https://www.youtube.com/watch?v=tCDvOQI3pco",
                    "https://youtu.be/tCDvOQI3pco",
                    "https://www.youtube.com/embed/tCDvOQI3pco",
                    "https://www.youtube.com/shorts/tCDvOQI3pco",
                    "https://www.youtube.com/watch?v=tCDvOQI3pco&list=xyz"):
            with self.subTest(url=url):
                self.assertEqual(euterpe.video_id(url), "tCDvOQI3pco")

    def test_no_url_and_no_id_are_both_None(self):
        for url in (None, "", "https://example.com/song.mp3"):
            with self.subTest(url=url):
                self.assertIsNone(euterpe.video_id(url))


class TestRecord(_EuterpeCase):
    def test_a_play_round_trips(self):
        entry = self._play("Fleetwood Mac - Dreams (Official Audio)",
                           channel="Fleetwood Mac",
                           url="https://youtu.be/tCDvOQI3pco", duration=258)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["key"], "fleetwood mac - dreams")
        self.assertEqual(entry["video_id"], "tCDvOQI3pco")
        self.assertEqual(entry["source"], "user")
        self.assertEqual(euterpe.entries()[-1], entry)

    def test_the_source_defaults_to_the_listener_not_the_dj(self):
        """The safe default: a play whose origin nobody stated is Matthew's, so
        it feeds the taste anchor. Guessing "dj" would let the model's own
        choices quietly become the taste."""
        self.assertEqual(self._play("Song")["source"], "user")

    def test_an_unknown_source_is_refused(self):
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            self.assertIsNone(self._play("Song", source="robot"))
        self.assertEqual(euterpe.entries(), [])

    def test_a_blank_title_is_refused(self):
        for title in ("", "   ", "[Official Video]"):
            with self.subTest(title=title):
                self.assertIsNone(self._play(title))
        self.assertEqual(euterpe.entries(), [])

    def test_the_log_is_bounded_and_keeps_the_NEWEST(self):
        with mock.patch.object(euterpe, "_MAX_ENTRIES", 5):
            for i in range(12):
                self._play(f"Artist - Song {i}")
        rows = euterpe.entries()
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1]["title"], "Artist - Song 11")
        self.assertEqual(rows[0]["title"], "Artist - Song 7")

    def test_a_failed_write_returns_None_and_does_not_raise(self):
        """Written from a Kairos worker with nobody watching — a disk problem
        must reach the log, never the tick. _save() owns the log line; what
        record() owes its caller is an honest None and an unchanged ledger, so
        a caller that trusted the return value is never told a play was stored
        when it was not."""
        self._play("Artist - First")
        before = euterpe.entries()
        with mock.patch.object(euterpe, "_save", return_value=False) as save:
            self.assertIsNone(self._play("Artist - Second"))
        save.assert_called_once()
        self.assertEqual(euterpe.entries(), before)

    def test_an_unwritable_directory_is_logged_not_raised(self):
        with mock.patch("tools.euterpe.tempfile.mkstemp",
                        side_effect=OSError("read-only")):
            with self.assertLogs("METIS.euterpe", level="ERROR") as caught:
                self.assertIsNone(self._play("Artist - Song"))
        self.assertTrue(any("ledger" in line for line in caught.output))


class TestOneRowPerSong(_EuterpeCase):
    """Playing something already on the list moves it; it does not copy it.

    Note what this does NOT change, so nobody tests for it and is misled:
    taste_anchor() and recent_titles() both de-duplicate before they answer, so
    the anchor reads the same either way. What a duplicate actually costs is a
    line in the editor and a slot against _MAX_ENTRIES — and the second of those
    can push a real song off the list, which is the case pinned below.
    """

    def test_replaying_a_song_moves_its_row_instead_of_adding_one(self):
        self._play("Artist - First")
        self._play("Artist - Second")
        self._play("Artist - First")
        self.assertEqual([r["key"] for r in euterpe.entries()],
                         ["artist - second", "artist - first"])

    def test_a_different_upload_of_the_same_song_is_the_same_row(self):
        """The whole reason normalize() exists. Identity is the key, never the
        video id — a second test on the id could only split a row the key had
        already joined."""
        self._play("Fleetwood Mac - Dreams (Official Music Video) [4K]",
                   url="https://youtu.be/aaaaaaaaaaa", channel="Fleetwood Mac")
        self._play("Dreams (2004 Remaster)",
                   url="https://youtu.be/bbbbbbbbbbb", channel="Fleetwood Mac")
        rows = euterpe.entries()
        self.assertEqual(len(rows), 1, [r["title"] for r in rows])
        self.assertEqual(rows[0]["video_id"], "bbbbbbbbbbb",
                         "the row kept the OLD upload's metadata")

    def test_a_genuinely_different_song_still_appends(self):
        self._play("Artist - First")
        self._play("Artist - Second")
        self.assertEqual(len(euterpe.entries()), 2)

    def test_replaying_one_song_cannot_push_another_off_the_list(self):
        """The cost a duplicate really has. With rows spent on copies, the cap
        trims from the front — so playing your favourite twice would quietly
        drop the oldest song you actually like."""
        with mock.patch.object(euterpe, "_MAX_ENTRIES", 3):
            for title in ("Artist - First", "Artist - Second", "Artist - Third"):
                self._play(title)
            self._play("Artist - Third")
            self._play("Artist - Third")
        self.assertEqual([r["key"] for r in euterpe.entries()],
                         ["artist - first", "artist - second", "artist - third"])

    def test_the_moved_row_carries_the_new_play_not_the_old_one(self):
        self._play("Artist - Song", url="https://youtu.be/aaaaaaaaaaa",
                   channel="Old Channel", duration=200)
        self._play("Artist - Song", url="https://youtu.be/bbbbbbbbbbb",
                   channel="New Channel", duration=300)
        row = euterpe.entries()[0]
        self.assertEqual(row["channel"], "New Channel")
        self.assertEqual(row["duration"], 300)

    def test_a_declined_replay_leaves_the_existing_row_alone(self):
        """A play that is not a song must not take the old row down with it —
        record() declines BEFORE it touches the list, and this is the ordering
        that proves it."""
        self._play("Artist - Song")
        before = euterpe.entries()
        self.assertIsNone(self._play("Artist - Song", duration=5))
        self.assertEqual(euterpe.entries(), before)


class TestDurationRule(_EuterpeCase):
    """The same rule Awen's too_long guard applies to a pick, deliberately."""

    def test_an_ordinary_song_is_recorded(self):
        self.assertIsNotNone(self._play("Artist - Song", duration=258))

    def test_something_over_the_cap_is_excluded(self):
        """Morpheus also plays hour-long lore videos. Those must never become
        priors that steer a DJ's taste."""
        cap = euterpe.settings()["max_song_seconds"]
        self.assertIsNone(self._play("A Very Long Lore Video", duration=cap + 1))
        self.assertIsNotNone(self._play("Artist - Song", duration=cap))

    def test_no_duration_at_all_is_excluded(self):
        """Measured 2026-09-16: yt-dlp reports duration None for a live stream.
        A stream never ends, so it is not a song and cannot be capped."""
        self.assertIsNone(self._play("lofi hip hop radio", duration=None))

    def test_a_non_numeric_duration_is_excluded_rather_than_crashing(self):
        self.assertIsNone(self._play("Artist - Song", duration="four minutes"))

    def test_zero_and_negative_durations_are_excluded(self):
        for bad in (0, -5):
            with self.subTest(duration=bad):
                self.assertIsNone(self._play("Artist - Song", duration=bad))

    def test_the_cap_is_read_from_config_not_hardcoded(self):
        self._write_config(min_song_seconds=1, max_song_seconds=60)
        self.assertIsNone(self._play("Artist - Song", duration=61))
        self.assertIsNotNone(self._play("Artist - Song", duration=59))

    def test_the_floor_is_read_from_config_not_hardcoded(self):
        self._write_config(min_song_seconds=120)
        self.assertIsNone(self._play("Artist - Song", duration=119))
        self.assertIsNotNone(self._play("Artist - Song", duration=121))


class TestMusicEvidence(_EuterpeCase):
    """A title and a duration could not catch the chainsaw.

    A video about sharpening a chainsaw contains none of the fifteen denylist
    phrases and four minutes sits comfortably inside the 90-900s band, so it
    passed every check there was, played in full, and was announced as Linkin
    Park. The denylist could never have caught it either: it can only ban what
    has already burned somebody, and the next offender is about something else.

    So the judgment grew a third argument rather than a sibling function. Two
    properties hold, and the tests below exist to stop either being simplified
    away: without details the answer is byte-identical to before, and with them
    details can only ever ADD a refusal.
    """

    _CHAINSAW = "Sharpening a chainsaw chain the right way"
    _HOWTO = {"categories": ["Howto & Style"], "channel": "Chainsaw Dan"}
    _TOPIC = {"categories": ["Music"], "channel": "Linkin Park - Topic",
              "track": "In The End", "artist": "Linkin Park"}

    # ── the defect itself ───────────────────────────────────────────────────
    def test_the_chainsaw_passes_every_check_a_TITLE_can_make(self):
        """The starting position, asserted so the fix has something to be a fix
        OF. If this ever fails, the denylist grew a phrase and this test is
        measuring the wrong thing."""
        self.assertIsNone(euterpe.is_a_song(self._CHAINSAW, 240))

    def test_the_chainsaw_is_refused_once_its_CATEGORY_is_known(self):
        self.assertEqual(euterpe.is_a_song(self._CHAINSAW, 240, self._HOWTO),
                         "not_music_category")

    # ── details may only ADD a refusal ──────────────────────────────────────
    def test_music_metadata_cannot_APPROVE_a_track(self):
        """The tempting mutation. A video YouTube files under Music that runs
        thirty seconds is still too short — evidence is allowed to reject, never
        to override the rules that were already there."""
        self.assertEqual(euterpe.is_a_song("Artist - Song", 20, self._TOPIC),
                         "too_short")

    def test_an_existing_slug_is_never_CHANGED_by_details(self):
        """The category test runs last precisely so this holds: adding metadata
        must not make the same input refuse for a different reason."""
        self.assertEqual(
            euterpe.is_a_song("Moss Echo User Manual", 282, self._HOWTO),
            "not_music")

    def test_music_metadata_survives_a_bad_category(self):
        """Both halves are required. A real track miscategorised as Education
        is still a track, and dropping the second half of that condition is the
        simplification this test exists to catch."""
        details = {"categories": ["Education"], "track": "In The End",
                   "artist": "Linkin Park"}
        self.assertIsNone(euterpe.is_a_song("Artist - Song", 240, details))

    def test_a_category_that_is_NOT_on_the_denylist_passes(self):
        """Entertainment and People & Blogs are absent from the list on
        purpose: a great deal of real music lives in both, and flipping this
        into an allowlist would refuse most of it."""
        for name in ("Entertainment", "People & Blogs", "Film & Animation"):
            with self.subTest(category=name):
                self.assertIsNone(
                    euterpe.is_a_song("Artist - Song", 240,
                                      {"categories": [name]}))

    # ── degradation ─────────────────────────────────────────────────────────
    def test_a_failed_describe_falls_back_to_the_two_argument_answer(self):
        """One bad network call must not take the DJ off the air."""
        failed = {"error": "describe_failed"}
        self.assertIsNone(euterpe.is_a_song("Artist - Song", 240, failed))
        self.assertEqual(euterpe.is_a_song("Artist - Song", 20, failed),
                         "too_short")

    def test_details_that_are_not_a_dict_change_nothing(self):
        self.assertIsNone(euterpe.is_a_song("Artist - Song", 240, None))

    def test_categories_arriving_as_a_STRING_does_not_iterate_it(self):
        """"Music" as a bare string would match category "m", "u", "s"... and a
        denylisted name would match nothing. Neither is an answer."""
        details = {"categories": "Howto & Style"}
        self.assertIsNone(euterpe.is_a_song(self._CHAINSAW, 240, details))
        self.assertFalse(euterpe.has_music_evidence({"categories": "Music"}))

    def test_no_details_reproduces_every_existing_verdict(self):
        """The property the ledger depends on: _note_playing records from an
        mpv title and a duration and has no URL, so it will always call this
        with two arguments. If that answer ever moved, the ledger and the guard
        would start disagreeing about what a song is."""
        cases = [("Artist - Song", 240, None), ("Artist - Song", 20, "too_short"),
                 ("Artist - Song", None, "no_duration"),
                 ("Moss Echo User Manual", 282, "not_music")]
        for title, duration, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(euterpe.is_a_song(title, duration), expected)
                self.assertEqual(euterpe.is_a_song(title, duration, None),
                                 expected)

    # ── the evidence itself ─────────────────────────────────────────────────
    def test_the_music_category_is_evidence(self):
        self.assertTrue(euterpe.has_music_evidence({"categories": ["Music"]}))

    def test_a_track_and_artist_PAIR_is_evidence(self):
        self.assertTrue(euterpe.has_music_evidence(
            {"track": "In The End", "artist": "Linkin Park"}))

    def test_a_track_WITHOUT_an_artist_is_not(self):
        self.assertFalse(euterpe.has_music_evidence({"track": "In The End"}))
        self.assertFalse(euterpe.has_music_evidence(
            {"track": "In The End", "artist": "   "}))

    def test_a_topic_channel_is_evidence(self):
        """YouTube generates these from a music rights feed, so an upload on one
        is a track with near-certainty."""
        self.assertTrue(euterpe.has_music_evidence(
            {"channel": "Linkin Park - Topic"}))

    def test_VEVO_and_official_are_NOT_evidence(self):
        """_CHANNEL_JUNK strips all three when building a key, which is why the
        Topic pattern is a second regex and not a reuse of it: the other two say
        nothing about whether a video is music."""
        for channel in ("LinkinParkVEVO", "Linkin Park Official"):
            with self.subTest(channel=channel):
                self.assertFalse(euterpe.has_music_evidence({"channel": channel}))

    def test_a_failed_describe_is_NOT_evidence(self):
        self.assertFalse(euterpe.has_music_evidence({"error": "describe_failed"}))
        self.assertFalse(euterpe.has_music_evidence(None))

    def test_absence_of_evidence_is_never_evidence_of_absence(self):
        """False means "nothing here says music", never "this is not music".
        Live sets, covers and indie uploads routinely carry none of it, and a
        hard gate on this would starve the DJ of exactly the music worth
        finding."""
        plain = {"categories": ["Entertainment"], "channel": "Some Band"}
        self.assertFalse(euterpe.has_music_evidence(plain))
        self.assertIsNone(euterpe.is_a_song("Artist - Song", 240, plain))

    # ── config ──────────────────────────────────────────────────────────────
    def test_the_category_denylist_is_config(self):
        self._write_config(not_music_categories=["Comedy"])
        self.assertEqual(
            euterpe.is_a_song("Artist - Song", 240, {"categories": ["Comedy"]}),
            "not_music_category")
        self.assertIsNone(
            euterpe.is_a_song(self._CHAINSAW, 240, self._HOWTO))

    def test_an_empty_list_disables_it(self):
        """The same escape hatch not_music has, for the same reason: every
        denylist is eventually wrong about something."""
        self._write_config(not_music_categories=[])
        self.assertIsNone(euterpe.is_a_song(self._CHAINSAW, 240, self._HOWTO))

    def test_a_malformed_denylist_falls_back_to_the_default(self):
        self._write_config(not_music_categories="Education")
        self.assertEqual(euterpe.not_music_categories(),
                         euterpe._DEFAULT_NOT_MUSIC_CATEGORIES)


class TestIsASong(_EuterpeCase):
    """One judgment, shared by the ledger and by Awen's guard.

    The fixtures are REAL titles the DJ actually played on 2026-09-16, not
    invented ones — an evening that produced a gear manual, a hardware review, a
    documentary, a room-echo demo and an AI-art slideshow, every one of which
    passed the only guard that existed (a 15-minute cap).
    """

    _REAL_OFFENDERS = [
        ("Moss Echo User Manual", 282, "not_music"),
        ("An In-depth Review of Moss Air", 156, "not_music"),
        ("Grunge: A Brief History of a Brief Genre", 858, "not_music"),
        ("Hidden Messages | STATIC ft. Hatsune Miku REACTION", 713, "not_music"),
        ("Long Flutter Echo in Big Hall", 26, "too_short"),
        ("amoeba is nearby red blood cells but coffee with brown sugar",
         55, "too_short"),
    ]

    _REAL_SONGS = [
        ("Pink Floyd - Time (Official Audio)", 416),
        ("Korn - A.D.I.D.A.S. (Official Video)", 154),
        ("Alice In Chains - Nutshell (Official Audio)", 260),
        ("Radiohead - Creep", 237),
    ]

    def test_every_real_offender_is_caught(self):
        for title, duration, expected in self._REAL_OFFENDERS:
            with self.subTest(title=title[:36]):
                self.assertEqual(euterpe.is_a_song(title, duration), expected)

    def test_every_real_song_passes(self):
        for title, duration in self._REAL_SONGS:
            with self.subTest(title=title[:36]):
                self.assertIsNone(euterpe.is_a_song(title, duration))

    def test_a_live_stream_has_its_own_reason(self):
        self.assertEqual(euterpe.is_a_song("lofi hip hop radio", None),
                         "no_duration")

    def test_over_the_cap_is_too_long(self):
        cap = euterpe.settings()["max_song_seconds"]
        self.assertEqual(euterpe.is_a_song("Artist - Song", cap + 1), "too_long")

    def test_the_denylist_is_case_folded(self):
        self.assertEqual(euterpe.is_a_song("MOSS ECHO USER MANUAL", 280),
                         "not_music")

    def test_the_denylist_comes_from_config(self):
        """A denylist is eventually wrong about something, so the recourse has
        to be a JSON edit rather than a release (§4)."""
        self._write_config(not_music=["banjo"])
        self.assertEqual(euterpe.is_a_song("A Banjo Song", 200), "not_music")
        self.assertIsNone(euterpe.is_a_song("Moss Echo User Manual", 282))

    def test_an_empty_denylist_disables_it(self):
        """Explicitly empty is a real choice, distinct from absent — it is how
        an operator turns the feature off after it refuses one song too many."""
        self._write_config(not_music=[])
        self.assertIsNone(euterpe.is_a_song("Moss Echo User Manual", 282))

    def test_a_malformed_denylist_falls_back_rather_than_crashing(self):
        self._write_config(not_music="user manual")
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            self.assertEqual(euterpe.not_music_phrases(),
                             euterpe._DEFAULT_NOT_MUSIC)

    def test_a_denied_title_is_not_recorded_either(self):
        """The rule has to be the same in both directions. A thing the DJ may
        not play but the ledger stores would become a prior the DJ could never
        match against a fresh search."""
        self.assertIsNone(self._play("Moss Echo User Manual", duration=282))
        self.assertEqual(euterpe.entries(), [])


class TestWasRecentlyPlayed(_EuterpeCase):
    """The guard itself. Both keys matter — see the module's "Dedupe" section."""

    def test_an_exact_video_id_match_counts(self):
        self._play("Some Title", url="https://youtu.be/tCDvOQI3pco")
        self.assertTrue(euterpe.was_recently_played(
            "A Completely Different Title",
            url="https://www.youtube.com/watch?v=tCDvOQI3pco"))

    def test_a_normalized_title_match_counts_across_uploads(self):
        """The case that matters most: the DJ asks for a song it already played,
        YouTube resolves a DIFFERENT upload of it, and the id check misses."""
        title, channel, dur = _DREAMS[0]
        self._play(title, channel=channel, duration=dur,
                   url="https://youtu.be/aaaaaaaaaaa")
        other_title, other_channel, _ = _DREAMS[3]
        self.assertTrue(euterpe.was_recently_played(
            other_title, url="https://youtu.be/bbbbbbbbbbb",
            channel=other_channel))

    def test_a_song_never_played_is_not_recent(self):
        self._play("Artist - Song")
        self.assertFalse(euterpe.was_recently_played("Someone Else - Other"))

    def test_two_url_less_plays_do_not_collide_on_a_None_id(self):
        """A stored None id must not match an absent one, or every play started
        from the Morpheus panel would look like a repeat of every other."""
        self._play("Artist - First")
        self.assertFalse(euterpe.was_recently_played("Artist - Second"))

    def test_the_window_is_read_from_config(self):
        self._write_config(no_repeat_window=2)
        self._play("Artist - Old")
        self._play("Artist - Mid")
        self._play("Artist - New")
        self.assertFalse(euterpe.was_recently_played("Artist - Old"))
        self.assertTrue(euterpe.was_recently_played("Artist - Mid"))

    def test_a_song_ages_out_of_the_window(self):
        self._write_config(no_repeat_window=3)
        self._play("Artist - Target")
        self.assertTrue(euterpe.was_recently_played("Artist - Target"))
        for i in range(3):
            self._play(f"Artist - Filler {i}")
        self.assertFalse(euterpe.was_recently_played("Artist - Target"))

    def test_an_empty_ledger_says_no(self):
        self.assertFalse(euterpe.was_recently_played("Artist - Song"))


class TestViews(_EuterpeCase):
    def test_recent_titles_are_normalized_and_ordered_oldest_first(self):
        self._play("Fleetwood Mac - Dreams (Official Audio)")
        self._play("Eagles - Hotel California [HD]")
        self.assertEqual(euterpe.recent_titles(),
                         ["fleetwood mac - dreams", "eagles - hotel california"])

    def test_recent_titles_deduplicate_while_keeping_order(self):
        for title, channel, dur in _DREAMS:
            self._play(title, channel=channel, duration=dur)
        self._play("Eagles - Hotel California")
        self.assertEqual(euterpe.recent_titles(),
                         ["fleetwood mac - dreams", "eagles - hotel california"])

    def test_the_taste_anchor_reads_only_the_listeners_own_picks(self):
        self._play("Fleetwood Mac - Dreams", source="user")
        self._play("Led Zeppelin - Stairway To Heaven", source="dj")
        self._play("AC/DC - Thunderstruck", source="dj")
        self.assertEqual(euterpe.taste_anchor(), ["fleetwood mac - dreams"])

    def test_the_anchor_looks_past_the_no_repeat_window(self):
        """After a long DJ run there may be no user pick inside the window at
        all, and an empty anchor is exactly what lets the taste drift — so the
        anchor reads the WHOLE log, not the window."""
        self._write_config(no_repeat_window=2, taste_anchor=3)
        self._play("Fleetwood Mac - Dreams", source="user")
        for i in range(10):
            self._play(f"Robot - Song {i}", source="dj")
        self.assertEqual(euterpe.taste_anchor(), ["fleetwood mac - dreams"])

    def test_the_anchor_is_windowed_by_config(self):
        self._write_config(taste_anchor=2)
        for name in ("First", "Second", "Third"):
            self._play(f"Artist - {name}", source="user")
        self.assertEqual(euterpe.taste_anchor(),
                         ["artist - second", "artist - third"])

    def test_an_anchor_with_no_user_picks_is_empty(self):
        """Awen reads [] as "no vibe yet" and says so rather than guessing."""
        self._play("Robot - Song", source="dj")
        self.assertEqual(euterpe.taste_anchor(), [])

    def test_a_view_can_be_folded_over_one_read(self):
        """The plutus.positions(events) idiom — several views, one file read."""
        self._play("Artist - Song", source="user")
        rows = euterpe.entries()
        with mock.patch.object(euterpe, "entries",
                               side_effect=AssertionError("re-read the file")):
            self.assertEqual(euterpe.recent_titles(plays=rows), ["artist - song"])
            self.assertEqual(euterpe.taste_anchor(plays=rows), ["artist - song"])


class TestDegradedReads(_EuterpeCase):
    """Written from a job nobody is watching, so every one of these must read as
    an empty history and never raise (§10)."""

    def test_a_missing_file_is_an_empty_history(self):
        self.assertEqual(euterpe.entries(), [])
        self.assertEqual(euterpe.recent_titles(), [])
        self.assertEqual(euterpe.taste_anchor(), [])

    def test_corrupt_json_is_logged_and_empty(self):
        self._write_raw_ledger("{not json at all")
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            self.assertEqual(euterpe.entries(), [])

    def test_a_root_of_the_wrong_type_is_logged_and_empty(self):
        self._write_raw_ledger('["a", "list", "not", "an", "object"]')
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            self.assertEqual(euterpe.entries(), [])

    def test_a_plays_key_of_the_wrong_type_is_empty(self):
        self._write_raw_ledger('{"plays": "nope"}')
        self.assertEqual(euterpe.entries(), [])

    def test_rows_that_are_not_objects_are_dropped_not_handed_on(self):
        """One hand-edited line must not break every reader."""
        self._write_raw_ledger(
            '{"plays": [{"title": "Artist - Song", "key": "artist - song",'
            ' "source": "user"}, "junk", 42, null]}')
        rows = euterpe.entries()
        self.assertEqual(len(rows), 1)
        self.assertEqual(euterpe.recent_titles(), ["artist - song"])

    def test_an_unreadable_file_is_logged_and_empty(self):
        self._play("Artist - Song")
        with mock.patch("tools.euterpe.open", side_effect=OSError("locked")):
            with self.assertLogs("METIS.euterpe", level="WARNING"):
                self.assertEqual(euterpe.entries(), [])

    def test_a_row_with_no_stored_key_is_normalized_on_read(self):
        """Forward compatibility with a log written before `key` existed."""
        self._write_raw_ledger(
            '{"plays": [{"title": "Artist - Song [HD]", "source": "user"}]}')
        self.assertEqual(euterpe.recent_titles(), ["artist - song"])
        self.assertTrue(euterpe.was_recently_played("Artist - Song"))


class TestAtomicWrite(_EuterpeCase):
    def test_a_crash_mid_write_leaves_the_previous_log_intact(self):
        self._play("Artist - First")
        before = euterpe.entries()
        with mock.patch("tools.euterpe.json.dump",
                        side_effect=OSError("disk full")):
            with self.assertLogs("METIS.euterpe", level="ERROR"):
                self.assertIsNone(self._play("Artist - Second"))
        self.assertEqual(euterpe.entries(), before)

    def test_a_failed_write_leaves_no_temp_file_behind(self):
        with mock.patch("tools.euterpe.json.dump",
                        side_effect=OSError("disk full")):
            with self.assertLogs("METIS.euterpe", level="ERROR"):
                self._play("Artist - Song")
        leftovers = [n for n in os.listdir(self._tmp.name) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"temp files left: {leftovers}")

    def test_the_write_replaces_rather_than_truncating(self):
        """os.replace on a same-directory temp file is what makes this atomic —
        a plain open(path, "w") would empty the real file before writing."""
        with mock.patch("tools.euterpe.os.replace") as replace:
            self._play("Artist - Song")
        replace.assert_called_once()
        source, destination = replace.call_args[0]
        self.assertEqual(os.path.dirname(source), os.path.dirname(destination))


class _EditorCase(_EuterpeCase):
    """Shared scaffolding for the TUNES editor's three functions.

    Every refusal case asserts the same two things: the slug that came back,
    and that the file on disk did not move. The second is the one that matters
    — a validator that reports a problem after writing the bad text has not
    validated anything.
    """

    def _row(self, **overrides):
        row = {"title": "Artist - Song", "key": "artist - song",
               "video_id": None, "url": "", "channel": "", "duration": 240.0,
               "source": "user", "at": "2026-09-17T09:00:00"}
        row.update(overrides)
        return row

    def _document(self, *rows):
        return json.dumps({"plays": list(rows)}, indent=2, ensure_ascii=False)

    def _assert_refused(self, text, slug):
        before = euterpe.raw_text()
        self.assertEqual(euterpe.replace_all(text), slug)
        self.assertEqual(euterpe.raw_text(), before,
                         "the file moved on a refusal")


class TestRawText(_EditorCase):
    """What the editor opens on."""

    def test_an_absent_ledger_opens_on_the_empty_document(self):
        """Not "" — an editor that opens on a blank box is asking the operator
        to remember the schema, and what comes back must be valid JSON that
        replace_all() would accept."""
        text = euterpe.raw_text()
        self.assertEqual(json.loads(text), {"plays": []})
        self.assertIsNone(euterpe.replace_all(text))

    def test_it_is_the_file_verbatim_not_a_reserialisation(self):
        """The whole promise of the tab is that what you see is what is on
        disk. A json.dumps(entries()) would quietly reindent a hand edit and
        drop any row entries() rejects, so the operator would be editing a
        cleaned-up copy of their file rather than their file."""
        odd = '{"plays":[],   "note": "kept"}'
        self._write_raw_ledger(odd)
        self.assertEqual(euterpe.raw_text(), odd)

    def test_an_unreadable_ledger_still_opens(self):
        with mock.patch("builtins.open", side_effect=OSError("locked")):
            with self.assertLogs("METIS.euterpe", level="WARNING"):
                self.assertEqual(json.loads(euterpe.raw_text()), {"plays": []})


class TestStamp(_EditorCase):
    def test_absent_is_zero(self):
        self.assertEqual(euterpe.stamp(), 0.0)

    def test_it_moves_when_the_file_is_written(self):
        self.assertIsNone(euterpe.replace_all(self._document(self._row())))
        first = euterpe.stamp()
        self.assertNotEqual(first, 0.0)
        # A stamp is compared for equality only, so "changed" is the assertion,
        # not "increased" — os.utime proves the editor notices a write it did
        # not make without waiting on the filesystem's clock resolution.
        os.utime(euterpe._LEDGER_PATH, (first + 10, first + 10))
        self.assertNotEqual(euterpe.stamp(), first)


class TestReplaceAll(_EditorCase):
    """The operator's write path. Nothing here is reachable by the model."""

    def test_a_good_document_round_trips(self):
        row = self._row()
        self.assertIsNone(euterpe.replace_all(self._document(row)))
        self.assertEqual(euterpe.entries(), [row])

    def test_raw_text_round_trips_through_it_unchanged(self):
        """Loading the editor and saving without typing must be a no-op. If it
        is not, every RELOAD-then-SAVE quietly rewrites the operator's file."""
        self._play("Artist - Song")
        text = euterpe.raw_text()
        self.assertIsNone(euterpe.replace_all(text))
        self.assertEqual(euterpe.raw_text(), text)

    def test_it_can_flip_a_dj_row_to_user(self):
        """The bug this tab exists for. taste_anchor() reads "user" rows and
        nothing else, so flipping the source IS editing the taste."""
        self._play("Artist - Song", source="dj")
        self.assertEqual(euterpe.taste_anchor(), [])
        flipped = [dict(row, source="user") for row in euterpe.entries()]
        self.assertIsNone(euterpe.replace_all(self._document(*flipped)))
        self.assertEqual(euterpe.taste_anchor(), ["artist - song"])

    def test_deleting_a_row_removes_it_from_the_no_repeat_window(self):
        self._play("Artist - First")
        self._play("Artist - Second")
        kept = [r for r in euterpe.entries() if r["key"] != "artist - first"]
        self.assertIsNone(euterpe.replace_all(self._document(*kept)))
        self.assertFalse(euterpe.was_recently_played("Artist - First"))
        self.assertTrue(euterpe.was_recently_played("Artist - Second"))

    def test_an_empty_plays_list_is_allowed(self):
        """Clearing the log by hand is the operator's business — the "no
        delete" rule is about a programmatic clear(), not about what a human
        may type into their own file."""
        self._play("Artist - Song")
        self.assertIsNone(euterpe.replace_all(self._document()))
        self.assertEqual(euterpe.entries(), [])

    # ── refusals ──────────────────────────────────────────────────────────────

    def test_broken_json_is_refused_with_its_line(self):
        self._play("Artist - Song")
        before = euterpe.raw_text()
        error = euterpe.replace_all('{\n  "plays": [\n')
        self.assertTrue(error.startswith("invalid_json: line "), error)
        self.assertEqual(euterpe.raw_text(), before)

    def test_a_non_object_root_is_refused(self):
        self._play("Artist - Song")
        self._assert_refused("[]", "bad_root")

    def test_a_plays_that_is_not_a_list_is_refused(self):
        self._play("Artist - Song")
        self._assert_refused('{"plays": "nope"}', "bad_root")

    def test_a_row_that_is_not_an_object_is_refused_by_number(self):
        self._play("Artist - Song")
        self._assert_refused(
            json.dumps({"plays": [self._row(), "nope"]}), "bad_row: 2")

    def test_a_missing_field_is_refused(self):
        row = self._row()
        del row["url"]
        self._assert_refused(json.dumps({"plays": [row]}), "bad_row: 1")

    def test_a_wrongly_typed_field_is_refused(self):
        self._assert_refused(self._document(self._row(duration="240")),
                             "bad_row: 1")

    def test_a_boolean_duration_is_refused(self):
        """isinstance(True, int) is True, so a bool would pass a naive numeric
        check and then divide nothing sensibly downstream."""
        self._assert_refused(self._document(self._row(duration=True)),
                             "bad_row: 1")

    def test_a_blank_key_is_refused(self):
        """The key is what the DJ matches on — the anchor and the no-repeat
        window are both lists of keys, never titles. A row with a blank key
        would sit in the file doing nothing at all."""
        self._assert_refused(self._document(self._row(key="   ")),
                             "bad_row: 1")

    def test_a_blank_title_is_refused(self):
        self._assert_refused(self._document(self._row(title="")),
                             "bad_row: 1")

    def test_an_unknown_source_is_refused_by_number(self):
        self._assert_refused(
            self._document(self._row(), self._row(source="robot")),
            "bad_source: 2")

    def test_two_rows_for_the_same_song_are_refused_naming_both(self):
        """REFUSED, not collapsed. This is the function that promises not to
        rewrite what was typed, and both rows are named because "there is a
        duplicate somewhere in sixty lines" is not an actionable message."""
        error = euterpe.replace_all(self._document(
            self._row(key="artist - song"),
            self._row(key="other - track", title="Other - Track"),
            self._row(key="artist - song")))
        self.assertEqual(error,
                         "duplicate_row: plays 1 and 3 are both 'artist - song'")

    def test_a_duplicate_is_refused_even_when_the_titles_differ(self):
        """The key is identity, not the title — two uploads of one song reach
        here as two different titles sharing a key."""
        self._assert_refused(
            self._document(
                self._row(title="Fleetwood Mac - Dreams (Official Video)",
                          key="fleetwood mac - dreams"),
                self._row(title="Dreams (2004 Remaster)",
                          key="fleetwood mac - dreams")),
            "duplicate_row: plays 1 and 2 are both 'fleetwood mac - dreams'")

    def test_distinct_songs_are_not_a_duplicate(self):
        self.assertIsNone(euterpe.replace_all(self._document(
            self._row(key="artist - song"),
            self._row(key="other - track", title="Other - Track"))))
        self.assertEqual(len(euterpe.entries()), 2)

    def test_a_duplicate_ledger_cannot_become_a_base_copy(self):
        """_validate is one judgment (see TestValidateIsOneJudgment), so the
        duplicate rule reaches take_base() for free — a restore point SAVE would
        refuse to commit is not a restore point."""
        self._write_raw_ledger(self._document(
            self._row(), self._row()))
        self.assertEqual(euterpe.take_base(),
                         "duplicate_row: plays 1 and 2 are both 'artist - song'")
        self.assertIsNone(euterpe.base_snapshot())

    def test_a_failed_write_is_reported_not_swallowed(self):
        self._play("Artist - Song")
        before = euterpe.entries()
        with mock.patch("tools.euterpe.json.dump", side_effect=OSError("disk full")):
            with self.assertLogs("METIS.euterpe", level="ERROR"):
                self.assertEqual(euterpe.replace_all(self._document(self._row())),
                                 "write_failed")
        self.assertEqual(euterpe.entries(), before)

    # ── the cap, and the backup ───────────────────────────────────────────────

    def test_it_honours_the_same_cap_record_does(self):
        """An editor that walked around _MAX_ENTRIES would be a hole in the
        only thing keeping the file finite."""
        rows = [self._row(key=f"song {i}", title=f"Artist - Song {i}")
                for i in range(euterpe._MAX_ENTRIES + 50)]
        self.assertIsNone(euterpe.replace_all(self._document(*rows)))
        kept = euterpe.entries()
        self.assertEqual(len(kept), euterpe._MAX_ENTRIES)
        self.assertEqual(kept[-1]["key"], rows[-1]["key"], "trimmed the wrong end")

    def test_a_save_backs_the_previous_content_up_first(self):
        self._play("Artist - First")
        previous = euterpe.raw_text()
        self.assertIsNone(euterpe.replace_all(self._document(self._row())))
        backup = euterpe._LEDGER_PATH + euterpe._BACKUP_SUFFIX
        self.assertTrue(os.path.exists(backup), "no backup was written")
        with open(backup, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), previous,
                             "the backup holds the NEW content, which is no backup")

    def test_a_first_save_needs_no_backup(self):
        """Nothing to protect is not a failure — a fresh install would
        otherwise be unable to save at all."""
        self.assertIsNone(euterpe.replace_all(self._document(self._row())))
        self.assertFalse(
            os.path.exists(euterpe._LEDGER_PATH + euterpe._BACKUP_SUFFIX))

    def test_only_one_backup_is_kept(self):
        self._play("Artist - First")
        for index in range(3):
            self.assertIsNone(euterpe.replace_all(
                self._document(self._row(key=f"song {index}"))))
        backups = [n for n in os.listdir(self._tmp.name)
                   if n.startswith("euterpe_ledger.json")
                   and n != "euterpe_ledger.json"]
        self.assertEqual(backups, ["euterpe_ledger.json" + euterpe._BACKUP_SUFFIX])

    def test_a_save_is_refused_when_the_backup_cannot_be_written(self):
        """The backup IS the undo this module otherwise does not have, so a
        save without one is exactly the situation the operator asked not to be
        in. Refuse rather than take it blind."""
        self._play("Artist - First")
        before = euterpe.entries()
        with mock.patch("tools.euterpe.shutil.copy2", side_effect=OSError("denied")):
            with self.assertLogs("METIS.euterpe", level="ERROR"):
                self.assertEqual(euterpe.replace_all(self._document(self._row())),
                                 "backup_failed")
        self.assertEqual(euterpe.entries(), before)


class TestBaseCopy(_EditorCase):
    """The restore point the rolling .bak cannot be.

    The distinction between the two files is the thing under test. A .bak that
    survived one more save would already be a base copy, and it does not; a base
    copy that anything but the operator could replace would already be a .bak.
    """

    def _base_path(self):
        return euterpe._LEDGER_PATH + euterpe._BASE_SUFFIX

    def test_no_base_reads_as_none_not_as_an_empty_one(self):
        self.assertIsNone(euterpe.base_snapshot())

    def test_taking_one_captures_the_ledger_on_disk(self):
        self._play("Artist - Song")
        self.assertIsNone(euterpe.take_base())
        base = euterpe.base_snapshot()
        self.assertEqual(base["text"], euterpe.raw_text())
        self.assertEqual(base["plays"], 1)

    def test_it_survives_saves_that_roll_the_backup_away(self):
        """The whole reason it exists. Two saves later the .bak holds the state
        before the SECOND one; the base still holds where the operator was when
        they took it."""
        self._play("Artist - First")
        self.assertIsNone(euterpe.take_base())
        taken = euterpe.raw_text()

        for key in ("second", "third"):
            self.assertIsNone(euterpe.replace_all(
                self._document(self._row(key=key, title=f"Artist - {key}"))))

        self.assertEqual(euterpe.base_snapshot()["text"], taken)
        with open(euterpe._LEDGER_PATH + euterpe._BACKUP_SUFFIX, encoding="utf-8") as fh:
            self.assertNotEqual(fh.read(), taken, "the .bak kept up — it should roll")

    def test_restoring_it_is_just_a_save_of_its_text(self):
        """No special restore path in this module: the base is text, and the
        editor commits it through the same validated write as anything else."""
        self._play("Artist - First")
        self.assertIsNone(euterpe.take_base())
        original = euterpe.entries()

        self._play("Artist - Second")
        self.assertEqual(len(euterpe.entries()), 2)

        self.assertIsNone(euterpe.replace_all(euterpe.base_snapshot()["text"]))
        self.assertEqual(euterpe.entries(), original)

    def test_taking_one_twice_replaces_it_and_leaves_no_temp_file(self):
        self._play("Artist - First")
        self.assertIsNone(euterpe.take_base())
        self.assertIsNone(euterpe.replace_all(self._document(self._row(key="later"))))
        self.assertIsNone(euterpe.take_base())
        self.assertEqual(euterpe.base_snapshot()["plays"], 1)
        self.assertEqual(euterpe.base_snapshot()["text"], euterpe.raw_text())
        leftovers = [n for n in os.listdir(self._tmp.name) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"temp files left: {leftovers}")

    def test_taking_one_with_no_ledger_is_refused(self):
        self.assertEqual(euterpe.take_base(), "no_ledger")
        self.assertIsNone(euterpe.base_snapshot())

    def test_a_corrupt_ledger_cannot_become_a_base(self):
        """A restore point that SAVE would refuse to commit is not a restore
        point. _validate() is asked here for exactly that reason."""
        self._write_raw_ledger('{"plays": [{"title": "x"}]}')
        self.assertEqual(euterpe.take_base(), "bad_row: 1")
        self.assertIsNone(euterpe.base_snapshot())

    def test_a_failed_write_leaves_the_previous_base_intact(self):
        self._play("Artist - First")
        self.assertIsNone(euterpe.take_base())
        kept = euterpe.base_snapshot()["text"]
        self.assertIsNone(euterpe.replace_all(self._document(self._row(key="later"))))
        with mock.patch("tools.euterpe.os.replace", side_effect=OSError("denied")):
            with self.assertLogs("METIS.euterpe", level="ERROR"):
                self.assertEqual(euterpe.take_base(), "write_failed")
        self.assertEqual(euterpe.base_snapshot()["text"], kept)

    def test_a_corrupt_base_is_still_handed_back_to_be_looked_at(self):
        """plays=None, not None-the-whole-snapshot. A restore point you cannot
        even open is worse than one you can see is broken."""
        with open(self._base_path(), "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        base = euterpe.base_snapshot()
        self.assertIsNotNone(base)
        self.assertIsNone(base["plays"])
        self.assertIn("not json", base["text"])

    def test_a_base_that_merely_breaks_a_RULE_still_reports_its_count(self):
        """A base taken before a rule existed is readable and countable, and
        saying "unreadable" about it would be a lie about a file the operator
        can open. The real base copy this project first made is 59 legible rows
        with one duplicate in it; the cost of the new rule is a refusal at SAVE
        naming the rows, not a caption that pretends the file is rubble."""
        with open(self._base_path(), "w", encoding="utf-8") as handle:
            handle.write(self._document(self._row(), self._row()))
        base = euterpe.base_snapshot()
        self.assertEqual(base["plays"], 2)
        self.assertTrue(euterpe.replace_all(base["text"]).startswith("duplicate_row"))

    def test_nothing_but_take_base_ever_writes_it(self):
        """record() and replace_all() must leave it alone — that is the entire
        difference between this file and the .bak."""
        self._play("Artist - First")
        self.assertIsNone(euterpe.take_base())
        before = euterpe.base_snapshot()["text"]
        self._play("Artist - Second")
        self.assertIsNone(euterpe.replace_all(self._document(self._row(key="x"))))
        self.assertEqual(euterpe.base_snapshot()["text"], before)

    def test_the_base_copy_is_gitignored(self):
        """Same personal history as the log, so the same rule (§9). Asserted
        because the obvious name for it, euterpe_ledger.base.json, is NOT
        matched by euterpe_ledger.json* and would have been committed."""
        import subprocess
        result = subprocess.run(
            ["git", "check-ignore", "euterpe_ledger.json" + euterpe._BASE_SUFFIX],
            cwd=_APP_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "the base copy is not gitignored")


class TestValidateIsOneJudgment(_EditorCase):
    """replace_all() and take_base() must not be able to disagree about what a
    ledger is, in the is_a_song() spirit. If they could, the operator could take
    a base copy that SAVE would then refuse to restore."""

    def test_both_refuse_the_same_documents(self):
        for text, expected in (
                ("{", "invalid_json"),
                ("[]", "bad_root"),
                (self._document(self._row(source="robot")), "bad_source: 1"),
                (self._document(self._row(key=" ")), "bad_row: 1")):
            with self.subTest(expected=expected):
                self._write_raw_ledger(text)
                from_base = euterpe.take_base()
                from_save = euterpe.replace_all(text)
                self.assertEqual(from_base.split(":")[0], expected.split(":")[0])
                self.assertEqual(from_base, from_save)


class TestTheModelStillCannotWrite(unittest.TestCase):
    """replace_all() is the operator's hand, not a hole in the "no delete" rule.

    The rule forbids a PROGRAMMATIC erase — something Reset Session, a panel
    button or the model could call to wipe history as a side effect. That is
    still true, and this is the assertion that keeps it true: euterpe exposes no
    clear(), and it is still absent from Pythia's registry, so no amount of
    prompting reaches the write path.
    """

    def test_euterpe_is_not_a_brain_tool(self):
        import pythia
        names = [module.__name__.rsplit(".", 1)[-1]
                 for module in pythia._TOOL_MODULES]
        self.assertNotIn("euterpe", names)

    def test_there_is_still_no_clear_or_erase(self):
        for forbidden in ("clear", "erase", "reset", "undo", "delete"):
            self.assertFalse(hasattr(euterpe, forbidden),
                             f"euterpe grew a {forbidden}() — see 'No delete'")


class TestSettings(_EuterpeCase):
    def test_defaults_apply_with_no_config_file(self):
        self.assertEqual(euterpe.settings(), euterpe._DEFAULTS)
        self.assertEqual(euterpe.not_music_phrases(),
                         euterpe._DEFAULT_NOT_MUSIC)

    def test_a_partial_config_keeps_the_other_defaults(self):
        self._write_config(no_repeat_window=7)
        values = euterpe.settings()
        self.assertEqual(values["no_repeat_window"], 7)
        self.assertEqual(values["taste_anchor"],
                         euterpe._DEFAULTS["taste_anchor"])

    def test_one_bad_value_does_not_discard_the_good_ones(self):
        """Per-field fail-soft (§2), not wholesale — the themis precedent."""
        self._write_config(no_repeat_window="twenty", taste_anchor=9)
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            values = euterpe.settings()
        self.assertEqual(values["no_repeat_window"],
                         euterpe._DEFAULTS["no_repeat_window"])
        self.assertEqual(values["taste_anchor"], 9)

    def test_a_window_below_one_falls_back(self):
        self._write_config(no_repeat_window=0)
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            self.assertEqual(euterpe.settings()["no_repeat_window"],
                             euterpe._DEFAULTS["no_repeat_window"])

    def test_corrupt_config_is_logged_and_defaults_apply(self):
        with open(euterpe._CONFIG_PATH, "w", encoding="utf-8") as handle:
            handle.write("{nope")
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            self.assertEqual(euterpe.settings(), euterpe._DEFAULTS)

    def test_a_root_of_the_wrong_type_is_logged_and_defaults_apply(self):
        with open(euterpe._CONFIG_PATH, "w", encoding="utf-8") as handle:
            handle.write("[1, 2, 3]")
        with self.assertLogs("METIS.euterpe", level="WARNING"):
            self.assertEqual(euterpe.settings(), euterpe._DEFAULTS)

    def test_config_is_re_read_rather_than_cached(self):
        """A JSON edit takes effect on the next read, with no restart — the
        machine_spirit.effective_prompt() discipline."""
        self._write_config(taste_anchor=3)
        self.assertEqual(euterpe.settings()["taste_anchor"], 3)
        self._write_config(taste_anchor=8)
        self.assertEqual(euterpe.settings()["taste_anchor"], 8)


class TestShippedConfigMatchesTheCode(unittest.TestCase):
    """Lockstep, not a hardcoded count (§10): the shipped JSON and _DEFAULTS are
    two live sources, asserted against each other. A default changed in one
    place and not the other fails here rather than shipping a config that
    silently disagrees with the code it configures.

    Reads the REPO's file, so it deliberately does not use _EuterpeCase's
    redirect — and the file is committed config (§4), not gitignored state, so
    a fresh clone has it.
    """

    def test_the_shipped_file_exists_and_parses(self):
        self.assertTrue(os.path.isfile(_SHIPPED_CONFIG),
                        f"{_SHIPPED_CONFIG} is missing from the repo")
        with open(_SHIPPED_CONFIG, encoding="utf-8") as handle:
            self.assertIsInstance(json.load(handle), dict)

    def _code_defaults(self):
        """Every default the code ships, numbers AND the denylist. They live in
        two constants because only one of them is integers, so the lockstep has
        to span both or the list half is unguarded."""
        return {**euterpe._DEFAULTS,
                "not_music": euterpe._DEFAULT_NOT_MUSIC,
                "not_music_categories": euterpe._DEFAULT_NOT_MUSIC_CATEGORIES}

    def test_every_shipped_key_is_one_the_code_knows(self):
        with open(_SHIPPED_CONFIG, encoding="utf-8") as handle:
            shipped = json.load(handle)
        self.assertEqual(set(shipped), set(self._code_defaults()),
                         "shipped config and the code defaults name different keys")

    def test_the_shipped_values_equal_the_code_defaults(self):
        with open(_SHIPPED_CONFIG, encoding="utf-8") as handle:
            shipped = json.load(handle)
        self.assertEqual(shipped, self._code_defaults())

    def test_the_shipped_config_is_tracked_by_git(self):
        """Committed config, not state (§4/§9) — and asserted against git, not
        the filesystem, because a file that happens to exist on this machine is
        not the same as one a fresh clone gets (§10)."""
        import subprocess
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "config/euterpe_config.json"],
            cwd=_APP_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         "config/euterpe_config.json is not tracked by git")

    def test_the_ledger_itself_is_NOT_tracked_by_git(self):
        """Generated, personal state (§9). A stranger cloning this must not
        inherit what the author listens to.

        The backup is asserted alongside it and for the same reason: it is a
        COPY of the same personal history, and a pattern that covered the log
        but not its copy would leak exactly as much as no pattern at all.
        """
        import subprocess
        for name in ("euterpe_ledger.json", "euterpe_ledger.json" + euterpe._BACKUP_SUFFIX):
            with self.subTest(name=name):
                result = subprocess.run(
                    ["git", "check-ignore", name],
                    cwd=_APP_ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{name} is not gitignored")


if __name__ == "__main__":
    unittest.main()
