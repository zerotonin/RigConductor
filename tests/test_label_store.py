import pytest

from rigconductor import ConductorError
from rigconductor.labels.store import LabelSet, list_sets, scale_name


def test_scale_names_group_arena_sizes():
    assert scale_name(207) == "200px" and scale_name(494.9) == "500px"


def test_versions_packages_and_frames_index(tmp_path):
    s = LabelSet.create("triad", ["head", "thorax"], root=tmp_path)
    assert list_sets(tmp_path) == ["triad"]
    with pytest.raises(ConductorError):
        s.add_version({"frames": 1})  # file not written yet
    (s.path / "v1.pkg.slp").write_bytes(b"x")
    e = s.add_version({"frames": 2, "frames_index": [["rec", 0, 5], ["rec", 1, 9]]}, note="seed")
    assert e["version"] == 1 and s.version_file() == s.path / "v1.pkg.slp"
    s.add_package({"id": "p1", "frames_index": [["rec2", 3, 100]]})
    s.update_package("p1", status="returned")
    s.set_validation("500px", ["rec2", "rec2"])
    again = LabelSet.load("triad", root=tmp_path)
    assert again.labelled_frames() == {("rec", 0, 5), ("rec", 1, 9), ("rec2", 3, 100)}
    assert again.validation_recordings() == {"rec2"}
    assert again.package("p1")["status"] == "returned" and again.next_version() == 2
    with pytest.raises(ConductorError):
        LabelSet.create("triad", [], root=tmp_path)


def test_training_roles_keep_validation_without_frames(tmp_path):
    s = LabelSet.create("t", ["thorax"], root=tmp_path)
    s.set_validation("500px", ["new_val"])  # marked, not labelled yet
    s.set_validation("200px", ["june"])
    shown = {"oct": "450px", "june": "200px", "aug": "500px"}
    s.set_training_roles(shown, {"oct": "val", "june": "train", "aug": "out"})
    assert s.data["validation"] == {"450px": ["oct"], "500px": ["new_val"]}
    assert s.left_out() == {"aug"}
    assert LabelSet.load(s.path).validation_recordings() == {"oct", "new_val"}


def test_default_set(tmp_path):
    from rigconductor.labels import store

    assert store.default_set(tmp_path) is None
    LabelSet.create("a", ["thorax"], root=tmp_path)
    assert store.default_set(tmp_path) == "a"  # the only set
    LabelSet.create("b", ["thorax"], root=tmp_path)
    assert store.default_set(tmp_path) is None
    store.set_default("b", tmp_path)
    assert store.default_set(tmp_path) == "b" and store.list_sets(tmp_path) == ["a", "b"]
    with pytest.raises(ConductorError):
        store.set_default("c", tmp_path)
