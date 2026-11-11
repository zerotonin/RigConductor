import json
import zipfile

import pytest

from rigconductor import ConductorError
from rigconductor.config import ConductorConfig
from rigconductor.relay import (
    find_packages,
    import_job,
    import_results,
    pack_job,
    pack_results,
    read_manifest,
)
from rigconductor.session import ConductorSession
from tests.fake_profile import FakeProfile
from tests.test_session import _probe, _registry, _spec


def _cfg(root):
    data = root / "DATA"
    data.mkdir(parents=True)
    return ConductorConfig(data_root=data, sessions_dir=root / "sessions")


def _rig_session(cfg, prof):
    _registry(cfg)
    rec = cfg.data_root / "fake_run_20261111_090000"
    rec.mkdir()
    (rec / "metadata.yaml").write_text("title: fake", encoding="utf-8")
    s = ConductorSession(cfg, _spec(cfg, recording_dir=str(rec)), None, lambda m: None, prof)
    s.probe = _probe
    s.run(("record", "cut", "register"))
    return s


def test_round_trip_rig_relay_rig(tmp_path, monkeypatch):
    rig_cfg, relay_cfg = _cfg(tmp_path / "rig"), _cfg(tmp_path / "relay")
    _registry(relay_cfg)
    import rigconductor.relay as relay_mod
    import rigconductor.session as sess_mod

    relay_prof = FakeProfile()
    monkeypatch.setattr(sess_mod, "make_profile", lambda name: relay_prof)
    rig = _rig_session(rig_cfg, FakeProfile())
    stick = tmp_path / "stick"
    job = pack_job(rig, stick, log=lambda m: None)
    assert job.name.endswith(".job.zip") and find_packages(stick) == [job]
    m = read_manifest(job)
    assert m["kind"] == "conductor-job" and set(m["files"]) == {
        "metadata.yaml",
        "clips/a0.mp4",
        "clips/a1.mp4",
    }
    with zipfile.ZipFile(job) as z:
        assert z.getinfo("payload/clips/a0.mp4").compress_type == zipfile.ZIP_STORED

    rel = import_job(relay_cfg, job, log=lambda m: None)
    rel.probe = _probe
    assert rel.is_done("cut") and not rel.is_done("upload")
    assert (rel._ctx().clips_dir / "a0.mp4").is_file()
    relay_mod.run_cluster_stages(rel)
    assert relay_prof.calls == ["upload", "submit", "poll", "fetch"]
    res = pack_results(rel, stick, log=lambda m: None)
    assert read_manifest(res)["files"].keys() == {"results/pose.h5"}

    import_results(rig, res, log=lambda m: None)
    for st in ("upload", "submit", "poll", "fetch"):
        assert rig.is_done(st) and rig.state["done"][st]["via_relay"]
    assert rig.state["done"]["submit"]["job_id"] == "4242"
    assert (rig._ctx().results_dir / "pose.h5").read_bytes() == b"h5"
    rig.score()
    assert rig.is_done("score")


def test_pack_refuses_unfinished_and_wrong_session(tmp_path):
    cfg = _cfg(tmp_path / "rig")
    _registry(cfg)
    rec = cfg.data_root / "r"
    rec.mkdir()
    (rec / "metadata.yaml").write_text("x: 1", encoding="utf-8")
    s = ConductorSession(
        cfg, _spec(cfg, recording_dir=str(rec)), None, lambda m: None, FakeProfile()
    )
    with pytest.raises(ConductorError, match="not done"):
        pack_job(s, tmp_path)
    s.run(("record", "cut", "register"))
    job = pack_job(s, tmp_path, log=lambda m: None)
    with pytest.raises(ConductorError, match="job package"):
        import_results(s, job)


def test_corrupt_package_is_rejected(tmp_path):
    cfg = _cfg(tmp_path / "rig")
    s = _rig_session(cfg, FakeProfile())
    job = pack_job(s, tmp_path / "stick", log=lambda m: None)
    # rewrite one member with different bytes, keep the manifest
    bad = tmp_path / "bad.job.zip"
    with zipfile.ZipFile(job) as zin, zipfile.ZipFile(bad, "w") as zout:
        for i in zin.infolist():
            data = zin.read(i.filename)
            zout.writestr(i, b"tampered" if i.filename.endswith("a0.mp4") else data)
    relay_cfg = _cfg(tmp_path / "relay")
    with pytest.raises(ConductorError, match="corrupt"):
        import_job(relay_cfg, bad, log=lambda m: None)


def test_path_escape_is_rejected(tmp_path):
    bad = tmp_path / "evil.job.zip"
    man = {
        "format": 1,
        "kind": "conductor-job",
        "session_id": "s",
        "profile": "fake",
        "recording_name": "r",
        "model_id": "m1",
        "conductor_state": {},
        "files": {"../../etc/x": {"sha256": "0", "bytes": 1}},
    }
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("manifest.json", json.dumps(man))
        z.writestr("payload/../../etc/x", b"x")
    with pytest.raises(ConductorError, match="escapes"):
        import_job(_cfg(tmp_path / "relay"), bad, log=lambda m: None)
