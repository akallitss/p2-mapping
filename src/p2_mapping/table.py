"""The channel table: Stage A cabling x Stage B routing x Stage C geometry.

One row per instrumented (fec, vmm, ch). Columns, in order:
    vmm, ch, detector, station, connector_N, half, sector, strip, channel_id,
    pad_cx, pad_cy, radius, phi, pad_w, pad_h, pad_angle, delta_phi, mapped,
    fec, hybrid, cable, order, z_mm

A VMM id is 5 bits, so with more than one FEC the same id repeats: a channel
is identified by (fec, vmm, ch). fec = -1 means the cabling row names no FEC
and matches any (setups with a single FEC, like the SPS 2026 and lab stands).

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
    placement = setup.at(when).detectors          # the stand at that time
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
            fec = -1 if r.get("fec") is None else int(r["fec"])
            for within in range(des["channels_per_half"]):
                strip = routing.within_to_strip(within, half, o)
                rows.append((int(vmm), within, det, n, half, n - 1, strip,
                             routing.channel_id(n, strip, des["strips_per_connector"]),
                             fec, int(r["hybrid"]), r.get("cable", ""), o,
                             placement[det].get("z_mm", np.nan)))

    tab = pd.DataFrame(rows, columns=[
        "vmm", "ch", "detector", "connector_N", "half", "sector", "strip",
        "channel_id", "fec", "hybrid", "cable", "order", "z_mm"])
    tab.insert(3, "station", tab["detector"])
    pads = load_pads(setup.design)
    cols = [c for c in PAD_COLUMNS if c in pads.columns]
    tab = tab.merge(pads[cols], left_on="channel_id", right_index=True, how="left")
    tab["mapped"] = tab["pad_cx"].notna()
    tab = tab.sort_values(["fec", "vmm", "ch"]).reset_index(drop=True)
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
        full = setup if hasattr(setup, "cabling") else load_setup(setup)
        self.when = as_datetime(when)
        # the stand as it was at `when`: that entry's detectors and trigger, so
        # everything reading mapping.setup follows dated changes of placement
        # and trigger, not only of cabling
        self.setup = full.at(self.when)
        self.cabling = self.setup.cabling[0]
        self.table = build_table(full, self.when, **table_kw)
        # (fec 0-15 + "any" at 16, vmm, ch) -> row in table, -1 if not cabled
        self._row = np.full((17, 32, 64), -1, dtype=np.int64)
        f = self.table["fec"].to_numpy()
        self._row[np.where(f < 0, 16, f), self.table["vmm"].to_numpy(),
                  self.table["ch"].to_numpy()] = np.arange(len(self.table))
        self.fecs = sorted(int(x) for x in np.unique(f) if x >= 0)

    def rows(self, vmm, ch, fec=None):
        """Row index into `table` per hit, -1 for channels nothing is cabled to.

        With `fec`, a row for that FEC wins; rows that name no FEC match any.
        """
        vmm = np.asarray(vmm, dtype=np.int64)
        ch = np.asarray(ch, dtype=np.int64)
        ok = (vmm >= 0) & (vmm < 32) & (ch >= 0) & (ch < 64)
        out = np.full(vmm.shape, -1, dtype=np.int64)
        out[ok] = self._row[16, vmm[ok], ch[ok]]
        if fec is not None and self.fecs:
            fec = np.broadcast_to(np.asarray(fec, dtype=np.int64), vmm.shape)
            okf = ok & (fec >= 0) & (fec < 16)
            specific = self._row[fec[okf], vmm[okf], ch[okf]]
            sub = out[okf]
            out[okf] = np.where(specific >= 0, specific, sub)
        return out

    def lookup(self, hits, columns=None):
        """Table columns for each hit (a DataFrame or dict with vmm, ch, maybe fec).

        Returns a DataFrame aligned with `hits`; uncabled channels get NaN and
        mapped=False. Indexes a lookup array, so it never joins on every hit.
        """
        r = self.rows(hits["vmm"], hits["ch"], hits["fec"] if "fec" in hits else None)
        cols = list(columns) if columns is not None else \
            [c for c in self.table.columns if c not in ("vmm", "ch", "fec")]
        # the table's index is 0..n-1, so row -1 reindexes to an all-NaN row
        out = self.table[cols].reindex(r).reset_index(drop=True)
        if "mapped" in out:
            out["mapped"] = out["mapped"].fillna(False).astype(bool)
        if isinstance(hits, pd.DataFrame):
            out.index = hits.index
        return out

    def pad_xy(self):
        """(32, 64) arrays of pad x and y, NaN where unmapped (single-FEC setups)."""
        if self.fecs:
            raise ValueError("pad_xy is per VMM id; this setup names FECs, use lookup()")
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
