import json

import pytest

from rigconductor import ConductorError
from rigconductor.motif import FakeMotif
from rigconductor.session import STAGES, ConductorSession, ConductorSpec, new_session_id
from tests.fake_profile import FakeProfile

REGISTRY = """
- id: m1
  organism: fake
  kind: sleap-topdown
  run: run_m1
  trained: 2026-11-01
  expects: {channels: 1, polarity: bright, invert: true, min_side_px: 224, fps: 25}
"""


def _probe(path):
    from rigconductor.compat import ClipInfo

    return ClipInfo(path, 512, 512, 25.0, "gray", 220.0)


def _spec(cfg, **kw):
    d = {
        "profile": "fake",
        "session_id": new_session_id("fake"),
        "title": "Fake_Run",
        "duration_s": 5,
        "metadata": {"ok": True},
        "model_id": "m1",
        "export_dir": str(cfg.sessions_dir.parent / "usb"),
    }
    d.update(kw)
    return ConductorSpec(**d)


def _registry(cfg):
    p = cfg.registry_cache("fake")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(REGISTRY, encoding="utf-8")


def _fast_record(sess, motif):
    t = {"now": 0.0}

    def clock():
        return t["now"] if t["now"] > 1e6 else __import__("time").time() + t["now"]

    def sleep(s):
        t["now"] += s
        motif.tick(s)

    return sess.record(clock=clock, sleep=sleep, grace_s=30)


def test_full_run_with_fake_motif(cfg):
    _registry(cfg)
    motif = FakeMotif(cfg.data_root)
    prof = FakeProfile()
    sess = ConductorSession(cfg, _spec(cfg), motif, log=lambda s: None, profile=prof)
    sess.probe = _probe
    rec = _fast_record(sess, motif)
    assert rec.is_dir() and sess.spec.recording_dir == str(rec)
    assert motif.calls[0] == ("configure", cfg.motif.camera)  # standard settings first
    start = motif.calls[1]
    assert start[0] == "start" and start[3] == cfg.motif.codec
    assert start[4]["conductor_session"] == sess.spec.session_id
    for st in STAGES[1:]:
        getattr(sess, st)() if st != "export" else sess.export(None)
    assert prof.calls == ["cut", "register", "upload", "submit", "poll", "fetch", "score", "export"]
    assert sess.state["done"]["reduce"]["skipped"] is True  # fake profile has no arenas
    assert all(sess.is_done(s) for s in STAGES)
    dest = cfg.sessions_dir.parent / "usb" / "fake" / sess.spec.session_id
    assert (dest / "MANIFEST.sha256").is_file()
    assert (dest / "events.csv").is_file() and (dest / "session.json").is_file()
    # the model run reached the profile
    assert sess.state["profile_state"]["model_run"] == "run_m1"
    # state copies
    assert (rec / "conductor_session.json").is_file()
    st = json.loads(sess.state_path.read_text(encoding="utf-8"))
    assert st["done"]["submit"]["job_id"] == "4242"


def test_resume_skips_done_stages(cfg):
    _registry(cfg)
    motif = FakeMotif(cfg.data_root)
    prof = FakeProfile(fail_on="upload")
    spec = _spec(cfg)
    sess = ConductorSession(cfg, spec, motif, log=lambda s: None, profile=prof)
    _fast_record(sess, motif)
    sess.cut()
    sess.register()
    with pytest.raises(RuntimeError):
        sess.upload()
    # new process, same session id
    prof2 = FakeProfile()
    spec2 = ConductorSpec(
        profile="fake",
        session_id=spec.session_id,
        title="x",
        duration_s=1,
        metadata={"ok": True},
        model_id="m1",
        export_dir=spec.export_dir,
    )
    sess2 = ConductorSession(cfg, spec2, None, log=lambda s: None, profile=prof2)
    sess2.probe = _probe
    assert sess2.spec.recording_dir == spec.recording_dir
    sess2.run(("record", "cut", "register", "upload", "submit"))
    assert prof2.calls == ["upload", "submit"]


