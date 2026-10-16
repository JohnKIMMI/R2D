"""Live coverage: follow a running ROSA campaign and measure each input as ROSA decides it.

Polls rosa-out/backdoors/*/ (or traces/ with --watch traces), waits for the input's
decisions/<id>.toml, replays it with drcov through the target's harness, folds the
result into the unions and appends the record. State lives in a RunStore so a
restarted watcher continues where it stopped (seen ids = inputs.jsonl).

drcov logs are parsed and then deleted (keep_logs=False) -- the block list is
written to blocks.jsonl.gz, and the parse is only trusted (and the log deleted)
when the parsed BB count equals the count the log declares.

Stops when <run>/STOP exists.
"""
import shutil
import time
from pathlib import Path

from . import campaign, drcov, replay
from .metrics import Analyzer
from .store import RunStore


class Watcher:
    def __init__(self, target, rosa_out, run_dir, watch="backdoors", ground_truth=True,
                 keep_logs=False, interval=5.0, resume=True):
        self.target = target
        self.rosa_out = Path(rosa_out)
        self.store = RunStore(run_dir, create=True, resume=resume)
        self.watch = watch
        self.gt = ground_truth and bool(target.gt_binary())
        self.keep_logs = keep_logs
        self.interval = interval
        self.an = Analyzer(target.binary, markers=target.markers)
        self.seen = self.store.seen_ids()
        if self.seen:
            self.an.load_unions(self.store)
            self.an.counts.update(self.store.read_run().get("counts", {}))
        self.store.write_run(target=target.name, binary=target.binary, mode="live",
                             rosa_version=target.rosa_version, image=target.image,
                             watch=watch, ground_truth=self.gt, keep_logs=keep_logs,
                             rosa_out=str(self.rosa_out))
        self.log = open(self.store.path / "live.log", "a")

    # -- discovery ------------------------------------------------------------------
    def new_ids(self):
        found = {}
        if self.watch == "backdoors":
            for cat in (self.rosa_out / "backdoors").glob("*_cluster_*"):
                for f in cat.iterdir():
                    if f.is_file() and f.name not in self.seen:
                        found[f.name] = cat.name
        else:
            tr = self.rosa_out / "traces"
            if tr.is_dir():
                for f in tr.iterdir():
                    if f.is_file() and not f.name.endswith(".trace") and f.name != "README.txt" and f.name not in self.seen:
                        found[f.name] = None
        # the backdoors/ copy can appear a moment before decisions/; wait for the decision
        return {u: c for u, c in found.items() if (self.rosa_out / "decisions" / f"{u}.toml").is_file()}

    # -- one input --------------------------------------------------------------------
    def process(self, uid, category, blocks_fh):
        if category is None:
            category = campaign.read_reported(self.rosa_out).get(uid)
        inp = campaign.input_path(self.rosa_out, uid, category)
        dec = campaign.read_decision(self.rosa_out / "decisions" / f"{uid}.toml")
        label = {"reported": category is not None, "phase": dec["phase"], "cluster": dec["cluster"],
                 "verdict": None, "backdoor": None}
        logdir = self.store.log_dir(uid)
        t0 = time.time()
        res = replay.replay_cov(self.target, inp, logdir)
        rec = {"id": uid, "seconds": dec["seconds"], "category": category, "label": label,
               "replay_seconds": res.seconds, "timed_out": res.timed_out}
        if not res.ok:
            rec["error"] = "replay failed"
            self.an.counts["replay_failed"] += 1
            return rec
        mods, st, nproc = drcov.parse_input_dir(logdir)
        full, bm = self.an.input_record(uid, mods, nproc)
        rec.update(full)
        if res.timed_out:
            self.an.counts["timed_out"] += 1
        if self.gt and category is not None:
            fired = replay.replay_gt(self.target, inp)
            label["verdict"] = "true_positive" if fired else "false_positive"
        self.an.accumulate(rec, bm, label)
        RunStore.write_blocks(blocks_fh, uid, nproc, mods)
        rec["parse_ok"] = (st.bb_lines == st.bb_declared)
        if not self.keep_logs and rec["parse_ok"]:
            shutil.rmtree(logdir, ignore_errors=True)
        rec["wall_seconds"] = round(time.time() - t0, 3)
        return rec

    # -- persistence --------------------------------------------------------------------
    def flush(self):
        self.an.save_unions(self.store)
        self.store.write_run(counts=dict(self.an.counts))
        self.store.write_summary(self.an.summary(with_functions=True))

    def run(self, once=False):
        print(f"[{time.strftime('%H:%M:%S')}] watcher start: {self.target.name}, watch={self.watch}, gt={self.gt}, seen={len(self.seen)}",
              file=self.log, flush=True)
        while True:
            new = self.new_ids()
            if new:
                t0 = time.time()
                with self.store.open_blocks_writer() as bfh:
                    for uid, cat in sorted(new.items()):
                        rec = self.process(uid, cat, bfh)
                        self.seen.add(uid)
                        self.store.append_input(rec)
                self.flush()
                c = self.an.counts
                print(f"[{time.strftime('%H:%M:%S')}] +{len(new)} in {time.time()-t0:.1f}s  processed {c['processed']}"
                      f"  reported {c['reported']}  tp {c['tp']}  fp {c['fp']}"
                      f"  union_reported {self.an.union_stats('reported', with_functions=False)['bytes']}B",
                      file=self.log, flush=True)
            if once or (self.store.path / "STOP").is_file():
                self.flush()
                self.store.fix_owner()
                print(f"[{time.strftime('%H:%M:%S')}] watcher stop", file=self.log, flush=True)
                return
            time.sleep(self.interval)
