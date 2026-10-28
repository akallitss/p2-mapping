"""Setups: one TOML file per experimental setup, chosen once at startup.

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

try:
    import tomllib
except ModuleNotFoundError:          # Python < 3.11
    import tomli as tomllib

from . import routing

DEFAULT_SETUP_FILE = Path.home() / ".config" / "p2-readout" / "setup"


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
                if p.name.endswith(".toml"):
                    out.setdefault(p.name[:-5], p)
    return out


def find_setup(name_or_path):
    p = Path(name_or_path)
    if p.suffix == ".toml" or p.is_file():
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
    data = tomllib.loads(raw.decode("utf-8"))
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
    for det in need(d, "detector", "the setup"):
        dn = need(det, "name", "a [[detector]]")
        if dn in detectors:
            raise ValueError(f"{path}: detector {dn!r} listed twice")
        detectors[dn] = {k: v for k, v in det.items() if k != "name"}

    entries = []
    for e in need(d, "cabling", "the setup"):
        vf = as_datetime(need(e, "valid_from", "a [[cabling]] entry"))
        rows = need(e, "hybrids", f"cabling from {vf}")
        seen_vmm, seen_conn = set(), set()
        for r in rows:
            for k in ("detector", "connector", "hybrid", "bottom_vmm", "top_vmm"):
                need(r, k, f"a hybrid row of cabling from {vf}")
            if r["detector"] not in detectors:
                raise ValueError(f"{path}: cabling from {vf} uses unknown detector "
                                 f"{r['detector']!r}")
            if not 1 <= r["connector"] <= des["n_connectors"]:
                raise ValueError(f"{path}: connector {r['connector']} out of range")
            for v in (r["bottom_vmm"], r["top_vmm"]):
                if v in seen_vmm:
                    raise ValueError(f"{path}: VMM {v} cabled twice in entry from {vf}")
                seen_vmm.add(v)
            key = (r["detector"], r["connector"])
            if key in seen_conn:
                raise ValueError(f"{path}: {key} cabled twice in entry from {vf}")
            seen_conn.add(key)
            for k in ("bottom_order", "top_order"):
                if k in r and r[k] not in routing.ORDERS:
                    raise ValueError(f"{path}: unknown {k} {r[k]!r}")
        entries.append(CablingEntry(valid_from=vf, hybrids=tuple(dict(r) for r in rows),
                                    note=e.get("note", "")))
    entries.sort(key=lambda e: e.valid_from)
    if len({e.valid_from for e in entries}) != len(entries):
        raise ValueError(f"{path}: two cabling entries share a valid_from")

    known = {"name", "site", "design", "detector", "cabling", "trigger", "description"}
    return Setup(name=name, site=d.get("site", ""), design=design,
                 detectors=detectors, cabling=tuple(entries),
                 trigger=d.get("trigger"), description=d.get("description", ""),
                 path=path, extra={k: v for k, v in d.items() if k not in known})
