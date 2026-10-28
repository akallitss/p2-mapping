"""Stage B, routing: a VMM channel on a detector connector to a strip and a pad id.

Fixed by the hardware design, not by the setup. For the P2 basket:

    (connector N, half, within-half channel 0..63)
      -> strip      = half_base + f(within)    half_base 1 (bot) or 65 (top)
      -> channel_id = (N - 1) * 128 + (strip - 1)
      -> pad        via the geometry CSV (Stage C)

f() is the within-half channel order. 'reverse' is validated on the SPS beam
for the VMM hybrid (and on M3 tracks for the DREAM/K59V readout); the others
exist for validation scans and for cabling rows that differ.
"""

from importlib import resources

ORDERS = ("linear", "reverse", "pairswap", "reverse-pairswap")
HALVES = ("bot", "top")

# One entry per detector design: its pad table and its connector layout.
DESIGNS = {
    "p2_basket": {
        "pads": "geometry/p2_basket_pads.csv",
        "n_connectors": 10,
        "strips_per_connector": 128,
        "channels_per_half": 64,
        # which half the hybrid's bottom VMM reads (strips 1-64 = 'bot')
        "bottom_vmm_half": "bot",
        "default_order": "reverse",
    },
}


def design(name):
    try:
        return DESIGNS[name]
    except KeyError:
        raise ValueError(f"unknown detector design {name!r}; known: {sorted(DESIGNS)}")


def pads_path(design_name):
    return resources.files("p2_mapping") / "data" / design(design_name)["pads"]


def within_to_strip(within, half, order):
    """within (0..63) + half -> strip (1..128)."""
    if half not in HALVES:
        raise ValueError(f"half must be 'bot' or 'top', got {half!r}")
    base = 65 if half == "top" else 1
    if order == "linear":
        off = within
    elif order == "reverse":
        off = 63 - within
    elif order == "pairswap":
        off = within ^ 1
    elif order == "reverse-pairswap":
        off = (63 - within) ^ 1
    else:
        raise ValueError(f"unknown channel order {order!r}; known: {ORDERS}")
    return base + off


def channel_id(connector, strip, strips_per_connector=128):
    """Pad id of the geometry table: sector = connector - 1."""
    return (connector - 1) * strips_per_connector + (strip - 1)
