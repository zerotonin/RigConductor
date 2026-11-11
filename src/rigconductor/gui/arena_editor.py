# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.arena_editor                                ║
# ║  « drag, resize, add and remove arena circles »                  ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Qt version of ThermoKourt's ArenaEditorCV2 (same controls). OpenCV ║
# ║  and Qt cannot share this process, so it is painted with QPainter. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Interactive arena circle editor."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rigconductor.arenas import (
    Circle,
    radius_outliers,
    scale_radii,
    sort_row_major,
    uniform_radii,
)
from rigconductor.constants import (
    ARENA_MIN_RADIUS_PX,
    ARENA_RESIZE_STEP,
    COLOUR_ARENA_ODD_SIZE,
    COLOUR_ARENA_SELECTED,
    COLOUR_ARENAS_OK,
    EDITOR_HANDLE_PX,
    LABEL_BOX_RGBA,
    OVERLAY_FONT_PX,
    OVERLAY_PEN_PX,
)

HELP = (
    "Drag a centre to move · drag the rim to resize · mouse wheel on an arena resizes it · "
    "right-click an arena to remove it, right-click empty space to add one · "
    "+ / − all bigger / smaller · U same size for all · Ctrl+Z undo"
)


class ArenaCanvas(QWidget):
    """The frame with editable circles. Circles are kept in full camera px; the
    image may be a crop of the camera frame starting at ``offset``."""

    changed = pyqtSignal()

    def __init__(self, image: QImage, circles: list[Circle], offset: tuple[int, int] = (0, 0)):
        super().__init__()
        self.image = image
        self.offset = offset
        self.circles: list[Circle] = sort_row_major(circles)
        self.selected: int | None = None
        self._drag: tuple[int, str, float, float] | None = None  # index, mode, dx, dy
        self._undo: list[list[Circle]] = []
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(480, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ── geometry ─────────────────────────────────────────────────────
    def _scale(self) -> float:
        return min(self.width() / self.image.width(), self.height() / self.image.height())

    def _origin(self) -> QPointF:
        s = self._scale()
        return QPointF(
            (self.width() - self.image.width() * s) / 2,
            (self.height() - self.image.height() * s) / 2,
        )

    def to_screen(self, x: float, y: float) -> QPointF:
        s, o = self._scale(), self._origin()
        return QPointF(o.x() + (x - self.offset[0]) * s, o.y() + (y - self.offset[1]) * s)

    def to_camera(self, p: QPointF) -> tuple[float, float]:
        s, o = self._scale(), self._origin()
        return (p.x() - o.x()) / s + self.offset[0], (p.y() - o.y()) / s + self.offset[1]

    def hit(self, p: QPointF) -> tuple[int | None, str]:
        """Arena under the screen point and whether it is the centre or the rim."""
        s = self._scale()
        best = (None, "", math.inf)
        for i, (cx, cy, r) in enumerate(self.circles):
            c = self.to_screen(cx, cy)
            d = math.hypot(p.x() - c.x(), p.y() - c.y())
            if abs(d - r * s) <= EDITOR_HANDLE_PX and abs(d - r * s) < best[2]:
                best = (i, "rim", abs(d - r * s))
            elif d < r * s - EDITOR_HANDLE_PX and d < best[2]:
                best = (i, "move", d)
        return best[0], best[1]

    # ── editing ──────────────────────────────────────────────────────
    def _remember(self):
        self._undo.append(list(self.circles))

    def set_circles(self, circles: list[Circle], renumber: bool = True):
        self.circles = sort_row_major(circles) if renumber else list(circles)
        self.changed.emit()
        self.update()

    def undo(self):
        if self._undo:
            self.selected = None
            self.set_circles(self._undo.pop())

    def same_size(self):
        self._remember()
        self.set_circles(uniform_radii(self.circles), renumber=False)

    def resize_all(self, factor: float):
        self._remember()
        self.set_circles(scale_radii(self.circles, factor), renumber=False)

    def _resize_one(self, i: int, r: float):
        cx, cy, _ = self.circles[i]
        self.circles[i] = (cx, cy, max(ARENA_MIN_RADIUS_PX, r))
        self.changed.emit()
        self.update()

    # ── events ───────────────────────────────────────────────────────
    def mousePressEvent(self, e):  # noqa: N802
        p = e.position()
        i, mode = self.hit(p)
        if e.button() == Qt.MouseButton.RightButton:
            self._remember()
            if i is not None:
                del self.circles[i]
                self.selected = None
            else:
                x, y = self.to_camera(p)
                rs = [c[2] for c in self.circles] or [100.0]
                self.circles.append((x, y, sorted(rs)[len(rs) // 2]))
            self.set_circles(self.circles)
            return
        self.selected = i
        if i is not None:
            self._remember()
            x, y = self.to_camera(p)
            cx, cy, _ = self.circles[i]
            self._drag = (i, mode, x - cx, y - cy)
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        p = e.position()
        if self._drag is None:
            _, mode = self.hit(p)
            self.setCursor(
                Qt.CursorShape.SizeAllCursor
                if mode == "move"
                else Qt.CursorShape.SizeFDiagCursor
                if mode == "rim"
                else Qt.CursorShape.ArrowCursor
            )
            return
        i, mode, dx, dy = self._drag
        x, y = self.to_camera(p)
        cx, cy, r = self.circles[i]
        if mode == "move":
            self.circles[i] = (x - dx, y - dy, r)
            self.changed.emit()
            self.update()
        else:
            self._resize_one(i, math.hypot(x - cx, y - cy))

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._drag is not None:
            self._drag = None
            sel = self.circles[self.selected] if self.selected is not None else None
            self.set_circles(self.circles)
            self.selected = self.circles.index(sel) if sel in self.circles else None

    def wheelEvent(self, e):  # noqa: N802
        i, _ = self.hit(e.position())
        if i is None:
            return
        self._remember()
        step = 1 + ARENA_RESIZE_STEP if e.angleDelta().y() > 0 else 1 - ARENA_RESIZE_STEP
        self.selected = i
        self._resize_one(i, self.circles[i][2] * step)

    def keyPressEvent(self, e):  # noqa: N802
        k, mod = e.key(), e.modifiers()
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.resize_all(1 + ARENA_RESIZE_STEP)
        elif k == Qt.Key.Key_Minus:
            self.resize_all(1 - ARENA_RESIZE_STEP)
        elif k == Qt.Key.Key_U:
            self.same_size()
        elif k == Qt.Key.Key_Z and mod & Qt.KeyboardModifier.ControlModifier:
            self.undo()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected is not None:
            self._remember()
            del self.circles[self.selected]
            self.selected = None
            self.set_circles(self.circles)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(17, 17, 17))
        s, o = self._scale(), self._origin()
        target = QRectF(o.x(), o.y(), self.image.width() * s, self.image.height() * s)
        p.drawPixmap(target, QPixmap.fromImage(self.image), QRectF(self.image.rect()))
        font = QFont(p.font())
        font.setPixelSize(OVERLAY_FONT_PX)
        font.setBold(True)
        p.setFont(font)
        odd = set(radius_outliers(self.circles))
        for i, (cx, cy, r) in enumerate(self.circles):
            colour = QColor(
                *(
                    COLOUR_ARENA_SELECTED
                    if i == self.selected
                    else COLOUR_ARENA_ODD_SIZE
                    if i in odd
                    else COLOUR_ARENAS_OK
                )
            )
            c = self.to_screen(cx, cy)
            pen = QPen(colour)
            pen.setWidth(OVERLAY_PEN_PX)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r * s, r * s)
            p.setBrush(colour)
            p.drawEllipse(c, 4, 4)
            rim = QPointF(c.x() + r * s, c.y())
            p.drawPolygon(
                [
                    QPointF(rim.x() - 6, rim.y()),
                    QPointF(rim.x(), rim.y() - 6),
                    QPointF(rim.x() + 6, rim.y()),
                    QPointF(rim.x(), rim.y() + 6),
                ]
            )
            label = f"A{i:02d}  r {r:.0f}"
            w = p.fontMetrics().horizontalAdvance(label) + 8
            box = QRectF(c.x() - w / 2, c.y() + 8, w, p.fontMetrics().height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*LABEL_BOX_RGBA))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(colour)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class ArenaEditorDialog(QDialog):
    """Edit a layout; ``circles()`` returns the result in full camera px."""

    def __init__(
        self,
        image: QImage,
        circles: list[Circle],
        offset=(0, 0),
        n_expected=10,
        detect=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Fix arenas")
        self.resize(1100, 900)
        self.n_expected = n_expected
        self.detect = detect  # () -> list[Circle] in full camera px, or None
        self.original = list(circles)
        self.canvas = ArenaCanvas(image, circles, offset)
        self.canvas.changed.connect(self._status)
        lay = QVBoxLayout(self)
        tools = QHBoxLayout()
        for text, fn in (
            ("Same size for all", self.canvas.same_size),
            ("All bigger", lambda: self.canvas.resize_all(1 + ARENA_RESIZE_STEP)),
            ("All smaller", lambda: self.canvas.resize_all(1 - ARENA_RESIZE_STEP)),
            ("Undo", self.canvas.undo),
            ("Reset", lambda: self.canvas.set_circles(self.original)),
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            tools.addWidget(b)
        if detect is not None:
            b = QPushButton("Find arenas again")
            b.clicked.connect(self._redetect)
            tools.addWidget(b)
        tools.addStretch(1)
        self.status = QLabel()
        tools.addWidget(self.status)
        lay.addLayout(tools)
        lay.addWidget(self.canvas, 1)
        hint = QLabel(HELP)
        hint.setWordWrap(True)
        lay.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._status()

    def _redetect(self):
        try:
            found = self.detect()
        except Exception as e:  # noqa: BLE001 - shown to the operator
            self.status.setText(f"detection failed: {e}")
            return
        if found:
            self.canvas._remember()
            self.canvas.set_circles(found)

    def _status(self):
        c = self.canvas.circles
        odd = radius_outliers(c)
        msg = f"{len(c)}/{self.n_expected} arenas"
        if odd:
            msg += " · odd size: " + ", ".join(f"A{i:02d}" for i in odd)
        self.status.setText(msg)

    def circles(self) -> list[Circle]:
        return sort_row_major(self.canvas.circles)
