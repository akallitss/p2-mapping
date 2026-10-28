"""Checks of a mapping against data: is the channel order right?

Moved from DAQ_Control_VMM_Beam/vmm_qa/vmm_stations.py. Both take a hits table
(anything with vmm and ch columns) and a Mapping, so they need no decoder.
Scan candidate orders by building one Mapping per candidate
(Mapping(setup, when, order=..., orders=..., bottom_half=...)) and comparing.
"""

import numpy as np


def _occupancy(hits, mapping, detector):
    """Hit counts and pad centres for the mapped channels of one detector."""
    t = mapping.table
    sub = t[(t["detector"] == detector) & t["mapped"]].reset_index(drop=True)
    key = sub["vmm"].to_numpy().astype(np.int64) * 64 + sub["ch"].to_numpy()
    hk = np.asarray(hits["vmm"], dtype=np.int64) * 64 + np.asarray(hits["ch"])
    uk, uc = np.unique(hk, return_counts=True)
    occ = dict(zip(uk.tolist(), uc.tolist()))
    n = np.array([occ.get(int(k), 0) for k in key], dtype=float)
    return n, sub["pad_cx"].to_numpy(float), sub["pad_cy"].to_numpy(float)


def beam_spread(hits, mapping, detector=None):
    """RMS radius (mm) of the hit distribution in pad space, and the hit count.

    The beam is a compact spot, so the correct order minimises this. A wrong
    within-half order smears hits across the fan and inflates it. Nearly blind
    to a permutation inside one half: use map_roughness for that.
    """
    p = mapping.lookup(hits, columns=["detector", "pad_cx", "pad_cy", "mapped"])
    m = p["mapped"].to_numpy()
    if detector is not None:
        m = m & (p["detector"] == detector).to_numpy()
    if m.sum() < 100:
        return float("nan"), int(m.sum())
    x = p["pad_cx"].to_numpy(float)[m]
    y = p["pad_cy"].to_numpy(float)[m]
    return float(np.sqrt(np.var(x) + np.var(y))), int(m.sum())


def _neighbours(cx, cy, k=6):
    """Indices of the k nearest pads for each pad (brute force; ~400 pads)."""
    d = np.hypot(cx[:, None] - cx[None, :], cy[:, None] - cy[None, :])
    np.fill_diagonal(d, np.inf)
    return np.argsort(d, axis=1)[:, :k]


def map_roughness(hits, mapping, detector, min_frac=0.05):
    """How SPECKLED the occupancy is: the metric that finds a scrambled order.

    A wrong order inside a half leaves hits in the same part of the fan but
    produces dead pads next to bright ones. For every pad in the illuminated
    region, compare its count to the median of its 6 nearest neighbours; the
    result is the median over pads of |n - med| / (n + med + 1), in [0, 1].
    Correct maps are locally smooth (small), scrambled ones speckled (large).
    """
    n, cx, cy = _occupancy(hits, mapping, detector)
    if n.sum() < 500 or len(n) < 20:
        return float("nan")
    nb = _neighbours(cx, cy)
    med = np.median(n[nb], axis=1)
    # Only judge the illuminated region: empty fringes are genuinely empty.
    live = med > min_frac * np.max(med) if np.max(med) > 0 else np.zeros(len(n), bool)
    if live.sum() < 10:
        return float("nan")
    return float(np.median(np.abs(n[live] - med[live]) / (n[live] + med[live] + 1.0)))
