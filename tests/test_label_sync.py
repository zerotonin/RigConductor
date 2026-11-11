import subprocess

from rigconductor.config import ClusterConfig, ConductorConfig
from rigconductor.labels import store, sync


def _cfg(tmp_path):
    return ConductorConfig(
        data_root=tmp_path,
        sessions_dir=tmp_path / "s",
        cluster=ClusterConfig(lab_root="/lab"),
        profiles={"drosophila_triad": {"registry": "Heat/models/registry.yaml"}},
    )


def test_sync_sends_the_default_set_without_train(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LABEL_SETS_DIR", str(tmp_path / "sets"))
    store.LabelSet.create("triad", ["thorax"])
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = "<f+++++++++ v2.pkg.slp\n" if cmd[0] == "rsync" else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    line = sync.sync(_cfg(tmp_path), run=run)
    assert calls[0][-1] == "mkdir -p /lab/Heat/sleap_labels/sets/triad"
    assert "--exclude=train/" in calls[1]
    assert calls[1][-1] == "aoraki:/lab/Heat/sleap_labels/sets/triad/"
    assert "--delete" not in calls[1] and line.endswith("1 file(s): v2.pkg.slp")


def test_cron_line_is_replaced_not_duplicated():
    line = sync.cron_line("/env/bin/rigconductor", hours=2)
    assert line.startswith("0 */2 * * * /env/bin/rigconductor label-sync >> ")
    tab = sync.with_cron_line("MAILTO=x\n" + line + "\n", line)
    assert tab.count(sync.CRON_TAG) == 1 and tab.startswith("MAILTO=x\n")
    assert sync.CRON_TAG not in sync.with_cron_line(tab, None)
