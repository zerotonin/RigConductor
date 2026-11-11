import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import Qt  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def wiz(tmp_path, monkeypatch, cfg):
    from rigconductor import sleapenv
    from rigconductor.gui import labelling_wizard as lw
    from rigconductor.labels import store

    monkeypatch.setattr(store, "LABEL_SETS_DIR", str(tmp_path / "sets"))
    monkeypatch.setattr(
        sleapenv, "find", lambda run=None: sleapenv.SleapEnv(tmp_path / "py", "1.6")
    )
    monkeypatch.setattr(lw, "removable_disks", lambda: [])
    s = store.LabelSet.create("triad", ["thorax"])
    (s.path / "v1.pkg.slp").write_bytes(b"x")
    s.add_version(
        {"frames": 31, "instances": 91, "recordings": {"oct": 31}, "scales": {"450px": 31}}
    )
    rec = cfg.data_root / "cam" / "rec_20261111_120000"
    (rec / "clips").mkdir(parents=True)
    for a in range(2):
        (rec / "clips" / f"rec_20261111_120000_arena_{a:02d}.mp4").write_bytes(b"")
    (rec / "clips" / "rec_20261111_120000_arenas.json").write_text(
        json.dumps({"arenas": [{"r": 247}, {"r": 247}]}), encoding="utf-8"
    )
    return lw.LabellingWizard(cfg)


def test_start_page_lists_sets_and_branches(wiz):
    from rigconductor.gui import labelling_wizard as lw

    start = wiz.page(lw.PAGE_START)
    start.initializePage()
    start.check_sleap()
    assert start.set_combo.currentText() == "triad" and start.isComplete()
    assert "31 frames" in start.summary.text()
    assert start.nextId() == lw.PAGE_RECS
    start.mode.button(1).setChecked(True)
    assert start.nextId() == lw.PAGE_CONTINUE
    start.mode.button(3).setChecked(True)
    assert start.nextId() == lw.PAGE_DATA


def test_recordings_validation_and_frames(wiz):
    from rigconductor.gui import labelling_wizard as lw

    wiz.page(lw.PAGE_START).initializePage()
    recs = wiz.page(lw.PAGE_RECS)
    recs.initializePage()
    assert recs.table.rowCount() == 1 and recs.table.item(0, 4).text() == "500px"
    assert not recs.isComplete()
    recs.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    recs.table.item(0, 1).setCheckState(Qt.CheckState.Checked)
    assert recs.isComplete() and recs.validatePage()
    assert wiz.lset.data["validation"] == {"500px": ["rec_20261111_120000"]}

    frames = wiz.page(lw.PAGE_FRAMES)
    frames.initializePage()
    frames.show_frames(
        [
            {
                "recording": "rec_20261111_120000",
                "arena": 0,
                "frame": 5,
                "reason": "uniform",
                "score": 0,
            }
        ],
        {"rec_20261111_120000"},
    )
    assert frames.isComplete() and "validation" in frames.table.item(0, 3).text()


def test_back_report_and_train_verdict(wiz):
    from rigconductor.gui import labelling_wizard as lw
    from rigconductor.labels.train import TrainPlan

    wiz.page(lw.PAGE_START).initializePage()
    back = wiz.page(lw.PAGE_BACK)
    back.report = {
        "skeleton": None,
        "summary": {"accepted": 1, "rejected": 1, "unlabelled": 3},
        "frames": [
            {
                "hash": "a",
                "status": "accepted",
                "reasons": [],
                "warnings": ["w"],
                "flies": 3,
                "recording": "r",
                "arena": 0,
                "source_frame": 1,
            },
            {"hash": "b", "status": "rejected", "reasons": ["2 flies"], "warnings": [], "flies": 2},
        ],
    }
    back.show_report()
    assert back.table.rowCount() == 2 and back.merge_btn.isEnabled()
    back.with_warnings.setChecked(False)
    assert back.accepted() == []

    train = wiz.page(lw.PAGE_TRAIN)
    train.plan = TrainPlan("triad", 1, "new", "old", "old")
    wiz.lset.add_model({"run": "new", "status": "submitted"})
    train.show_verdict(
        [
            {"model": "new", "set": "500px", "mAP": 0.8, "frames": 9},
            {"model": "old", "set": "500px", "mAP": 0.6, "frames": 9},
        ]
    )
    assert train.promote_btn.isEnabled() and train.table.item(0, 2).text() == "0.800"
    assert wiz.lset.data["models"][0]["status"] == "trained"


