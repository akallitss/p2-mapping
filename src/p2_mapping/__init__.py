"""p2-mapping: VMM electronics channel to P2 detector pad, per setup and date.

    from p2_mapping import load_mapping
    m = load_mapping("lab_saclay_2026", when="2026-10-27T10:00")
    pads = m.lookup(hits)                      # hits: anything with vmm, ch

Three stages, each changing for its own reasons:
    A  cabling   hybrid -> detector connector; per setup, dated   (setups/*.toml)
    B  routing   connector half + channel -> strip -> channel_id  (routing.py)
    C  geometry  channel_id -> pad position and shape            (geometry/*.csv)
"""

__version__ = "0.1.0"

from .routing import DESIGNS, ORDERS, channel_id, within_to_strip   # noqa: E402
from .setups import (CablingEntry, Setup, available_setups,         # noqa: E402
                     load_setup, resolve_setup)
from .table import Mapping, build_table, load_mapping, load_pads    # noqa: E402

__all__ = [
    "Mapping", "load_mapping", "build_table", "load_pads", "Setup", "CablingEntry",
    "load_setup", "resolve_setup", "available_setups", "DESIGNS", "ORDERS",
    "within_to_strip", "channel_id", "__version__",
]
