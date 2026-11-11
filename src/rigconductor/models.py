# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — models                                          ║
# ║  « which AI models exist »                                       ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Model registry: one YAML per organism on Aoraki, cached here.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Model registry: one YAML per organism on the cluster, cached locally.

Entry example::

    - id: flies13_lab_v1
      organism: drosophila
      kind: sleap-topdown          # sleap-topdown | dlc | tadpose-assign
      run: flies13_lab_v1          # what the SLURM script takes (models/<run>.centroid …)
      env: sleap
      trained: 2026-11-20
      validation: {frames: 150, mAP: 0.82}
      notes: fine-tuned from flies13 on 150 October frames
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rigconductor import ConductorError


@dataclass
class Model:
    id: str
    organism: str
    kind: str
    run: str
    env: str = "sleap"
    trained: str | None = None
    validation: dict = field(default_factory=dict)
    notes: str = ""
    deprecated: bool = False
    expects: dict = field(default_factory=dict)  # training-data description, see compat.py
    grayscale_input: bool = False  # the model's config converts any input to gray

    @property
    def label(self) -> str:
        """Dropdown text: id, date and the headline validation number."""
        bits = [self.id]
        if self.trained:
            bits.append(str(self.trained))
        for k in ("mAP", "f1", "silhouette", "frames"):
            if k in self.validation:
                bits.append(f"{k} {self.validation[k]}")
                break
        if self.deprecated:
            bits.append("(deprecated)")
        return " · ".join(bits)


def parse_registry(text: str) -> list[Model]:
    data = yaml.safe_load(text) or []
    if isinstance(data, dict):
        data = data.get("models", [])
    out = []
    for i, d in enumerate(data):
        for k in ("id", "organism", "kind"):
            if k not in d:
                raise ConductorError(f"registry entry {i} lacks '{k}'")
        d = dict(d)
        d.setdefault("run", d["id"])
        d["validation"] = d.get("validation") or {}
        d["expects"] = d.get("expects") or {}
        out.append(Model(**{k: v for k, v in d.items() if k in Model.__dataclass_fields__}))
    return out


def load_cached(cache: Path) -> list[Model]:
    if not Path(cache).is_file():
        return []
    return parse_registry(Path(cache).read_text(encoding="utf-8"))


def refresh(ssh_host: str, remote_path: str, cache: Path, run=subprocess.run) -> list[Model]:
    r = run(
        ["ssh", "-o", "BatchMode=yes", ssh_host, f"cat {shlex.quote(remote_path)}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        raise ConductorError(
            f"could not read the model registry {ssh_host}:{remote_path}: {r.stderr.strip()[-300:]}"
        )
    models = parse_registry(r.stdout)  # validate before caching
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    Path(cache).write_text(r.stdout, encoding="utf-8")
    return models


def models_for(models: list[Model], organism: str, include_deprecated: bool = False) -> list[Model]:
    return [
        m for m in models if m.organism == organism and (include_deprecated or not m.deprecated)
    ]


def by_id(models: list[Model], model_id: str) -> Model:
    for m in models:
        if m.id == model_id:
            return m
    raise ConductorError(f"model '{model_id}' is not in the registry; press 'Refresh models'")
