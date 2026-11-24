"""
test_machine_spirit.py — unit tests for machine_spirit.py (Pythia's system
prompt: default, override, effective, revert).

The deterministic seam is _DATA_PATH, redirected at a temp file per test (the
themis.py precedent) so the real machine_spirit_config.json is never touched.
Run from the package root:

    python -X utf8 -m unittest tests.test_machine_spirit
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import machine_spirit


class MachineSpiritBase(unittest.TestCase):
    def setUp(self):
        self._orig_path = machine_spirit._DATA_PATH
        self._dir = tempfile.mkdtemp(prefix="machine_spirit_test_")
        machine_spirit._DATA_PATH = os.path.join(self._dir, "machine_spirit_config.json")

    def tearDown(self):
        machine_spirit._DATA_PATH = self._orig_path
        for f in os.listdir(self._dir):
            os.unlink(os.path.join(self._dir, f))
        os.rmdir(self._dir)


class TestEffectivePrompt(MachineSpiritBase):
    def test_missing_file_returns_default(self):
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_garbled_file_falls_back_to_default(self):
        with open(machine_spirit._DATA_PATH, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_non_object_root_falls_back(self):
        with open(machine_spirit._DATA_PATH, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_override_takes_effect(self):
        machine_spirit.save("Be terse.")
        self.assertEqual(machine_spirit.effective_prompt(), "Be terse.")


class TestSave(MachineSpiritBase):
    def test_save_then_effective(self):
        machine_spirit.save("Speak like a pirate.")
        self.assertEqual(machine_spirit.effective_prompt(), "Speak like a pirate.")

    def test_save_strips_whitespace(self):
        machine_spirit.save("  padded prompt  ")
        self.assertEqual(machine_spirit.effective_prompt(), "padded prompt")

    def test_save_blank_clears_override(self):
        machine_spirit.save("custom")
        machine_spirit.save("   ")
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_save_is_atomic_no_tmp_left(self):
        machine_spirit.save("custom")
        leftovers = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_default_text_never_written_to_disk(self):
        machine_spirit.save("custom")
        with open(machine_spirit._DATA_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.assertNotIn(machine_spirit.DEFAULT_SYSTEM_PROMPT, json.dumps(raw))


    def test_saving_the_default_text_clears_the_override(self):
        # What REVERT TO DEFAULT then SAVE sends (the editor adds a newline).
        # Stored, it would keep this install on today's default through every
        # later change to DEFAULT_SYSTEM_PROMPT.
        machine_spirit.save("custom")
        machine_spirit.save(machine_spirit.DEFAULT_SYSTEM_PROMPT + "\n")
        self.assertFalse(os.path.exists(machine_spirit._DATA_PATH))
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)


class TestRevert(MachineSpiritBase):
    def test_revert_clears_override(self):
        machine_spirit.save("custom")
        machine_spirit.revert()
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)
        self.assertFalse(os.path.exists(machine_spirit._DATA_PATH))

    def test_revert_when_no_override_is_a_no_op(self):
        machine_spirit.revert()   # must not raise
        self.assertEqual(machine_spirit.effective_prompt(),
                         machine_spirit.DEFAULT_SYSTEM_PROMPT)


class TestDefaultText(unittest.TestCase):
    """What a fresh install's Pythia reads first (CONVENTIONS §12, 2026-11-16)."""

    def test_names_no_tool(self):
        # The schemas are the tool list. A copy in the prompt drifted (it
        # never learned music, the sky, the archive or Daedalus), and §8 says
        # anything restating _TOOL_MODULES is a copy that will drift.
        import pythia
        names = [t["function"]["name"] for t in pythia.TOOLS]
        self.assertTrue(names)
        for name in names:
            with self.subTest(tool=name):
                self.assertNotIn(name, machine_spirit.DEFAULT_SYSTEM_PROMPT)

    def test_the_call_directive_comes_before_the_markdown_rule(self):
        # Order is load-bearing for a small model: in July the no-Markdown
        # sentence with nothing ahead of it stopped write_document being called
        # (0/5); with the call directive first, 8/8 (measured 2026-11-16).
        text = machine_spirit.DEFAULT_SYSTEM_PROMPT
        self.assertIn("Call a tool whenever", text)
        if "Markdown" in text:
            self.assertLess(text.index("Call a tool whenever"),
                            text.index("Markdown"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
