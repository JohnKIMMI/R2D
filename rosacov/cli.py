"""rosacov command line.

  rosacov targets [name] [--export targets.json]
  rosacov config   <target> --out-dir <container campaign dir> [--path f] [--phase1 60] [--instances six|one]
  rosacov campaign <target> --dir <campaign dir> --fuzz S [--phase2 14400] [--live] [--watch backdoors|traces]
  rosacov live     <target> --rosa-out <dir> [--run <dir>] [--watch ...] [--interval 5] [--keep-logs] [--once]
  rosacov replay   <target> --rosa-out <dir> [--run <dir>] [--ids f] [--shard i/n] [--gt]
  rosacov metrics  <target> (--campaign <dir> | --run <dir>) [--cutoff C] [--logs <dir>] [--binary b] [--force]
  rosacov check    (<run A> <run B> | --campaign <dir>)
  Coverage results live inside the campaign's rosa-out: rosa-out/coverage/live/ and rosa-out/coverage/batch/.
"""
import argparse
import json
import sys
from pathlib import Path

from . import targets as T


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rosacov")
    ap.add_argument("--targets", help="targets.toml / targets.json (default: package dir)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("targets"); p.add_argument("name", nargs="?"); p.add_argument("--export")

    p = sub.add_parser("config"); p.add_argument("target"); p.add_argument("--out-dir", required=True)
    p.add_argument("--path"); p.add_argument("--phase1", type=int, default=60); p.add_argument("--instances", default="six")

    p = sub.add_parser("campaign"); p.add_argument("target"); p.add_argument("--dir", required=True)
    p.add_argument("--fuzz", type=int, required=True); p.add_argument("--phase2", type=int, default=14400)
    p.add_argument("--phase1", type=int, default=60); p.add_argument("--instances", default="six")
    p.add_argument("--live", action="store_true"); p.add_argument("--watch", default="backdoors", choices=["backdoors", "traces"])
    p.add_argument("--live-keep-logs", action="store_true")
    p.add_argument("--cutoffs", default="60,300,900,1800,3600")

    p = sub.add_parser("live"); p.add_argument("target"); p.add_argument("--rosa-out", required=True); p.add_argument("--run", help="default <rosa-out>/coverage/live")
    p.add_argument("--watch", default="backdoors", choices=["backdoors", "traces"]); p.add_argument("--interval", type=float, default=5)
    p.add_argument("--keep-logs", action="store_true"); p.add_argument("--no-gt", action="store_true"); p.add_argument("--once", action="store_true")

    p = sub.add_parser("replay"); p.add_argument("target"); p.add_argument("--rosa-out", required=True); p.add_argument("--run", help="default <rosa-out>/coverage/batch")
    p.add_argument("--ids"); p.add_argument("--shard", default="0/1"); p.add_argument("--gt", action="store_true")

    p = sub.add_parser("metrics"); p.add_argument("target"); p.add_argument("--run", help="default <campaign>/rosa-out/coverage/batch")
    p.add_argument("--campaign"); p.add_argument("--cutoff", type=int); p.add_argument("--logs"); p.add_argument("--binary")
    p.add_argument("--cluster-unions", action="store_true"); p.add_argument("--no-functions", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--legacy-functions", action="store_true", help="function table as the legacy pipeline built it (comparisons only)")

    p = sub.add_parser("check"); p.add_argument("a", nargs="?"); p.add_argument("b", nargs="?")
    p.add_argument("--campaign", help="compare <campaign>/rosa-out/coverage/live with .../batch")

    a = ap.parse_args(argv)

    if a.cmd == "targets":
        g, ts = T.load(a.targets)
        if a.export:
            Path(a.export).write_text(json.dumps({"defaults": g, "targets": {n: t.cfg for n, t in ts.items()}}, indent=1))
            print(f"exported {len(ts)} targets -> {a.export}")
            return
        for n, t in sorted(ts.items()):
            if a.name and n != a.name:
                continue
            print(t.describe() if a.name else f"{n:18s} {t.image:40s} rosa {t.rosa_version}  {'GT' if t.gt_binary() else 'no GT'}")
        return

    t = T.get(a.target, a.targets) if hasattr(a, "target") else None

    if a.cmd == "config":
        from . import config
        text = config.generate(t, a.out_dir, phase1=a.phase1, instances=a.instances)
        if a.path:
            Path(a.path).write_text(text); print(f"wrote {a.path}")
        else:
            sys.stdout.write(text)
    elif a.cmd == "campaign":
        from . import runner
        runner.run(t, a.dir, a.fuzz, phase2=a.phase2, phase1=a.phase1, instances=a.instances, live=a.live,
                   live_watch=a.watch, live_keep_logs=a.live_keep_logs,
                   cutoffs=tuple(int(x) for x in a.cutoffs.split(",") if x))
    elif a.cmd == "live":
        from .live import Watcher
        run = a.run or str(Path(a.rosa_out) / "coverage" / "live")
        Watcher(t, a.rosa_out, run, watch=a.watch, ground_truth=not a.no_gt, keep_logs=a.keep_logs,
                interval=a.interval).run(once=a.once)
    elif a.cmd == "replay":
        from . import batch, campaign
        ids = Path(a.ids).read_text().split() if a.ids else campaign.list_inputs(a.rosa_out)
        i, n = (int(x) for x in a.shard.split("/"))
        run = a.run or str(Path(a.rosa_out) / "coverage" / "batch")
        batch.replay_ids(t, a.rosa_out, run, ids, shard=(i, n), gt=a.gt)
    elif a.cmd == "metrics":
        from . import batch
        if not a.run and not a.campaign:
            sys.exit("metrics: pass --run or --campaign")
        run = Path(a.run) if a.run else Path(a.campaign) / "rosa-out" / "coverage" / "batch"
        if (run / "summary.json").is_file():
            if not a.force:
                sys.exit(f"{run} already has summary.json; use --force (the previous metrics are kept in prev-<time>/)")
            # keep the replay logs in place; move only the derived files aside
            import shutil, time
            prev = run / ("prev-" + time.strftime("%Y%m%d-%H%M%S")); prev.mkdir()
            for name in ("summary.json", "inputs.jsonl", "blocks.jsonl.gz", "unions", "run.json"):
                if (run / name).exists():
                    shutil.move(str(run / name), str(prev / name))
            (run / "unions").mkdir()
            print(f"  previous metrics moved to {prev.name}/")
        batch.build(run, t, campaign_dir=a.campaign, cutoff=a.cutoff, logs_dir=a.logs, binary=a.binary,
                    cluster_unions=a.cluster_unions, with_functions=not a.no_functions,
                    legacy_functions=a.legacy_functions)
    elif a.cmd == "check":
        from .check import compare
        if a.campaign:
            base = Path(a.campaign) / "rosa-out" / "coverage"
            compare(base / "live", base / "batch")
        elif a.a and a.b:
            compare(a.a, a.b)
        else:
            sys.exit("check: pass two run directories or --campaign")


if __name__ == "__main__":
    main()
