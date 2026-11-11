"""Round trip through the SLEAP-side worker: seed → select → build → label →
check → merge → split. Needs the SLEAP 1.6 env and ffmpeg; skipped otherwise."""

import shutil
import subprocess
from pathlib import Path

import pytest

from rigconductor import sleapenv
from rigconductor.labels import package as pkg_mod
from rigconductor.labels.store import LabelSet

ENV = sleapenv.find()
pytestmark = pytest.mark.skipif(
    ENV is None or shutil.which("ffmpeg") is None, reason="needs the SLEAP 1.6 env and ffmpeg"
)
NODES = ["head", "thorax", "abdomen", "eyeL", "eyeR"]

MAKE_SEED = """
import sys, numpy as np, sleap_io as sio
clip, out = sys.argv[1:3]
sk = sio.Skeleton(["head", "thorax", "abdomen", "eyeL", "eyeR"])
v = sio.load_video(clip)
L = sio.Labels(skeletons=[sk], videos=[v])
for f in (3, 40):
    insts = []
    for k, x in enumerate((20.0, 60.0, 100.0)):
        xy = np.array([[x, 10], [x, 20], [x, 30], [x - 2, 8], [x + 2, 8]], float)
        if k == 2:
            xy[[0, 3, 4]] = np.nan
        insts.append(sio.Instance.from_numpy(xy, skeleton=sk))
    L.append(sio.LabeledFrame(video=v, frame_idx=f, instances=insts))
L.save(out)
"""

LABEL = """
import sys, numpy as np, sleap_io as sio
src, n = sys.argv[1], int(sys.argv[2])
L = sio.load_slp(src)
sk = L.skeletons[0]
for i, s in enumerate(L.suggestions[:n]):
    insts = []
    for k, x in enumerate((25.0, 65.0, 105.0)):
        xy = np.array([[x, 12], [x, 22], [x, 32], [x - 2, 9], [x + 2, 9]], float)
        if k == 2:
            xy[[0, 3, 4]] = np.nan
        insts.append(sio.Instance.from_numpy(xy, skeleton=sk))
    if i == 0:
        insts = insts[:2]  # wrong count -> rejected
    L.append(sio.LabeledFrame(video=s.video, frame_idx=s.frame_idx, instances=insts))
L.save(src)
"""


MORE_LABELS = """
import sys, numpy as np, sleap_io as sio
L = sio.load_slp(sys.argv[1])
sk, v = L.skeletons[0], L.videos[0]
insts = []
for k, x in enumerate((30.0, 70.0, 110.0)):
    xy = np.array([[x, 10], [x, 20], [x, 30], [x - 2, 8], [x + 2, 8]], float)
    if k == 2:
        xy[[0, 3, 4]] = np.nan
    insts.append(sio.Instance.from_numpy(xy, skeleton=sk))
L.append(sio.LabeledFrame(video=v, frame_idx=80, instances=insts))
L.save(sys.argv[1])
"""


def _py(code: str, *args):
    subprocess.run([str(ENV.python), "-I", "-c", code, *map(str, args)], check=True)


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "rec_20261111_120000_arena_00.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=144x144:rate=25:duration=4",
            "-pix_fmt",
            "yuv420p",
            str(p),
        ],
        check=True,
    )
    return p


