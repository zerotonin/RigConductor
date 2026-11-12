# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.train_progress                              ║
# ║  « how far the training on Aoraki has come, as a picture »       ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Steps (centroid → instance → scoring), an epoch bar with time   ║
# ║  left, and the loss curves sleap-nn writes per epoch.            ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Training progress for the Train on Aoraki page.

Fed with :func:`rigconductor.labels.train.parse_status` results. Training
stops early when the validation loss stops improving, so the time left is an
upper bound.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from rigconductor.constants import WONG
from rigconductor.gui.duration import format_duration

HEAD_TEXT = {"centroid": "Centroid model", "centered_instance": "Instance model"}
STEP_TEXT = (*HEAD_TEXT.values(), "Scoring")
TRAIN_COLOUR = QColor(*WONG["blue"])
VAL_COLOUR = QColor(*WONG["vermillion"])
DONE_COLOUR = QColor(*WONG["bluish_green"])
RUN_COLOUR = QColor(*WONG["orange"])
WAIT_COLOUR = QColor(150, 150, 150)
PLOT_HEIGHT = 170
PLOT_PAD = (46, 10, 12, 24)  # left, top, right, bottom (px)


def epochs_done(rows: list[tuple]) -> int:
    """Finished epochs: rows with a training loss (row 0 is the check before training)."""
    return sum(1 for r in rows if r[1] is not None)


def seconds_per_epoch(rows: list[tuple]) -> float | None:
    times = [r[3] for r in rows if r[3] is not None]
    return sum(times) / len(times) if times else None


def best_epoch(rows: list[tuple]) -> tuple[int, float] | None:
    """Epoch with the lowest validation loss (the checkpoint sleap-nn keeps)."""
    vals = [(r[0], r[2]) for r in rows if r[2] is not None and r[1] is not None]
    return min(vals, key=lambda x: x[1]) if vals else None


def stop_epoch(rows: list[tuple], patience: int | None, max_epochs: int) -> int:
    """Last epoch training can reach: early stopping ends it ``patience`` epochs
    after the best one unless the validation loss improves again."""
    best = best_epoch(rows)
    if patience is None or best is None:
        return max_epochs
    return min(max_epochs, best[0] + patience + 1)


def current_step(st: dict) -> int:
    """0 centroid, 1 instance, 2 scoring, 3 done."""
    if st.get("done"):
        return 3
    return min(int(st.get("trained") or 0), 2)


