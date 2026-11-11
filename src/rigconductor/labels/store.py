# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.store                                    ║
# ║  « label sets, their versions and where frames came from »       ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  One folder per set: vN.pkg.slp files, registry.yaml, packages/. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Label sets on disk.

Layout::

    <sets_dir>/DEFAULT            name of the default set
    <sets_dir>/<name>/
        registry.yaml        versions, validation recordings, packages, models
        v1.pkg.slp           frames embedded, only hand-placed instances
        v2.pkg.slp
        packages/<id>/       outgoing labelling packages (and what came back)
        train/vN/            train / validation split written before training
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from rigconductor import ConductorError
from rigconductor.constants import LABEL_POLARITY, LABEL_SETS_DIR

SCALE_STEP_PX = 50  # arena diameters are grouped to this step ("500px")
DEFAULT_FILE = "DEFAULT"  # in the sets folder: name of the default label set


def scale_name(arena_diameter_px: float) -> str:
    """Camera-scale label of an arena, e.g. ``500px``."""
    return f"{int(round(arena_diameter_px / SCALE_STEP_PX) * SCALE_STEP_PX)}px"


def sets_dir(root: str | Path | None = None) -> Path:
    return Path(root or LABEL_SETS_DIR).expanduser()


def list_sets(root: str | Path | None = None) -> list[str]:
    d = sets_dir(root)
    return sorted(p.parent.name for p in d.glob("*/registry.yaml")) if d.is_dir() else []


def default_set(root: str | Path | None = None) -> str | None:
    """Name of the default label set (wizard pre-selects it, ``label-sync`` sends it)."""
    sets = list_sets(root)
    f = sets_dir(root) / DEFAULT_FILE
    name = f.read_text(encoding="utf-8").strip() if f.is_file() else ""
    if name in sets:
        return name
    return sets[0] if len(sets) == 1 else None


def set_default(name: str, root: str | Path | None = None) -> None:
    if name not in list_sets(root):
        raise ConductorError(f"no label set '{name}' in {sets_dir(root)}")
    (sets_dir(root) / DEFAULT_FILE).write_text(name + "\n", encoding="utf-8")


