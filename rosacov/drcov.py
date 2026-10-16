"""drcov text logs -> block lists.

A drcov log has two tables: the module table (every binary/library mapped in the
process, big binaries split per segment) and the BB table (one line per basic
block that executed: module id, offset in the module, size). We normalise each
block to (vaddr, size) with vaddr = segment preferred_base + offset, which undoes
PIE/ASLR so numbers are comparable across runs and match `readelf` addresses.

One input = one directory with one log per process (drrun -follow_children). The
input's coverage is the union over those logs. Every module is kept, libraries
included; which modules matter is decided later, in metrics.
"""
import re
from pathlib import Path

MOD_RE = re.compile(r"^\s*(\d+),\s*(\d+),\s*0x([0-9a-f]+),\s*0x([0-9a-f]+),"
                    r"\s*0x([0-9a-f]+),\s*([0-9a-f]+),\s*0x([0-9a-f]+),\s*(.+?)\s*$")
# older module-table versions (no containing_id / offset / preferred_base columns)
MOD_RE_V2 = re.compile(r"^\s*(\d+),\s*0x([0-9a-f]+),\s*0x([0-9a-f]+),\s*0x([0-9a-f]+),\s*(.+?)\s*$")
BB_RE = re.compile(r"^\s*module\[\s*(\d+)\]:\s*0x([0-9a-f]+),\s*(\d+)")


class LogStats:
    __slots__ = ("bb_lines", "bb_declared", "modules")

    def __init__(self):
        self.bb_lines = 0        # BB lines we parsed
        self.bb_declared = 0     # "BB Table: N bbs"
        self.modules = 0


def parse_log(path, stats=None):
    """-> {module path: {(vaddr, size), ...}}  (sets; duplicates across segments collapse)"""
    seg, out = {}, {}
    in_bb = False
    st = stats or LogStats()
    for ln in Path(path).read_text(errors="replace").splitlines():
        if ln.startswith("DRCOV VERSION"):
            # one file can hold several dumps (drcov dumps again after fork/exec in
            # the same pid): each dump brings its own module table
            in_bb = False
            seg = {}
            continue
        if not in_bb:
            if ln.startswith("BB Table:"):
                in_bb = True
                m = re.search(r"(\d+)\s+bbs", ln)
                st.bb_declared += int(m.group(1)) if m else 0
                continue
            m = MOD_RE.match(ln)
            if m:
                seg[int(m.group(1))] = (m.group(8), int(m.group(7), 16))
                st.modules += 1
                continue
            m = MOD_RE_V2.match(ln)
            if m and ln.lstrip()[0].isdigit():
                seg[int(m.group(1))] = (m.group(5), 0)   # no preferred_base: offsets as given
                st.modules += 1
            continue
        m = BB_RE.match(ln)
        if m:
            st.bb_lines += 1
            sid = int(m.group(1))
            if sid in seg:
                mod, base = seg[sid]
                out.setdefault(mod, set()).add((base + int(m.group(2), 16), int(m.group(3))))
    return out


def parse_input_dir(logdir):
    """All logs of one input -> (modules dict, LogStats, number of logs).

    Raises FileNotFoundError when there is no log at all (replay produced nothing).
    """
    logs = sorted(Path(logdir).glob("*.log"))
    if not logs:
        raise FileNotFoundError(f"no drcov log in {logdir}")
    st = LogStats()
    mods = {}
    for lg in logs:
        for mod, bbs in parse_log(lg, st).items():
            mods.setdefault(mod, set()).update(bbs)
    return mods, st, len(logs)


def pick_module(mods, binary_name):
    """Blocks of the target binary (matched by file name) -> set of (vaddr, size)."""
    for m, b in mods.items():
        if Path(m).name == binary_name:
            return b
    return set()
