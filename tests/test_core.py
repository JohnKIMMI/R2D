#!/usr/bin/env python3
"""Core modules against the legacy pipeline's data (no containers needed).

  drcov.parse_input_dir  vs  legacy blocks/<run>.jsonl.gz   (same block sets)
  elf.load_functions     vs  legacy metrics/<run>.json      (same denominators)
  bitmap byte counts     vs  legacy per-input `bytes`        (same numbers)

Usage: python3 -I tests/test_core.py [run] [n_inputs]
  run defaults to Full_Hour_artifact__dlink (binary bins/thttpd), n to 300.
"""
import gzip, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
from legacy import resolve_binary  # noqa: E402
LEGACY = HERE.parent.parent          # TEST/analysis/coverage

from rosacov import drcov, elf
from rosacov.bitmap import Bitmap

run = sys.argv[1] if len(sys.argv) > 1 else "Full_Hour_artifact__dlink"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 300
met = json.loads((LEGACY / "metrics" / f"{run}.json").read_text())
binary = resolve_binary(met["binary"])
assert binary.is_file(), f"binary not found: {binary}"
print(f"run {run}  binary {binary.name}")

# --- elf -----------------------------------------------------------------
t = time.time()
funcs = elf.load_functions(binary, dedup="name", demangle=False)   # legacy rule, for parity
tstart, tsize = elf.text_range(binary)
d = met["denominators"]
ok_elf = (funcs.program_bytes == d["program_bytes"] and len(funcs) == d["program_functions"]
          and tsize == d["text_bytes"])
print(f"elf: functions {len(funcs)} (legacy {d['program_functions']}), program_bytes {funcs.program_bytes}"
      f" (legacy {d['program_bytes']}), text {tsize} (legacy {d['text_bytes']})  {'OK' if ok_elf else 'MISMATCH'}  {time.time()-t:.2f}s")

# --- drcov vs legacy blocks ---------------------------------------------
legacy_blocks = {}
with gzip.open(LEGACY / "blocks" / f"{run}.jsonl.gz", "rt") as fh:
    for ln in fh:
        x = json.loads(ln)
        legacy_blocks[x["uid"]] = {m: set(map(tuple, b)) for m, b in x["modules"].items()}
        if len(legacy_blocks) >= N:
            break

is_func = Bitmap.from_functions(funcs)
n_ok = n_bad = n_bytes_ok = n_bytes_bad = 0
t = time.time()
for uid, lb in legacy_blocks.items():
    logdir = LEGACY / "out" / run / uid
    mods, st, nlogs = drcov.parse_input_dir(logdir)
    same = all(mods.get(m, set()) == b for m, b in lb.items()) and set(mods) == set(lb)
    n_ok += same; n_bad += (not same)
    if not same and n_bad <= 3:
        print("  block mismatch", uid, {m: (len(mods.get(m, ())), len(b)) for m, b in lb.items() if mods.get(m, set()) != b})
    # integrity: parsed BB lines == declared
    if st.bb_lines != st.bb_declared:
        print("  bb count mismatch", uid, st.bb_lines, st.bb_declared)
    # bytes vs legacy metrics
    tgt = drcov.pick_module(mods, binary.name)
    bm = Bitmap(funcs.span).paint(tgt)
    inside = bm.count_and(is_func)
    lm = met["inputs"][uid]
    if inside == lm["bytes"] and len(tgt) == lm["bbs"] and len(funcs.covered_by_blocks(tgt)) == lm["views"]["functions_covered"]:
        n_bytes_ok += 1
    else:
        n_bytes_bad += 1
        if n_bytes_bad <= 3:
            print("  bytes mismatch", uid, inside, lm["bytes"], len(tgt), lm["bbs"])
dt = time.time() - t
print(f"drcov: block sets identical {n_ok}/{n_ok+n_bad};  bytes/bbs/functions identical {n_bytes_ok}/{n_bytes_ok+n_bytes_bad}"
      f"  ({dt/ max(1, n_ok+n_bad)*1000:.0f} ms/input)")

# --- bitmap sanity --------------------------------------------------------
b = Bitmap(100).paint([(3, 4), (5, 4)])
assert b.count() == 6, b.count()                       # overlapping blocks counted once
b2 = Bitmap(100).paint([(50, 10)])
assert b.count_and(b2) == 0 and Bitmap(100).paint([(3, 4)]).count_and(b) == 4
u = Bitmap(100).union(b).union(b2); assert u.count() == 16
p = HERE / "_tmp.bin"; u.save(p); assert Bitmap.load(p, 100).count() == 16; p.unlink()
print("bitmap: OK")
sys.exit(0 if (ok_elf and n_bad == 0 and n_bytes_bad == 0) else 1)
