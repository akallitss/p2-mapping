import json

import numpy as np
import pandas as pd
import pytest

import p2_mapping as pm
from p2_mapping.validate import beam_spread, map_roughness


def test_shipped_setups_load():
    assert {"sps_h4_2026", "lab_saclay_2026"} <= set(pm.available_setups())
    for name in ("sps_h4_2026", "lab_saclay_2026"):
        s = pm.load_setup(name)
        assert s.sha256 and len(s.cabling) == 2


@pytest.mark.parametrize("when, connectors", [
    ("2026-10-22T18:08", {4, 5, 6}),
    ("2026-10-25T21:34", {4, 5, 6}),
    ("2026-10-26T12:26", {3, 4, 5}),
])
def test_lab_cabling_by_date(when, connectors):
    e = pm.load_setup("lab_saclay_2026").cabling_at(when)
    assert {r["connector"] for r in e.hybrids} == connectors


def test_no_cabling_before_first_entry():
    with pytest.raises(LookupError, match="no cabling before"):
        pm.load_setup("sps_h4_2026").cabling_at("2026-07-09")


def test_when_is_required():
    with pytest.raises(ValueError, match="time is required"):
        pm.Mapping("sps_h4_2026", None)


def test_sps_table():
    t = pm.build_table("sps_h4_2026", "2026-07-30")
    assert len(t) == 18 * 64 and t["vmm"].min() == 2
    assert t.groupby("detector")["vmm"].nunique().to_dict() == \
        {"P2_IN": 6, "P2_MID": 6, "P2_OUT": 6}
    assert (t["detector"] == t["station"]).all()
    # the two flipped P2_IN top halves, everything else reverse
    lin = t[t["order"] == "linear"].groupby(["vmm"]).size().to_dict()
    assert lin == {5: 64, 7: 64}
    # VMM 2 ch 0: c4 bottom half, reverse -> strip 64 -> channel_id 3*128 + 63
    r = t[(t.vmm == 2) & (t.ch == 0)].iloc[0]
    assert (r.connector_N, r.half, r.strip, r.channel_id) == (4, "bot", 64, 447)
    assert t["mapped"].all()


def test_sps_cable_change():
    before = pm.build_table("sps_h4_2026", "2026-08-01T11:00")
    after = pm.build_table("sps_h4_2026", "2026-08-01T12:00")
    changed = after.loc[before["cable"] != after["cable"], "vmm"].unique().tolist()
    assert sorted(changed) == [8, 9, 12, 13]
    cols = ["vmm", "ch", "channel_id", "pad_cx", "pad_cy"]
    assert before[cols].equals(after[cols])


def test_lab_recabling_moves_pads():
    a = pm.build_table("lab_saclay_2026", "2026-10-24")
    b = pm.build_table("lab_saclay_2026", "2026-10-27")
    assert (b["sector"] == a["sector"] - 1).all()
    assert (b["channel_id"] == a["channel_id"] - 128).all()
    assert set(a["order"]) == set(b["order"]) == {"reverse"}


def test_order_overrides():
    t = pm.build_table("sps_h4_2026", "2026-07-30", order="linear",
                       orders={("P2_OUT", 6, "top"): "pairswap"})
    assert set(t.loc[t.vmm != 19, "order"]) == {"linear"}
    assert set(t.loc[t.vmm == 19, "order"]) == {"pairswap"}
    swapped = pm.build_table("sps_h4_2026", "2026-07-30", bottom_half="top")
    assert set(swapped.loc[swapped.vmm == 2, "half"]) == {"top"}


def test_lookup_aligned_with_hits():
    m = pm.load_mapping("lab_saclay_2026", "2026-10-27")
    hits = pd.DataFrame({"vmm": [2, 0, 19, 31, 2], "ch": [0, 44, 63, 5, 0]},
                        index=[10, 11, 12, 13, 14])
    p = m.lookup(hits)
    assert list(p.index) == list(hits.index)
    assert p["mapped"].tolist() == [True, False, True, False, True]
    assert p.loc[10, "connector_N"] == 3 and p.loc[10, "detector"] == "P2_IN"
    assert np.isnan(p.loc[11, "pad_cx"])
    assert p["pad_cx"].dtype == np.float64
    x, y = m.pad_xy()
    assert x[2, 0] == p.loc[10, "pad_cx"] and np.isnan(x[0, 44])


def test_resolve_setup_precedence(monkeypatch, tmp_path):
    f = tmp_path / "setup"
    f.write_text("lab_saclay_2026\n")
    monkeypatch.delenv("P2_SETUP", raising=False)
    monkeypatch.setenv("P2_SETUP_FILE", str(f))
    assert pm.resolve_setup() == "lab_saclay_2026"
    monkeypatch.setenv("P2_SETUP", "sps_h4_2026")
    assert pm.resolve_setup() == "sps_h4_2026"
    assert pm.resolve_setup("x.yaml") == "x.yaml"
    monkeypatch.delenv("P2_SETUP")
    monkeypatch.setenv("P2_SETUP_FILE", str(tmp_path / "missing"))
    with pytest.raises(LookupError, match="no setup chosen"):
        pm.resolve_setup()


