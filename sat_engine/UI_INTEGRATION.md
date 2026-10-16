# SAT panel hook for ui_tkinter.py

Same two edits work in the worktree and the larger root `ui_tkinter.py` (both build an action bar
whose `self.form_error_lbl` the button is packed next to; without it the button floats top-right).

1. After the `from ui_server import ...` line:

```python
try:  # optional SAT generation panel
    from sat_engine.ui_panel import install as install_sat_panel
except ImportError:
    install_sat_panel = None
```

2. In `KnowleyTkinterApp.__init__`, right after `self._build_ui()`:

```python
        if install_sat_panel:
            install_sat_panel(self)
```

Adds a "SAT Üretimi" button; it opens the panel in its own window. The run is a detached subprocess
(`python -m sat_engine`), so it survives closing the main app. Writes need "Firebase'e kaydet" + confirmation;
the write flag is set only in the child's environment.
