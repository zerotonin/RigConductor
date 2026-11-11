import pytest

from rigconductor.motif import (
    FakeMotif,
    MotifError,
    find_new_recording,
    motif_dir_name,
    recording_is_closed,
    wait_for_stop,
)


def test_fake_records_and_finishes(tmp_path):
    m = FakeMotif(tmp_path)
    assert not m.camera().is_recording
    m.start_recording("Droso_28deg", 10, "lq-gray", {"experiment_id": "E1"})
    st = m.camera()
    assert st.is_recording and st.status == "running"
    rec = m.recordings_made[0]
    assert rec.name.startswith("droso_28deg_")
    assert (rec / "metadata.yaml").is_file() and (rec / "000000.mp4").is_file()
    assert "experiment_id: E1" in (rec / "metadata.yaml").read_text(encoding="utf-8")
    m.tick(9)
    assert m.camera().is_recording
    m.tick(1)
    assert not m.camera().is_recording


def test_fake_refuses_when_camera_error(tmp_path):
    m = FakeMotif(tmp_path, camera_status="ERROR")
    assert not m.camera().is_ok
    with pytest.raises(MotifError):
        m.start_recording("x", 1)


def test_stop_aborts(tmp_path):
    m = FakeMotif(tmp_path)
    m.start_recording("x", 100)
    m.stop_recording()
    assert not m.camera().is_recording
    assert m.calls[-1] == ("stop",)


def test_wait_for_stop_uses_injected_clock(tmp_path):
    m = FakeMotif(tmp_path)
    m.start_recording("x", 5)
    t = {"now": 0.0}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += s
        m.tick(s)

    wait_for_stop(m, timeout_s=30, poll_s=1, clock=clock, sleep=sleep)
    assert t["now"] == pytest.approx(5)


def test_wait_for_stop_times_out(tmp_path):
    m = FakeMotif(tmp_path)
    m.start_recording("x", 1000)
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    with pytest.raises(MotifError):
        wait_for_stop(m, timeout_s=3, poll_s=1, clock=lambda: t["now"], sleep=sleep)


def test_find_new_recording_filters_by_title_and_time(tmp_path):
    import time

    old = tmp_path / "droso_old_20250101_000000"
    old.mkdir()
    (old / "metadata.yaml").write_text("a: 1", encoding="utf-8")
    import os

    os.utime(old, (1, 1))
    (tmp_path / "notarecording").mkdir()
    new = tmp_path / "droso_28deg_20261201_120000"
    new.mkdir()
    (new / "metadata.yaml").write_text("a: 1", encoding="utf-8")
    assert find_new_recording(tmp_path, time.time() - 60, "Droso_28deg") == new
    assert find_new_recording(tmp_path, time.time() - 60, "tadpole") is None
    assert find_new_recording(tmp_path, time.time() - 60) == new
    # Motif's real layout: <data_root>/<camera serial>/<title>_<stamp>
    nested = tmp_path / "40366151" / "conductor_test_20261110_085455"
    nested.mkdir(parents=True)
    (nested / "metadata.yaml").write_text("a: 1", encoding="utf-8")
    assert find_new_recording(tmp_path, time.time() - 60, "Conductor_Test") == nested


def test_recording_is_closed(tmp_path):
    rec = tmp_path / "r"
    rec.mkdir()
    assert not recording_is_closed(rec)
    (rec / "000000.mp4").write_bytes(b"x")
    assert not recording_is_closed(rec, quiet_s=5)
    assert recording_is_closed(rec, quiet_s=0)


def test_dir_name():
    from datetime import datetime

    assert motif_dir_name("Droso_18deg", datetime(2025, 10, 2, 10, 24, 48)) == (
        "droso_18deg_20251002_102448"
    )
