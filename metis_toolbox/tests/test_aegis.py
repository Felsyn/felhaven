"""
test_aegis.py — the bin/ programs are vouched for before they run.

Every test works on a temporary bin/ and a temporary manifest (aegis.BIN_DIR
and aegis.MANIFEST_PATH are redirected), so nothing here reads or writes the
real ones. tests/test_bin_manifest.py checks, wherever bin/ exists, the real
programs against this machine's real manifest.

The rules under test (owner's decisions, 2026-10-01):
  * a bin/ copy that matches its manifest entry runs;
  * a bin/ copy that doesn't (one byte flipped), or that has no entry, or
    under a manifest that can't be read, is REFUSED, and is never swapped
    for the copy on PATH;
  * a program only on PATH runs, unverified, with ONE warning per process;
  * each program is hashed once per process, again only if the file changes;
  * `record` writes an entry only when the publisher's checksum agrees.

Hash literals here are computed, never typed (tests/test_secret_fixtures.py).

Run from the package root:
    python -X utf8 -m unittest tests.test_aegis
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import aegis, hermes

_BODY = b"MZ fake program bytes for the aegis tests " * 64


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class _AegisCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        self.manifest = os.path.join(self.root, "bin_manifest.json")
        for name, value in (("BIN_DIR", self.bin), ("MANIFEST_PATH", self.manifest)):
            patcher = mock.patch.object(aegis, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        aegis._clear_cache()
        self.addCleanup(aegis._clear_cache)

    def put(self, name, data=_BODY):
        path = os.path.join(self.bin, f"{name}.exe")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def record(self, **programs):
        """programs: name -> bytes the manifest should vouch for."""
        entries = {name: {"file": f"{name}.exe", "version": "1", "sha256": _sha(data),
                          "source_url": "https://example.invalid/x",
                          "published_sha256": _sha(data), "published_for": f"{name}.exe",
                          "checksum_source": "https://example.invalid/sums",
                          "recorded": "2026-10-01"}
                   for name, data in programs.items()}
        with open(self.manifest, "w", encoding="utf-8") as fh:
            json.dump({"programs": entries}, fh)


class TheBinCopyMustMatch(_AegisCase):

    def test_a_matching_copy_runs(self):
        path = self.put("ffmpeg")
        self.record(ffmpeg=_BODY)
        self.assertEqual(aegis.resolve("ffmpeg"), aegis.Resolution(path, None))

    def test_one_flipped_byte_is_refused_loudly(self):
        flipped = bytearray(_BODY)
        flipped[100] ^= 0x01
        self.put("ffmpeg", bytes(flipped))
        self.record(ffmpeg=_BODY)
        with self.assertLogs("METIS.aegis", "ERROR") as logs:
            found = aegis.resolve("ffmpeg")
        self.assertIsNone(found.path)
        self.assertTrue(found.problem.startswith("binary_unverified: "))
        self.assertIn("record", logs.output[0])      # the message names the fix

    def test_a_refused_copy_never_falls_back_to_path(self):
        """Otherwise breaking the bin/ copy would be a way to run PATH's."""
        self.put("ffmpeg", b"tampered")
        self.record(ffmpeg=_BODY)
        with mock.patch.object(aegis.shutil, "which", return_value="C:/elsewhere/ffmpeg.exe"), \
                self.assertLogs("METIS.aegis", "ERROR"):
            self.assertIsNone(aegis.resolve("ffmpeg").path)

    def test_a_copy_with_no_entry_is_refused(self):
        self.put("mpv")
        self.record(ffmpeg=_BODY)
        with self.assertLogs("METIS.aegis", "ERROR"):
            found = aegis.resolve("mpv")
        self.assertIsNone(found.path)
        self.assertIn("no manifest entry", found.problem)

    def test_an_unreadable_manifest_refuses_everything(self):
        self.put("ffmpeg")
        with open(self.manifest, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        with self.assertLogs("METIS.aegis", "ERROR"):
            self.assertIsNone(aegis.resolve("ffmpeg").path)

    def test_a_missing_manifest_refuses_everything(self):
        """A fresh clone before `record`: the manifest is per machine, so the
        problem says what to do rather than 'unreadable'."""
        self.put("ffmpeg")
        with self.assertLogs("METIS.aegis", "ERROR"):
            found = aegis.resolve("ffmpeg")
        self.assertIsNone(found.path)
        self.assertIn("not recorded yet", found.problem)


class ThePathFallback(_AegisCase):

    def test_a_program_only_on_path_runs_with_one_warning(self):
        with mock.patch.object(aegis.shutil, "which", return_value="C:/tools/mpv.exe"), \
                self.assertLogs("METIS.aegis", "WARNING") as logs:
            first = aegis.resolve("mpv")
            second = aegis.resolve("mpv")
        self.assertEqual(first, aegis.Resolution("C:/tools/mpv.exe", None))
        self.assertEqual(second, first)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("unverified", logs.output[0])

    def test_nowhere_is_none_without_a_problem(self):
        with mock.patch.object(aegis.shutil, "which", return_value=None):
            self.assertEqual(aegis.resolve("mpv"), aegis.Resolution(None, None))


class HashingIsOncePerProcess(_AegisCase):

    def test_a_second_resolve_does_not_hash_again(self):
        self.put("ffmpeg")
        self.record(ffmpeg=_BODY)
        with mock.patch.object(aegis, "sha256_of", wraps=aegis.sha256_of) as hashed:
            aegis.resolve("ffmpeg")
            aegis.resolve("ffmpeg")
        self.assertEqual(hashed.call_count, 1)

    def test_a_changed_file_is_checked_again(self):
        """The cache is keyed on size and mtime, so swapping the file after
        it was vouched for doesn't ride on the old verdict."""
        path = self.put("ffmpeg")
        self.record(ffmpeg=_BODY)
        self.assertIsNotNone(aegis.resolve("ffmpeg").path)
        time.sleep(0.01)
        self.put("ffmpeg", _BODY + b"!")
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        with self.assertLogs("METIS.aegis", "ERROR"):
            self.assertIsNone(aegis.resolve("ffmpeg").path)


class Recording(_AegisCase):

    def setUp(self):
        super().setUp()
        self.record()                                  # an empty, valid manifest

    def _sums(self, digest, name="yt-dlp.exe"):
        text = f"{'0' * 64}  yt-dlp_linux\n{digest}  {name}\n"
        return hermes.Reply(200, {}, text.encode(), "https://example.invalid/SHA2-256SUMS")

    def _version(self, version="2026.10.01"):
        return mock.patch.object(aegis, "_ytdlp_version", return_value=version)

    def test_yt_dlp_is_recorded_when_its_published_sum_agrees(self):
        self.put("yt-dlp")
        with self._version(), \
                mock.patch.object(aegis.hermes, "get", return_value=self._sums(_sha(_BODY))) as get:
            entry = aegis.record_ytdlp()
        self.assertTrue(get.call_args.args[0].endswith("/2026.10.01/SHA2-256SUMS"))
        self.assertEqual(entry["sha256"], _sha(_BODY))
        self.assertEqual(entry["version"], "2026.10.01")
        # The shape test_bin_manifest checks on a real manifest, held here too,
        # since CI has no real manifest to check.
        self.assertEqual(set(entry), set(aegis._FIELDS))
        with open(self.manifest, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["programs"]["yt-dlp"], entry)
        self.assertIsNotNone(aegis.resolve("yt-dlp").path)

    def test_yt_dlp_is_refused_when_it_is_not_the_published_file(self):
        self.put("yt-dlp", b"not what was published")
        with self._version(), \
                mock.patch.object(aegis.hermes, "get", return_value=self._sums(_sha(_BODY))):
            with self.assertRaises(aegis.RecordError):
                aegis.record_ytdlp()
        with open(self.manifest, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["programs"], {})

    def _fake_ytdlp(self, events, version=b"2026.10.01"):
        """subprocess.run as the real yt-dlp --version would answer, logging
        each run into `events`."""
        def run(args, **_kw):
            events.append(("run", args))
            return mock.Mock(returncode=0, stdout=version)
        return mock.patch.object(aegis.subprocess, "run", side_effect=run)

    def test_a_file_that_is_not_the_latest_release_never_runs(self):
        """The whole point of record: nothing in bin/ runs before it is
        vouched for. A file that isn't the published one must be refused
        without ever being started, not after."""
        self.put("yt-dlp", b"not what was published")
        events = []
        with self._fake_ytdlp(events), \
                mock.patch.object(aegis.hermes, "get", return_value=self._sums(_sha(_BODY))):
            with self.assertRaises(aegis.RecordError):
                aegis.record_ytdlp()
        self.assertEqual(events, [])
        with open(self.manifest, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["programs"], {})

    def test_the_latest_sums_are_read_before_the_file_runs(self):
        self.put("yt-dlp")
        events = []

        def get(url, **_kw):
            events.append(("get", url))
            return self._sums(_sha(_BODY))
        with self._fake_ytdlp(events), mock.patch.object(aegis.hermes, "get", side_effect=get):
            aegis.record_ytdlp()
        self.assertEqual(events[0], ("get", aegis._YTDLP_LATEST + "SHA2-256SUMS"))
        self.assertEqual(events[1][0], "run")
        self.assertEqual(events[2], ("get", aegis._YTDLP_RELEASE.format(version="2026.10.01")
                                     + "SHA2-256SUMS"))

    def test_a_latest_file_must_also_match_its_own_versions_sums(self):
        """--version names the release the manifest records; that release's own
        published sum must agree, or the record would name the wrong source."""
        self.put("yt-dlp")
        replies = [self._sums(_sha(_BODY)), self._sums(_sha(b"another build"))]
        with self._fake_ytdlp([]), mock.patch.object(aegis.hermes, "get", side_effect=replies):
            with self.assertRaises(aegis.RecordError):
                aegis.record_ytdlp()
        with open(self.manifest, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["programs"], {})

    def test_an_exe_checked_directly(self):
        self.put("mpv")
        entry = aegis.record_other("mpv", "1", "https://example.invalid/mpv.7z", _sha(_BODY))
        self.assertEqual(entry["published_for"], "mpv.exe")
        self.assertEqual(set(entry), set(aegis._FIELDS))
        with self.assertRaises(aegis.RecordError):
            aegis.record_other("mpv", "1", "https://example.invalid/mpv.7z", _sha(b"other"))

    def _archive(self, inner):
        path = os.path.join(self.root, "ffmpeg-9-essentials.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ffmpeg-9-essentials/bin/ffmpeg.exe", inner)
            zf.writestr("ffmpeg-9-essentials/bin/ffprobe.exe", b"probe")
        with open(path, "rb") as fh:
            return path, _sha(fh.read())

    def test_an_exe_inside_a_published_zip(self):
        self.put("ffmpeg")
        archive, digest = self._archive(_BODY)
        entry = aegis.record_other("ffmpeg", "9", "https://example.invalid/f.zip",
                                   digest, archive=archive)
        self.assertEqual(entry["published_for"], "ffmpeg-9-essentials.zip")

    def test_a_zip_whose_exe_differs_from_bin_is_refused(self):
        self.put("ffmpeg")
        archive, digest = self._archive(b"a different build")
        with self.assertRaises(aegis.RecordError):
            aegis.record_other("ffmpeg", "9", "https://example.invalid/f.zip",
                               digest, archive=archive)

    def test_a_zip_that_is_not_the_published_one_is_refused(self):
        self.put("ffmpeg")
        archive, _ = self._archive(_BODY)
        with self.assertRaises(aegis.RecordError):
            aegis.record_other("ffmpeg", "9", "https://example.invalid/f.zip",
                               _sha(b"published"), archive=archive)

    def test_a_non_zip_archive_needs_the_extracted_exe(self):
        self.put("mpv")
        archive = os.path.join(self.root, "mpv.7z")
        with open(archive, "wb") as fh:
            fh.write(b"7z archive bytes")
        extracted = os.path.join(self.root, "mpv-extracted.exe")
        with open(extracted, "wb") as fh:
            fh.write(_BODY)
        with self.assertRaises(aegis.RecordError):
            aegis.record_other("mpv", "1", "https://example.invalid/mpv.7z",
                               _sha(b"7z archive bytes"), archive=archive)
        entry = aegis.record_other("mpv", "1", "https://example.invalid/mpv.7z",
                                   _sha(b"7z archive bytes"), archive=archive,
                                   extracted=extracted)
        self.assertEqual(entry["sha256"], _sha(_BODY))

    def test_only_a_program_felhaven_runs_can_be_recorded(self):
        """A typo would otherwise leave a stray entry nothing ever reads."""
        self.put("mvp")
        with self.assertRaises(aegis.RecordError):
            aegis.record_other("mvp", "1", "https://example.invalid/m", _sha(_BODY))
        with open(self.manifest, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["programs"], {})

    def test_a_fresh_manifest_says_it_is_per_machine(self):
        os.remove(self.manifest)
        self.put("mpv")
        aegis.record_other("mpv", "1", "https://example.invalid/m", _sha(_BODY))
        with open(self.manifest, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("Per machine", data["_about"])
        self.assertEqual(list(data["programs"]), ["mpv"])

    def test_the_cli_reports_a_refusal_and_exits_non_zero(self):
        self.put("mpv")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = aegis.main(["record", "mpv", "--version", "1",
                               "--source", "https://example.invalid/m",
                               "--expect", _sha(b"other")])
        self.assertEqual(code, 1)
        self.assertIn("refused", out.getvalue())

    def test_verify_reports_each_entry(self):
        self.put("ffmpeg")
        self.put("mpv", b"tampered")
        self.record(ffmpeg=_BODY, mpv=_BODY, **{"yt-dlp": _BODY})
        results = aegis.verify()
        self.assertEqual(results["ffmpeg"], "ok")
        self.assertTrue(results["mpv"].startswith("binary_unverified"))
        self.assertEqual(results["yt-dlp"], "not in bin/")

    def test_verify_without_a_manifest_names_each_program(self):
        """A fresh clone: an unrecorded program in bin/ is a problem, an absent
        one is just absent, and the exit code says which."""
        os.remove(self.manifest)
        self.put("ffmpeg")
        results = aegis.verify()
        self.assertEqual(set(results), set(aegis.PROGRAMS))
        self.assertIn("not recorded yet", results["ffmpeg"])
        self.assertEqual(results["mpv"], "not in bin/")
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(aegis.main(["verify"]), 1)
        os.remove(os.path.join(self.bin, "ffmpeg.exe"))
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(aegis.main(["verify"]), 0)


if __name__ == "__main__":
    unittest.main()