def test_continue_lists_open_packages_and_projects(wiz, monkeypatch, tmp_path):
    from rigconductor.gui import labelling_wizard as lw
    from rigconductor.labels import package as pkg_mod

    wiz.page(lw.PAGE_START).initializePage()
    lset = wiz.lset
    for pid, status in (("p1", "out"), ("p2", "merged")):
        d = lset.package_dir(pid)
        d.mkdir(parents=True)
        info = {"id": pid, "set": "triad", "frames": {"h": {}}, "prefill": ["m"]}
        (d / pkg_mod.INFO_FILE).write_text(json.dumps(info), encoding="utf-8")
        (d / pkg_mod.LABELS_FILE).write_bytes(b"x")
        lset.add_package({"id": pid, "frames": 12, "recordings": ["oct"]})
        lset.update_package(pid, status=status)
    proj = tmp_path / "oct2025_labels.slp"
    proj.write_bytes(b"x")
    lset.add_project(proj, note="seed of v1")
    opened = []
    monkeypatch.setattr(lw.sleapenv, "open_labelling", lambda env, slp: opened.append(slp))
    monkeypatch.setattr(lw.ContinuePage, "count_states", lambda self: None)
    page = wiz.page(lw.PAGE_CONTINUE)
    page.initializePage()
    assert page.table.rowCount() == 2
    assert [page.table.item(i, 1).text() for i in range(2)] == ["package", "project"]
    page.show_states({1: {"total": 151, "labelled": 31, "predicted": 120, "empty": 0}})
    assert page.table.item(1, 3).text() == "31" and "120 predicted" in page.bars[1].toolTip()
    page.bars[1].grab()  # paints
    page.table.selectRow(1)
    page.open_selected()
    assert opened == [proj.resolve()] and page.isComplete()

    back = wiz.page(lw.PAGE_BACK)
    back.initializePage()
    assert back.pkgs.count() == 2 and back.pkgs.itemData(1)["kind"] == "project"


def test_training_data_roles_and_totals(wiz, tmp_path, monkeypatch):
    from rigconductor.gui import labelling_wizard as lw

    wiz.page(lw.PAGE_START).initializePage()
    page = wiz.page(lw.PAGE_DATA)
    monkeypatch.setattr(page, "load", lambda *_: None)
    page.initializePage()
    assert page.version.currentData() == 1
    png = tmp_path / "ex.png"
    QtWidgets.QApplication.instance()  # pixmaps need the app
    from PyQt6.QtGui import QImage

    QImage(20, 20, QImage.Format.Format_RGB888).save(str(png))
    row = {
        "example": str(png),
        "example_from": [0, 5],
        "scale": "450px",
        "frames": 31,
        "instances": 91,
        "flies": {"3": 29, "2": 2},
        "sizes": {"560×560": 31},
        "arenas": [0, 1],
        "labellers": {"seed": 31},
    }
    page.show_rows(
        [
            {**row, "recording": "droso_18deg_20251002_102448"},
            {**row, "recording": "droso_20250610_101010", "scale": "200px", "frames": 10},
        ]
    )
    assert page.table.item(0, 7).text() == "3 flies: 29 · 2 flies: 2"
    assert page.table.item(0, 3).text() == "2025-10-02"
    assert not page.isComplete() and "No validation" in page.totals.text()
    page.roles[1].setCurrentIndex(page.roles[1].findData("val"))
    assert page.isComplete() and page.counts() == {"train": 31, "val": 10, "out": 0}
    page.roles[0].setCurrentIndex(page.roles[0].findData("out"))
    assert not page.isComplete()
    page.roles[0].setCurrentIndex(page.roles[0].findData("train"))
    assert page.validatePage()
    assert wiz.lset.data["validation"] == {"200px": ["droso_20250610_101010"]}
    assert wiz.train_version == 1 and wiz.excluded == []

    # "All": no validation recording needed, the frames are split automatically
    page.roles[1].setCurrentIndex(page.roles[1].findData("train"))
    assert not page.isComplete()
    page.roles[0].setCurrentIndex(page.roles[0].findData("all"))
    test = round(31 * lw.LABEL_TEST_SHARE)
    assert page.counts() == {"train": 31 - test + 10, "val": test, "out": 0}
    assert page.isComplete() and "≈" in page.totals.text()
    assert page.validatePage()
    assert wiz.shared == ["droso_18deg_20251002_102448"]
    assert wiz.lset.shared() == {"droso_18deg_20251002_102448": "450px"}
    assert wiz.lset.data["validation"] == {} and wiz.lset.has_validation()
    assert page.role_of("droso_18deg_20251002_102448") == "all"


