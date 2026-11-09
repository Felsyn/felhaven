# Virgil

Give a tooltip its words: a module's own gloss, or a glossary entry. A module
tip is the module's `Job:` first sentence, read through Daedalus at hover time
and never copied; everything else comes from a hand-written glossary.

- **UI:** a hover tooltip on the nav rail's rows (glossary), the Moderati,
  Cogitator and Vox Array tabs (each module's gloss), and the Hephaestus
  readouts (glossary); one shared window per root (`panels/virgil_tooltip.py`)
- **Brain tool:** none — module glosses already reach Pythia through Daedalus,
  and a glossary tool would cost prompt tokens on every chat turn (BACKLOG)
- **Files:** `tools/virgil.py`, `panels/virgil_tooltip.py`, `config/virgil_glossary.json`
- **Test:** `python -X utf8 -m unittest tests.test_virgil tests.test_virgil_tooltip_smoke tests.test_virgil_surfaces tests.test_panel_after_teardown`

The glossary is config, committed: edit `config/virgil_glossary.json` while
Felhaven runs and the next hover shows the change. No glossary key may equal a
module's key, so a module is only ever described by its own docstring.

Details: see the docstring in `tools/virgil.py`.
