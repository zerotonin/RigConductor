# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.dialogs                                     ║
# ║  « message boxes that are as big as their message »              ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Drop-in for QMessageBox.information / warning / critical /      ║
# ║  question, text and list input, and a log viewer, used by every  ║
# ║  window of the Conductor.                                        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Message boxes sized to their text.

Qt's static message boxes keep a narrow fixed width, so long paths and log
lines get cut or wrap into a tall sliver. These versions widen the box to the
longest line (up to a share of the screen), make the text selectable, and move
very long messages behind *Show Details…*.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from rigconductor.constants import DIALOG_MAX_LINES, DIALOG_MIN_WIDTH, DIALOG_SCREEN_SHARE

Btn = QMessageBox.StandardButton
TAG_RE = re.compile(r"<[^>]+>")


def _plain_lines(text: str) -> list[str]:
    if Qt.mightBeRichText(text):
        text = TAG_RE.sub("", re.sub(r"<br\s*/?>|</p>|</li>", "\n", text))
    return text.splitlines() or [""]


def split_long(text: str, max_lines: int = DIALOG_MAX_LINES) -> tuple[str, str | None]:
    """Shown text and details: long plain messages keep their head in the box."""
    lines = text.splitlines()
    if Qt.mightBeRichText(text) or len(lines) <= max_lines:
        return text, None
    head = lines[: max_lines // 2]
    return "\n".join([*head, "", "… (more under Show Details)"]), text


def _screen_cap(parent: QWidget | None) -> int:
    screen = (parent.screen() if parent else None) or QGuiApplication.primaryScreen()
    return int(screen.availableGeometry().width() * DIALOG_SCREEN_SHARE) if screen else 900


def screen_side(parent: QWidget | None) -> int:
    """Largest square that fits the share of the screen (for pictures)."""
    screen = (parent.screen() if parent else None) or QGuiApplication.primaryScreen()
    if not screen:
        return 900
    g = screen.availableGeometry()
    return int(min(g.width(), g.height()) * DIALOG_SCREEN_SHARE)


def text_width(parent: QWidget | None, widget: QWidget, *texts: str) -> int:
    """Width that fits the longest line of ``texts``, within the screen share."""
    fm = widget.fontMetrics()
    lines = [line for t in texts for line in _plain_lines(str(t))]
    want = max(fm.horizontalAdvance(line) for line in lines) + 12
    return max(DIALOG_MIN_WIDTH, min(want, _screen_cap(parent)))


def fit(box: QMessageBox, parent: QWidget | None = None) -> QMessageBox:
    """Widen a hand-built message box to its text and informative text."""
    for name in ("qt_msgbox_label", "qt_msgbox_informativelabel"):
        label = box.findChild(QLabel, name)
        if label is not None:  # QMessageBox ignores resize(); it honours the label's minimum
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setMinimumWidth(text_width(parent, label, label.text()))
    return box


def make_box(
    parent: QWidget | None,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    buttons=Btn.Ok,
    default=Btn.NoButton,
) -> QMessageBox:
    shown, details = split_long(str(text))
    box = QMessageBox(icon, title, shown, buttons, parent)
    if default != Btn.NoButton:
        box.setDefaultButton(default)
    if details:
        box.setDetailedText(details)
    return fit(box, parent)


def _show(icon, parent, title, text, buttons, default) -> Btn:
    return Btn(make_box(parent, icon, title, text, buttons, default).exec())


def information(parent, title, text, buttons=Btn.Ok, default=Btn.NoButton) -> Btn:
    return _show(QMessageBox.Icon.Information, parent, title, text, buttons, default)


def warning(parent, title, text, buttons=Btn.Ok, default=Btn.NoButton) -> Btn:
    return _show(QMessageBox.Icon.Warning, parent, title, text, buttons, default)


def critical(parent, title, text, buttons=Btn.Ok, default=Btn.NoButton) -> Btn:
    return _show(QMessageBox.Icon.Critical, parent, title, text, buttons, default)


def question(parent, title, text, buttons=Btn.Yes | Btn.No, default=Btn.NoButton) -> Btn:
    return _show(QMessageBox.Icon.Question, parent, title, text, buttons, default)


# ── input ─────────────────────────────────────────────────────────
def _run_input(dlg: QInputDialog, parent, texts: list[str]) -> tuple[str, bool]:
    """Widen the input field (QInputDialog resets its own minimum) and run."""
    field = dlg.findChild(QComboBox) or dlg.findChild(QLineEdit)
    if field is not None:
        field.setMinimumWidth(text_width(parent, field, *texts))
    ok = dlg.exec() == QDialog.DialogCode.Accepted
    return dlg.textValue(), ok


def _input(parent, title: str, label: str) -> QInputDialog:
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    return dlg


def get_text(parent, title: str, label: str, text: str = "") -> tuple[str, bool]:
    """Like ``QInputDialog.getText``, wide enough for the label and the default."""
    dlg = _input(parent, title, label)
    dlg.setTextValue(text)
    return _run_input(dlg, parent, [label, text + "    "])


def get_item(
    parent, title: str, label: str, items: list[str], current: int = 0
) -> tuple[str, bool]:
    """Like ``QInputDialog.getItem`` (not editable), wide enough for the longest choice."""
    dlg = _input(parent, title, label)
    dlg.setComboBoxItems(items)
    dlg.setComboBoxEditable(False)
    if items:
        dlg.setTextValue(items[current])
    return _run_input(dlg, parent, [label, *[i + "        " for i in items]])


# ── long text ─────────────────────────────────────────────────────
class TextDialog(QDialog):
    """A heading and a read-only monospace text, sized to the text."""

    def __init__(self, parent, title: str, heading: str, text: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        head = QLabel(heading)
        head.setWordWrap(True)
        head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(head)
        self.view = QPlainTextEdit(text)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(self.view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._size_to(parent, text)
        self.view.moveCursor(self.view.textCursor().MoveOperation.End)

    def _size_to(self, parent, text: str):
        lines = text.splitlines() or [""]
        fm = self.view.fontMetrics()
        w = text_width(parent, self.view, *lines) + 40
        screen = (parent.screen() if parent else None) or QGuiApplication.primaryScreen()
        cap_h = int(screen.availableGeometry().height() * DIALOG_SCREEN_SHARE) if screen else 700
        h = min(fm.lineSpacing() * (len(lines) + 2) + 120, cap_h)
        self.resize(w, max(h, 240))


def show_text(parent, title: str, heading: str, text: str) -> None:
    """Show a log or other long text in a window that fits it."""
    TextDialog(parent, title, heading, text).exec()
