"""`p2-mapping` command: list setups, show the cabling, export the table."""

import argparse

from .setups import available_setups, load_setup, resolve_setup
from .table import Mapping


def main(argv=None):
    ap = argparse.ArgumentParser(prog="p2-mapping",
                                 description="P2 VMM channel-to-pad mapping.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setups", help="list the setups on the search path")
    for name, text in (("show", "print the cabling in force"),
                       ("table", "write the channel table as CSV")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--setup", help="name or .yaml path (else $P2_SETUP, "
                                       "else the machine default)")
        p.add_argument("--when", default="now",
                       help="ISO date/time, e.g. a run start (default: now)")
        if name == "table":
            p.add_argument("out", help="output CSV path")
    sub.add_parser("which", help="print the setup that would be used")
    a = ap.parse_args(argv)

    if a.cmd == "setups":
        for n, p in available_setups().items():
            print(f"{n:24s} {p}")
        return 0
    if a.cmd == "which":
        print(resolve_setup())
        return 0

    m = Mapping(load_setup(a.setup), a.when)
    if a.cmd == "table":
        m.table.to_csv(a.out, index=False)
        print(f"{len(m.table)} channels, {int(m.table['mapped'].sum())} mapped -> {a.out}")
        return 0

    s, e = m.setup, m.cabling
    print(f"setup    {s.name} ({s.site}): {s.description}")
    print(f"file     {s.path}")
    print(f"cabling  from {e.valid_from:%Y-%m-%d %H:%M}: {e.note}")
    print(f"trigger  {s.trigger or 'none'}")
    for r in e.hybrids:
        z = s.detectors[r['detector']].get('z_mm')
        print(f"  H{r['hybrid']:<2} {r['detector']:7s} c{r['connector']}  "
              f"VMM {r['bottom_vmm']:>2} bot / {r['top_vmm']:>2} top  "
              f"{r.get('cable', ''):11s} z={z} mm  orders "
              f"{r.get('bottom_order', '-')}/{r.get('top_order', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
