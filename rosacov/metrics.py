"""Per-input coverage records, unions and the summary. Shared by batch and live.

Numbers (same definitions as the legacy pipeline, verified against it):
  bytes               bytes inside functions that some block of the target binary covered
                      (painted into a bitmap, so overlapping blocks count once)
  blocks              number of distinct (addr, size) blocks of the target binary
  functions_covered   functions with at least one covered byte
  program_ratio       bytes / sum of function sizes
  text_ratio          covered .text bytes / .text size
  functions           {name: [covered bytes, function size]}  (exact, from the bitmap)
  markers             {name: true/false} for targets.toml `markers` (observation, not a verdict)
Batch-only, second pass (needs the complete unions):
  reachable_ratio     bytes / |union of all inputs|
  normal_union_ratio  |input & normal union| / |normal union|
  bytes_not_in_normal |input & ~normal union|

Unions kept: all, reported, unreported (ROSA's view), phase1, tp, fp, backdoor and
benign (ground-truth view, incl. false negatives), marker_<fn>; optionally cluster_<id>.
"normal" in the relative views means benign when a ground truth exists, else unreported.
"""
import statistics
from pathlib import Path

from . import elf
from .bitmap import Bitmap

def marker_hit(marker, fn_names):
    """a marker matches the function of that exact name, or a demangled C++ function
    whose name before the parameter list equals the marker"""
    if marker in fn_names:
        return True
    return any(n.startswith(marker + "(") for n in fn_names)


BASE_UNIONS = ("all", "reported", "unreported", "phase1", "tp", "fp", "backdoor", "benign")


