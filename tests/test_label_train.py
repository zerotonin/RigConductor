from datetime import date

from rigconductor.config import ClusterConfig, ConductorConfig
from rigconductor.labels import train
from rigconductor.labels.store import LabelSet


def _cfg(tmp_path):
    return ConductorConfig(
        data_root=tmp_path,
        sessions_dir=tmp_path / "s",
        cluster=ClusterConfig(lab_root="/lab"),
        profiles={"drosophila_triad": {"registry": "Heat/models/registry.yaml"}},
    )


def test_project_root_comes_from_the_registry(tmp_path):
    assert train.project_root(_cfg(tmp_path), "drosophila_triad") == "/lab/Heat"


def test_submit_command_names_every_validation_set():
    plan = train.TrainPlan(
        "t",
        2,
        "t_v2",
        "old",
        "old",
        remote_dir="/p/sl/t/v2",
        val={"500px": "/p/sl/t/v2/val_500px.pkg.slp"},
    )
    cmd = train.submit_command("/p", plan, epochs=5)
    assert "EPOCHS=5" in cmd and "sbatch --parsable scripts/slurm/sleap_nn_train.sh" in cmd
    assert cmd.endswith("500px=/p/sl/t/v2/val_500px.pkg.slp")
    assert "NN_ENV=/p/envs/uv-tools/sleap" in cmd


def test_parse_status():
    st = train.parse_status("STATE=RUNNING\nEpoch 3/200\nEpoch 4/200 loss 0.1")
    assert st["state"] == "RUNNING" and st["epoch"] == 4 and not st["done"]
    st = train.parse_status("STATE=COMPLETED\nDONE")
    assert st["done"] and not st["failed"]
    assert train.parse_status("STATE=TIMEOUT")["failed"] == "TIMEOUT"


def test_parse_status_reads_loss_curves_and_patience():
    text = "\n".join(
        [
            "STATE=RUNNING",
            "TRAINED=1",
            "CURVE=centroid",
            "0,,0.0003,",
            "1,0.0005,0.0002,14.9",
            "2,0.0001,0.00025,14.1",
            "PATIENCE=20",
            "CURVE_END",
            "CURVE=centered_instance",
            "PATIENCE=",
            "CURVE_END",
            "Epoch 7/200",
        ]
    )
    st = train.parse_status(text)
    assert st["trained"] == 1 and st["patience"] == {"centroid": 20}
    assert st["curves"]["centroid"] == [
        (0, None, 0.0003, None),
        (1, 0.0005, 0.0002, 14.9),
        (2, 0.0001, 0.00025, 14.1),
    ]
    assert "centered_instance" not in st["curves"] and st["epoch"] == 7
    assert st["tail"] == ["Epoch 7/200"]


def test_awk_programs_pick_columns_and_patience_by_name(tmp_path):
    import shutil
    import subprocess

    import pytest

    if shutil.which("awk") is None:
        pytest.skip("needs awk")
    csv = tmp_path / "training_log.csv"
    csv.write_text(
        "epoch,train/loss,val/loss,learning_rate,train/time\n0,,0.3,,\n1,0.5,0.2,1e-4,14.9\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["awk", "-F,", train.CURVE_AWK, str(csv)], capture_output=True, text=True, check=True
    ).stdout
    assert out.split() == ["0,,0.3,", "1,0.5,0.2,14.9"]
    cfg = tmp_path / "training_config.yaml"
    cfg.write_text(
        "trainer_config:\n  lr_scheduler:\n    patience: 5\n"
        "  early_stopping:\n    min_delta: 1.0e-08\n    patience: 20\n",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["awk", train.PATIENCE_AWK, str(cfg)], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "20"


def test_promotion_rule():
    rows = [
        {"model": "new", "set": "500px", "mAP": 0.8, "frames": 50},
        {"model": "old", "set": "500px", "mAP": 0.6, "frames": 50},
        {"model": "new", "set": "200px", "mAP": 0.5, "frames": 20},
        {"model": "old", "set": "200px", "mAP": 0.5, "frames": 20},
    ]
    assert train.compare(rows, "new", "old")["promotable"]
    rows[2]["mAP"] = 0.4
    assert not train.compare(rows, "new", "old")["promotable"]


def test_registry_entry(tmp_path):
    s = LabelSet.create("triad", ["thorax"], root=tmp_path)
    verdict = {"table": [{"set": "500px", "new": 0.8, "old": 0.6, "frames": 50}]}
    base = {
        "id": "lab_oct2025_v2",
        "organism": "drosophila",
        "kind": "sleap-topdown",
        "expects": {"channels": 1, "fps": 25, "invert": True},
    }
    e = train.registry_entry("triad_v2", base, s, 2, verdict, True, today=date(2026, 11, 12))
    assert e["env"] == "sleap-nn" and e["default"] is True and "fps" not in e["expects"]
    assert e["validation"] == {"frames": 50, "mAP": 0.8, "sets": {"500px": 0.8}}
    assert train.run_name(s, 2, date(2026, 11, 12)) == "triad_v2_20261112"


def test_local_runs_need_both_halves(tmp_path):
    for d in ("a.centroid", "a.centered_instance", "b.centroid"):
        (tmp_path / d).mkdir()
    assert train.local_runs(tmp_path) == ["a"]


def test_run_names_stay_unique_and_plans_round_trip(tmp_path):
    s = LabelSet.create("triad", ["thorax"], root=tmp_path)
    day = date(2026, 11, 12)
    s.add_model({"run": "triad_v2_20261112"})
    assert train.run_name(s, 2, day) == "triad_v2_20261112b"
    s.add_model({"run": "triad_v2_20261112b"})
    assert train.run_name(s, 2, day) == "triad_v2_20261112c"
    plan = train.TrainPlan("triad", 2, "r", "a", "b", job_id="7", excluded=["june"])
    entry = {**plan.__dict__, "status": "submitted", "epochs": 5}
    assert train.TrainPlan.from_entry(entry) == plan


def test_registry_entry_names_left_out_recordings(tmp_path):
    s = LabelSet.create("triad", ["thorax"], root=tmp_path)
    verdict = {"table": [{"set": "500px", "new": 0.8, "old": 0.6, "frames": 5}]}
    e = train.registry_entry("r", {}, s, 2, verdict, False, excluded=["june", "aug"])
    assert e["label_set"] == {"name": "triad", "version": 2, "left_out": ["aug", "june"]}
