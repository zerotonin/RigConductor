# rigconductor

[![Tests](https://github.com/zerotonin/RigConductor/actions/workflows/tests.yml/badge.svg)](https://github.com/zerotonin/RigConductor/actions/workflows/tests.yml)
[![Lint](https://github.com/zerotonin/RigConductor/actions/workflows/lint.yml/badge.svg)](https://github.com/zerotonin/RigConductor/actions/workflows/lint.yml)
[![Docs](https://github.com/zerotonin/RigConductor/actions/workflows/docs.yml/badge.svg)](https://zerotonin.github.io/RigConductor/)
[![Release](https://github.com/zerotonin/RigConductor/actions/workflows/release.yml/badge.svg)](https://github.com/zerotonin/RigConductor/releases)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![GUI: PyQt6](https://img.shields.io/badge/GUI-PyQt6-41CD52.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![Pose: SLEAP 1.6](https://img.shields.io/badge/pose-SLEAP%201.6-0072B2.svg)](https://sleap.ai/)
[![Uses ThermoKourt](https://img.shields.io/badge/uses-ThermoKourt-009E73.svg)](https://github.com/zerotonin/thermokourt)
[![Manual](https://img.shields.io/badge/manual-user%20guide-E69F00.svg)](https://zerotonin.github.io/RigConductor/manual/user_manual.html)

One wizard from "press record" to "results on the USB disk", for a loopbio Motif
rig. Two organism profiles:

| Profile | Animals | Backend |
|---|---|---|
| `drosophila_triad` | 10 circular arenas, 2 males + decapitated female | [ThermoKourt](https://github.com/zerotonin/thermokourt) (arena finder, SLEAP on the cluster, rules/SimBA scoring, SQLite + DrosoDB copy) |
| `tadpole_24well` | 24-well plate of *Xenopus* tadpoles | [tadpose](https://github.com/zerotonin/tadpose) (well split, SLEAP/DLC tracking, assignment to the canonical prototypes) |

Stages, each idempotent and resumable: `record → cut → register → upload → submit →
poll → fetch → score → export`.

## Install (rig PC)

```bash
conda activate heatagg                      # python ≥3.10, PyQt6, thermokourt installed
pip install -e ".[gui,dev]"
rigconductor init-config                    # writes ~/.config/rigconductor/rig.yaml — edit it
rigconductor doctor                         # Motif, camera, disk, cluster, DrosoDB, registry
rigconductor models --refresh               # pull the model registry from the cluster
rigconductor gui
```

Motif is reached through its REST API on the rig PC (`recnode-apikey` supplies the
key). The cluster is reached by key-based SSH (`ssh <alias>` must work without a
password); off campus that needs the VPN first.

## Command line

```
rigconductor doctor [--profile P] [--no-ssh]
rigconductor motif status | record TITLE SECONDS | stop
rigconductor models [--profile P] [--refresh]
rigconductor disks
rigconductor run SESSION.json [--from STAGE] [--to STAGE] [--disk /dev/sdX1]
rigconductor status SESSION_ID
rigconductor sessions
```

`SESSION.json` is what the wizard writes (see `rigconductor.session.ConductorSpec`).
Give `recording_dir` to analyse an existing recording instead of recording.

## Model registry

One YAML per organism on the cluster (path in `rig.yaml`), cached locally:

```yaml
- id: flies13_lab_v1
  organism: drosophila
  kind: sleap-topdown
  run: flies13_lab_v1        # models/<run>.centroid + .centered_instance
  env: sleap
  trained: 2026-11-20
  validation: {mAP: 0.82, frames: 150}
```

The chosen model id is stamped into the results (ThermoKourt `annotators`,
tadpose run metadata).

## Export bundle

`<disk>/<profile>/<session_id>/` with the database export (optionally blinded:
no genotypes, no vial codes), `events.csv`, `window_summary.csv`, the arena
overlay, `session.json` and `MANIFEST.sha256`; every file is re-read and checked
before the disk is unmounted.

## Development

```bash
ruff check . && ruff format --check . && pytest -q
```

Tests run without Motif, a cluster or a GUI (`FakeMotif`, a fake profile).