def test_existing_recording_skips_motif(cfg):
    rec = cfg.data_root / "droso_x_20260101_000000"
    rec.mkdir()
    (rec / "metadata.yaml").write_text("title: x", encoding="utf-8")
    sess = ConductorSession(
        cfg, _spec(cfg, recording_dir=str(rec)), None, log=lambda s: None, profile=FakeProfile()
    )
    assert sess.record() == rec
    assert sess.state["done"]["record"]["existing"] is True


def test_record_refuses_bad_metadata_and_camera_error(cfg):
    motif = FakeMotif(cfg.data_root, camera_status="ERROR")
    sess = ConductorSession(
        cfg, _spec(cfg, metadata={}), motif, log=lambda s: None, profile=FakeProfile()
    )
    with pytest.raises(ConductorError, match="metadata"):
        sess.record()
    sess2 = ConductorSession(cfg, _spec(cfg), motif, log=lambda s: None, profile=FakeProfile())
    with pytest.raises(ConductorError, match="camera status"):
        sess2.record()
    assert motif.calls == []


def test_poll_failure_raises(cfg):
    _registry(cfg)
    rec = cfg.data_root / "r_20260101_000000"
    rec.mkdir()
    (rec / "metadata.yaml").write_text("title: r", encoding="utf-8")
    prof = FakeProfile(poll_ok=False)
    sess = ConductorSession(
        cfg, _spec(cfg, recording_dir=str(rec)), None, log=lambda s: None, profile=prof
    )
    sess.probe = _probe
    sess.run(("record", "cut", "register", "upload", "submit"))
    with pytest.raises(ConductorError, match="did not finish"):
        sess.poll()
    assert sess.is_done("poll") and sess.state["done"]["poll"]["ok"] is False


def test_submit_needs_model(cfg):
    rec = cfg.data_root / "r_20260101_000000"
    rec.mkdir()
    (rec / "metadata.yaml").write_text("title: r", encoding="utf-8")
    sess = ConductorSession(
        cfg,
        _spec(cfg, recording_dir=str(rec), model_id=None),
        None,
        log=lambda s: None,
        profile=FakeProfile(),
    )
    sess.run(("record", "cut", "register", "upload"))
    with pytest.raises(ConductorError, match="model"):
        sess.submit()


def test_status_lines(cfg):
    sess = ConductorSession(cfg, _spec(cfg), None, log=lambda s: None, profile=FakeProfile())
    lines = sess.status_lines()
    assert lines[0].startswith("session fake_") and len(lines) == 1 + len(STAGES)


def test_submit_blocked_by_model_mismatch_unless_forced(cfg):
    from rigconductor.compat import ClipInfo

    _registry(cfg)
    rec = cfg.data_root / "r_20260101_000000"
    rec.mkdir()
    (rec / "metadata.yaml").write_text("title: r", encoding="utf-8")
    prof = FakeProfile()
    sess = ConductorSession(
        cfg, _spec(cfg, recording_dir=str(rec)), None, log=lambda s: None, profile=prof
    )
    sess.probe = lambda p: ClipInfo(p, 512, 512, 25.0, "gray", 20.0)  # bright flies: no inversion
    sess.run(("record", "cut", "register", "upload"))
    with pytest.raises(ConductorError, match="does not match"):
        sess.submit()
    assert "submit" not in prof.calls and sess.state["model_check"]["ok"] is False
    sess.spec.force_model = True
    sess.submit()
    assert "submit" in prof.calls


def test_redo_cut_keeps_record_and_crop(cfg):
    _registry(cfg)
    motif = FakeMotif(cfg.data_root)
    prof = FakeProfile()
    sess = ConductorSession(cfg, _spec(cfg), motif, log=lambda s: None, profile=prof)
    sess.probe = _probe
    _fast_record(sess, motif)
    sess.cut()
    sess.reduce()
    sess.register()
    sess.redo_cut()
    assert sess.is_done("record") and sess.is_done("reduce")
    assert not sess.is_done("cut") and not sess.is_done("register")
    rec = sess._ctx().recording_dir
    assert list(rec.glob("clips.old-*")) and not (rec / "clips").exists()
    sess.cut()
    assert prof.calls.count("cut") == 2