@dataclass
class LabelSet:
    path: Path
    data: dict

    # ── loading and saving ────────────────────────────────────────
    @classmethod
    def create(
        cls,
        name: str,
        skeleton: list[str],
        root: str | Path | None = None,
        polarity: str = LABEL_POLARITY,
    ) -> LabelSet:
        path = sets_dir(root) / name
        if (path / "registry.yaml").exists():
            raise ConductorError(f"label set '{name}' already exists: {path}")
        path.mkdir(parents=True, exist_ok=True)
        data = {
            "name": name,
            "skeleton": list(skeleton),
            "polarity": polarity,
            "created": str(date.today()),
            "validation": {},
            "versions": [],
            "packages": [],
            "models": [],
        }
        s = cls(path, data)
        s.save()
        return s

    @classmethod
    def load(cls, name_or_path: str | Path, root: str | Path | None = None) -> LabelSet:
        p = Path(name_or_path)
        if not (p / "registry.yaml").is_file():
            p = sets_dir(root) / str(name_or_path)
        reg = p / "registry.yaml"
        if not reg.is_file():
            raise ConductorError(f"no label set at {p}")
        data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
        for k in ("validation", "versions", "packages", "models", "projects"):
            data.setdefault(k, {} if k == "validation" else [])
        return cls(p, data)

    def save(self) -> None:
        tmp = self.path / "registry.yaml.tmp"
        tmp.write_text(yaml.safe_dump(self.data, sort_keys=False), encoding="utf-8")
        tmp.replace(self.path / "registry.yaml")

    # ── properties ────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def skeleton(self) -> list[str]:
        return list(self.data["skeleton"])

    @property
    def polarity(self) -> str:
        return self.data.get("polarity", LABEL_POLARITY)

    @property
    def versions(self) -> list[dict]:
        return self.data["versions"]

    def latest(self) -> dict | None:
        return self.versions[-1] if self.versions else None

    def version(self, n: int) -> dict:
        for v in self.versions:
            if v["version"] == n:
                return v
        raise ConductorError(f"label set '{self.name}' has no version {n}")

    def version_file(self, n: int | None = None) -> Path:
        v = self.version(n) if n is not None else self.latest()
        if v is None:
            raise ConductorError(f"label set '{self.name}' has no versions yet")
        return self.path / v["file"]

    def next_version(self) -> int:
        return (self.latest() or {"version": 0})["version"] + 1

    # ── changes ───────────────────────────────────────────────────
    def add_version(self, stats: dict, note: str = "", parent: int | None = None) -> dict:
        """Record a new version file (already written as ``v<N>.pkg.slp``)."""
        n = self.next_version()
        entry = {
            "version": n,
            "file": f"v{n}.pkg.slp",
            "parent": parent if parent is not None else (n - 1 or None),
            "created": datetime.now().isoformat(timespec="seconds"),
            "note": note,
            **stats,
        }
        if not (self.path / entry["file"]).is_file():
            raise ConductorError(f"version file missing: {self.path / entry['file']}")
        self.versions.append(entry)
        self.save()
        return entry

    def set_validation(self, scale: str, recordings: list[str]) -> None:
        """Recordings whose frames are only ever used to test models at ``scale``."""
        self.data["validation"][scale] = sorted(set(recordings))
        self.save()

    def set_training_roles(self, shown: dict[str, str], roles: dict[str, str]) -> None:
        """Roles chosen for the recordings of one version.

        Args:
            shown: Recording → its scale, for every recording on the page.
            roles: Recording → ``train``, ``all`` (split by frame), ``val`` or ``out``.

        Validation and shared recordings not shown (no frames in that version yet)
        are kept.
        """
        val = {s: set(r) - set(shown) for s, r in self.data["validation"].items()}
        shared = {r: s for r, s in self.shared().items() if r not in shown}
        for rec, role in roles.items():
            if role == "val":
                val.setdefault(shown[rec], set()).add(rec)
            elif role == "all":
                shared[rec] = shown[rec]
        self.data["validation"] = {s: sorted(r) for s, r in sorted(val.items()) if r}
        self.data["shared"] = dict(sorted(shared.items()))
        self.data["left_out"] = sorted(r for r, role in roles.items() if role == "out")
        self.save()

    def shared(self) -> dict[str, str]:
        """Recordings split by frame into training and validation (recording → scale)."""
        return dict(self.data.get("shared") or {})

    def has_validation(self) -> bool:
        return bool(self.data["validation"]) or bool(self.shared())

    def left_out(self) -> set[str]:
        return set(self.data.get("left_out", []))

    def validation_recordings(self) -> set[str]:
        return {r for recs in self.data["validation"].values() for r in recs}

    def new_package_id(self) -> str:
        return f"{datetime.now():%Y%m%d}-{secrets.token_hex(2)}"

    def package_dir(self, pkg_id: str) -> Path:
        return self.path / "packages" / pkg_id

    def add_package(self, entry: dict) -> None:
        self.data["packages"].append({"status": "out", **entry})
        self.save()

    def package(self, pkg_id: str) -> dict:
        for p in self.data["packages"]:
            if p["id"] == pkg_id:
                return p
        raise ConductorError(f"package {pkg_id} does not belong to label set '{self.name}'")

    def update_package(self, pkg_id: str, **changes) -> None:
        self.package(pkg_id).update(changes)
        self.save()

    # ── SLEAP projects labelled outside packages ──────────────────
    def projects(self) -> list[dict]:
        return self.data.setdefault("projects", [])

    def add_project(self, path: str | Path, note: str = "") -> dict:
        """A SLEAP project (``.slp``) whose new hand labels can be taken into the set."""
        path = str(Path(path).expanduser().resolve())
        for p in self.projects():
            if p["path"] == path:
                return p
        entry = {"path": path, "added": str(date.today()), "note": note}
        self.projects().append(entry)
        self.save()
        return entry

    def remove_project(self, path: str | Path) -> None:
        """Take a project off the list (its file is not touched)."""
        path = str(Path(path).expanduser().resolve())
        self.data["projects"] = [p for p in self.projects() if p["path"] != path]
        self.save()

    def update_project(self, path: str | Path, **changes) -> None:
        path = str(Path(path).expanduser().resolve())
        for p in self.projects():
            if p["path"] == path:
                p.update(changes)
                self.save()
                return
        raise ConductorError(f"{path} is not a project of label set '{self.name}'")

    def add_model(self, entry: dict) -> None:
        self.data["models"].append(entry)
        self.save()

    def update_model(self, run: str, **changes) -> None:
        for m in self.data["models"]:
            if m["run"] == run:
                m.update(changes)
                self.save()
                return
        raise ConductorError(f"no training run '{run}' in label set '{self.name}'")

    def labelled_frames(self) -> set[tuple[str, int, int]]:
        """(recording, arena, frame) of everything in the set or out for labelling."""
        out: set[tuple[str, int, int]] = set()
        for p in self.data["packages"]:
            if p.get("status") in ("out", "returned", "merged"):
                for rec, arena, frame in p.get("frames_index", []):
                    out.add((rec, int(arena), int(frame)))
        for rec, arena, frame in (self.latest() or {}).get("frames_index", []):
            out.add((rec, int(arena), int(frame)))  # versions are cumulative
        return out
