"""Result directory of one coverage run (batch or live). Same layout for both.

  <run>/run.json            target, binary, mode (batch|live), rosa version, created, counts
  <run>/inputs.jsonl        one record per input, in processing order (append-only)
  <run>/blocks.jsonl.gz     one line per input: {"id", "processes", "modules": {path: [[addr,size],...]}}
  <run>/unions/<name>.bin   bitmaps (all, reported, normal, phase1, tp, fp, marker_<fn>, cluster_<id>)
  <run>/summary.json        denominators, counts, union stats with function detail
  <run>/logs/<id>/          drcov logs (batch keeps them; live deletes after parsing)

Never overwrites: opening an existing run for writing fails unless `force`, and
then the old directory is renamed to <run>.bak-<timestamp> first.
"""
import gzip
import json
import os
import shutil
import time
from pathlib import Path


class RunStore:
    def __init__(self, path, create=False, force=False, resume=False):
        self.path = Path(path)
        if create:
            if self.path.exists() and not resume:
                if not force:
                    raise FileExistsError(f"{self.path} exists; pass --force to replace (a backup is kept)")
                bak = self.path.with_name(self.path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
                shutil.move(str(self.path), str(bak))
                print(f"  moved existing {self.path.name} -> {bak.name}")
            self.path.mkdir(parents=True, exist_ok=True)
            (self.path / "unions").mkdir(exist_ok=True)
            (self.path / "logs").mkdir(exist_ok=True)
        elif not self.path.is_dir():
            raise FileNotFoundError(f"{self.path} is not a run directory")

    # -- run.json -------------------------------------------------------------
    def read_run(self):
        p = self.path / "run.json"
        return json.loads(p.read_text()) if p.is_file() else {}

    def write_run(self, **fields):
        cur = self.read_run()
        cur.update(fields)
        cur.setdefault("created", time.strftime("%Y-%m-%d %H:%M:%S"))
        cur["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = self.path / "run.json.tmp"
        tmp.write_text(json.dumps(cur, indent=1, ensure_ascii=False))
        os.replace(tmp, self.path / "run.json")
        return cur

    # -- inputs.jsonl ---------------------------------------------------------
    def append_input(self, rec):
        with open(self.path / "inputs.jsonl", "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read_inputs(self):
        p = self.path / "inputs.jsonl"
        if not p.is_file():
            return []
        with open(p) as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]

    def seen_ids(self):
        return {r["id"] for r in self.read_inputs()}

    # -- blocks.jsonl.gz ------------------------------------------------------
    def open_blocks_writer(self):
        return gzip.open(self.path / "blocks.jsonl.gz", "at")

    @staticmethod
    def write_blocks(fh, uid, processes, mods):
        fh.write(json.dumps({"id": uid, "processes": processes,
                             "modules": {m: sorted(b) for m, b in sorted(mods.items())}}) + "\n")

    def iter_blocks(self):
        p = self.path / "blocks.jsonl.gz"
        if not p.is_file():
            return
        with gzip.open(p, "rt") as fh:
            for ln in fh:
                if ln.strip():
                    yield json.loads(ln)

    # -- unions ---------------------------------------------------------------
    def union_path(self, name):
        return self.path / "unions" / f"{name}.bin"

    def union_names(self):
        return sorted(p.stem for p in (self.path / "unions").glob("*.bin"))

    # -- summary --------------------------------------------------------------
    def write_summary(self, doc):
        tmp = self.path / "summary.json.tmp"
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        os.replace(tmp, self.path / "summary.json")

    def read_summary(self):
        p = self.path / "summary.json"
        return json.loads(p.read_text()) if p.is_file() else {}

    def log_dir(self, uid):
        return self.path / "logs" / uid

    def fix_owner(self):
        """Inside a container we run as root; hand the files to the host user (HOST_UID)."""
        uid = os.environ.get("HOST_UID")
        if uid and os.geteuid() == 0:
            import subprocess
            subprocess.run(["chown", "-R", f"{uid}:{uid}", str(self.path)], check=False)
