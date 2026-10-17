import os
import tkinter
import unittest
from unittest.mock import patch

import ui_tkinter


def _app():
    try:
        with patch.object(ui_tkinter.KnowleyTkinterApp, "_load_initial_data", lambda self: None), \
                patch("ui_tkinter.load_llm_selection", lambda: ["gemini", "groq"]), \
                patch("ui_server.load_selection", lambda: ["gemini", "groq"]):
            app = ui_tkinter.KnowleyTkinterApp()
    except tkinter.TclError as exc:  # no display available
        raise unittest.SkipTest(f"Tk unavailable: {exc}")
    app.withdraw()
    return app


class LlmSelectionUiTests(unittest.TestCase):
    def setUp(self):
        # JobManager applies LLM_ALLOWED_PROVIDERS process-wide; keep other tests unaffected.
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(lambda: setattr(__import__("core.llm_manager", fromlist=["x"]), "_global_llm_manager", None))

    def test_default_checkboxes_and_empty_selection_is_rejected(self):
        app = _app()
        try:
            self.assertEqual({k: v.get() for k, v in app.llm_vars.items()},
                             {"gemini": True, "groq": True, "cohere": False, "nvidia": False})
            for var in app.llm_vars.values():
                var.set(False)
            with patch("ui_tkinter.load_llm_selection", lambda: ["gemini", "groq"]):
                app._on_llm_selection_changed()  # rejected before anything is saved
            self.assertEqual({k for k, v in app.llm_vars.items() if v.get()}, {"gemini", "groq"})
            self.assertIn("En az bir", app.llm_select_lbl.cget("text"))
        finally:
            app.destroy()

    def test_building_the_app_does_not_change_the_process_environment(self):
        os.environ.pop("LLM_ALLOWED_PROVIDERS", None)
        app = _app()
        try:
            self.assertNotIn("LLM_ALLOWED_PROVIDERS", os.environ)
        finally:
            app.destroy()

    def test_model_boxes_default_to_all_and_follow_their_provider(self):
        app = _app()
        try:
            self.assertTrue(all(v.get() for models in app.llm_model_vars.values() for v in models.values()))
            self.assertEqual(set(app.llm_model_vars["groq"]), set(__import__("core.llm_settings", fromlist=["x"]).available_models("groq")))
            states = {name: {str(b.cget("state")) for b in buttons} for name, buttons in app.llm_model_buttons.items()}
            self.assertEqual(states["cohere"], {"disabled"})  # provider off -> its models are disabled
            for name in app.llm_model_vars["gemini"]:
                app.llm_model_vars["gemini"][name].set(False)
            with patch("ui_tkinter.load_llm_selection", lambda: ["gemini", "groq"]):
                app._on_llm_selection_changed()
            self.assertTrue(all(v.get() for v in app.llm_model_vars["gemini"].values()))  # rejected -> reverted
            self.assertIn("en az bir model", app.llm_select_lbl.cget("text").lower())
        finally:
            app.destroy()

    def test_payload_carries_the_firestore_write_flag(self):
        app = _app()
        try:
            self.assertIs(app._collect_payload()["write_to_firestore"], True)
            app.var_write_fs.set(False)
            self.assertIs(app._collect_payload()["write_to_firestore"], False)
        finally:
            app.destroy()

    def test_local_country_shows_country_languages_plus_en(self):
        app = _app()
        try:
            app._country_languages = {"Portekiz": ["pt"]}
            app.combo_country.set("Portekiz")
            app._update_country_languages()
            self.assertEqual(app.country_lang_lbl.cget("text"), "Yerel soru dilleri (otomatik): pt, en")
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