class Analyzer:
    def __init__(self, binary, markers=(), cluster_unions=False, legacy_functions=False):
        self.binary = str(binary)
        self.name = Path(binary).name
        self.funcs = (elf.load_functions(binary, dedup="name", demangle=False) if legacy_functions
                      else elf.load_functions(binary))
        self.legacy_functions = legacy_functions
        self.text = elf.text_range(binary)
        self.is_func = Bitmap.from_functions(self.funcs)
        self.markers = list(markers)
        self.cluster_unions = cluster_unions
        self.unions = {k: Bitmap(self.funcs.span) for k in BASE_UNIONS}
        for m in self.markers:
            self.unions[f"marker_{m}"] = Bitmap(self.funcs.span)
        self.counts = {k: 0 for k in BASE_UNIONS}
        self.counts.update({f"marker_{m}": 0 for m in self.markers})
        self.counts["processed"] = 0
        self.counts["replay_failed"] = 0
        self.counts["timed_out"] = 0

    # -- per input ----------------------------------------------------------------
    def bitmap_for(self, mods):
        """paint the target binary's blocks -> (Bitmap, blocks set)"""
        blocks = next((b for m, b in mods.items() if Path(m).name == self.name), set())
        return Bitmap(self.funcs.span).paint(blocks), blocks

    def input_record(self, uid, mods, processes=1):
        bm, blocks = self.bitmap_for(mods)
        inside = bm.count_and(self.is_func)
        cand = self.funcs.covered_by_blocks(blocks)          # which functions (fast)
        fn = {}
        for s, e, name in self._funcs_named(cand):
            c = bm.count_in(s, e)
            if c:
                fn[name] = [c, e - s]
        fn = dict(sorted(fn.items(), key=lambda kv: -kv[1][0]))
        ts, tz = self.text
        rec = {
            "id": uid,
            "processes": processes,
            "bytes": inside,
            "blocks": len(blocks),
            "functions_covered": len(fn),
            "program_ratio": round(inside / self.funcs.program_bytes, 6) if self.funcs.program_bytes else None,
            "text_ratio": round(bm.count_in(ts, ts + tz) / tz, 6) if tz else None,
            "functions": fn,
            "markers": {m: marker_hit(m, fn) for m in self.markers},
            "modules": {Path(m).name: len(b) for m, b in mods.items() if Path(m).name != self.name},
        }
        return rec, bm

    def _funcs_named(self, names):
        if not hasattr(self, "_by_name"):
            self._by_name = {n: (s, e) for s, e, n in self.funcs.funcs}
        for n in names:
            s, e = self._by_name[n]
            yield s, e, n

    # -- unions ---------------------------------------------------------------------
    def accumulate(self, rec, bm, label):
        """label: {"reported": bool, "phase": "1"|"2"|None, "verdict": "true_positive"|"false_positive"|None,
                   "backdoor": bool|None, "cluster": str|None}"""
        self.counts["processed"] += 1
        self._add("all", bm)
        if label.get("reported"):
            self._add("reported", bm)
        else:
            self._add("unreported", bm)
        if label.get("phase") == "1":
            self._add("phase1", bm)
        v = label.get("verdict")
        if v == "true_positive":
            self._add("tp", bm)
        elif v == "false_positive":
            self._add("fp", bm)
        if label.get("backdoor") is True:
            self._add("backdoor", bm)
        elif label.get("backdoor") is False:
            self._add("benign", bm)
        for m, hit in rec.get("markers", {}).items():
            if hit:
                self._add(f"marker_{m}", bm)
        cl = label.get("cluster")
        if self.cluster_unions and cl:
            k = f"cluster_{cl}"
            if k not in self.unions:
                self.unions[k] = Bitmap(self.funcs.span)
                self.counts[k] = 0
            self._add(k, bm)

    def _add(self, name, bm):
        self.unions[name].union(bm)
        self.counts[name] = self.counts.get(name, 0) + 1

    # -- second pass (batch) --------------------------------------------------------
    def normal_union(self):
        """'normal' for the relative views: ground-truth benign inputs when a ground truth
        exists, otherwise the inputs ROSA did not report."""
        if self.counts.get("benign", 0) or self.counts.get("backdoor", 0):
            return "benign", self.unions["benign"]
        return "unreported", self.unions["unreported"]

    def relative_views(self, bm):
        u_all = self.unions["all"]
        _, u_nm = self.normal_union()
        reach, nm = u_all.count(), u_nm.count()
        # whole-span numerators over whole-span denominators (legacy definition)
        return {
            "reachable_ratio": round(bm.count() / reach, 6) if reach else None,
            "normal_union_ratio": round(bm.count_and(u_nm) / nm, 6) if nm else None,
            "bytes_not_in_normal": bm.count_and_not(u_nm) if nm else None,
        }

    # -- summary --------------------------------------------------------------------
    def union_stats(self, name, with_functions=True):
        u = self.unions[name]
        inside = u.count_and(self.is_func)
        o = {"inputs": self.counts.get(name, 0),
             "bytes": inside,
             "program_ratio": round(inside / self.funcs.program_bytes, 6) if self.funcs.program_bytes else None}
        if with_functions:
            hit = u.functions_hit(self.funcs)
            o["functions"] = len(hit)
            o["function_detail"] = {n: [c, self.funcs.size[n]]
                                    for n, c in sorted(hit.items(), key=lambda kv: -kv[1] / self.funcs.size[kv[0]])}
        return o

    def summary(self, records=None, with_functions=True):
        ts, tz = self.text
        doc = {
            "binary": self.binary,
            "denominators": {
                "program_bytes": self.funcs.program_bytes,
                "program_functions": len(self.funcs),
                "text_bytes": tz,
                "reachable_bytes": self.unions["all"].count(),
                "normal_union_bytes": self.normal_union()[1].count(),
                "normal_union_source": self.normal_union()[0],
            },
            "counts": dict(self.counts),
            "unions": {k: self.union_stats(k, with_functions) for k in self.unions},
            "note": "drcov records block execution, not execution counts.",
            "function_table": "legacy (one per symbol name, mangled)" if self.legacy_functions else "one per start address, demangled",
        }
        if records:
            doc["distributions"] = distributions(records)
        return doc

    def save_unions(self, store):
        for k, u in self.unions.items():
            u.save(store.union_path(k))

    def load_unions(self, store):
        for k in store.union_names():
            self.unions[k] = Bitmap.load(store.union_path(k), self.funcs.span)
            self.counts.setdefault(k, 0)


def distributions(records, keys=("bytes", "blocks", "functions_covered", "program_ratio",
                                 "reachable_ratio", "normal_union_ratio", "bytes_not_in_normal")):
    """min / median / max per numeric key, over all records and per class"""
    def one(xs):
        o = {}
        for k in keys:
            s = [r[k] for r in xs if r.get(k) is not None]
            if s:
                o[k] = {"min": min(s), "median": statistics.median(s), "max": max(s)}
        return o
    classes = {"all": records,
               "reported": [r for r in records if r.get("label", {}).get("reported")],
               "unreported": [r for r in records if not r.get("label", {}).get("reported")],
               "backdoor": [r for r in records if r.get("label", {}).get("backdoor") is True],
               "benign": [r for r in records if r.get("label", {}).get("backdoor") is False],
               "tp": [r for r in records if r.get("label", {}).get("verdict") == "true_positive"],
               "fp": [r for r in records if r.get("label", {}).get("verdict") == "false_positive"]}
    return {c: {"n": len(xs), **one(xs)} for c, xs in classes.items() if xs}
