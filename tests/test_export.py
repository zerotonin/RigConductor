import json

import pytest

from rigconductor import ConductorError
from rigconductor.export import Disk, mount, parse_lsblk, verify_bundle, write_bundle

LSBLK = {
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "rm": False,
            "hotplug": False,
            "type": "disk",
            "size": "931.5G",
            "fstype": None,
            "label": None,
            "mountpoint": None,
            "children": [
                {
                    "name": "sda1",
                    "path": "/dev/sda1",
                    "rm": False,
                    "hotplug": False,
                    "type": "part",
                    "size": "931.5G",
                    "fstype": "ext4",
                    "label": "DATA",
                    "mountpoint": "/mnt/DATA",
                }
            ],
        },
        {
            "name": "sdb",
            "path": "/dev/sdb",
            "rm": False,
            "hotplug": True,
            "type": "disk",
            "size": "3.6T",
            "fstype": None,
            "label": None,
            "mountpoint": None,
            "children": [
                {
                    "name": "sdb1",
                    "path": "/dev/sdb1",
                    "rm": False,
                    "hotplug": True,
                    "type": "part",
                    "size": "16M",
                    "fstype": None,
                    "label": None,
                    "mountpoint": None,
                },
                {
                    "name": "sdb2",
                    "path": "/dev/sdb2",
                    "rm": False,
                    "hotplug": True,
                    "type": "part",
                    "size": "3.6T",
                    "fstype": "exfat",
                    "label": "GEURTEN01",
                    "mountpoint": "/media/labadmin/GEURTEN01",
                },
            ],
        },
        {
            "name": "sdc",
            "path": "/dev/sdc",
            "rm": True,
            "hotplug": True,
            "type": "disk",
            "size": "29G",
            "fstype": None,
            "label": None,
            "mountpoint": None,
            "children": [
                {
                    "name": "sdc1",
                    "path": "/dev/sdc1",
                    "rm": True,
                    "hotplug": True,
                    "type": "part",
                    "size": "29G",
                    "fstype": "vfat",
                    "label": "STICK",
                    "mountpoint": None,
                }
            ],
        },
        {
            "name": "nvme0n1",
            "path": "/dev/nvme0n1",
            "rm": False,
            "hotplug": False,
            "type": "disk",
            "size": "238G",
            "fstype": None,
            "label": None,
            "mountpoint": None,
            "children": [
                {
                    "name": "nvme0n1p2",
                    "path": "/dev/nvme0n1p2",
                    "rm": False,
                    "hotplug": False,
                    "type": "part",
                    "size": "238G",
                    "fstype": "ext4",
                    "label": None,
                    "mountpoint": "/",
                }
            ],
        },
    ]
}


def test_parse_lsblk_picks_removable_partitions_with_fs():
    disks = parse_lsblk(json.dumps(LSBLK))
    assert [d.device for d in disks] == ["/dev/sdb2", "/dev/sdc1"]
    assert disks[0].label == "GEURTEN01" and disks[0].mountpoint
    assert "not mounted" in disks[1].text and "STICK" in disks[1].text


def test_mount_parses_udisksctl():
    class R:
        returncode = 0
        stdout = "Mounted /dev/sdc1 at /media/labadmin/STICK.\n"
        stderr = ""

    d = Disk("/dev/sdc1", "29G", "vfat", "STICK", None)
    assert mount(d, run=lambda *a, **k: R()) == __import__("pathlib").Path("/media/labadmin/STICK")
    assert d.mountpoint == "/media/labadmin/STICK"


def test_bundle_roundtrip_and_verify(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.csv").write_text("1,2\n", encoding="utf-8")
    (src / "b.sqlite3").write_bytes(b"\x00" * 100)
    dest = tmp_path / "usb" / "s1"
    man = write_bundle({"a.csv": src / "a.csv", "sub/b.sqlite3": src / "b.sqlite3"}, dest)
    assert man.is_file() and (dest / "sub" / "b.sqlite3").is_file()
    assert verify_bundle(dest) == []
    (dest / "a.csv").write_text("corrupt", encoding="utf-8")
    assert verify_bundle(dest) == ["a.csv"]
    (dest / "sub" / "b.sqlite3").unlink()
    assert sorted(verify_bundle(dest)) == ["a.csv", "sub/b.sqlite3"]


def test_bundle_refuses_nonempty_dest_and_missing_source(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "x").write_text("x", encoding="utf-8")
    with pytest.raises(ConductorError):
        write_bundle({}, dest)
    with pytest.raises(ConductorError):
        write_bundle({"a": tmp_path / "nope"}, tmp_path / "e")
