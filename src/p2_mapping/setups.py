"""Setups: one YAML file per experimental setup, chosen once at startup.

A setup names its detectors (and their placement), the trigger channel if any,
and a list of dated cabling entries (Stage A). Nothing in this package
branches on a site name; everything that differs between the lab, a beam test
or another bench is in these files.

Choosing the setup, highest priority first:
    1. an explicit name or path (e.g. a --setup command-line option)
    2. the environment variable P2_SETUP
    3. a one-line machine default in $P2_SETUP_FILE, else ~/.config/p2-readout/setup
With none of these, resolve_setup() refuses rather than guessing a site.

A name is looked up in the directories of $P2_SETUPS_DIR (colon-separated,
for site-local setups) and then in the setups shipped with the package.
"""

import datetime as dt
import hashlib
import os
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from . import routing

DEFAULT_SETUP_FILE = Path.home() / ".config" / "p2-readout" / "setup"
SUFFIXES = (".yaml", ".yml")


_UNSET = object()


@dataclass(frozen=True)
class CablingEntry:
    """The stand from `valid_from`: which hybrid reads which detector connector,
    and optionally where the detectors are and which trigger is used.

    detectors/trigger left out (None/_UNSET) mean "as in the setup's top level";
    an entry written by the DAQ's Setup tab gives both, so it is a complete
    snapshot of the stand.
    """
    valid_from: dt.datetime
    hybrids: tuple
    note: str = ""
    detectors: dict = None           # name -> {"z_mm", "alias"}; None = setup's
    trigger: object = _UNSET         # dict, None (= no trigger) or _UNSET = setup's


@dataclass(frozen=True)
class Setup:
    name: str
    site: str
    design: str
    detectors: dict                  # name -> {"z_mm": ..., "alias": ...}
    cabling: tuple                   # CablingEntry, sorted by valid_from
    trigger: dict = None             # {"hybrid", "vmm", "ch"} or None
    description: str = ""
    hv: dict = None                  # see _parse_hv; None = the setup says nothing about HV
    path: str = None
    sha256: str = None
    extra: dict = field(default_factory=dict)

    def cabling_at(self, when):
        """The cabling entry in force at `when` (datetime, date, ISO string or 'now')."""
        t = as_datetime(when)
        found = None
        for e in self.cabling:
            if e.valid_from <= t:
                found = e
        if found is None:
            raise LookupError(
                f"setup {self.name!r} has no cabling before {t:%Y-%m-%d %H:%M}; "
                f"first entry starts {self.cabling[0].valid_from:%Y-%m-%d %H:%M}")
        return found

    def at(self, when):
        """This setup as the stand was at `when`: detectors and trigger of the
        entry in force then (falling back to the top level), and only that
        cabling entry. What a mapping for that time should describe."""
        e = self.cabling_at(when)
        return Setup(**{**self.__dict__,
                        "detectors": e.detectors if e.detectors is not None else self.detectors,
                        "trigger": self.trigger if e.trigger is _UNSET else e.trigger,
                        "cabling": (e,)})


def as_datetime(when):
    if when is None:
        raise ValueError("a time is required to pick the cabling: pass the run "
                         "start time, or 'now' for live use")
    if isinstance(when, str):
        if when == "now":
            return dt.datetime.now()
        when = dt.datetime.fromisoformat(when)
    if isinstance(when, dt.datetime):
        # setup files hold local wall-clock times; compare without zones
        return when.replace(tzinfo=None) if when.tzinfo else when
    if isinstance(when, dt.date):
        return dt.datetime(when.year, when.month, when.day)
    raise TypeError(f"cannot read a time from {when!r}")


# ---------------------------------------------------------------------------
# finding and loading setup files
# ---------------------------------------------------------------------------

def _search_dirs():
    dirs = [Path(d) for d in os.environ.get("P2_SETUPS_DIR", "").split(":") if d]
    dirs.append(resources.files("p2_mapping") / "data" / "setups")
    return dirs


def available_setups():
    """{name: path} of every setup file on the search path (first one wins)."""
    out = {}
    for d in _search_dirs():
        if d.is_dir():
            for p in sorted(d.iterdir(), key=lambda p: p.name):
                stem, dot, ext = p.name.rpartition(".")
                if dot and "." + ext in SUFFIXES:
                    out.setdefault(stem, p)
    return out