def _package(lset, pid, folder=None):
    from rigconductor.labels import package as pkg_mod

    d = folder or lset.package_dir(pid)
    d.mkdir(parents=True)
    info = {"id": pid, "set": lset.name, "frames": {}}
    (d / pkg_mod.INFO_FILE).write_text(json.dumps(info), encoding="utf-8")
    (d / pkg_mod.LABELS_FILE).write_bytes(b"x")
    return d


def test_delete_package_warns_and_needs_the_name_when_labelled(wiz, monkeypatch, tmp_path):
    from rigconductor.gui import labelling_wizard as lw

    wiz.page(lw.PAGE_START).initializePage()
    lset = wiz.lset
    local = _package(lset, "p1")
    stick = _package(lset, "p1", tmp_path / "stick" / "triad_p1")
    lset.add_package({"id": "p1", "frames": 10, "frames_index": [["r", 0, 5]]})
    monkeypatch.setattr(lw.ContinuePage, "count_states", lambda self: None)
    monkeypatch.setattr(
        lw, "removable_disks", lambda: [type("D", (), {"mountpoint": str(tmp_path / "stick")})]
    )
    page = wiz.page(lw.PAGE_CONTINUE)
    page.initializePage()
    assert page.items[0]["folder"] == stick
    page.show_states({0: {"total": 10, "labelled": 4, "predicted": 6, "empty": 0}})
    shown = []

    def warn(_p, _t, text, *_a):
        shown.append(text)
        return QtWidgets.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(lw.dialogs, "warning", warn)
    monkeypatch.setattr(lw, "_ask_text", lambda *a, **k: ("wrong", True))
    page.delete_selected()
    assert "4 labelled frame(s)" in shown[0] and str(local) in shown[0] and str(stick) in shown[0]
    assert local.is_dir() and stick.is_dir() and page.result.text() == "Not deleted."

    monkeypatch.setattr(lw, "_ask_text", lambda *a, **k: ("p1", True))
    page.delete_selected()
    assert not local.exists() and not stick.exists()
    assert lset.package("p1")["status"] == "deleted" and page.table.rowCount() == 0
    assert ("r", 0, 5) not in lset.labelled_frames()  # its frames can be picked again


def test_delete_cancelled_and_merged_packages_stay(wiz, monkeypatch):
    from rigconductor import ConductorError
    from rigconductor.gui import labelling_wizard as lw
    from rigconductor.labels import package as pkg_mod

    wiz.page(lw.PAGE_START).initializePage()
    lset = wiz.lset
    d = _package(lset, "p1")
    lset.add_package({"id": "p1", "frames": 3})
    monkeypatch.setattr(lw.ContinuePage, "count_states", lambda self: None)
    monkeypatch.setattr(
        lw.dialogs, "warning", lambda *a: QtWidgets.QMessageBox.StandardButton.Cancel
    )
    page = wiz.page(lw.PAGE_CONTINUE)
    page.initializePage()
    page.delete_selected()
    assert d.is_dir() and lset.package("p1")["status"] == "out"
    lset.update_package("p1", status="merged")
    with pytest.raises(ConductorError):
        pkg_mod.delete_package(lset, "p1")
    assert d.is_dir()


def test_copy_package_to_a_stick_and_replace_only_after_a_warning(wiz, monkeypatch, tmp_path):
    from rigconductor import ConductorError
    from rigconductor.gui import labelling_wizard as lw
    from rigconductor.labels import package as pkg_mod

    wiz.page(lw.PAGE_START).initializePage()
    lset = wiz.lset
    local = _package(lset, "p1")
    lset.add_package({"id": "p1", "frames": 3})
    stick = tmp_path / "stick"
    stick.mkdir()
    target = pkg_mod.copy_package(lset, local, stick)
    assert target == stick / "triad_p1" and (target / pkg_mod.LABELS_FILE).is_file()
    assert lset.package("p1")["copies"] == [str(target)]
    with pytest.raises(ConductorError):
        pkg_mod.copy_package(lset, local, stick)
    (target / pkg_mod.LABELS_FILE).write_bytes(b"labelled on the stick")

    monkeypatch.setattr(lw.ContinuePage, "count_states", lambda self: None)
    monkeypatch.setattr(lw, "removable_disks", lambda: [])
    page = wiz.page(lw.PAGE_CONTINUE)
    page.initializePage()
    monkeypatch.setattr(page, "_ask_destination", lambda: stick)
    monkeypatch.setattr(
        lw.dialogs, "warning", lambda *a: QtWidgets.QMessageBox.StandardButton.Cancel
    )
    page.copy_selected()
    assert (target / pkg_mod.LABELS_FILE).read_bytes() == b"labelled on the stick"

    pkg_mod.delete_package(lset, "p1")  # finds the recorded copy on the stick too
    assert not local.exists() and not target.exists()
