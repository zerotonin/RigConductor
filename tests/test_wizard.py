import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")


def test_going_back_to_setup_updates_the_session(tmp_path):
    from rigconductor.config import ConductorConfig
    from rigconductor.gui.wizard import Wizard

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rig = tmp_path / "rig.yaml"
    rig.write_text(
        f"sessions_dir: {tmp_path / 'sessions'}\ndata_root: {tmp_path}\n", encoding="utf-8"
    )
    w = Wizard(ConductorConfig.load(rig))
    setup = w.pages["setup"]
    setup.duration.setValue(1800)
    assert w.ensure_session().spec.duration_s == 1800
    setup.duration.setValue(18)
    setup.rec_title.setText("short test")
    s = w.ensure_session()
    assert (s.spec.duration_s, s.spec.title) == (18, "short test")


class _Cam:
    def __init__(self, fmt="Mono8", fps=69.0):
        self.settings = {"PixelFormat": fmt, "AcquisitionFrameRate": fps, "DeviceModelName": "x"}
        self.configured = []

    def read(self, name):
        return self.settings.get(name)

    def configure(self, **kw):
        self.configured.append(kw)
        self.settings.update(kw)


def _wizard_with_model(tmp_path, cam):
    from rigconductor.config import ConductorConfig
    from rigconductor.gui.wizard import Wizard
    from rigconductor.models import Model

    rig = tmp_path / "rig.yaml"
    rig.write_text(
        f"sessions_dir: {tmp_path / 'sessions'}\ndata_root: {tmp_path}\n", encoding="utf-8"
    )
    w = Wizard(ConductorConfig.load(rig))
    w.make_recorder = lambda: cam
    setup = w.pages["setup"]
    m = Model(
        id="lab_oct2025_v2",
        organism="drosophila",
        kind="sleap-topdown",
        run="r",
        expects={"channels": 1, "fps": 25},
        grayscale_input=False,
    )
    setup.models = [m]
    setup.model.clear()
    setup.model.addItem(m.label, m.id)
    return w, setup


def test_page1_offers_to_set_camera_fps(tmp_path):
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    cam = _Cam(fps=69.0)
    w, setup = _wizard_with_model(tmp_path, cam)
    from rigconductor.presets import Preset

    fixed = Preset("f25", "fixed 25", fps=25)
    setup.current_preset = lambda: fixed
    asked = []
    setup._ask = lambda kind, c, p: asked.append(kind) or "set_fps"
    assert setup.check_camera(interactive=True)
    assert asked == ["fps"] and cam.configured == [{"AcquisitionFrameRate": 25.0}]
    setup._ask = lambda kind, c, p: asked.append(kind) or "cancel"
    cam.settings["AcquisitionFrameRate"] = 69.0
    assert not setup.check_camera(interactive=True)  # cancel keeps you on page 1


def test_page1_colour_camera_convert_to_gray(tmp_path):
    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from dataclasses import replace

    cam = _Cam(fmt="BayerRG8", fps=25.0)
    w, setup = _wizard_with_model(tmp_path, cam)
    colour = replace(setup.current_preset(), gray=False)
    setup.current_preset = lambda: colour
    setup._ask = lambda kind, c, p: "gray" if kind == "channels" else "cancel"
    assert setup.check_camera(interactive=True)
    assert setup.gray_override is True
    assert w.ensure_session().spec.gray is True
    assert w.session.spec.model_id == "lab_oct2025_v2"


def test_record_page_duration_is_the_setup_duration(tmp_path):
    from rigconductor.config import ConductorConfig
    from rigconductor.gui.duration import format_duration
    from rigconductor.gui.wizard import Wizard

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rig = tmp_path / "rig.yaml"
    rig.write_text(
        f"sessions_dir: {tmp_path / 'sessions'}\ndata_root: {tmp_path}\n", encoding="utf-8"
    )
    w = Wizard(ConductorConfig.load(rig))
    setup, rec = w.pages["setup"], w.pages["record"]
    setup.duration.setValue(1800)
    w.ensure_session()
    rec.duration.setValue(20)  # changed on the Record page, after the session exists
    assert setup.duration.value() == 20
    assert w.ensure_session().spec.duration_s == 20
    assert "20 s" in rec.status.text()
    assert format_duration(1800) == "30 min" and format_duration(3725) == "1 h 2 min 5 s"
    setup.duration.setValue(1)
    assert setup.duration.value() == 5  # clamped to the minimum, not ignored


def test_choosing_an_old_recording_resumes_its_session(tmp_path):
    import json

    from rigconductor.config import ConductorConfig
    from rigconductor.gui.wizard import Wizard

    _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rig = tmp_path / "rig.yaml"
    rig.write_text(
        f"sessions_dir: {tmp_path / 'sessions'}\ndata_root: {tmp_path}\n", encoding="utf-8"
    )
    rec = tmp_path / "more_20261111_112621"
    rec.mkdir()
    meta = {
        "experiment_id": "E2",
        "group_code": "G07",
        "test_temp_c": 30.0,
        "stimulus": "triad",
        "notes": "warm room",
        "arenas": {"3": [{"role": "male_1", "print_code": "ABC12", "age_days": 4.0}]},
    }
    state = {
        "spec": {
            "profile": "drosophila_triad",
            "session_id": "drosophila_triad_x",
            "title": "more",
            "duration_s": 60,
            "operator": "BG",
            "metadata": meta,
            "recording_dir": str(rec),
        },
        "done": {"record": {"at": "t"}, "cut": {"at": "t"}},
        "profile_state": {},
    }
    (rec / "conductor_session.json").write_text(json.dumps(state), encoding="utf-8")

    w = Wizard(ConductorConfig.load(rig))
    setup = w.pages["setup"]
    assert setup.load_recording(rec)
    form = w.pages["animals"].form
    assert (form.group.text(), form.temp.value(), form.notes.text()) == ("G07", 30.0, "warm room")
    assert form.board.values()["3"][0]["print_code"] == "ABC12"
    assert setup.duration.value() == 60 and setup.operator.text() == "BG"
    s = w.ensure_session()
    assert s.spec.session_id == "drosophila_triad_x" and s.is_done("cut")
    assert (tmp_path / "sessions" / "drosophila_triad_x.json").is_file()

    plain = tmp_path / "plain_rec"
    plain.mkdir()
    assert not setup.load_recording(plain)
    assert "not recorded with the Conductor" in setup.report.toPlainText()
