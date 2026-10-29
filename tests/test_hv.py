"""The hv section of a setup: channel map, operating point, ceilings."""

import pytest
import yaml

from p2_mapping import load_setup

BASE = {
    "name": "t", "design": "p2_basket",
    "detectors": [{"name": "A", "z_mm": 0.0}],
    "cabling": [{"valid_from": "2026-01-01T00:00:00",
                 "hybrids": [{"detector": "A", "connector": 1, "hybrid": 1,
                              "bottom_vmm": 2, "top_vmm": 3}]}],
}
HV = {"set_here": True, "ceilings": {"mesh": 450, "drift": 900}, "references": ["R"],
      "channels": {"A": {"drift": [8, 0], "mesh": [8, 1]}, "R": {"drift": [8, 2]}},
      "operating": {"A": {"drift": 750, "mesh": 450}, "R": {"drift": 620}}}


def _load(tmp_path, hv):
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump({**BASE, "hv": hv}))
    return load_setup(str(p))


def test_shipped_setups():
    sps = load_setup("sps_h4_2026").hv
    assert sps["set_here"] and sps["channels"]["P2_IN"] == {"drift": (8, 0), "mesh": (8, 1)}
    assert sps["operating"]["P2_IN"] == {"drift": 740, "mesh": 440}
    assert sps["references"] == ["EIC_uRWELL_front", "EIC_uRWELL_back"]
    lab = load_setup("lab_saclay_2026").hv
    assert not lab["set_here"] and lab["channels"] == {} and lab["operating"] == {}


def test_valid(tmp_path):
    hv = _load(tmp_path, HV).hv
    assert hv["channels"]["R"] == {"drift": (8, 2)} and hv["ceilings"]["mesh"] == 450.0


@pytest.mark.parametrize("change, message", [
    ({"channels": {"A": {"drift": [8, 0], "mesh": [8, 0]}}}, "feeds both"),
    ({"channels": {"B": {"drift": [8, 5]}}}, "unknown 'B'"),
    ({"channels": {"A": {"drift": [8]}}}, r"\[card, channel\]"),
    ({"operating": {"A": {"drift": 750, "mesh": 460}}}, "above the mesh ceiling"),
    ({"operating": {"A": {"drift": 750, "resist": 400}}}, "has no channel"),
    ({"operating": {"A": {"drift": 750}}}, "no operating voltage for A.mesh"),
])
def test_refused(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        _load(tmp_path, {**HV, **change})


def test_not_set_here_needs_no_voltages(tmp_path):
    hv = _load(tmp_path, {**HV, "set_here": False, "operating": {}}).hv
    assert not hv["set_here"] and hv["operating"] == {}


def test_no_hv_section(tmp_path):
    p = tmp_path / "old.yaml"            # a file written before the hv section
    p.write_text(yaml.safe_dump(BASE))
    assert load_setup(str(p)).hv is None