def find_setup(name_or_path):
    p = Path(name_or_path)
    if p.suffix in SUFFIXES or p.is_file():
        if not p.is_file():
            raise FileNotFoundError(f"setup file {p} does not exist")
        return p
    known = available_setups()
    if name_or_path not in known:
        raise LookupError(f"no setup named {name_or_path!r}; known: {sorted(known)}")
    return known[name_or_path]


def resolve_setup(explicit=None):
    """The setup to use: explicit > $P2_SETUP > machine default file."""
    if explicit:
        return explicit
    env = os.environ.get("P2_SETUP")
    if env:
        return env
    f = Path(os.environ.get("P2_SETUP_FILE", DEFAULT_SETUP_FILE))
    if f.is_file():
        name = f.read_text().strip()
        if name:
            return name
    raise LookupError(
        "no setup chosen: pass one explicitly (e.g. --setup lab_saclay_2026), set "
        f"P2_SETUP, or write its name into {f}. Known setups: {sorted(available_setups())}")


def load_setup(name_or_path=None):
    """Load and check a setup; None resolves it via resolve_setup()."""
    path = find_setup(resolve_setup(name_or_path))
    raw = path.read_bytes()
    data = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a setup (expected a YAML mapping)")
    setup = _parse(data, str(path))
    return Setup(**{**setup.__dict__, "sha256": hashlib.sha256(raw).hexdigest()})


def _parse(d, path):
    def need(obj, key, where):
        if key not in obj:
            raise ValueError(f"{path}: {where} is missing '{key}'")
        return obj[key]

    name = need(d, "name", "the setup")
    design = need(d, "design", "the setup")
    des = routing.design(design)

    def parse_detectors(lst, where):
        out = {}
        for det in lst:
            dn = need(det, "name", f"an entry of {where}")
            if dn in out:
                raise ValueError(f"{path}: detector {dn!r} listed twice in {where}")
            if "z_mm" in det and not isinstance(det["z_mm"], (int, float)):
                raise ValueError(f"{path}: {where} {dn}: z_mm must be a number")
            out[dn] = {k: v for k, v in det.items() if k != "name"}
        return out

    def parse_trigger(t, where):
        if t is None:
            return None
        if not isinstance(t, dict) or not all(isinstance(t.get(k), int) and t[k] >= 0
                                              for k in ("hybrid", "vmm", "ch")):
            raise ValueError(f"{path}: {where} trigger must be null or "
                             "{hybrid, vmm, ch} (non-negative integers)")
        if not 0 <= t["ch"] <= 63:
            raise ValueError(f"{path}: {where} trigger channel {t['ch']} out of range 0-63")
        return dict(t)

    detectors = parse_detectors(need(d, "detectors", "the setup"), "detectors")
    top_trigger = parse_trigger(d.get("trigger"), "the setup's")

    entries = []
    for e in need(d, "cabling", "the setup"):
        vf = as_datetime(need(e, "valid_from", "an entry of cabling"))
        e_dets = (parse_detectors(e["detectors"], f"cabling from {vf}")
                  if "detectors" in e else None)
        e_trig = parse_trigger(e["trigger"], f"cabling from {vf}") if "trigger" in e else _UNSET
        here = e_dets if e_dets is not None else detectors
        trig = top_trigger if e_trig is _UNSET else e_trig
        rows = need(e, "hybrids", f"cabling from {vf}")
        seen_vmm, seen_conn = {}, set()
        for r in rows:
            if "fec" not in r and "fec" in d:
                r = {**r, "fec": d["fec"]}       # setup-level default FEC
            fec = r.get("fec")
            if fec is not None and not 0 <= int(fec) <= 15:
                raise ValueError(f"{path}: fec {fec} out of range 0-15")
            for k in ("detector", "connector", "hybrid", "bottom_vmm", "top_vmm"):
                need(r, k, f"a hybrid row of cabling from {vf}")
            if r["detector"] not in here:
                raise ValueError(f"{path}: cabling from {vf} uses unknown detector "
                                 f"{r['detector']!r}")
            if trig and fec is None and trig["vmm"] in (r["bottom_vmm"], r["top_vmm"]):
                raise ValueError(f"{path}: cabling from {vf}: VMM {trig['vmm']} is the "
                                 f"trigger channel's VMM and also cabled to {r['detector']}")
            if not 1 <= r["connector"] <= des["n_connectors"]:
                raise ValueError(f"{path}: connector {r['connector']} out of range")
            # a VMM id repeats across FECs; a row without fec matches any FEC,
            # so it clashes with every other row of the same VMM id
            for v in (r["bottom_vmm"], r["top_vmm"]):
                for other in seen_vmm.get(v, []):
                    if fec is None or other is None or other == fec:
                        where = f"VMM {v}" + (f" on FEC {fec}" if fec is not None else "")
                        raise ValueError(f"{path}: {where} cabled twice in entry from {vf}")
                seen_vmm.setdefault(v, []).append(fec)
            key = (r["detector"], r["connector"])
            if key in seen_conn:
                raise ValueError(f"{path}: {key} cabled twice in entry from {vf}")
            seen_conn.add(key)
            for k in ("bottom_order", "top_order"):
                if k in r and r[k] not in routing.ORDERS:
                    raise ValueError(f"{path}: unknown {k} {r[k]!r}")
        rows = [{**r, "fec": d["fec"]} if "fec" not in r and "fec" in d else r
                for r in rows]
        entries.append(CablingEntry(valid_from=vf, hybrids=tuple(dict(r) for r in rows),
                                    note=e.get("note", ""), detectors=e_dets,
                                    trigger=e_trig))
    entries.sort(key=lambda e: e.valid_from)
    if len({e.valid_from for e in entries}) != len(entries):
        raise ValueError(f"{path}: two cabling entries share a valid_from")

    known = {"name", "site", "design", "detectors", "cabling", "trigger", "description",
             "fec", "hv"}
    return Setup(name=name, site=d.get("site", ""), design=design,
                 detectors=detectors, cabling=tuple(entries),
                 trigger=top_trigger, description=d.get("description", ""),
                 hv=_parse_hv(d.get("hv"), detectors, path),
                 path=path, extra={k: v for k, v in d.items() if k not in known})


