"""Batch replay and metrics for a finished campaign.

replay_ids(target, campaign, run, ids, shard)   drcov for each id -> <run>/logs/<id>/  (parallel by shards)
build(run, campaign, cutoff, ...)              logs -> blocks.jsonl.gz, inputs.jsonl, unions, summary

Batch keeps the drcov logs (the user wants them for the finished-campaign path).
The metrics step is two passes over the block list: pass 1 paints and accumulates
unions, pass 2 computes the views that need the finished unions.
"""
import json
import time
from pathlib import Path

from . import campaign as camp, drcov, replay
from .metrics import Analyzer
from .store import RunStore


def replay_ids(target, rosa_out, run_dir, ids, shard=(0, 1), gt=False, progress=200):
    """Replay the ids whose index % n == i. Skips ids that already have a log."""
    store = RunStore(run_dir, create=True, resume=True)
    i, n = shard
    mine = [u for k, u in enumerate(ids) if k % n == i]
    done = fail = skipped = 0
    t0 = time.time()
    rep = camp.read_reported(rosa_out)
    results = {}
    for k, uid in enumerate(mine, 1):
        logdir = store.log_dir(uid)
        if any(logdir.glob("*.log")):
            skipped += 1
            continue
        inp = camp.input_path(rosa_out, uid, rep.get(uid))
        res = replay.replay_cov(target, inp, logdir)
        results[uid] = {"ok": res.ok, "timed_out": res.timed_out, "replay_seconds": res.seconds}
        if gt and target.gt_binary():
            results[uid]["gt"] = replay.replay_gt(target, inp)
        done += res.ok; fail += (not res.ok)
        if progress and k % progress == 0:
            print(f"  shard {i}/{n}: {k}/{len(mine)} ({time.time()-t0:.0f}s)", flush=True)
    with open(store.path / f"replay-{i}of{n}.json", "w") as fh:
        json.dump(results, fh)
    print(f"shard {i}/{n}: replayed {done}, failed {fail}, skipped {skipped}  ({time.time()-t0:.0f}s)")
    store.fix_owner()
    return done, fail, skipped


def _replay_meta(store):
    meta = {}
    for p in store.path.glob("replay-*of*.json"):
        meta.update(json.loads(p.read_text()))
    return meta


