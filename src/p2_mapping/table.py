"""The channel table: Stage A cabling x Stage B routing x Stage C geometry.

One row per instrumented (vmm, ch). Columns, in order:
    vmm, ch, detector, station, connector_N, half, sector, strip, channel_id,
    pad_cx, pad_cy, radius, phi, pad_w, pad_h, pad_angle, delta_phi, mapped,
    hybrid, cable, order, z_mm

`station` repeats `detector`; it is the column name the existing QA and
analysis code (vmm_stations.build_pad_table) uses.
"""

import datetime as dt
import hashlib
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from . import __version__, routing
from .setups import as_datetime, load_setup

PAD_COLUMNS = ("pad_cx", "pad_cy", "radius", "phi", "pad_w", "pad_h",
               "pad_angle", "delta_phi")


@lru_cache(maxsize=4)
def load_pads(design_name):
    """Stage C: the pad geometry of a design, indexed by channel_id."""
    path = routing.pads_path(design_name)
    return pd.read_csv(path).set_index("channel_id").sort_index()


def build_table(setup, when, order=None, orders=None, bottom_half=None):
    """The channel table of `setup` with the cabling in force at `when`.

    By default every half uses the order its cabling row gives, else the
    design's default. For validation scans:
        order        one order for every half, ignoring the cabling rows' own
        orders       {(detector, connector, half): order}, applied last
        bottom_half  'bot' or 'top': which half the bottom VMM reads
    """
    if isinstance(setup, str) or setup is None:
        setup = load_setup(setup)
    des = routing.design(setup.design)
    entry = setup.cabling_at(when)
    bot = bottom_half or des["bottom_vmm_half"]
    if bot not in routing.HALVES:
        raise ValueError(f"bottom_half must be 'bot' or 'top', got {bot!r}")
    top = "top" if bot == "bot" else "bot"
    orders = dict(orders or {})

    rows = []
    for r in entry.hybrids:
        det, n = r["detector"], int(r["connector"])
        for vmm, half, own in ((r["bottom_vmm"], bot, r.get("bottom_order")),
                               (r["top_vmm"], top, r.get("top_order"))):
            o = order or own or des["default_order"]
            o = orders.get((det, n, half), o)
            for within in range(des["channels_per_half"]):
                strip = routing.within_to_strip(within, half, o)
                rows.append((int(vmm), within, det, n, half, n - 1, strip,
                             routing.channel_id(n, strip, des["strips_per_connector"]),
                             int(r["hybrid"]), r.get("cable", ""), o,
                             setup.detectors[det].get("z_mm", np.nan)))

    tab = pd.DataFrame(rows, columns=[
        "vmm", "ch", "detector", "connector_N", "half", "sector", "strip",
        "channel_id", "hybrid", "cable", "order", "z_mm"])
    tab.insert(3, "station", tab["detector"])
    pads = load_pads(setup.design)
    cols = [c for c in PAD_COLUMNS if c in pads.columns]
    tab = tab.merge(pads[cols], left_on="channel_id", right_index=True, how="left")
    tab["mapped"] = tab["pad_cx"].notna()
    tab = tab.sort_values(["vmm", "ch"]).reset_index(drop=True)
    first = ["vmm", "ch", "detector", "station", "connector_N", "half", "sector",
             "strip", "channel_id", *cols, "mapped"]
    tab = tab[first + [c for c in tab.columns if c not in first]]
    tab.attrs.update(setup=setup.name, cabling_valid_from=entry.valid_from.isoformat(),
                     order=order, orders=orders, bottom_half=bot)
    return tab


class Mapping:
    """A setup's channel table at one time, with fast per-hit lookup.

        m = Mapping("lab_saclay_2026", when="2026-10-27T10:00")
        pads = m.lookup(hits)            # rows aligned with hits
    """

    def __init__(self, setup=None, when=None, **table_kw):
        self.setup = setup if hasattr(setup, "cabling") else load_setup(setup)
        self.when = as_datetime(when)
        self.cabling = self.setup.cabling_at(self.when)
        self.table = build_table(self.setup, self.when, **table_kw)
        # (32, 64) -> row in table, -1 where nothing is cabled
        self._row = np.full((32, 64), -1, dtype=np.int64)
        self._row[self.table["vmm"].to_numpy(), self.table["ch"].to_numpy()] = \
            np.arange(len(self.table))

    def rows(self, vmm, ch):
        """Row index into `table` per hit, -1 for channels nothing is cabled to."""
        vmm = np.asarray(vmm, dtype=np.int64)
        ch = np.asarray(ch, dtype=np.int64)
        ok = (vmm >= 0) & (vmm < 32) & (ch >= 0) & (ch < 64)
        out = np.full(vmm.shape, -1, dtype=np.int64)
        out[ok] = self._row[vmm[ok], ch[ok]]
        return out

    def lookup(self, hits, columns=None):
        """Table columns for each hit (a DataFrame or dict with vmm and ch).

        Returns a DataFrame aligned with `hits`; uncabled channels get NaN and
        mapped=False. Indexes a 32x64 lookup, so it never joins on every hit.
        """
        r = self.rows(hits["vmm"], hits["ch"])
        cols = list(columns) if columns is not None else \
            [c for c in self.table.columns if c not in ("vmm", "ch")]
        # the table's index is 0..n-1, so row -1 reindexes to an all-NaN row
        out = self.table[cols].reindex(r).reset_index(drop=True)
        if "mapped" in out:
            out["mapped"] = out["mapped"].fillna(False).astype(bool)
        if isinstance(hits, pd.DataFrame):
            out.index = hits.index
        return out

    def pad_xy(self):
        """(32, 64) arrays of pad x and y, NaN where unmapped."""
        x = np.full((32, 64), np.nan)
        y = np.full((32, 64), np.nan)
        t = self.table
        x[t["vmm"], t["ch"]] = t["pad_cx"]
        y[t["vmm"], t["ch"]] = t["pad_cy"]
        return x, y

    def resolved(self):
        """Everything this mapping was built from, for setup_resolved.json."""
        pads_raw = routing.pads_path(self.setup.design).read_bytes()
        e = self.cabling
        return {
            "setup": self.setup.name,
            "site": self.setup.site,
            "setup_file": self.setup.path,
            "setup_sha256": self.setup.sha256,
            "design": self.setup.design,
            "pads_sha256": hashlib.sha256(pads_raw).hexdigest(),
            "p2_mapping_version": __version__,
            "when": self.when.isoformat(),
            "detectors": self.setup.detectors,
            "trigger": self.setup.trigger,
            "cabling": {"valid_from": e.valid_from.isoformat(), "note": e.note,
                        "hybrids": list(e.hybrids)},
            "table_options": {k: v for k, v in self.table.attrs.items()
                              if k in ("order", "bottom_half")} | {
                "orders": {f"{d}:c{n}:{h}": o for (d, n, h), o in
                           self.table.attrs.get("orders", {}).items()}},
        }

    def write_resolved(self, path):
        """Write resolved() as JSON, e.g. into a run directory."""
        with open(path, "w") as f:
            json.dump(self.resolved(), f, indent=2, default=_json_default)
        return path


def _json_default(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def load_mapping(setup=None, when=None, **table_kw):
    """Mapping for `setup` (resolved via resolve_setup when None) at `when`."""
    return Mapping(setup, when, **table_kw)
