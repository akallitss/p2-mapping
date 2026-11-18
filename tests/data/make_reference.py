"""Regenerate the reference outputs of the test capture (run from the repo root).

    python tests/data/make_reference.py      # needs p2-decode

Only do this for an intended change of the mapping (a setup, routing or
geometry fix), and say why in the commit: the reference is what
test_reference.py holds p2-mapping to.

Inputs:
  * lab_run263_head.pcapng -- the same 33-packet capture p2-decode tests on
    (its tests/data): the start of lab run_263,
    cfg_gain6.0_peaktime200_opt_cosmic_gscan_hvon50_hvm/raw_daq_data/
    enp4s0f1_00001_20261118171801.pcapng on vmm_daplxa, FEC 192.168.1.13.
  * lab_run263_head_hits.npz -- its decoded fec, vmm, ch (p2-decode 0.9.0),
    written here so the mapping tests run without p2-decode installed.
  * setup lab_saclay_2026 at WHEN, the time the capture file was opened
    (run_263 records no setup name; it is a lab run, cabling c3-c5).

How the reference was checked when it was made (2026-11-18, p2-mapping 0.6.0):
  * the lab c3-c5 table it is built from matches, pad for pad, the independent
    P2_basket_analysis/cosmic_vmm_analysis orientation_check.Mapper(conn_shift=-1)
    (tests/test_legacy.py), and every hit's pad_cx/pad_cy was compared with
    that Mapper's xy_table at (vmm, ch): identical for all 44676 hits,
    including NaN for the 154 hits on VMM 0, which nothing is cabled to.
"""

import json
import os

import numpy as np

import p2_mapping

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "lab_run263_head.pcapng")
SETUP, WHEN = "lab_saclay_2026", "2026-11-18T17:18:01"
DETECTORS = ["P2_IN", "P2_MID", "P2_OUT"]          # detector code = index, -1 none


def encode(a):
    """The annotated columns as compact arrays; -1 where a hit is unmapped."""
    def ints(c, t):
        return a[c].fillna(-1).to_numpy().astype(t)
    return {
        "detector": a["detector"].map({d: i for i, d in enumerate(DETECTORS)})
                                 .fillna(-1).to_numpy().astype(np.int8),
        "connector_N": ints("connector_N", np.int8),
        "strip": ints("strip", np.int16),
        "channel_id": ints("channel_id", np.int32),
        "pad_cx": a["pad_cx"].to_numpy(np.float64),
        "pad_cy": a["pad_cy"].to_numpy(np.float64),
        "z_mm": a["z_mm"].to_numpy(np.float64),
        "mapped": a["mapped"].to_numpy(bool),
    }


def summarise(a, m):
    """The readable part: what a person looks at when the reference changes."""
    mapped = a["mapped"].to_numpy(bool)
    out = {"setup": SETUP, "when": WHEN, "p2_mapping_version": p2_mapping.__version__,
           "setup_sha256": m.setup.sha256, "pads_sha256": m.resolved()["pads_sha256"],
           "n_hits": int(len(a)), "n_unmapped": int((~mapped).sum()),
           "unmapped_vmms": sorted(int(v) for v in np.unique(a["vmm"][~mapped])),
           "detectors": {}}
    for d, g in a[mapped].groupby("detector"):
        out["detectors"][d] = {
            "n_hits": int(len(g)), "z_mm": float(g["z_mm"].iloc[0]),
            "connectors": sorted(int(c) for c in g["connector_N"].unique()),
            "mean_pad_cx": round(float(g["pad_cx"].mean()), 6),
            "mean_pad_cy": round(float(g["pad_cy"].mean()), 6)}
    return out


def main():
    import p2_decode
    hits, _ = p2_decode.decode(CAPTURE)
    hits = hits[["fec", "vmm", "ch"]]
    np.savez_compressed(os.path.join(HERE, "lab_run263_head_hits.npz"),
                        **{c: hits[c].to_numpy() for c in hits.columns})
    m = p2_mapping.load_mapping(SETUP, WHEN)
    a = m.annotate(hits)
    np.savez_compressed(os.path.join(HERE, "lab_run263_head_pads.npz"), **encode(a))
    summary = summarise(a, m)
    with open(os.path.join(HERE, "lab_run263_head_pads.json"), "w") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
