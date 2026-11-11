from rigconductor.doctor import check_disk, check_motif, check_snapshot, check_ssh
from rigconductor.motif import FakeMotif


def test_motif_checks(tmp_path):
    ok = check_motif(lambda: FakeMotif(tmp_path))
    assert [c.ok for c in ok] == [True, True]
    bad = check_motif(lambda: FakeMotif(tmp_path, camera_status="ERROR"))
    assert [c.ok for c in bad] == [True, False] and "USB" in bad[1].hint

    def boom():
        raise OSError("connection refused")

    down = check_motif(boom)
    assert len(down) == 1 and not down[0].ok and "supervisorctl" in down[0].hint


def test_disk_and_snapshot(tmp_path):
    assert check_disk(tmp_path, min_free_gb=0).ok
    assert not check_disk(tmp_path / "missing").ok
    assert check_snapshot(None).ok
    assert not check_snapshot(tmp_path / "nope.sqlite3").ok
    p = tmp_path / "ddb.sqlite3"
    p.write_bytes(b"x")
    assert check_snapshot(p).ok


def test_ssh_check_uses_runner():
    class R:
        def __init__(self, rc, out, err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    assert check_ssh("h", run=lambda *a, **k: R(0, "ok\n")).ok
    c = check_ssh("h", run=lambda *a, **k: R(255, "", "Permission denied"))
    assert not c.ok and "VPN" in c.hint
