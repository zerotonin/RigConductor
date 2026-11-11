import pytest

from rigconductor import ConductorError
from rigconductor.profiles.tadpole import Tadpole24Well, parse_plate_csv, validate_wells

CSV = """well,clutch,genotype,drug,dose,stage,plate_id
A1,c3,WT,PTZ,5 mM,47,P7
A2,c3,WT,PTZ,10 mM,47,P7
b1,c3,NeuroD2 crispant,,,47,P7
"""


def test_parse_plate_csv_keeps_unknown_columns_in_notes():
    rows = parse_plate_csv(CSV)
    assert [r["well"] for r in rows] == ["A1", "A2", "B1"]
    assert rows[0]["dose"] == "5 mM"
    assert "plate_id" in rows[0]["notes"]
    assert validate_wells(rows) == []


def test_validate_wells_errors():
    errs = validate_wells(
        [{"well": "Z9", "genotype": "x"}, {"well": "A1"}, {"well": "A1", "drug": "d"}]
    )
    assert any("Z9" in e for e in errs)
    assert any("listed 2 times" in e for e in errs)
    assert any("genotype or a drug" in e for e in errs)
    assert validate_wells([]) == ["enter at least one occupied well"]


def test_bad_csv():
    with pytest.raises(ConductorError):
        parse_plate_csv("foo,bar\n1,2\n")


def test_profile_validate(cfg):
    p = Tadpole24Well()
    assert p.validate_metadata({"wells": parse_plate_csv(CSV)}, cfg) == [
        "experiment / series name is required"
    ]
    assert p.validate_metadata({"experiment": "PTZ", "wells": parse_plate_csv(CSV)}, cfg) == []
    assert p.motif_metadata({"experiment": "PTZ", "wells": [1, 2]})["n_wells"] == 2


def test_cut_explains_missing_tadpose(cfg, tmp_path):
    from rigconductor.profiles.base import SessionContext

    ctx = SessionContext(cfg, "s", {}, tmp_path, lambda s: None)
    with pytest.raises(ConductorError, match="tadpose"):
        Tadpole24Well().cut(ctx)
