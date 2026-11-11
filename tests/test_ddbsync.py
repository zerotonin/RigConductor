import json
import sqlite3

import pytest

from rigconductor import ConductorError
from rigconductor.ddbsync import find_on, import_snapshot, validate_snapshot


def make_ddb(path, n_active=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE genotype (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute(
        "CREATE TABLE vial (id INTEGER PRIMARY KEY, print_code TEXT, "
        "genotype_id INT, is_active INT)"
    )
    con.execute("INSERT INTO genotype VALUES (1, 'CS')")
    for i in range(n_active):
        con.execute("INSERT INTO vial VALUES (?, ?, 1, 1)", (i, f"V{i}"))
    con.execute("INSERT INTO vial VALUES (99, 'OLD', 1, 0)")
    con.commit()
    con.close()


def stick_with(tmp_path, created, n=3, name="stick"):
    import hashlib

    root = tmp_path / name
    snap = root / "drosodb" / "ddb.latest.sqlite3"
    make_ddb(snap, n)
    h = hashlib.sha256(snap.read_bytes()).hexdigest()
    (root / "drosodb" / "snapshot.json").write_text(
        json.dumps({"created": created, "sha256": h, "n_active_vials": n, "host": "tablet"}),
        encoding="utf-8",
    )
    return root


def test_import_then_refuse_older_then_accept_newer(tmp_path):
    target = tmp_path / "rig" / "ddb.latest.sqlite3"
    s1 = find_on(stick_with(tmp_path, "2026-11-10T09:00:00", n=3, name="s1"))
    assert import_snapshot(s1, target, log=lambda m: None) == 3
    assert validate_snapshot(target) == 3
    s0 = find_on(stick_with(tmp_path, "2026-11-01T09:00:00", n=1, name="s0"))
    with pytest.raises(ConductorError, match="not newer"):
        import_snapshot(s0, target, log=lambda m: None)
    s2 = find_on(stick_with(tmp_path, "2026-11-11T09:00:00", n=5, name="s2"))
    assert import_snapshot(s2, target, log=lambda m: None) == 5
    assert validate_snapshot(target.with_suffix(".sqlite3.prev")) == 3


def test_rejects_corrupt_and_foreign(tmp_path):
    root = stick_with(tmp_path, "2026-11-10T09:00:00")
    info = find_on(root)
    info.path.write_bytes(info.path.read_bytes()[:-10] + b"0123456789")
    with pytest.raises(ConductorError, match="checksum"):
        import_snapshot(info, tmp_path / "t.sqlite3", log=lambda m: None)
    other = tmp_path / "x.sqlite3"
    sqlite3.connect(other).execute("CREATE TABLE foo (a)").connection.commit()
    with pytest.raises(ConductorError, match="not a DrosoDB"):
        validate_snapshot(other)
    assert find_on(tmp_path / "empty") is None
