# Integration notes (integration/full-app)

Base: main 406e657 + WIP snapshot of the user's uncommitted main-checkout files + merge of
`claude/firebase-character-system-setup-2c2db0`. Run: `python ui_tkinter.py`.

Conflicts (4 files) and resolutions - both sides kept:
- `ui_tkinter.py`: imports (SAT hook + `core.llm_settings` + `question_report`/`firebase_question_report`);
  custom-generation card (WIP) AND "Firebase'e kaydet" checkbox (branch); country combo bound to both
  `_on_localized_country_selected` (WIP, local mode) and `_update_country_languages` (branch);
  sheet-load block merges WIP country profile/local lists with the branch language map.
- `ui_server.py`: `write_to_firestore` keeps the WIP env gate (`KNOWLEY_GENERATION_WRITES_ENABLED`) AND the
  request flag. So the UI checkbox can only disable writes; writes stay off unless the env var is set.
- `core/translation_service.py`: kept WIP Azure warning globals plus the branch alias comment.
- `tests/test_llm_models.py`: subset test used gemini[2], but WIP trims Gemini to 2 models -> now uses groq.
Other files auto-merged.

Other changes: SAT panel (`sat_engine/ui_panel.py`) defaults to the global provider/model selection
(`core.llm_settings`) when `ui_prefs.json` has no saved choice; claude stays unchecked. One test added.

Dropped: nothing.
