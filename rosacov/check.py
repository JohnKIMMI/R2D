"""Compare two runs of the same target: live vs batch, or old vs new.

Per input present in both: bytes / blocks / functions_covered. A difference with a
different block count is replay nondeterminism (the program took a slightly
different path); the same block count with different numbers is a pipeline bug.
Verdicts are compared where both have one. Unions are compared by byte count
and by the functions only one side covers.
"""
import json
from pathlib import Path

from .bitmap import Bitmap
from .store import RunStore


def compare(a, b):
    A, B = RunStore(a), RunStore(b)
    ra, rb = {r["id"]: r for r in A.read_inputs()}, {r["id"]: r for r in B.read_inputs()}
    both = sorted(set(ra) & set(rb))
    print(f"A {A.path} ({len(ra)} inputs)  B {B.path} ({len(rb)} inputs)  common {len(both)}")
    same = nd = bug = 0; drift = []
    v_ok = v_bad = 0
    for u in both:
        x, y = ra[u], rb[u]
        if "bytes" not in x or "bytes" not in y:
            continue
        if (x["bytes"], x["blocks"], x["functions_covered"]) == (y["bytes"], y["blocks"], y["functions_covered"]):
            same += 1
        elif x["blocks"] != y["blocks"]:
            nd += 1; drift.append(abs(x["bytes"] - y["bytes"]) / max(y["bytes"], 1))
        else:
            bug += 1
            if bug <= 5:
                print(f"  BUG? {u}: A {x['bytes']}/{x['blocks']}/{x['functions_covered']}  B {y['bytes']}/{y['blocks']}/{y['functions_covered']}")
        va, vb = x.get("label", {}).get("verdict"), y.get("label", {}).get("verdict")
        if va and vb:
            v_ok += (va == vb); v_bad += (va != vb)
    n = same + nd + bug
    print(f"per-input identical {same}/{n};  different path (nondeterminism) {nd}"
          + (f" (bytes diff median {sorted(drift)[len(drift)//2]*100:.2f}%, max {max(drift)*100:.2f}%)" if drift else "")
          + f";  same blocks but different numbers {bug}")
    if v_ok + v_bad:
        print(f"verdicts agree {v_ok}/{v_ok+v_bad}")
    sa, sb = A.read_summary(), B.read_summary()
    for k in sorted(set(sa.get("unions", {})) & set(sb.get("unions", {}))):
        ua, ub = sa["unions"][k], sb["unions"][k]
        if not (ua["inputs"] or ub["inputs"]):
            continue
        fa, fb = set(ua.get("function_detail", {})), set(ub.get("function_detail", {}))
        print(f"union {k:12s} A {ua['bytes']:8d}B/{ua.get('functions','?')} fn (n={ua['inputs']})"
              f"  B {ub['bytes']:8d}B/{ub.get('functions','?')} fn (n={ub['inputs']})"
              f"  only-A fn {len(fa-fb)}  only-B fn {len(fb-fa)}")
    # restricted comparison: unions over the common inputs only, from blocks
    ids = set(both)
    bin_a = sa.get("binary");
    if bin_a and (A.path / "blocks.jsonl.gz").is_file() and (B.path / "blocks.jsonl.gz").is_file():
        name = Path(bin_a).name
        span = len(Bitmap.load(A.union_path("all")).buf)
        def union_over(store):
            u = Bitmap(span)
            for x in store.iter_blocks():
                if x["id"] in ids:
                    u.paint(next((b for m, b in x["modules"].items() if Path(m).name == name), []))
            return u
        ua, ub = union_over(A), union_over(B)
        print(f"union over the {len(ids)} common inputs: A {ua.count()}B  B {ub.count()}B  "
              f"only-A {ua.count_and_not(ub)}B  only-B {ub.count_and_not(ua)}B")
