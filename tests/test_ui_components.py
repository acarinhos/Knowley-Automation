import unittest
from types import SimpleNamespace

from ui_tkinter import SmoothProgressBar


class SmoothProgressBarTests(unittest.TestCase):
    def _bar(self, percent):
        calls = []
        bar = SimpleNamespace(
            pct=percent,
            bar_bg="#000000",
            delete=lambda *args: None,
            winfo_width=lambda: 100,
            winfo_height=lambda: 10,
            create_rectangle=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        return bar, calls

    def test_zero_progress_draws_only_track(self):
        bar, calls = self._bar(0)
        SmoothProgressBar._draw(bar)
        self.assertEqual(len(calls), 1)

    def test_positive_progress_draws_fill(self):
        bar, calls = self._bar(25)
        SmoothProgressBar._draw(bar)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], (0, 0, 25, 10))


if __name__ == "__main__":
    unittest.main()
