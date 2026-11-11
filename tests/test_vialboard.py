import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")

from rigconductor.gui.vialboard import VialBoard  # noqa: E402

CAT = [
    {
        "code": "AAA01",
        "genotype": "nlg4 KO70",
        "wildtype": 0,
        "location": "Geurten Lab",
        "generation": 3,
        "owner": "JC",
        "created": "2026-11-01",
    },
    {
        "code": "BBB02",
        "genotype": "Oregon R",
        "wildtype": 1,
        "location": "Hot Stock",
        "generation": 9,
        "owner": "BG",
        "created": "2026-10-01",
    },
    {
        "code": "SICK1",
        "genotype": "Oregon R",
        "wildtype": 1,
        "location": "Sickbay",
        "generation": 1,
        "owner": "BG",
        "created": "2026-10-01",
    },
]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_single_vial_fills_both_males_then_female(app):
    b = VialBoard(CAT, 4)
    c = b.cards[0]
    c.drop_codes(["AAA01"])
    assert c.value() == {"male_1": "AAA01", "male_2": "AAA01", "female_decap": ""}
    c.drop_codes(["BBB02"])  # fills the remaining empty slot
    assert c.value()["female_decap"] == "BBB02"


def test_copy_first_values_and_unticked_arenas(app):
    b = VialBoard(CAT, 4)
    b.cards[0].drop_codes(["AAA01", "AAA01", "BBB02"])
    b.cards[0].age.setValue(4)
    b.cards[3].use.setChecked(False)
    b.copy_first()
    v = b.values(3)
    assert set(v) == {"0", "1", "2"}
    assert v["2"][2] == {"role": "female_decap", "print_code": "BBB02", "age_days": 4.0}
    assert b.values(2)["1"] == [
        {"role": "male_1", "print_code": "AAA01", "age_days": 4.0},
        {"role": "male_2", "print_code": "AAA01", "age_days": 4.0},
    ]
    assert "3/3 arenas complete" in b.summary.text()


def test_sickbay_hidden_until_asked_and_search(app):
    b = VialBoard(CAT, 2)
    sh = b.shelf

    def visible():
        out = []
        for gi in range(sh.tree.topLevelItemCount()):
            g = sh.tree.topLevelItem(gi)
            out += [g.child(i).text(0) for i in range(g.childCount()) if not g.child(i).isHidden()]
        return sorted(out)

    assert visible() == ["AAA01", "BBB02"]
    sh.show_quarantine.setChecked(True)
    assert "SICK1" in visible()
    sh.search.setText("nlg4")
    assert visible() == ["AAA01"]


def test_unknown_code_is_flagged_and_set_values_round_trip(app):
    b = VialBoard(CAT, 2)
    b.cards[1].slots["male_1"].set_code("NOPE9")
    assert "not in DrosoDB" in b.cards[1].slots["male_1"].label.text()
    arenas = {
        "0": [
            {"role": "male_1", "print_code": "AAA01", "age_days": 2},
            {"role": "male_2", "print_code": "AAA01", "age_days": 2},
            {"role": "female_decap", "print_code": "BBB02", "age_days": 2},
        ]
    }
    b.set_values(arenas)
    assert not b.cards[1].use.isChecked()
    assert b.values(3) == {"0": [{**f, "age_days": 2.0} for f in arenas["0"]]}


def test_genotypes_by_arena(app):
    b = VialBoard(CAT, 3)
    b.cards[0].drop_codes(["AAA01"])
    b.cards[0].slots["female_decap"].set_code("BBB02")
    b.cards[1].drop_codes(["BBB02"])
    b.cards[2].drop_codes(["AAA01"])
    b.cards[2].use.setChecked(False)
    assert b.genotypes_by_arena() == {0: ["nlg4 KO70", "Oregon R"], 1: ["Oregon R"]}