def test_site_local_setup_dir(monkeypatch, tmp_path):
    src = pm.load_setup("lab_saclay_2026").path
    (tmp_path / "bench_x.yaml").write_text(open(src).read().replace(
        "name: lab_saclay_2026", "name: bench_x"))
    monkeypatch.setenv("P2_SETUPS_DIR", str(tmp_path))
    assert pm.load_setup("bench_x").name == "bench_x"


@pytest.mark.parametrize("old, new, msg", [
    ("bottom_vmm: 2,  top_vmm: 3", "bottom_vmm: 4,  top_vmm: 5", "VMM 4 cabled twice"),
    ("detector: P2_MID, connector: 4", "detector: P2_MID, connector: 6", "cabled twice"),
    ("connector: 6, hybrid: 9", "connector: 11, hybrid: 9", "out of range"),
    ("top_order: linear", "top_order: sideways", "unknown top_order"),
    ("detectors:", "detector_list:", "missing 'detectors'"),
])
def test_invalid_setups_rejected(tmp_path, old, new, msg):
    src = open(pm.load_setup("sps_h4_2026").path).read()
    p = tmp_path / "bad.yaml"
    p.write_text(src.replace(old, new, 1))
    with pytest.raises(ValueError, match=msg):
        pm.load_setup(str(p))


def test_write_resolved(tmp_path):
    m = pm.load_mapping("sps_h4_2026", "2026-08-01T12:00")
    d = json.load(open(m.write_resolved(tmp_path / "setup_resolved.json")))
    assert d["setup"] == "sps_h4_2026" and d["cabling"]["valid_from"].startswith("2026-08-01")
    assert len(d["cabling"]["hybrids"]) == 9 and d["trigger"]["ch"] == 44
    assert len(d["setup_sha256"]) == len(d["pads_sha256"]) == 64


def test_validation_metrics_prefer_the_true_order():
    # a compact "beam spot" on P2_MID under the true map; a wrong order scatters it
    true = pm.load_mapping("sps_h4_2026", "2026-07-30")
    t = true.table[(true.table.detector == "P2_MID") & true.table.mapped]
    d = np.hypot(t.pad_cx - t.pad_cx.median(), t.pad_cy - t.pad_cy.median())
    w = np.exp(-(d / 60.0) ** 2)
    rng = np.random.default_rng(1)
    pick = rng.choice(len(t), size=20000, p=w / w.sum())
    hits = pd.DataFrame({"vmm": t.vmm.to_numpy()[pick], "ch": t.ch.to_numpy()[pick]})
    wrong = pm.load_mapping("sps_h4_2026", "2026-07-30", order="pairswap")
    assert beam_spread(hits, true, "P2_MID")[0] <= beam_spread(hits, wrong, "P2_MID")[0]
    assert map_roughness(hits, true, "P2_MID") < map_roughness(hits, wrong, "P2_MID")


MULTI_FEC = """
name: two_fecs
design: p2_basket
detectors:
  - {name: A, z_mm: 0.0}
  - {name: B, z_mm: 100.0}
cabling:
  - valid_from: 2026-01-01T00:00:00
    hybrids:
      - {fec: 1, detector: A, connector: 1, hybrid: 1, bottom_vmm: 2, top_vmm: 3}
      - {fec: 2, detector: B, connector: 1, hybrid: 2, bottom_vmm: 2, top_vmm: 3}
      - {fec: 2, detector: B, connector: 2, hybrid: 3, bottom_vmm: 4, top_vmm: 5}
"""


def test_same_vmm_ids_on_two_fecs(tmp_path):
    p = tmp_path / "two_fecs.yaml"
    p.write_text(MULTI_FEC)
    m = pm.load_mapping(str(p), "2026-06-01")
    assert len(m.table) == 6 * 64 and m.fecs == [1, 2]
    hits = pd.DataFrame({"fec": [1, 2, 2, 1, 3], "vmm": [2, 2, 4, 4, 2], "ch": [0, 0, 0, 0, 0]})
    p_ = m.lookup(hits)
    assert p_["detector"].tolist()[:3] == ["A", "B", "B"]
    assert p_["mapped"].tolist() == [True, True, True, False, False]   # VMM 4 is only on FEC 2
    with pytest.raises(ValueError, match="names FECs"):
        m.pad_xy()


def test_fec_clash_rules(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(MULTI_FEC.replace("{fec: 2, detector: B, connector: 1", "{fec: 1, detector: B, connector: 1"))
    with pytest.raises(ValueError, match="VMM 2 on FEC 1 cabled twice"):
        pm.load_setup(str(p))
    p.write_text(MULTI_FEC.replace("{fec: 2, detector: B, connector: 1, hybrid: 2,",
                                   "{detector: B, connector: 1, hybrid: 2,"))
    with pytest.raises(ValueError, match="cabled twice"):            # no-FEC row matches any
        pm.load_setup(str(p))


def test_single_fec_setups_match_any_fec():
    m = pm.load_mapping(LAB := "lab_saclay_2026", "2026-10-27")
    assert m.fecs == [] and set(m.table["fec"]) == {-1}
    hits = pd.DataFrame({"fec": [12, 1], "vmm": [2, 2], "ch": [0, 0]})
    assert m.lookup(hits)["mapped"].tolist() == [True, True]
