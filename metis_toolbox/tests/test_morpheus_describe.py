"""
test_morpheus_describe.py — the full metadata read.

search() runs with --flat-playlist, which is right for a search and is why ten
results are one fast call. The cost is that flat entries carry NO music
metadata, so for a long time the only facts anything knew about a candidate were
a title, a channel, a duration and a URL. That is how a video about sharpening a
chainsaw was played and announced as Linkin Park: no rule reading only a title
and a duration could have caught it.

describe() is the same call without --flat-playlist, for one video. Two of its
properties are the ones worth pinning, because both are easy to "simplify" back
into the old blindness:

  * --flat-playlist must NOT be in the argv. Reusing search()'s arguments would
    return a dict that parses fine, passes every test that only counts fields,
    and silently contains none of the metadata the whole feature depends on.
    TestTheArgumentsAreNotSearchs pins that.
  * it REPORTS and decides nothing. Only the named fields survive; description
    and tags are dropped rather than forwarded into a small model's context.

Hermetic: subprocess.run and the binary resolver are patched throughout, so no
yt-dlp, no network, no mpv.

Run from the package root:
    python -X utf8 -m unittest tests.test_morpheus_describe
"""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import morpheus

_URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"

# A realistically fat yt-dlp payload: the fields describe() wants, buried in the
# ones it must drop.
_FAT = {
    "id": "aaaaaaaaaaa",
    "title": "Linkin Park - In The End",
    "channel": "Linkin Park",
    "uploader": "LinkinParkTV",
    "duration": 216,
    "categories": ["Music"],
    "track": "In The End",
    "artist": "Linkin Park",
    "album": "Hybrid Theory",
    "description": "a" * 5000,
    "tags": ["nu metal"] * 40,
    "thumbnails": [{"url": "x"}] * 20,
    "view_count": 1_900_000_000,
    "formats": [{"format_id": "251"}] * 30,
}


class _DescribeCase(unittest.TestCase):
    def setUp(self):
        self._resolve = mock.patch("tools.morpheus._resolve",
                                   return_value="yt-dlp.exe")
        self._resolve.start()
        self.addCleanup(self._resolve.stop)

    def _run(self, payload=None, *, returncode=0, stdout=None, side_effect=None):
        """describe() against a stubbed yt-dlp. Returns (result, the run mock)."""
        if stdout is None:
            stdout = json.dumps(_FAT if payload is None else payload).encode()
        proc = mock.Mock(returncode=returncode, stdout=stdout, stderr=b"boom")
        with mock.patch("tools.morpheus.subprocess.run",
                        return_value=proc, side_effect=side_effect) as run:
            return morpheus.describe(_URL), run


class TestTheArgumentsAreNotSearchs(_DescribeCase):
    """The load-bearing test of this module. Everything else here still passes
    if someone copies search()'s argv, and the feature is dead."""

    def test_flat_playlist_is_NOT_passed(self):
        _, run = self._run()
        self.assertNotIn("--flat-playlist", run.call_args.args[0])

    def test_the_url_is_passed_not_a_ytsearch_query(self):
        _, run = self._run()
        args = run.call_args.args[0]
        self.assertIn(_URL, args)
        self.assertFalse([a for a in args if str(a).startswith("ytsearch")])

    def test_one_video_not_its_playlist(self):
        _, run = self._run()
        self.assertIn("--no-playlist", run.call_args.args[0])

    def test_it_asks_for_json(self):
        _, run = self._run()
        self.assertIn("-J", run.call_args.args[0])

    def test_it_does_not_open_a_console_window(self):
        """Felhaven runs under pythonw.exe; a flashing console is a bug."""
        _, run = self._run()
        self.assertEqual(run.call_args.kwargs["creationflags"], morpheus._NO_WINDOW)

    def test_it_cannot_block_for_ever(self):
        _, run = self._run()
        self.assertEqual(run.call_args.kwargs["timeout"], 20)


