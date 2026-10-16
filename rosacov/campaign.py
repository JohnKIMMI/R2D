"""Read what a ROSA campaign left behind.

Layout (ROSA 0.5.1 and 0.6.x, keys differ: trace_uid/cluster_uid vs trace_id/cluster_id):

  rosa-out/traces/<id>            input bytes         (+ <id>.trace: edges/syscalls)
  rosa-out/decisions/<id>.toml    seconds, cluster, reason ("seed" = Phase 1)
  rosa-out/backdoors/<cat>/<id>   reported inputs, <cat> = <discriminant>_<cluster>
  rosa-out/clusters/cluster_*.txt
  sim-<C>/...                     the same, re-simulated with Phase 1 cutoff C
  evalD-<C>.full                  rosa-evaluate -D (no dedup): id -> result  (needs ground truth)

An input's coverage never changes; only its labels do (per cutoff). So labels are
collected for every cutoff available and stored on the record.
"""
import csv
import re
from pathlib import Path

CUTOFFS = (60, 300, 900, 1800, 3600)
BACKDOOR_RESULTS = {"true_positive", "false_negative"}
_DEC_CLUSTER = re.compile(r'cluster_(?:uid|id)\s*=\s*"([^"]+)"')
_DEC_REASON = re.compile(r'reason\s*=\s*"([^"]+)"')
_DEC_SECONDS = re.compile(r"^seconds\s*=\s*(\d+)", re.M)


def read_decision(path):
    """-> {"seconds", "cluster", "phase"}  (cluster None for Phase 1 / "<none>")"""
    t = Path(path).read_text()
    cl = _DEC_CLUSTER.search(t)
    rs = _DEC_REASON.search(t)
    sec = _DEC_SECONDS.search(t)
    cluster = cl.group(1) if cl else None
    if cluster == "<none>":
        cluster = None
    return {"seconds": int(sec.group(1)) if sec else None,
            "cluster": cluster,
            "phase": "1" if (rs and rs.group(1) == "seed") else "2"}


def read_decisions(out_dir):
    dd = Path(out_dir) / "decisions"
    if not dd.is_dir():
        return {}
    return {f.stem: read_decision(f) for f in dd.glob("*.toml")}


def read_reported(out_dir):
    """-> {id: category}"""
    out = {}
    bd = Path(out_dir) / "backdoors"
    if bd.is_dir():
        for cat in sorted(p for p in bd.iterdir() if p.is_dir()):
            for f in cat.iterdir():
                if f.is_file():
                    out[f.name] = cat.name
    return out


def read_eval(path):
    """evalD-<C>.full -> {id: result}"""
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return {}
    key = "trace_uid" if "trace_uid" in rows[0] else "trace_id"
    return {r[key]: r["result"] for r in rows}


def list_inputs(out_dir):
    """ids of all stored inputs (traces/ minus .trace files)"""
    tr = Path(out_dir) / "traces"
    return sorted(p.name for p in tr.iterdir()
                  if p.is_file() and not p.name.endswith(".trace") and p.name != "README.txt")


def input_path(out_dir, uid, category=None):
    p = Path(out_dir) / "traces" / uid
    if p.is_file():
        return p
    if category:
        q = Path(out_dir) / "backdoors" / category / uid
        if q.is_file():
            return q
    return p


class Campaign:
    """One campaign directory: <dir>/rosa-out plus optional sim-<C>/ and evalD-<C>.full."""

    def __init__(self, campaign_dir, rosa_out="rosa-out"):
        self.dir = Path(campaign_dir)
        self.rosa_out = self.dir / rosa_out
        if not self.rosa_out.is_dir():
            raise FileNotFoundError(f"{self.rosa_out} is not a directory")

    def cutoffs(self):
        return [c for c in CUTOFFS if (self.dir / f"sim-{c}").is_dir()]

    def labels(self):
        """-> {id: record} with every label we can derive, for every cutoff."""
        ids = list_inputs(self.rosa_out)
        live_dec = read_decisions(self.rosa_out)
        live_rep = read_reported(self.rosa_out)
        meta = {}
        for u in ids:
            d = live_dec.get(u, {})
            meta[u] = {"seconds": d.get("seconds"),
                       "live": {"phase": d.get("phase"), "cluster": d.get("cluster"),
                                "category": live_rep.get(u)},
                       "cutoffs": {}}
        for c in self.cutoffs():
            sim = self.dir / f"sim-{c}"
            dec = read_decisions(sim)
            rep = read_reported(sim)
            ev = self.dir / f"evalD-{c}.full"
            res = read_eval(ev) if ev.is_file() else {}
            for u in ids:
                d = dec.get(u)
                if d is None and u not in rep and u not in res:
                    continue
                meta[u]["cutoffs"][str(c)] = {
                    "phase": d["phase"] if d else None,
                    "cluster": d["cluster"] if d else None,
                    "category": rep.get(u),
                    "result": res.get(u)}
                if meta[u]["seconds"] is None and d:
                    meta[u]["seconds"] = d["seconds"]
        return meta

    def stats(self):
        out = {"inputs": len(list_inputs(self.rosa_out)), "cutoffs": {}}
        for c in self.cutoffs():
            sim = self.dir / f"sim-{c}"
            cl = sim / "clusters"
            out["cutoffs"][str(c)] = {
                "clusters": len(list(cl.glob("cluster_*.txt"))) if cl.is_dir() else 0,
                "reported": len(read_reported(sim)),
                "categories": len({v for v in read_reported(sim).values()})}
        return out


def is_backdoor(rec, cutoff):
    """ground-truth class of an input at a cutoff, or None when no evaluation exists"""
    r = rec.get("cutoffs", {}).get(str(cutoff), {}).get("result")
    if r is None:
        return None
    return r in BACKDOOR_RESULTS
