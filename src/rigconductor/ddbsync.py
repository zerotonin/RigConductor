# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — ddbsync                                         ║
# ║  « DrosoDB by USB stick »                                        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Validate and import a tablet snapshot on an offline rig.        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""DrosoDB snapshot by USB stick, for rigs without a network.

The tablet writes ``<stick>/drosodb/ddb.latest.sqlite3`` plus ``snapshot.json``
(``deploy/tablet/ddb_to_usb.py``); the rig imports it here. The snapshot is
checked (SHA-256, SQLite integrity, DrosoDB tables present) and must be newer
than the one the rig already has, then replaces it atomically; the previous copy
is kept as ``*.prev``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rigconductor import ConductorError

SUBDIR = "drosodb"
SNAPSHOT = "ddb.latest.sqlite3"
META = "snapshot.json"
REQUIRED_TABLES = {"vial", "genotype"}


@dataclass
class SnapshotInfo:
    path: Path
    created: str  # ISO time the tablet made it
    sha256: str
    n_active_vials: int
    source_host: str = ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_snapshot(path: Path) -> int:
    """Raise unless ``path`` is a sound DrosoDB file; return the active-vial count."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        n = (
            con.execute("SELECT count(*) FROM vial WHERE is_active = 1").fetchone()[0]
            if not missing
            else 0
        )
        con.close()
    except sqlite3.DatabaseError as e:
        raise ConductorError(f"{path.name} is not a readable SQLite database: {e}") from e
    if ok != "ok":
        raise ConductorError(f"{path.name} failed the integrity check: {ok}")
    if missing:
        raise ConductorError(f"{path.name} is not a DrosoDB snapshot (missing {sorted(missing)})")
    return int(n)


def find_on(root: Path) -> SnapshotInfo | None:
    d = Path(root) / SUBDIR
    snap, meta = d / SNAPSHOT, d / META
    if not snap.is_file():
        return None
    m = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
    return SnapshotInfo(
        path=snap,
        created=m.get("created") or datetime.fromtimestamp(snap.stat().st_mtime).isoformat(),
        sha256=m.get("sha256", ""),
        n_active_vials=int(m.get("n_active_vials", -1)),
        source_host=m.get("host", ""),
    )


def current_created(target: Path) -> str | None:
    meta = Path(str(target) + ".json")
    if meta.is_file():
        return json.loads(meta.read_text(encoding="utf-8")).get("created")
    return datetime.fromtimestamp(target.stat().st_mtime).isoformat() if target.is_file() else None


def import_snapshot(info: SnapshotInfo, target: Path, force: bool = False, log=print) -> int:
    target = Path(target)
    if info.sha256 and _sha256(info.path) != info.sha256:
        raise ConductorError("the snapshot on the stick is corrupt (checksum); export it again")
    n = validate_snapshot(info.path)
    have = current_created(target)
    if have and not force and info.created <= have:
        raise ConductorError(
            f"the stick's snapshot ({info.created}) is not newer than the rig's ({have})"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".incoming")
    shutil.copy2(info.path, tmp)
    validate_snapshot(tmp)
    if target.is_file():
        shutil.copy2(target, target.with_suffix(target.suffix + ".prev"))
    os.replace(tmp, target)
    Path(str(target) + ".json").write_text(
        json.dumps(
            {
                "created": info.created,
                "sha256": info.sha256,
                "host": info.source_host,
                "n_active_vials": n,
                "imported": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"DrosoDB: imported snapshot of {info.created} ({n} active vials) → {target}")
    return n