def test_round_trip(tmp_path, clip):
    seed = tmp_path / "seed.slp"
    _py(MAKE_SEED, clip, seed)
    lset = LabelSet.create("t", NODES, root=tmp_path / "sets")
    res = sleapenv.run_task(ENV, "seed", {"src": str(seed), "out": str(lset.path / "v1.pkg.slp")})
    assert res["frames"] == 2 and res["recordings"] == {"rec_20261111_120000": 2}
    lset.add_version(res)
    lset.add_project(seed, note="seed")
    st = sleapenv.run_task(ENV, "inspect", {"slp": str(seed)})["states"]
    assert st == {"total": 2, "labelled": 2, "predicted": 0, "empty": 0}

    clips = [
        {
            "recording": "rec2",
            "arena": 0,
            "clip": str(clip),
            "pose": None,
            "fps": 25,
            "scale": "150px",
        }
    ]
    picks = sleapenv.run_task(ENV, "select", {"clips": clips, "n": 4, "min_gap_s": 0.2})["frames"]
    assert len(picks) == 4

    folder = pkg_mod.build_package(lset, picks, ENV)
    assert pkg_mod.manifest_problems(folder) == []
    assert (folder / "label.sh").is_file() and (folder / "RULES.md").is_file()

    _py(LABEL, folder / pkg_mod.LABELS_FILE, 3)
    info, returned = pkg_mod.take_back(lset, folder)
    rep = pkg_mod.check_returned(lset, info, returned, ENV)
    s = rep["summary"]
    assert (s["accepted"], s["rejected"], s["unlabelled"]) == (2, 1, 1)

    acc = [r["hash"] for r in rep["frames"] if r["status"] == "accepted"]
    entry = pkg_mod.merge_returned(lset, info, returned, acc, "BG", ENV, report=rep)
    assert entry["version"] == 2 and entry["frames"] == 4 and entry["labellers"]["BG"] == 2
    assert lset.package(info["id"])["status"] == "merged"

    lset.set_validation("150px", ["rec2"])
    out = tmp_path / "split"
    counts = sleapenv.run_task(
        ENV,
        "split",
        {"slp": str(lset.version_file()), "val": lset.data["validation"], "out_dir": str(out)},
    )["counts"]
    assert counts == {"train": 2, "val_150px": 2}

    split = sleapenv.run_task(
        ENV,
        "split",
        {
            "slp": str(lset.version_file()),
            "val": {},
            "exclude": ["rec2"],
            "out_dir": str(tmp_path / "split2"),
        },
    )
    assert split["counts"] == {"train": 2} and split["left_out"] == 2

    def shared_split(share, name):
        return sleapenv.run_task(
            ENV,
            "split",
            {
                "slp": str(lset.version_file()),
                "val": {},
                "shared": {"rec2": "150px"},
                "test_share": share,
                "out_dir": str(tmp_path / name),
            },
        )

    every = shared_split(1.0, "all1")  # every rec2 frame tests, the other recording trains
    assert every["counts"] == {"train": 2, "val_150px": 2} and every["shared_test"] == 2
    assert shared_split(0.0, "all0")["counts"] == {"train": 4}

    _py(MORE_LABELS, seed)  # keep labelling the seed project outside a package
    rep = pkg_mod.check_project(lset, seed, ENV)
    assert rep["summary"]["accepted"] == 1 and rep["summary"]["already_in_set"] == 2
    acc = [r["hash"] for r in rep["frames"] if r["status"] == "accepted"]
    entry = pkg_mod.merge_project(lset, seed, rep, acc, "JC", ENV)
    assert entry["version"] == 3 and entry["frames"] == 5 and entry["labellers"]["JC"] == 1
    assert lset.projects()[0]["merged_into"] == 3
    assert pkg_mod.check_project(lset, seed, ENV)["summary"]["accepted"] == 0

    rows = sleapenv.run_task(
        ENV, "summary", {"slp": str(lset.version_file()), "out_dir": str(tmp_path / "ex")}
    )["recordings"]
    by = {r["recording"]: r for r in rows}
    assert set(by) == {"rec2", "rec_20261111_120000"}
    assert by["rec2"]["frames"] == 2 and by["rec2"]["flies"] == {"3": 2}
    assert by["rec2"]["sizes"] == {"144×144": 2} and by["rec2"]["labellers"] == {"BG": 2}
    assert all(Path(r["example"]).is_file() for r in rows)


def test_hash_share_is_fixed_per_frame():
    from rigconductor.labels.worker import hash_share

    assert hash_share("00000000ffffffff") == 0.0
    assert hash_share("80000000") == 0.5
    assert 0 <= hash_share("ffffffff00000000") < 1
