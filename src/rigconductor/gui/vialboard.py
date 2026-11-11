# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.vialboard                                   ║
# ║  « drag vials onto arenas »                                      ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Arena assignment straight from the DrosoDB snapshot.            ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Drag-and-drop arena assignment straight from the DrosoDB snapshot.

Left: the vial shelf (active vials grouped by genotype, searchable, filtered by
location; Sickbay hidden by default). Right: one card per arena with three slots
(male 1, male 2, decapitated female). Drag vials onto slots; drop on a card's
title to fill its empty slots (a single vial fills both male slots); drop several
selected vials to fill slots in order. Every genotype has its own colour.
"""

from __future__ import annotations

import hashlib
from datetime import date

from PyQt6.QtCore import QMimeData, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rigconductor.constants import N_ARENAS, N_FLY_ROLES

MIME = "application/x-rigconductor-vials"
ROLE_LABELS = {"male_1": "♂ 1", "male_2": "♂ 2", "female_decap": "♀ decap."}
QUARANTINE = ("sickbay",)  # location names hidden unless asked for


def genotype_colour(name: str) -> QColor:
    """Stable, readable pastel per genotype."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
    return QColor.fromHsl(h % 360, 140, 205)


def _age_days(created: str | None) -> str:
    try:
        return f"{(date.today() - date.fromisoformat(created[:10])).days} d"
    except (TypeError, ValueError):
        return ""


# ── the shelf ────────────────────────────────────────────────────────────