class LossPlot(QWidget):
    """Training and validation loss per epoch, log scale."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.rows: list[tuple] = []
        self.x_epochs = 1
        self.setMinimumHeight(PLOT_HEIGHT)

    def set_rows(self, rows: list[tuple], x_epochs: int) -> None:
        """``x_epochs``: how far the x axis reaches (the expected last epoch)."""
        self.rows, self.x_epochs = list(rows), max(1, x_epochs)
        self.update()

    def _series(self, col: int) -> list[tuple[int, float]]:
        return [(r[0], r[col]) for r in self.rows if r[col] is not None and r[col] > 0]

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        left, top, right, bottom = PLOT_PAD
        box = QRectF(left, top + 14, self.width() - left - right, self.height() - top - 14 - bottom)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        p.setFont(font)
        p.setPen(pal.text().color())
        p.drawText(QRectF(left, top - 2, box.width(), 16), Qt.AlignmentFlag.AlignLeft, self.title)
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawRect(box)
        train, val = self._series(1), self._series(2)
        if not train and not val:
            p.setPen(pal.placeholderText().color())
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, "waiting for the first epoch")
            return
        logs = [math.log10(v) for _, v in train + val]
        lo, hi = math.floor(min(logs)), math.ceil(max(logs))
        hi = hi if hi > lo else lo + 1
        x_max = max(10, self.x_epochs, max(e for e, _ in train + val) + 1)

        def at(epoch: float, value: float) -> QPointF:
            x = box.left() + box.width() * epoch / x_max
            y = box.bottom() - box.height() * (math.log10(value) - lo) / (hi - lo)
            return QPointF(x, y)

        p.setPen(pal.text().color())
        for dec in range(lo, hi + 1):  # 1e-5, 1e-4, …
            y = box.bottom() - box.height() * (dec - lo) / (hi - lo)
            p.drawText(QRectF(0, y - 8, left - 4, 16), Qt.AlignmentFlag.AlignRight, f"1e{dec}")
        p.drawText(QRectF(box.left(), box.bottom() + 2, 40, 16), Qt.AlignmentFlag.AlignLeft, "0")
        p.drawText(
            QRectF(box.right() - 80, box.bottom() + 2, 80, 16),
            Qt.AlignmentFlag.AlignRight,
            f"epoch {x_max}",
        )
        for series, colour in ((train, TRAIN_COLOUR), (val, VAL_COLOUR)):
            if not series:
                continue
            path = QPainterPath(at(*series[0]))
            for e, v in series[1:]:
                path.lineTo(at(e, v))
            p.setPen(QPen(colour, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        best = best_epoch(self.rows)
        if best:
            pt = at(*best)
            p.setPen(QPen(pal.base().color(), 1.5))
            p.setBrush(VAL_COLOUR)
            p.drawEllipse(pt, 4.5, 4.5)
            p.setPen(pal.text().color())
            p.drawText(pt + QPointF(6, -6), f"best {best[0]}")


class TrainingProgress(QWidget):
    """Steps, epoch bar and loss curves of one training run."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        steps = QHBoxLayout()
        self.steps = [QLabel(t) for t in STEP_TEXT]
        for i, s in enumerate(self.steps):
            if i:
                steps.addWidget(QLabel("→"))
            steps.addWidget(s)
        steps.addStretch()
        lay.addLayout(steps)
        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        lay.addWidget(self.bar)
        self.eta = QLabel("")
        lay.addWidget(self.eta)
        plots = QHBoxLayout()
        self.plots = {h: LossPlot(t) for h, t in HEAD_TEXT.items()}
        for plot in self.plots.values():
            plots.addWidget(plot, 1)
        lay.addLayout(plots)
        legend = QLabel(
            f'<span style="color:{TRAIN_COLOUR.name()}">━ training loss</span> &nbsp; '
            f'<span style="color:{VAL_COLOUR.name()}">━ validation loss</span> &nbsp; '
            "● best epoch (the one kept)"
        )
        lay.addWidget(legend)
        self.max_epochs = 1
        self.show_status({})

    def show_status(self, st: dict, max_epochs: int | None = None) -> None:
        """Draw a parsed status (empty dict: nothing started yet)."""
        if max_epochs:
            self.max_epochs = max_epochs
        curves = st.get("curves", {})
        step = current_step(st) if st else -1
        for i, label in enumerate(self.steps):
            colour = DONE_COLOUR if i < step else RUN_COLOUR if i == step else WAIT_COLOUR
            mark = "✓ " if i < step else "● " if i == step else ""
            weight = "bold" if i == step else "normal"
            label.setText(
                f'<span style="color:{colour.name()}; font-weight:{weight}">'
                f"{mark}{STEP_TEXT[i]}</span>"
            )
        patience = st.get("patience", {})
        for head, plot in self.plots.items():
            rows = curves.get(head, [])
            plot.set_rows(rows, stop_epoch(rows, patience.get(head), self.max_epochs))
        self._bar(step, curves, patience)

    def _bar(self, step: int, curves: dict, patience: dict) -> None:
        heads = list(HEAD_TEXT)
        if 0 <= step < len(heads):
            head = heads[step]
            rows = curves.get(head, [])
            done = epochs_done(rows)
            stop = stop_epoch(rows, patience.get(head), self.max_epochs)
            self.bar.setRange(0, stop)
            self.bar.setValue(min(done, stop))
            self.bar.setFormat(f"{HEAD_TEXT[head]}: epoch {done} of {stop}")
            per = seconds_per_epoch(rows)
            self.eta.setText(self._eta_text(done, stop, per, patience.get(head)))
        elif step == len(heads):
            self.bar.setRange(0, 0)  # busy: scoring both models on the validation sets
            self.bar.setFormat("Scoring the new and the current model")
            self.eta.setText("")
        else:
            self.bar.setRange(0, 1)
            self.bar.setValue(1 if step == 3 else 0)
            self.bar.setFormat("Done" if step == 3 else "Not started")
            self.eta.setText("")

    def _eta_text(self, done: int, stop: int, per: float | None, patience: int | None) -> str:
        if per is None:
            return ""
        left = format_duration(max(60, round((stop - done) * per / 60) * 60))
        why = (
            f"it stops {patience} epochs after the best one; a new best moves the end "
            f"(at most {self.max_epochs})"
            if patience is not None and stop < self.max_epochs
            else f"at most {self.max_epochs} epochs"
        )
        return f"{per:.0f} s per epoch, about {left} left for this model ({why})"
