#!/usr/bin/env python3
"""metrics.Analyzer against the legacy metrics/<run>.json, over the legacy blocks file.

Checks, for every input: bytes, blocks, functions_covered, text_ratio, program_ratio,
and (second pass) reachable_ratio / normal_union_ratio / bytes_not_in_normal; plus the
denominators (reachable, normal union) and the backdoor/normal union byte counts.
The legacy "normal" class is ground-truth normal (no backdoor result at any cutoff),
so the label here is built the same way.

Usage: python3 -I tests/test_metrics.py [run]
"""
import gzip, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
LEGACY = HERE.parent.parent
from rosacov.metrics import Analyzer
from legacy import resolve_binary  # noqa: E402

run = sys.argv[1] if len(sys.argv) > 1 else "Full_Hour_artifact__dlink"
met = json.loads((LEGACY / "metrics" / f"{run}.json").read_text())
binary = resolve_binary(met["binary"])
meta = json.loads((LEGACY / "lists" / run / "input_meta.json").read_text())
BD = {"true_positive", "false_negative"}

an = Analyzer(binary, legacy_functions=True)
t = time.time()
recs, bms = {}, {}
for x in gzip.open(LEGACY / "blocks" / f"{run}.jsonl.gz", "rt"):
    x = json.loads(x)
    uid = x["uid"]
    mods = {m: set(map(tuple, b)) for m, b in x["modules"].items()}
    rec, bm = an.input_record(uid, mods, x["processes"])
    res = set(meta.get(uid, {}).get("results", {}).values())
    label = {"reported": bool(meta.get(uid, {}).get("reported")),
             "backdoor": (True if BD & res else (False if res else None))}
    rec["label"] = label
    an.accumulate(rec, bm, label)
    recs[uid] = rec
    bms[uid] = bm
print(f"pass 1: {len(recs)} inputs  {time.time()-t:.1f}s")

d = met["denominators"]
print(f"denominators: reachable {an.unions['all'].count()} (legacy {d['reachable_bytes']}),"
      f" normal {an.normal_union()[1].count()} (legacy {d['normal_union_bytes']}), source {an.normal_union()[0]}")
ok = bad = 0
for uid, rec in recs.items():
    rec.update(an.relative_views(bms[uid]))
    lm = met["inputs"][uid]; lv = lm["views"]
    same = (rec["bytes"] == lm["bytes"] and rec["blocks"] == lm["bbs"]
            and rec["functions_covered"] == lv["functions_covered"]
            and rec["program_ratio"] == lv["program_bytes_ratio"]
            and rec["text_ratio"] == lv["text_bytes_ratio"]
            and rec["reachable_ratio"] == lv["reachable_ratio"]
            and rec["normal_union_ratio"] == lv["normal_union_ratio"]
            and rec["bytes_not_in_normal"] == lv["bytes_not_in_normal"])
    ok += same; bad += (not same)
    if not same and bad <= 3:
        print("  mismatch", uid, {k: (rec.get(k), lv.get(k) if k in lv else lm.get(k)) for k in
              ("bytes", "functions_covered", "reachable_ratio", "normal_union_ratio", "bytes_not_in_normal")}, "ratios", (rec["program_ratio"], lv["program_bytes_ratio"], rec["text_ratio"], lv["text_bytes_ratio"]))
print(f"per-input views identical: {ok}/{ok+bad}")
agg = met["aggregate"]
for cls, mine in (("backdoor", "backdoor"), ("normal", "benign")):
    if agg.get(cls):
        u = an.union_stats(mine)
        print(f"union {cls}: bytes {u['bytes']} (legacy {agg[cls]['union_bytes']}), functions {u['functions']}"
              f" (legacy {agg[cls]['union_functions']})  {'OK' if (u['bytes'], u['functions']) == (agg[cls]['union_bytes'], agg[cls]['union_functions']) else 'MISMATCH'}")
sys.exit(0 if bad == 0 else 1)
