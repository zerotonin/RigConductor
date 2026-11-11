import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")

from rigconductor.gui.progress import Eta, ProgressPanel, parse_progress  # noqa: E402


def test_parse_progress():
    p = parse_progress("@progress cut 0.4200 3/10 arenas, working on A04")
    assert (p.stage, p.fraction, p.detail) == ("cut", 0.42, "3/10 arenas, working on A04")
    assert parse_progress("cut: 10 arena clips") is None
    assert parse_progress("@progress cut nan-ish") is None
    assert parse_progress("@progress reduce 1.7").fraction == 1.0


def test_eta_like_tqdm():
    t = [100.0]
    eta = Eta(clock=lambda: t[0])
    assert eta.update(0.0) == (0.0, None)
    t[0] = 160.0
    elapsed, left = eta.update(0.25)
    assert elapsed == 60 and left == pytest.approx(180)


def test_stage_page_routes_progress_to_the_bar():
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = ProgressPanel()
    panel.show_line(parse_progress("@progress reduce 0.5 000000.mp4: frame 9000 of 18000"))
    assert panel.bar.value() == 500 and "Cropping the recording" in panel.text.text()
    panel.finish(True)
    assert panel.bar.value() == 1000
