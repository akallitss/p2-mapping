"""Dated stand entries (placement + trigger + cabling) and adding one to a file."""

import shutil
from importlib import resources

import pytest

from p2_mapping import Mapping, load_setup
from p2_mapping.edit import add_entry, write_atomic

LAB = resources.files("p2_mapping") / "data" / "setups" / "lab_saclay_2026.yaml"


def _lab_copy(tmp_path):
    p = tmp_path / "lab_saclay_2026.yaml"
    shutil.copy(LAB, p)
    return p


def _entry(rows=None, **kw):
    cur = load_setup("lab_saclay_2026").cabling[-1]
    return {"valid_from": "2026-11-02T09:00:00", "note": "test change",
            "detectors": [{"name": "P2_IN", "alias": "det5", "z_mm": 320.0},
                          {"name": "P2_MID", "alias": "det2", "z_mm": 630.0},
                          {"name": "P2_OUT", "alias": "det1", "z_mm": 940.0}],
            "trigger": None,
            "hybrids": rows if rows is not None else [dict(r) for r in cur.hybrids], **kw}


def test_add_keeps_comments_and_old_entries(tmp_path):
    p = _lab_copy(tmp_path)
    before = p.read_text()
    text = add_entry(p, _entry())
    for line in before.splitlines():
        assert line in text                       # nothing of the original lost
    write_atomic(p, text)
    s = load_setup(str(p))
    old = load_setup("lab_saclay_2026")
    assert s.cabling[:-1] == old.cabling and len(s.cabling) == len(old.cabling) + 1
    assert s.hv == old.hv                         # the section after cabling intact


def test_placement_and_trigger_are_dated(tmp_path):
    p = _lab_copy(tmp_path)
    e = _entry()
    e["trigger"] = {"hybrid": 0, "vmm": 0, "ch": 44}
    write_atomic(p, add_entry(p, e))
    s = load_setup(str(p))
    before, after = s.at("2026-10-30T12:00"), s.at("2026-11-03T12:00")
    assert before.detectors["P2_IN"]["alias"] == "det3" and before.trigger is None
    assert after.detectors["P2_IN"]["alias"] == "det5" and after.trigger["ch"] == 44
    m = Mapping(str(p), when="2026-11-03T12:00")
    assert m.setup.detectors["P2_IN"]["alias"] == "det5" and m.setup.trigger["vmm"] == 0
    assert Mapping(str(p), when="2026-10-30T12:00").setup.detectors["P2_IN"]["alias"] == "det3"


@pytest.mark.parametrize("change, message", [
    ({"valid_from": "2026-10-20T00:00:00"}, "start after the last one"),
    ({"note": " "}, "needs a note"),
    ({"trigger": {"hybrid": 1, "vmm": 2, "ch": 5}}, "trigger channel's VMM and also cabled"),
    ({"trigger": {"hybrid": 0, "vmm": 0, "ch": 64}}, "out of range"),
])
def test_refused(tmp_path, change, message):
    p = _lab_copy(tmp_path)
    before = p.read_text()
    with pytest.raises(ValueError, match=message):
        add_entry(p, {**_entry(), **change})
    assert p.read_text() == before


def test_refuses_a_vmm_cabled_twice(tmp_path):
    p = _lab_copy(tmp_path)
    rows = _entry()["hybrids"]
    rows[1] = {**rows[1], "bottom_vmm": rows[0]["bottom_vmm"]}
    with pytest.raises(ValueError, match="cabled twice"):
        add_entry(p, _entry(rows=rows))


def test_setup_without_entry_detectors_unchanged():
    s = load_setup("sps_h4_2026")
    assert s.at("2026-08-02").detectors == s.detectors and s.at("2026-08-02").trigger == s.trigger