def test_redo_analysis_allows_a_second_submit(cfg):
    _registry(cfg)
    motif = FakeMotif(cfg.data_root)
    prof = FakeProfile()
    sess = ConductorSession(cfg, _spec(cfg), motif, log=lambda s: None, profile=prof)
    sess.probe = _probe
    _fast_record(sess, motif)
    sess.run(("cut", "register", "upload", "submit", "poll"))
    sess.redo_analysis()
    assert sess.is_done("register") and not sess.is_done("submit")
    sess.run(("upload", "submit"))
    assert prof.calls.count("submit") == 2


@pytest.mark.parametrize("unmount", [False, True])
def test_export_unmounts_only_when_asked(cfg, monkeypatch, tmp_path, unmount):
    import rigconductor.session as sm
    from rigconductor.export import Disk

    _registry(cfg)
    motif = FakeMotif(cfg.data_root)
    sess = ConductorSession(
        cfg, _spec(cfg, unmount=unmount), motif, log=lambda s: None, profile=FakeProfile()
    )
    sess.probe = _probe
    _fast_record(sess, motif)
    for st in STAGES[1:-1]:
        getattr(sess, st)()
    calls = []
    usb = tmp_path / "usb"
    usb.mkdir()
    monkeypatch.setattr(sm, "mount", lambda d: usb)
    monkeypatch.setattr(sm, "unmount", lambda d: calls.append(d.device))
    disk = Disk(device="/dev/sdz1", size="1G", fstype="vfat", label="X", mountpoint=None)
    sess.export(disk)
    assert calls == (["/dev/sdz1"] if unmount else [])


def test_rerun_with_model_clears_cluster_stages_and_switches(cfg):
    _registry(cfg)
    motif = FakeMotif(cfg.data_root)
    prof = FakeProfile()
    archived = []
    prof.archive_analysis = lambda ctx, label: archived.append(label) or "archive/x"
    sess = ConductorSession(cfg, _spec(cfg), motif, log=lambda s: None, profile=prof)
    sess.probe = _probe
    _fast_record(sess, motif)
    sess.run(("cut", "register", "upload", "submit", "poll", "fetch", "score"))
    assert sess.rerun_with_model("m2") == "archive/x" and archived == ["m1"]
    assert sess.spec.model_id == "m2" and sess.is_done("register")
    assert not any(sess.is_done(s) for s in ("upload", "submit", "poll", "fetch", "score"))
    fresh = ConductorSession(
        cfg, _spec(cfg, model_id="m1"), motif, log=lambda s: None, profile=FakeProfile()
    )
    assert fresh.rerun_with_model("m2") is None  # nothing sent yet: just switch


def test_camera_that_ignores_settings_stops_the_recording(cfg):
    from rigconductor.constants import CAMERA_SETTINGS

    motif = FakeMotif(cfg.data_root)
    motif.read = lambda name: 25.0 if name == "AcquisitionFrameRate" else CAMERA_SETTINGS[name]
    sess = ConductorSession(cfg, _spec(cfg), motif, log=lambda s: None, profile=FakeProfile())
    with pytest.raises(ConductorError, match="AcquisitionFrameRate wanted 69.0 got 25.0"):
        sess.apply_camera_settings()


def test_rig_yaml_camera_overrides_defaults(tmp_path):
    from rigconductor.config import ConductorConfig

    rig = tmp_path / "rig.yaml"
    rig.write_text("motif:\n  camera:\n    ExposureTime: 5000\n", encoding="utf-8")
    cam = ConductorConfig.load(rig).motif.camera
    assert cam["ExposureTime"] == 5000 and cam["AcquisitionFrameRate"] == 69.0 and cam["Gain"] == 0
