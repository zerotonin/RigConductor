"""User manual: structure, links, and drift against the live wizards.

The drift tests build both wizards and fail when a page has no section in the
manual, or a button, checkbox or group box the manual never names. A GUI
change that skips the manual turns the tests red instead of leaving users
with stale instructions.
"""

from __future__ import annotations

import os
import re

import pytest

from rigconductor.manual import (
    MANUAL_DIR,
    MANUAL_PATH,
    anchor_targets,
    image_targets,
    load_manual,
    parse_sections,
    slugify,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

# Labels built at run time from data, not words a manual can name.
GENERATED = re.compile(r"^A\d\d$")  # arena tiles on the Animals page


@pytest.fixture(scope="module")
def manual() -> str:
    return load_manual()


def normalise(label: str) -> str:
    """Compare labels the way a reader sees them."""
    text = label.replace("&", "").replace("…", "").replace("...", "").strip()
    return re.sub(r"\s+", " ", text).lower()


@pytest.fixture
def wizards(tmp_path, monkeypatch):
    from rigconductor import sleapenv
    from rigconductor.config import ConductorConfig
    from rigconductor.gui import labelling_wizard as lw
    from rigconductor.gui.labelling_wizard import LabellingWizard
    from rigconductor.gui.wizard import Wizard
    from rigconductor.labels import store

    # never the real label sets (a running training would be polled on Aoraki)
    monkeypatch.setattr(store, "LABEL_SETS_DIR", str(tmp_path / "sets"))
    monkeypatch.setattr(sleapenv, "find", lambda run=None: None)
    monkeypatch.setattr(lw, "removable_disks", lambda: [])
    (tmp_path / "DATA").mkdir()
    cfg = ConductorConfig(
        data_root=tmp_path / "DATA",
        sessions_dir=tmp_path / "sessions",
        profiles={"fake": {"default_duration_s": 10}},
    )
    return Wizard(cfg), LabellingWizard(cfg)


def pages(wizard):
    return [wizard.page(pid) for pid in wizard.pageIds()]


# ── structure ─────────────────────────────────────────────────────
def test_manual_exists_with_a_title(manual):
    assert MANUAL_PATH.is_file()
    assert parse_sections(manual)[0].level == 1


def test_internal_links_resolve(manual):
    slugs = {s.slug for s in parse_sections(manual)}
    broken = [t for t in anchor_targets(manual) if t not in slugs]
    assert not broken, f"links to missing headings: {broken}"


def test_images_exist(manual):
    missing = [t for t in image_targets(manual) if not (MANUAL_DIR / t).is_file()]
    assert not missing, f"run scripts/manual_screenshots.py; missing: {missing}"


def test_slugs():
    assert slugify("4 · Cut & check") == "4-cut-check"
    assert slugify("Pick new frames: Where to label") == "pick-new-frames-where-to-label"


# ── drift ─────────────────────────────────────────────────────────
def test_every_wizard_page_has_its_section(wizards, manual):
    from rigconductor.gui.help_window import page_section

    slugs = {s.slug for s in parse_sections(manual)}
    missing = [
        page_section(p)
        for w in wizards
        for p in pages(w)
        if slugify(page_section(p) or "") not in slugs
    ]
    assert not missing, f"Help on these pages finds no heading: {missing}"


def test_every_control_is_documented(wizards, manual):
    from PyQt6.QtWidgets import QAbstractButton, QGroupBox

    text = normalise(manual)
    undocumented = {}
    for w in wizards:
        for p in pages(w):
            found = {b.text() for b in p.findChildren(QAbstractButton)}
            found |= {g.title() for g in p.findChildren(QGroupBox)}
            labels = {t for t in found if t.strip() and not GENERATED.match(t)}
            missing = sorted(t for t in labels if normalise(t) not in text)
            if missing:
                undocumented[p.title()] = missing
    assert not undocumented, f"not named in docs/manual/user_manual.md: {undocumented}"


def test_help_button_opens_the_manual_at_the_page(wizards):
    from PyQt6.QtWidgets import QWizard

    from rigconductor.gui import help_window

    _main, training = wizards
    training.show()
    training.setStartId(training.pageIds()[-1])  # Train on Aoraki
    training.restart()
    training.button(QWizard.WizardButton.HelpButton).click()
    for _ in range(5):
        _app.processEvents()
    win = help_window._window
    assert win is not None and win.isVisible()
    assert win.contents.currentItem().text(0) == "Train on Aoraki"
    assert win.browser.verticalScrollBar().value() > 0
    win.search.setText("thorax")
    assert win.match_lbl.text() == ""
    win.search.setText("zzz-no-such-text")
    assert win.match_lbl.text() == "No matches"
    win.close()
    training.close()


def test_f1_is_the_help_key(wizards):
    from PyQt6.QtGui import QKeySequence
    from PyQt6.QtWidgets import QWizard

    for w in wizards:
        btn = w.button(QWizard.WizardButton.HelpButton)
        assert btn.text() == "Help"
        assert btn.shortcut() == QKeySequence(QKeySequence.StandardKey.HelpContents)
