"""Adding a dated entry to a setup file, keeping everything else as written.

    text = add_entry(path, entry)        # checked new file content, not yet written

A setup file is hand-written and its comments are provenance (who cabled what,
when, from which logbook entry), so it is never re-dumped: the new entry is
inserted as text at the end of the `cabling:` list, and the result is loaded
again with the full checks of load_setup. It must read back as the old entries
unchanged plus exactly the new one, or nothing is returned.
"""

import datetime as dt
import json
import os
import re
import tempfile

import yaml

from .setups import _parse, as_datetime

_TOP_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")


def _flow(obj):
    return yaml.safe_dump(obj, default_flow_style=True, sort_keys=False,
                          allow_unicode=True, width=10_000).strip()


def render_entry(entry):
    """YAML text of one cabling entry, as an item of the `cabling:` list."""
    vf = as_datetime(entry["valid_from"]).replace(microsecond=0)
    lines = [f"  - valid_from: {vf.isoformat()}"]
    for key in ("note", "author"):
        if entry.get(key):
            # a JSON string is a valid double-quoted YAML scalar; safe_dump of a
            # lone string would add a document-end marker ("...")
            lines.append(f"    {key}: {json.dumps(str(entry[key]), ensure_ascii=False)}")
    if "detectors" in entry:
        lines.append("    detectors:")
        lines += [f"      - {_flow(d)}" for d in entry["detectors"]]
    if "trigger" in entry:
        lines.append(f"    trigger: {_flow(entry['trigger']) if entry['trigger'] else 'null'}")
    lines.append("    hybrids:")
    lines += [f"      - {_flow(r)}" for r in entry["hybrids"]]
    return "\n".join(lines) + "\n"


def _insert(text, block):
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if re.match(r"^cabling\s*:", l)), None)
    if start is None:
        raise ValueError("the setup file has no top-level 'cabling:' list")
    nxt = next((i for i in range(start + 1, len(lines)) if _TOP_KEY.match(lines[i])),
               len(lines))
    # comments and blank lines just above the next key belong to that key
    j = nxt
    while j > start + 1 and (not lines[j - 1].strip() or lines[j - 1].lstrip().startswith("#")):
        j -= 1
    head = lines[:j]
    if head and not head[-1].endswith("\n"):
        head[-1] += "\n"
    tail = lines[j:]
    return "".join(head) + "\n" + block + ("\n" if tail else "") + "".join(tail)


def add_entry(path, entry):
    """New content of the setup file at `path` with `entry` added (checked).

    entry: {"valid_from", "note", "hybrids": [...], optional "detectors": [...],
    "trigger": {...} or None, "author"}. The entry must be later than every
    existing one. Raises ValueError with the reason if anything is wrong.
    """
    with open(path, encoding="utf-8") as f:
        old_text = f.read()
    old = _parse(yaml.safe_load(old_text), path)
    vf = as_datetime(entry["valid_from"]).replace(microsecond=0)
    if old.cabling and vf <= old.cabling[-1].valid_from:
        raise ValueError(f"a new entry must start after the last one "
                         f"({old.cabling[-1].valid_from:%Y-%m-%d %H:%M})")
    if not str(entry.get("note", "")).strip():
        raise ValueError("a new entry needs a note saying what changed and why")
    new_text = _insert(old_text, render_entry({**entry, "valid_from": vf}))
    try:
        data = yaml.safe_load(new_text)
    except yaml.YAMLError as e:
        raise ValueError(f"the new entry does not read as YAML: {e}") from e
    new = _parse(data, path)                      # all the checks of load_setup
    if len(new.cabling) != len(old.cabling) + 1 or new.cabling[:-1] != old.cabling:
        raise ValueError("internal: the file would not read back as the old entries "
                         "plus the new one; nothing written")
    got = new.cabling[-1]
    want_rows = [{**r, "fec": data["fec"]} if "fec" not in r and "fec" in data else r
                 for r in entry["hybrids"]]
    if got.valid_from != vf or list(got.hybrids) != want_rows:
        raise ValueError("internal: the new entry does not read back as given; nothing written")
    return new_text


def write_atomic(path, text):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    st = os.stat(path)
    os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, path)


def now():
    return dt.datetime.now().replace(microsecond=0)