def build(run_dir, target, campaign_dir=None, cutoff=None, logs_dir=None, binary=None,
          cluster_unions=False, with_functions=True, ids=None, legacy_functions=False):
    """Metrics for a run. logs_dir defaults to <run>/logs. cutoff picks which simulated
    labels drive the unions (None = the campaign's own decisions)."""
    store = RunStore(run_dir, create=True, resume=True)
    logs_dir = Path(logs_dir or store.log_dir("")).resolve()
    an = Analyzer(binary or target.binary, markers=target.markers if target else (), cluster_unions=cluster_unions,
                  legacy_functions=legacy_functions)
    labels = {}
    if campaign_dir:
        c = camp.Campaign(campaign_dir)
        labels = c.labels()
    rmeta = _replay_meta(store)
    dirs = sorted(p for p in logs_dir.iterdir() if p.is_dir())
    if ids is not None:
        want = set(ids)
        dirs = [d for d in dirs if d.name in want]
    # reset outputs (store refuses to overwrite a finished run unless --force at the CLI level)
    for p in ("inputs.jsonl", "blocks.jsonl.gz"):
        if (store.path / p).exists():
            (store.path / p).unlink()
    # Pass 1: parse, paint, accumulate the unions, write the block list. No per-input
    # bitmap is kept (thousands of multi-MB bitmaps would exhaust memory); pass 2
    # repaints each input from the block list just written.
    records = []
    t0 = time.time()
    n_fail = 0
    with store.open_blocks_writer() as bfh:
        for k, d in enumerate(dirs, 1):
            uid = d.name
            try:
                mods, st, nproc = drcov.parse_input_dir(d)
            except FileNotFoundError:
                n_fail += 1
                records.append({"id": uid, "error": "no drcov log", "label": _label(labels.get(uid), cutoff)})
                continue
            rec, bm = an.input_record(uid, mods, nproc)
            lab = _label(labels.get(uid), cutoff)
            rec["label"] = lab
            rec["seconds"] = (labels.get(uid) or {}).get("seconds")
            rec["category"] = lab.get("category")
            rec["parse_ok"] = (st.bb_lines == st.bb_declared)
            rec.update({k: v for k, v in rmeta.get(uid, {}).items() if k != "gt"})
            if "gt" in rmeta.get(uid, {}) and lab.get("verdict") is None and lab.get("reported"):
                lab["verdict"] = "true_positive" if rmeta[uid]["gt"] else "false_positive"
            an.accumulate(rec, bm, lab)
            RunStore.write_blocks(bfh, uid, nproc, mods)
            records.append(rec)
            del bm
            if k % 1000 == 0:
                print(f"  pass 1: {k}/{len(dirs)} ({time.time()-t0:.0f}s)", flush=True)
    # Pass 2: views relative to the finished unions
    by_id = {r["id"]: r for r in records}
    for k, x in enumerate(store.iter_blocks(), 1):
        rec = by_id.get(x["id"])
        if rec is None or "bytes" not in rec:
            continue
        bm, _ = an.bitmap_for({m: b for m, b in x["modules"].items()})
        rec.update(an.relative_views(bm))
        del bm
        if k % 1000 == 0:
            print(f"  pass 2: {k}/{len(records)} ({time.time()-t0:.0f}s)", flush=True)
    for rec in records:
        store.append_input(rec)
    an.save_unions(store)
    doc = an.summary(records, with_functions=with_functions)
    doc["cutoff"] = cutoff
    store.write_summary(doc)
    store.write_run(target=target.name if target else None, binary=str(an.binary), mode="batch",
                    rosa_version=target.rosa_version if target else None, cutoff=cutoff,
                    campaign=str(campaign_dir) if campaign_dir else None, logs=str(logs_dir),
                    counts=dict(an.counts), inputs=len(records), no_log=n_fail)
    print(f"{store.path.name}: {len(records)} inputs ({n_fail} without log)  {time.time()-t0:.0f}s")
    d = doc["denominators"]
    print(f"  program {d['program_bytes']}B/{d['program_functions']} fn  reachable {d['reachable_bytes']}B"
          f"  normal({d['normal_union_source']}) {d['normal_union_bytes']}B")
    for k in ("all", "reported", "unreported", "tp", "fp", "backdoor", "benign"):
        u = doc["unions"].get(k)
        if u and u["inputs"]:
            print(f"  union {k:10s} n={u['inputs']:6d}  {u['bytes']}B  {u.get('functions', '?')} fn")
    store.fix_owner()
    return doc


def _label(meta, cutoff):
    """campaign.labels() record -> the flat label used by Analyzer, at one cutoff
    (None = the campaign's own live decisions)."""
    if not meta:
        return {"reported": False, "phase": None, "cluster": None, "verdict": None, "backdoor": None, "category": None}
    src = meta.get("live", {}) if cutoff is None else meta.get("cutoffs", {}).get(str(cutoff), {})
    result = src.get("result")
    # verdict (tp/fp) belongs to the cutoff: it says what ROSA reported in that window.
    # Whether the input IS a backdoor is a property of the input, so take the ground
    # truth from the requested cutoff if evaluated there, else from any cutoff that was.
    verdict = result if result in ("true_positive", "false_positive") else None
    gt = result
    if gt is None:
        for c in sorted(meta.get("cutoffs", {}), key=int):
            r = meta["cutoffs"][c].get("result")
            if r is not None:
                gt = r
                break
    backdoor = (gt in camp.BACKDOOR_RESULTS) if gt is not None else None
    return {"reported": src.get("category") is not None, "phase": src.get("phase"),
            "cluster": src.get("cluster"), "verdict": verdict, "backdoor": backdoor,
            "category": src.get("category"), "result": result, "ground_truth": gt}
