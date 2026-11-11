import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _dialog(circles, offset=(0, 0)):
    from PyQt6.QtGui import QImage

    from rigconductor.gui.arena_editor import ArenaEditorDialog

    img = QImage(400, 300, QImage.Format.Format_Grayscale8)
    img.fill(128)
    d = ArenaEditorDialog(img, circles, offset, 3)
    d.resize(800, 700)
    d.show()
    return d


def test_status_flags_odd_size_and_same_size_fixes_it():
    d = _dialog([(100, 100, 40), (200, 100, 40), (300, 100, 30)])
    assert "odd size: A02" in d.status.text()
    d.canvas.same_size()
    assert "odd" not in d.status.text()
    d.canvas.undo()
    assert d.circles()[2][2] == 30


def test_screen_camera_roundtrip_with_offset_and_hit():
    d = _dialog([(700, 400, 40)], offset=(600, 300))
    c = d.canvas
    p = c.to_screen(700, 400)
    assert c.to_camera(p) == pytest.approx((700, 400))
    assert c.hit(p) == (0, "move")
    s = c._scale()
    from PyQt6.QtCore import QPointF

    assert c.hit(QPointF(p.x() + 40 * s, p.y())) == (0, "rim")


def test_resize_all_and_reset():
    d = _dialog([(100, 100, 40), (200, 100, 40)])
    d.canvas.resize_all(1.5)
    assert {c[2] for c in d.circles()} == {60}
    d.canvas.set_circles(d.original)
    assert {c[2] for c in d.circles()} == {40}