class TestOnlyAWatchUrlReachesYtDlp(_DescribeCase):
    """The url is network data (a search row's "url", from yt-dlp's own JSON).
    yt-dlp builds it from the video id today, but nothing else stops a value
    starting with "-" from being read as an option. So describe() takes only a
    watch URL, rebuilds it from the id, and passes "--" before it. Each refused
    shape must spawn NOTHING: the check belongs before the process, not in
    yt-dlp's error handling."""

    _REFUSED = (
        "-J",
        "--exec=calc.exe",
        "--version",
        "",
        "https://evil.example/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com.evil.example/watch?v=aaaaaaaaaaa",
        "http://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PLx",
        "https://www.youtube.com/watch?v=aaaaaaaaaa",
        "https://www.youtube.com/watch?v=aaaaaaaaaaaa",
        "https://www.youtube.com/shorts/aaaaaaaaaaa",
        "https://youtu.be/aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=aaaaaaaaaaa\n--exec=calc.exe",
        "https://www.youtube.com/watch?v=aaaaaaaaaaa\n",   # "$" would pass this
        " https://www.youtube.com/watch?v=aaaaaaaaaaa",
    )

    def test_anything_but_a_watch_url_is_refused_before_yt_dlp_runs(self):
        for url in self._REFUSED:
            with self.subTest(url=url), \
                    mock.patch("tools.morpheus.subprocess.run") as run:
                self.assertEqual(morpheus.describe(url), {"error": "describe_failed"})
                run.assert_not_called()

    def test_the_url_comes_last_right_after_a_double_dash(self):
        _, run = self._run()
        args = run.call_args.args[0]
        self.assertEqual(args[-2:], ["--", _URL])

    def test_the_url_is_rebuilt_from_its_id(self):
        """The bare youtube.com host is accepted, and what yt-dlp gets is the
        canonical form built from the id, not the string that came in."""
        proc = mock.Mock(returncode=0, stdout=json.dumps(_FAT).encode(), stderr=b"")
        with mock.patch("tools.morpheus.subprocess.run", return_value=proc) as run:
            morpheus.describe("https://youtube.com/watch?v=a-b_c1234Z9")
        self.assertEqual(run.call_args.args[0][-1],
                         "https://www.youtube.com/watch?v=a-b_c1234Z9")


class TestOnlyTheNamedFieldsSurvive(_DescribeCase):
    def test_a_fat_payload_is_reduced_to_the_named_fields(self):
        result, _ = self._run()
        self.assertEqual(set(result), set(morpheus._DESCRIBE_FIELDS))

    def test_the_music_metadata_is_what_it_keeps(self):
        result, _ = self._run()
        self.assertEqual(result["categories"], ["Music"])
        self.assertEqual(result["track"], "In The End")
        self.assertEqual(result["artist"], "Linkin Park")

    def test_the_unbounded_uploader_text_is_DROPPED(self):
        """description and tags are uploader-controlled text heading into a
        small model's context, and they decide nothing categories/track/artist
        do not decide better."""
        result, _ = self._run()
        self.assertNotIn("description", result)
        self.assertNotIn("tags", result)

    def test_a_missing_field_is_absent_rather_than_None(self):
        """"Absent" and "present but empty" are different answers to
        has_music_evidence(), and only one of them is true."""
        result, _ = self._run({"title": "x", "duration": 200})
        self.assertNotIn("track", result)

    def test_uploader_stands_in_for_a_missing_channel(self):
        """The same fallback search() uses, so the two agree about what a
        channel is — has_music_evidence() reads it for '- Topic'."""
        result, _ = self._run({"title": "x", "uploader": "Someone - Topic"})
        self.assertEqual(result["channel"], "Someone - Topic")


class TestItNeverRaises(_DescribeCase):
    """It runs inside a DJ turn nobody is watching. A raise here is a tick that
    fails for a reason that has nothing to do with the music."""

    def _assert_failed(self, result):
        self.assertEqual(result, {"error": "describe_failed"})

    def test_a_non_zero_exit(self):
        result, _ = self._run(returncode=1)
        self._assert_failed(result)

    def test_a_timeout(self):
        result, _ = self._run(side_effect=subprocess.TimeoutExpired("yt-dlp", 20))
        self._assert_failed(result)

    def test_unparseable_output(self):
        result, _ = self._run(stdout=b"not json at all")
        self._assert_failed(result)

    def test_a_payload_that_is_not_an_object(self):
        result, _ = self._run(stdout=b"[1, 2, 3]")
        self._assert_failed(result)

    def test_an_os_error(self):
        result, _ = self._run(side_effect=OSError("no exe"))
        self._assert_failed(result)

    def test_a_missing_yt_dlp(self):
        self._resolve.stop()
        with mock.patch("tools.morpheus._resolve", return_value=None):
            self._assert_failed(morpheus.describe(_URL))
        self._resolve.start()

    def test_the_slug_never_carries_yt_dlps_own_text(self):
        """yt-dlp's stderr contains signed URLs (§2: no raw external text)."""
        result, _ = self._run(returncode=1)
        self.assertEqual(list(result), ["error"])
        self.assertEqual(result["error"], "describe_failed")


if __name__ == "__main__":
    unittest.main()