def _parse_hv(hv, detectors, path):
    """The HV section: which supply channel feeds which electrode, and the voltages.

        hv:
          set_here: true            # the DAQ of this setup sets the voltages
          ceilings: {mesh: 450, drift: 900}
          channels:                 # (card, channel) per electrode
            P2_IN: {drift: [8, 0], mesh: [8, 1]}
            EIC_uRWELL_front: {drift: [8, 6], resist: [12, 0]}
          references: [EIC_uRWELL_front]   # HV only, read out elsewhere
          operating:
            P2_IN: {drift: 740, mesh: 440}

    Every channel name is a detector of the setup or listed in references; a
    (card, channel) feeds one electrode only; every operating voltage is for a
    mapped electrode and under its ceiling. With set_here false (e.g. the lab,
    where another machine holds the crate) channels and operating may be empty.
    """
    if hv is None:
        return None
    if not isinstance(hv, dict):
        raise ValueError(f"{path}: hv must be a mapping")
    refs = list(hv.get("references") or [])
    names = set(detectors) | set(refs)
    channels, seen = {}, {}
    for det, roles in (hv.get("channels") or {}).items():
        if det not in names:
            raise ValueError(f"{path}: hv channel for unknown {det!r} (not a detector "
                             "and not in hv.references)")
        channels[det] = {}
        for role, cc in roles.items():
            if not (isinstance(cc, (list, tuple)) and len(cc) == 2
                    and all(isinstance(x, int) and x >= 0 for x in cc)):
                raise ValueError(f"{path}: hv channel {det}.{role} must be [card, channel]")
            key = (cc[0], cc[1])
            if key in seen:
                raise ValueError(f"{path}: HV card {key[0]} channel {key[1]} feeds both "
                                 f"{seen[key]} and {det}.{role}")
            seen[key] = f"{det}.{role}"
            channels[det][role] = key
    ceilings = {k: float(v) for k, v in (hv.get("ceilings") or {}).items()}
    operating = {}
    for det, roles in (hv.get("operating") or {}).items():
        operating[det] = {}
        for role, v in roles.items():
            if role not in channels.get(det, {}):
                raise ValueError(f"{path}: hv operating {det}.{role} has no channel")
            if not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{path}: hv operating {det}.{role} = {v!r} is not a voltage")
            if role in ceilings and v > ceilings[role]:
                raise ValueError(f"{path}: hv operating {det}.{role} = {v} V is above the "
                                 f"{role} ceiling {ceilings[role]:g} V")
            operating[det][role] = v
    if hv.get("set_here", True):
        missing = [f"{d}.{r}" for d, rr in channels.items() for r in rr
                   if r not in operating.get(d, {})]
        if missing:
            raise ValueError(f"{path}: hv set_here but no operating voltage for "
                             + ", ".join(missing))
    return {"set_here": bool(hv.get("set_here", True)), "ceilings": ceilings,
            "channels": channels, "references": refs, "operating": operating,
            "note": hv.get("note", "")}
