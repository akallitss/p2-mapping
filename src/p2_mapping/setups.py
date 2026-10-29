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


@dataclass(frozen=True)
class CablingEntry:
    """Stage A: which hybrid reads which detector connector, from `valid_from`."""
    valid_from: dt.datetime
    hybrids: tuple
    note: str = ""


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
    detectors = {}
    for det in need(d, "detectors", "the setup"):
        dn = need(det, "name", "an entry of detectors")
        if dn in detectors:
            raise ValueError(f"{path}: detector {dn!r} listed twice")
        detectors[dn] = {k: v for k, v in det.items() if k != "name"}

    entries = []
    for e in need(d, "cabling", "the setup"):
        vf = as_datetime(need(e, "valid_from", "an entry of cabling"))
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
            if r["detector"] not in detectors:
                raise ValueError(f"{path}: cabling from {vf} uses unknown detector "
                                 f"{r['detector']!r}")
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
                                    note=e.get("note", "")))
    entries.sort(key=lambda e: e.valid_from)
    if len({e.valid_from for e in entries}) != len(entries):
        raise ValueError(f"{path}: two cabling entries share a valid_from")

    known = {"name", "site", "design", "detectors", "cabling", "trigger", "description",
             "fec", "hv"}
    return Setup(name=name, site=d.get("site", ""), design=design,
                 detectors=detectors, cabling=tuple(entries),
                 trigger=d.get("trigger"), description=d.get("description", ""),
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
