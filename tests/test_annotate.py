import numpy as np
import pandas as pd
import pytest

import p2_mapping as pm


@pytest.fixture(scope="module")
def m():
    return pm.load_mapping("lab_saclay_2026", "2026-10-27")


def test_dataframe_gets_columns_appended(m):
    hits = pd.DataFrame({"vmm": [2, 0, 19], "ch": [0, 44, 63], "adc": [100, 200, 300]},
                        index=[7, 7, 3])                    # duplicate index on purpose
    a = m.annotate(hits)
    assert list(a.columns) == ["vmm", "ch", "adc", *pm.ANNOTATE_COLUMNS]
    assert list(a.index) == [7, 7, 3]
    assert a["adc"].tolist() == [100, 200, 300]
    assert a["mapped"].tolist() == [True, False, True]
    p = m.lookup(hits.reset_index(drop=True))
    for c in pm.ANNOTATE_COLUMNS:
        assert a[c].reset_index(drop=True).equals(p[c]), c
    assert "pad_cx" not in hits                              # input untouched


def test_columns_choice(m):
    hits = pd.DataFrame({"vmm": [2], "ch": [0]})
    assert list(m.annotate(hits, columns=["pad_cx"]).columns) == ["vmm", "ch", "pad_cx"]
    every = m.annotate(hits, columns="all")
    assert {"radius", "phi", "pad_w", "hybrid", "cable"} <= set(every.columns)


def test_reannotating_replaces(m):
    other = pm.load_mapping("lab_saclay_2026", "2026-10-24")     # c4-c6, before re-cabling
    hits = pd.DataFrame({"vmm": [2, 2], "ch": [0, 1]})
    twice = m.annotate(other.annotate(hits))
    once = m.annotate(hits)
    assert list(twice.columns) == list(once.columns)
    assert twice.equals(once)


def test_dict_of_arrays(m):
    hits = {"vmm": np.array([2, 0]), "ch": np.array([0, 44]), "fec": np.array([1, 1])}
    a = m.annotate(hits)
    assert a["vmm"] is hits["vmm"] and "detector" not in hits
    assert a["mapped"].tolist() == [True, False]
    assert a["connector_N"][0] == 3


def test_one_hit(m):
    a = m.annotate({"vmm": 2, "ch": 0, "adc": 512})
    assert a["adc"] == 512 and a["detector"] == "P2_IN" and a["mapped"] is True
    assert isinstance(a["pad_cx"], float) and isinstance(a["channel_id"], int)
    none = m.annotate({"vmm": 0, "ch": 44})
    assert none["detector"] is None and none["mapped"] is False
    assert np.isnan(none["pad_cx"])


def test_rejects_other_types(m):
    with pytest.raises(TypeError, match="DataFrame or a dict"):
        m.annotate([(2, 0)])
