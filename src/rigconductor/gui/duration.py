# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.duration                                    ║
# ║  « how long to record »                                          ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Seconds box that selects its content on click (typing replaces  ║
# ║  the number), clamps instead of ignoring, and says it in words.  ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Recording-duration field."""

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QAbstractSpinBox, QHBoxLayout, QLabel, QSpinBox, QWidget

from rigconductor.constants import DURATION_MAX_S, DURATION_MIN_S


def format_duration(seconds: float) -> str:
    """``1800`` → ``"30 min"``, ``90`` → ``"1 min 30 s"``, ``3725`` → ``"1 h 2 min 5 s"``."""
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    parts = [f"{h} h" if h else "", f"{m} min" if m else "", f"{sec} s" if sec else ""]
    return " ".join(p for p in parts if p) or "0 s"


class _SelectAllSpinBox(QSpinBox):
    def focusInEvent(self, e):  # noqa: N802
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)

    def mousePressEvent(self, e):  # noqa: N802
        had_focus = self.hasFocus()
        super().mousePressEvent(e)
        if not had_focus:
            QTimer.singleShot(0, self.selectAll)


class DurationEdit(QWidget):
    """``value()`` / ``setValue()`` in seconds, with the duration in words beside it."""

    seconds_changed = pyqtSignal(int)

    def __init__(self, seconds: int = 600):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.spin = _SelectAllSpinBox()
        self.spin.setRange(DURATION_MIN_S, DURATION_MAX_S)
        self.spin.setSuffix(" s")
        self.spin.setCorrectionMode(QAbstractSpinBox.CorrectionMode.CorrectToNearestValue)
        self.spin.setToolTip("Seconds. Click and type the number; it replaces the old one.")
        self.words = QLabel()
        lay.addWidget(self.spin)
        lay.addWidget(self.words, 1)
        self.spin.valueChanged.connect(self._changed)
        self.setValue(seconds)
        self._changed(self.spin.value())

    def _changed(self, v: int):
        self.words.setText(f"= {format_duration(v)}")
        self.seconds_changed.emit(v)

    def value(self) -> int:
        return self.spin.value()

    def setValue(self, seconds: int) -> None:  # noqa: N802 - mirrors QSpinBox
        self.spin.setValue(min(max(int(seconds), DURATION_MIN_S), DURATION_MAX_S))

    def lineEdit(self):  # noqa: N802
        return self.spin.lineEdit()
