# p2-mapping

VMM electronics channel `(vmm, ch)` to P2 detector pad, for a given setup and
date. Part of the P2 readout software split (decode / mapping / monitor); it
does not depend on `p2-decode` and works on any table with `vmm` and `ch`.

## Use

```python
from p2_mapping import load_mapping

m = load_mapping("lab_saclay_2026", when="2026-10-27T10:00")   # run start time
pads = m.lookup(hits)          # aligned with hits: detector, connector_N, strip,
                               # channel_id, pad_cx, pad_cy, z_mm, cable, mapped ...
m.table                        # one row per instrumented (vmm, ch)
m.write_resolved(run_dir / "setup_resolved.json")
```

```bash
p2-mapping setups                                   # list known setups
p2-mapping show  --setup sps_h4_2026 --when 2026-08-01T12:00
p2-mapping table --setup lab_saclay_2026 --when 2026-10-27 table.csv
```

## Three stages

| Stage | What | Where | Changes when |
| --- | --- | --- | --- |
| A cabling | hybrid → detector connector, cable, per-half channel order | `data/setups/<setup>.yaml`, dated entries | someone re-cables |
| B routing | connector half + channel → strip → `channel_id` | `routing.py` (`DESIGNS`) | a new adapter or PCB design |
| C geometry | `channel_id` → pad centre, size, radius, phi | `data/geometry/p2_basket_pads.csv` | never, for one detector design |

Detector placement (`z_mm`) is per setup, in the setup file.

## Several FECs

A VMM id is 5 bits, so with more than one FEC the same id repeats; the full
experiment (180 hybrids, 360 VMMs) needs at least 12 FECs. A hybrid row can
name its FEC (`fec: 3`), or the setup can give one default (`fec:` at top
level). A row without a FEC matches any FEC, which is how the single-FEC SPS
and lab setups are written. Lookup is by `(fec, vmm, ch)` when the hits carry
`fec`; a channel cabled twice on the same FEC, or a no-FEC row sharing a VMM id
with any other row, is refused.

## Choosing the setup

Highest priority first: an explicit name or path (`--setup`), the environment
variable `P2_SETUP`, a one-line default in `$P2_SETUP_FILE` or
`~/.config/p2-readout/setup`. With none of these the package refuses instead of
guessing a site. Site-local setup files can live outside the package: put their
directory in `P2_SETUPS_DIR`.

A cabling entry is picked by time, so a re-cabling is a new entry under
`cabling:` with its `valid_from`, not a new setup. A time before the first entry is an
error: there is no cabling to assume.

## Shipped setups

| Setup | Entries |
| --- | --- |
| `sps_h4_2026` | from 2026-07-29: c4-c6, HITACHI, P2_IN c5/c6 top halves `linear`; from 2026-08-01 11:14: P2_MID c4 + c6 SAMPTEC |
| `lab_saclay_2026` | from 2026-10-22: c4-c6, SAMPTEC, all `reverse`; from 2026-10-26: c3-c5 |

The P2_IN `linear` halves are recorded per cabling entry, not as a detector
property: they were found at SPS with HITACHI cables, and the lab cosmic study
(obs 63) does not favour them with Samtec cables.

## Tests

```bash
pytest
P2_MAPPING_VMM_QA=../DAQ_Control_VMM_Beam/vmm_qa \
P2_MAPPING_COSMIC=../P2_basket_analysis/cosmic_vmm_analysis pytest tests/test_legacy.py
```

`test_legacy.py` checks, row by row, that the tables equal what the existing
code builds: `vmm_stations.build_pad_table()` (SPS, with its overrides, and all
strategy / bottom-half scan variants) and `orientation_check.Mapper` for the
lab before and after the c3-c5 re-cabling (`conn_shift` 0 and -1).

Setup files are YAML (PyYAML, `yaml.safe_load`), like the group's other
configuration files.

## Versions

- **0.4.0** optional `hv` section in a setup (`Setup.hv`): which supply channel
  (card, channel) feeds which electrode, HV-only references, the operating point
  and ceilings; `set_here: false` where another machine holds the crate. Checked
  on load: one electrode per channel, names known, operating voltages mapped and
  under their ceilings. sps_h4_2026 carries the beam test's channels and
  operating point; lab_saclay_2026 says the crate is held by banco.
- **0.3.0** cabling rows can name their FEC; tables carry `fec` (-1 = any) and
  lookup is by `(fec, vmm, ch)`, for setups with many FECs. Single-FEC setups
  unchanged.
- **0.2.0** setup files in YAML instead of TOML; same content, same tables.
- **0.1.0** first release, TOML setup files.
