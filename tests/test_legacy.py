"""Row-by-row agreement with the maps the existing code builds.

Runs only when the legacy code is reachable:
    P2_MAPPING_VMM_QA   DAQ_Control_VMM_Beam/vmm_qa (vmm_stations.build_pad_table)
    P2_MAPPING_COSMIC   P2_basket_analysis/cosmic_vmm_analysis (orientation_check.Mapper)
"""

import importlib
import os
import sys

import numpy as np
import pytest

import p2_mapping as pm

VMM_QA = os.environ.get("P2_MAPPING_VMM_QA")
COSMIC = os.environ.get("P2_MAPPING_COSMIC")
COMPARED = ["vmm", "ch", "station", "connector_N", "half", "sector", "strip",
            "channel_id", "pad_cx", "pad_cy", "radius", "phi", "pad_w", "pad_h",
            "pad_angle", "delta_phi", "mapped"]


@pytest.fixture(scope="module")
def vs():
    if not VMM_QA:
        pytest.skip("set P2_MAPPING_VMM_QA")
    sys.path.insert(0, VMM_QA)
    return importlib.import_module("vmm_stations")


@pytest.fixture(scope="module")
def oc(vs):
    if not COSMIC:
        pytest.skip("set P2_MAPPING_COSMIC")
    sys.path.insert(0, COSMIC)
    return importlib.import_module("orientation_check")


def _same(a, b):
    a = a.sort_values(["vmm", "ch"]).reset_index(drop=True)
    b = b.sort_values(["vmm", "ch"]).reset_index(drop=True)
    assert len(a) == len(b)
    for c in COMPARED:
        x, y = a[c].to_numpy(), b[c].to_numpy()
        if x.dtype.kind == "f":
            assert np.array_equal(x, y, equal_nan=True), c
        else:
            assert (x == y).all(), c


def test_sps_matches_build_pad_table(vs):
    _same(pm.build_table("sps_h4_2026", "2026-07-30"), vs.build_pad_table())


@pytest.mark.parametrize("strategy", ["linear", "reverse", "pairswap"])
@pytest.mark.parametrize("bottom_half", ["bot", "top"])
def test_scans_match_build_pad_table(vs, strategy, bottom_half):
    new = pm.build_table("sps_h4_2026", "2026-07-30", order=strategy,
                         bottom_half=bottom_half)
    _same(new, vs.build_pad_table(strategy=strategy, bottom_half=bottom_half,
                                  overrides={}))


def test_lab_c4_c6_matches_plain_reverse(vs):
    # the lab entry carries no ribbon overrides
    _same(pm.build_table("lab_saclay_2026", "2026-10-24"),
          vs.build_pad_table(overrides={}))


@pytest.mark.parametrize("when, shift", [("2026-10-24", 0), ("2026-10-27", -1)])
def test_lab_matches_orientation_check_mapper(oc, when, shift):
    x_ref, y_ref = oc.Mapper(conn_shift=shift).xy_table()
    x, y = pm.load_mapping("lab_saclay_2026", when).pad_xy()
    assert np.array_equal(x, x_ref, equal_nan=True)
    assert np.array_equal(y, y_ref, equal_nan=True)
