import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QLabel, QMessageBox  # noqa: E402

from rigconductor.gui import dialogs  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
PATH = "/projects/sciences/zoology/geurten_lab/HeatAggression/models/lab_oct2025_v2.centroid"


def _shown(box: QMessageBox) -> QLabel:
    box.show()
    _app.processEvents()
    return box.findChild(QLabel, "qt_msgbox_label")


def test_box_is_as_wide_as_its_longest_line():
    short = dialogs.make_box(None, QMessageBox.Icon.Warning, "t", "Choose a model first")
    long = dialogs.make_box(None, QMessageBox.Icon.Warning, "t", f"could not read {PATH}")
    lab_s, lab_l = _shown(short), _shown(long)
    assert long.width() > short.width() >= dialogs.DIALOG_MIN_WIDTH
    fm = lab_l.fontMetrics()
    assert lab_l.width() >= fm.horizontalAdvance(f"could not read {PATH}")
    assert lab_s.height() >= lab_s.heightForWidth(lab_s.width())
    for b in (short, long):
        b.close()


def test_rich_text_fits_and_is_selectable():
    text = "Delete package <b>p1</b>?<br><br>These folders will be deleted:<br>• " + PATH
    box = dialogs.make_box(None, QMessageBox.Icon.Warning, "t", text)
    lab = _shown(box)
    assert lab.height() >= lab.heightForWidth(lab.width())
    assert lab.width() >= lab.fontMetrics().horizontalAdvance("• " + PATH)
    assert lab.textInteractionFlags() & dialogs.Qt.TextInteractionFlag.TextSelectableByMouse
    box.close()


def test_width_is_capped_and_long_logs_go_to_details():
    huge = "x" * 5000
    box = dialogs.make_box(None, QMessageBox.Icon.Critical, "t", huge)
    _shown(box)
    screen = _app.primaryScreen().availableGeometry().width()
    lab = box.findChild(QLabel, "qt_msgbox_label")
    assert lab.minimumWidth() <= screen * dialogs.DIALOG_SCREEN_SHARE
    assert lab.height() >= lab.heightForWidth(lab.width())
    box.close()
    log = "\n".join(f"line {i}" for i in range(100))
    shown, details = dialogs.split_long(log)
    assert details == log and "Show Details" in shown and "line 99" not in shown
    assert dialogs.split_long("one\ntwo") == ("one\ntwo", None)


def _want(fm, text: str) -> int:
    cap = _app.primaryScreen().availableGeometry().width() * dialogs.DIALOG_SCREEN_SHARE
    return min(fm.horizontalAdvance(text), int(cap))


def test_hand_built_box_fits_text_and_informative_text():
    box = QMessageBox()
    box.setText("Model vs camera")
    box.setInformativeText(f"The model at {PATH} was trained on grey frames")
    dialogs.fit(box)
    box.show()
    _app.processEvents()
    info = box.findChild(QLabel, "qt_msgbox_informativelabel")
    assert info.width() >= _want(info.fontMetrics(), info.text())
    box.close()


def test_input_dialogs_fit_their_choices(monkeypatch):
    seen = {}

    def fake_exec(dlg):
        dlg.show()
        _app.processEvents()
        seen["w"] = dlg.width()
        seen["fm"] = dlg.fontMetrics()
        return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(QtWidgets.QInputDialog, "exec", fake_exec)
    choice = f"USB stick: {PATH}"
    value, ok = dialogs.get_item(None, "Copy", "Copy to:", [choice, "A folder…"])
    assert ok and value == choice
    assert seen["w"] >= _want(seen["fm"], choice)
    value, ok = dialogs.get_text(None, "Name", "Name:", PATH)
    assert ok and value == PATH and seen["w"] >= _want(seen["fm"], PATH)


def test_text_dialog_is_sized_to_the_log():
    log = "\n".join(f"{PATH}/_queue/arena_{i:02d}.log ok" for i in range(10))
    dlg = dialogs.TextDialog(None, "Log", "Last lines", log)
    assert dlg.width() >= _want(dlg.view.fontMetrics(), log.splitlines()[0])
    assert dlg.view.isReadOnly()