class VialTree(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["vial", "gen.", "age", "location", "owner"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(True)

    def selected_codes(self) -> list[str]:
        codes = []
        for it in self.selectedItems():
            if it.childCount():  # a genotype row: all its visible vials
                codes += [
                    it.child(i).text(0)
                    for i in range(it.childCount())
                    if not it.child(i).isHidden()
                ]
            else:
                codes.append(it.text(0))
        return list(dict.fromkeys(codes))

    def startDrag(self, actions):  # noqa: N802 (Qt API)
        codes = self.selected_codes()
        if not codes:
            return
        md = QMimeData()
        md.setData(MIME, "\n".join(codes).encode())
        md.setText(" ".join(codes))
        d = QDrag(self)
        d.setMimeData(md)
        d.exec(Qt.DropAction.CopyAction)


class VialShelf(QWidget):
    def __init__(self, catalogue: list[dict]):
        super().__init__()
        self.catalogue = catalogue
        self.by_code = {v["code"]: v for v in catalogue}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        title = QLabel(f"<b>Vial shelf</b> — {len(catalogue)} active vials in DrosoDB")
        lay.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("search code, genotype, owner…")
        self.search.textChanged.connect(self.apply_filter)
        lay.addWidget(self.search)
        row = QHBoxLayout()
        self.location = QComboBox()
        self.location.addItem("all locations", None)
        for loc in sorted({v["location"] for v in catalogue if v["location"]}):
            self.location.addItem(loc, loc)
        self.location.currentIndexChanged.connect(self.apply_filter)
        self.show_quarantine = QCheckBox("show Sickbay")
        self.show_quarantine.toggled.connect(self.apply_filter)
        row.addWidget(self.location, 1)
        row.addWidget(self.show_quarantine)
        lay.addLayout(row)
        self.tree = VialTree()
        lay.addWidget(self.tree, 1)
        hint = QLabel("Drag vials onto the arena slots. Ctrl/Shift-click to take several.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._build()
        self.apply_filter()

    def _build(self):
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for v in self.catalogue:
            g = groups.get(v["genotype"])
            if g is None:
                g = QTreeWidgetItem([v["genotype"]])
                g.setBackground(0, genotype_colour(v["genotype"]))
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                g.setFlags(g.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                self.tree.addTopLevelItem(g)
                groups[v["genotype"]] = g
            it = QTreeWidgetItem(
                [
                    v["code"],
                    str(v.get("generation") or ""),
                    _age_days(v.get("created")),
                    v.get("location") or "",
                    v.get("owner") or "",
                ]
            )
            it.setToolTip(0, f"{v['code']} · {v['genotype']} · created {v.get('created') or '?'}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            g.addChild(it)
        for g in groups.values():
            g.setExpanded(True)
            g.setFirstColumnSpanned(True)  # long genotype names stay readable
        for i in range(5):
            self.tree.resizeColumnToContents(i)

    def apply_filter(self):
        q = self.search.text().strip().lower()
        loc = self.location.currentData()
        quar = self.show_quarantine.isChecked()
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            any_visible = False
            for ci in range(g.childCount()):
                it = g.child(ci)
                v = self.by_code[it.text(0)]
                text = " ".join(str(x) for x in v.values()).lower()
                hidden = (
                    (q and q not in text)
                    or (loc and v["location"] != loc)
                    or (not quar and v["location"].lower() in QUARANTINE)
                )
                it.setHidden(bool(hidden))
                any_visible |= not hidden
            g.setHidden(not any_visible)
            n = sum(not g.child(i).isHidden() for i in range(g.childCount()))
            g.setText(0, f"{g.text(0).split('  (')[0]}  ({n})")


# ── the arena board ──────────────────────────────────────────────────────


def _codes_from(md: QMimeData) -> list[str]:
    if md.hasFormat(MIME):
        return [c for c in bytes(md.data(MIME)).decode().split("\n") if c]
    if md.hasText():
        return [c for c in md.text().split() if c]
    return []


class VialSlot(QFrame):
    changed = pyqtSignal()

    def __init__(self, role: str, shelf: VialShelf):
        super().__init__()
        self.role, self.shelf, self.code = role, shelf, ""
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 2, 2)
        self.label = QLabel()
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedWidth(24)
        self.clear_btn.setFlat(True)
        self.clear_btn.setToolTip("empty this slot")
        self.clear_btn.clicked.connect(lambda: self.set_code(""))
        lay.addWidget(self.clear_btn)
        self.set_code("")

    def set_code(self, code: str):
        self.code = code
        v = self.shelf.by_code.get(code)
        role = ROLE_LABELS[self.role]
        if not code:
            self.label.setText(f"<span style='color:gray'>{role}: drop a vial</span>")
            self.setStyleSheet("VialSlot { border: 1px dashed gray; }")
        elif v is None:
            self.label.setText(
                f"{role}: <b>{code}</b> <span style='color:#b00'>not in DrosoDB</span>"
            )
            self.setStyleSheet("VialSlot { border: 2px solid #b00; }")
        else:
            c = genotype_colour(v["genotype"]).name()
            self.label.setText(f"{role}: <b>{code}</b><br><small>{v['genotype']}</small>")
            self.setStyleSheet(f"VialSlot {{ background: {c}; border: 1px solid #888; }}")
        self.clear_btn.setVisible(bool(code))
        self.changed.emit()

    def dragEnterEvent(self, e):  # noqa: N802
        if _codes_from(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        codes = _codes_from(e.mimeData())
        if codes:
            self.set_code(codes[0])
            e.acceptProposedAction()

    def mouseDoubleClickEvent(self, e):  # noqa: N802 - type a code by hand
        from PyQt6.QtWidgets import QInputDialog

        code, ok = QInputDialog.getText(self, "Vial", "Print code:", text=self.code)
        if ok:
            self.set_code(code.strip())


class ArenaCard(QFrame):
    changed = pyqtSignal()

    def __init__(self, index: int, shelf: VialShelf):
        super().__init__()
        self.index = index
        self.setFrameShape(QFrame.Shape.Box)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        head = QHBoxLayout()
        self.use = QCheckBox(f"A{index:02d}")
        f = self.use.font()
        f.setBold(True)
        self.use.setFont(f)
        self.use.setChecked(True)
        self.use.toggled.connect(self._toggle)
        head.addWidget(self.use)
        head.addStretch(1)
        head.addWidget(QLabel("age"))
        self.age = QDoubleSpinBox()
        self.age.setRange(0, 60)
        self.age.setDecimals(0)
        self.age.setSuffix(" d")
        self.age.setSpecialValueText("–")
        head.addWidget(self.age)
        lay.addLayout(head)
        self.slots = {r: VialSlot(r, shelf) for r in ROLE_LABELS}
        for s in self.slots.values():
            s.changed.connect(self.changed)
            lay.addWidget(s)

    def _toggle(self, on: bool):
        for s in self.slots.values():
            s.setEnabled(on)
        self.age.setEnabled(on)
        self.changed.emit()

    def drop_codes(self, codes: list[str], n_roles: int = N_FLY_ROLES):
        """Fill empty slots in order; a single vial fills both male slots."""
        roles = list(ROLE_LABELS)[:n_roles]
        if len(codes) == 1 and n_roles >= 2:
            codes = codes * 2
        empty = [r for r in roles if not self.slots[r].code]
        for r, c in zip(empty or roles, codes, strict=False):
            self.slots[r].set_code(c)

    def dragEnterEvent(self, e):  # noqa: N802
        if _codes_from(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        self.drop_codes(_codes_from(e.mimeData()))
        e.acceptProposedAction()

    def value(self) -> dict:
        return {r: s.code for r, s in self.slots.items()}


class VialBoard(QWidget):
    """Shelf + ten arena cards. ``values(n_roles)`` returns the ``arenas`` dict the
    Drosophila profile expects."""

    changed = pyqtSignal()

    def __init__(self, catalogue: list[dict], n_arenas: int = N_ARENAS):
        super().__init__()
        self.shelf = VialShelf(catalogue)
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        tools.addWidget(QLabel("<b>Arenas</b>"))
        tools.addStretch(1)
        for text, fn in (
            ("Copy A00 to all", self.copy_first),
            ("Fill from selection", self.fill_from_selection),
            ("Clear all", self.clear_all),
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            tools.addWidget(b)
        rlay.addLayout(tools)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        self.cards = [ArenaCard(i, self.shelf) for i in range(n_arenas)]
        for i, c in enumerate(self.cards):
            c.changed.connect(self.changed)
            grid.addWidget(c, i // 2, i % 2)
        scroll = QScrollArea()
        scroll.setWidget(grid_w)
        scroll.setWidgetResizable(True)
        rlay.addWidget(scroll, 1)
        self.summary = QLabel()
        rlay.addWidget(self.summary)
        self.changed.connect(self._summarise)
        split = QSplitter()
        split.addWidget(self.shelf)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        split.setSizes([440, 800])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self._summarise()

    # actions
    def copy_first(self):
        src = self.cards[0]
        for c in self.cards[1:]:
            if c.use.isChecked():
                for r, s in c.slots.items():
                    s.set_code(src.slots[r].code)
                c.age.setValue(src.age.value())

    def fill_from_selection(self):
        """Selected vials go round-robin into the male slots of the ticked arenas."""
        codes = self.shelf.tree.selected_codes()
        if not codes:
            return
        used = [c for c in self.cards if c.use.isChecked()]
        for i, c in enumerate(used):
            code = codes[i % len(codes)]
            c.slots["male_1"].set_code(code)
            c.slots["male_2"].set_code(code)

    def clear_all(self):
        for c in self.cards:
            for s in c.slots.values():
                s.set_code("")

    def _summarise(self):
        used = [c for c in self.cards if c.use.isChecked()]
        full = sum(all(s.code for s in c.slots.values()) for c in used)
        gts = {}
        for c in used:
            for s in c.slots.values():
                v = self.shelf.by_code.get(s.code)
                if v:
                    gts[v["genotype"]] = gts.get(v["genotype"], 0) + 1
        mix = ", ".join(f"{g} ×{n}" for g, n in sorted(gts.items()))
        self.summary.setText(f"{full}/{len(used)} arenas complete" + (f" — {mix}" if mix else ""))

    def genotypes_by_arena(self) -> dict[int, list[str]]:
        """Distinct genotypes per ticked arena, in slot order (for the preview overlay)."""
        out = {}
        for c in self.cards:
            if not c.use.isChecked():
                continue
            gts = []
            for s in c.slots.values():
                v = self.shelf.by_code.get(s.code)
                if v and v["genotype"] not in gts:
                    gts.append(v["genotype"])
            if gts:
                out[c.index] = gts
        return out

    # values for the profile
    def values(self, n_roles: int = N_FLY_ROLES) -> dict[str, list[dict]]:
        roles = list(ROLE_LABELS)[:n_roles]
        out = {}
        for c in self.cards:
            if not c.use.isChecked():
                continue
            age = c.age.value() or None
            out[str(c.index)] = [
                {"role": r, "print_code": c.slots[r].code, "age_days": age} for r in roles
            ]
        return out

    def set_values(self, arenas: dict) -> None:
        for c in self.cards:
            flies = arenas.get(str(c.index))
            c.use.setChecked(flies is not None)
            for f in flies or []:
                if f.get("role") in c.slots:
                    c.slots[f["role"]].set_code(f.get("print_code") or "")
                if f.get("age_days"):
                    c.age.setValue(float(f["age_days"]))
