"""A real capture (tests/data/lab_run263_head.pcapng): its hits' physical
coordinates against the reference.

The hits come from the committed lab_run263_head_hits.npz, so this runs without
p2-decode; with p2-decode installed, the capture itself is decoded too and must
give the same hits. See tests/data/make_reference.py for where the capture,
the setup time and the reference come from, and how the reference was checked.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

import p2_mapping as pm

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
sys.path.insert(0, DATA)
import make_reference as mr  # noqa: E402


@pytest.fixture(scope="module")
def hits():
    with np.load(os.path.join(DATA, "lab_run263_head_hits.npz")) as z:
        return pd.DataFrame({k: z[k] for k in ("fec", "vmm", "ch")})


@pytest.fixture(scope="module")
def ref():
    with np.load(os.path.join(DATA, "lab_run263_head_pads.npz")) as z:
        return {k: z[k] for k in z.files}


@pytest.fixture(scope="module")
def mapping():
    return pm.load_mapping(mr.SETUP, mr.WHEN)


def _check(annotated, ref):
    got = mr.encode(annotated)
    assert set(got) == set(ref)
    for c, r in ref.items():
        assert got[c].dtype == r.dtype, c
        assert np.array_equal(got[c], r, equal_nan=r.dtype.kind == "f"), c


def test_annotate_matches_reference(hits, ref, mapping):
    _check(mapping.annotate(hits), ref)


def test_summary_matches_reference(hits, mapping):
    with open(os.path.join(DATA, "lab_run263_head_pads.json")) as f:
        want = json.load(f)
    got = mr.summarise(mapping.annotate(hits), mapping)
    for k in ("p2_mapping_version",):            # changes on every release
        want.pop(k), got.pop(k)
    assert got == want


def test_reference_inputs_are_what_the_summary_says(mapping):
    # a changed setup file or pad geometry shows up here first, by name
    with open(os.path.join(DATA, "lab_run263_head_pads.json")) as f:
        want = json.load(f)
    assert mapping.setup.sha256 == want["setup_sha256"]
    assert mapping.resolved()["pads_sha256"] == want["pads_sha256"]


def test_lookup_agrees_with_annotate(hits, ref, mapping):
    p = mapping.lookup(hits)
    assert np.array_equal(p["pad_cx"].to_numpy(), ref["pad_cx"], equal_nan=True)
    assert np.array_equal(p["mapped"].to_numpy(), ref["mapped"])


def test_from_the_capture(hits, ref, mapping):
    p2_decode = pytest.importorskip("p2_decode")
    decoded, _ = p2_decode.decode(mr.CAPTURE)
    for c in ("fec", "vmm", "ch"):
        assert np.array_equal(decoded[c].to_numpy(), hits[c].to_numpy()), c
    # the decoder's full hit table, annotated: its own columns stay as they were
    a = mapping.annotate(decoded)
    assert list(a.columns[:len(decoded.columns)]) == list(decoded.columns)
    assert a[decoded.columns].equals(decoded)
    _check(a, ref)
