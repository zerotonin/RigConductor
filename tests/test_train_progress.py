import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from rigconductor.gui import train_progress as tp  # noqa: E402

ROWS = [(0, None, 3e-4, None)] + [
    (e, 1e-4 / e, 1e-4 if e == 12 else 2e-4, 15.0) for e in range(1, 31)
]


def test_best_epoch_and_end_by_early_stopping():
    assert tp.best_epoch(ROWS) == (12, 1e-4)
    assert tp.epochs_done(ROWS) == 30
    assert tp.stop_epoch(ROWS, 20, 200) == 33
    assert tp.stop_epoch(ROWS, None, 200) == 200
    assert tp.stop_epoch(ROWS, 500, 200) == 200


def test_steps():
    assert tp.current_step({"trained": 0}) == 0
    assert tp.current_step({"trained": 1}) == 1
    assert tp.current_step({"trained": 2}) == 2
    assert tp.current_step({"trained": 2, "done": True}) == 3


def test_widget_shows_the_running_head():
    w = tp.TrainingProgress()
    w.show_status({}, 200)
    assert w.bar.format() == "Not started"
    st = {"trained": 0, "curves": {"centroid": ROWS}, "patience": {"centroid": 20}}
    w.show_status(st)
    assert w.bar.maximum() == 33 and w.bar.value() == 30
    assert w.bar.format() == "Centroid model: epoch 30 of 33"
    assert "15 s per epoch" in w.eta.text() and "1 min" in w.eta.text()
    assert "Centroid model" in w.steps[0].text() and "●" in w.steps[0].text()
    w.show_status({**st, "trained": 2})
    assert w.bar.maximum() == 0  # busy: scoring
    w.show_status({**st, "trained": 2, "done": True})
    assert w.bar.format() == "Done"
    w.resize(800, 260)
    assert not w.grab().isNull()  # the plots paint
