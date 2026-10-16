# SAT panel hook for ui_tkinter.py

The hook is NOT in this repo's `ui_tkinter.py`; apply the two guarded additions below manually.

Same two edits work in the worktree and the larger root `ui_tkinter.py` (both build an action bar
whose `self.form_error_lbl` the button is packed next to; without it the button floats top-right).

1. After the `from ui_server import ...` line:

```python
try:  # optional SAT generation panel
    from sat_engine.ui_panel import install as install_sat_panel
except Exception:
    install_sat_panel = None
```

2. In `KnowleyTkinterApp.__init__`, right after `self._build_ui()`:

```python
        if install_sat_panel:
            try:
                install_sat_panel(self)
            except Exception:  # never crash app start
                logging.getLogger(__name__).exception("SAT panel could not be installed")
```

(`logging` must be imported in the target file.) Adds a "SAT Üretimi" button; it opens the panel in its own window. The run is a detached subprocess
(`python -m sat_engine`), so it survives closing the main app. Writes need "Firebase'e kaydet" + confirmation;
the write flag is set only in the child's environment.
