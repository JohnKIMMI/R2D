#!/usr/bin/env python3
"""Acceptance: recompute every legacy campaign from its drcov logs with the new
pipeline and compare with the legacy metrics/<run>.json, input by input.

Legacy run name -> (target, campaign dir); binary resolved on the host.
Writes runs to TEST/analysis/coverage/runs-legacy/<run>/ (never touches legacy data).

Usage: python3 -I tests/legacy_recompute.py [run ...]
"""
import json, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
from legacy import LEGACY, ROSA, resolve_binary

TEST = ROSA / "TEST"
def mapping(run):
    exp, _, tgt = run.partition("__")
    target = tgt + ("_artifact" if exp == "Full_Hour_artifact" else "")
    camp = TEST / exp / "results" / tgt
    return target, camp

runs = sys.argv[1:] or sorted(p.stem for p in (LEGACY / "metrics").glob("*__*.json"))
summary = []
for run in runs:
    target, camp = mapping(run)
    met = json.loads((LEGACY / "metrics" / f"{run}.json").read_text())
    binary = resolve_binary(met["binary"])
    out = LEGACY / "runs-legacy" / run
    t0 = time.time()
    cmd = [sys.executable, "-I", str(HERE.parent / "bin" / "rosacov"), "metrics", target, "--run", str(out),
           "--logs", str(LEGACY / "out" / run), "--campaign", str(camp), "--binary", str(binary), "--cutoff", "60",
           "--no-functions", "--legacy-functions"]
    if (out / "summary.json").is_file():
        cmd.append("--force")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"{run}: FAILED\n{r.stderr[-800:]}"); summary.append((run, "FAILED")); continue
    recs = {json.loads(l)["id"]: json.loads(l) for l in (out / "inputs.jsonl").open()}
    new = json.loads((out / "summary.json").read_text())
    ok = bad = missing = 0
    for uid, lm in met["inputs"].items():
        r_ = recs.get(uid)
        if not r_ or "bytes" not in r_:
            missing += 1; continue
        lv = lm["views"]
        same = (r_["bytes"] == lm["bytes"] and r_["blocks"] == lm["bbs"]
                and r_["functions_covered"] == lv["functions_covered"]
                and r_["program_ratio"] == lv["program_bytes_ratio"] and r_["text_ratio"] == lv["text_bytes_ratio"]
                and r_["reachable_ratio"] == lv["reachable_ratio"]
                and r_["normal_union_ratio"] == lv["normal_union_ratio"]
                and r_["bytes_not_in_normal"] == lv["bytes_not_in_normal"])
        ok += same; bad += (not same)
    d, od = new["denominators"], met["denominators"]
    den_ok = all(d[k] == od[k] for k in ("program_bytes", "program_functions", "text_bytes", "reachable_bytes", "normal_union_bytes"))
    line = f"{run:32s} inputs {ok}/{ok+bad} identical, {missing} missing, denominators {'OK' if den_ok else 'MISMATCH'}  ({time.time()-t0:.0f}s)"
    print(line, flush=True); summary.append((run, line))
print("\n=== summary ===")
for run, line in summary:
    print(line if line != "FAILED" else f"{run}: FAILED")
