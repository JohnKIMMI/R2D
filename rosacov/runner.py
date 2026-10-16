"""Run one ROSA campaign inside its container, with the optional live watcher.

  campaign/<name>/
    config.toml            generated from targets.toml
    rosa-out/ fuzzer-out/  ROSA
    rosa.log
    sim-<C>/ sim-<C>.log   rosa-simulate per cutoff (Phase 2 window = cutoff + phase2)
    evalD-<C>.{csv,full}   rosa-evaluate -D (only when the target has a ground truth)
    stat-<C>.txt           clusters / traces / reported / categories per cutoff
    rosa-out/coverage/live/   the live watcher's RunStore (when --live); batch goes to rosa-out/coverage/batch/
    .DONE

Phase 2 is a window of `phase2` seconds after the cutoff, so the fuzzing time must
be at least max(cutoff) + phase2 (18,600 s covers a 60-min cutoff + 4 h).
"""
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfgmod, campaign as camp, replay

CUTOFFS = (60, 300, 900, 1800, 3600)


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _rosa_bin(name):
    for p in (Path.home() / ".cargo" / "bin" / name, Path("/usr/local/bin") / name):
        if p.is_file():
            return str(p)
    return name


def _gt_command(target, instance="main"):
    """How rosa-evaluate runs the ground truth: the artifact's way, afl-qemu-trace under
    a KILL timeout, with the harness env passed via -e (LD_PRELOAD, not AFL_PRELOAD)."""
    q = "/root/rosa/fuzzers/aflpp/aflpp/afl-qemu-trace"
    cmd = ["timeout", "--signal=KILL", "15s", q, "--"] + target.replay_command(instance, ground_truth=True)
    return shlex.join(cmd)


def run(target, campaign_dir, fuzz_seconds, phase2=14400, phase1=60, instances="six", live=False,
        live_watch="backdoors", live_keep_logs=False, cutoffs=CUTOFFS, criteria=None):
    cdir = Path(campaign_dir).resolve()
    cdir.mkdir(parents=True, exist_ok=True)
    cfg_path = cdir / "config.toml"
    cfgmod.write(target, str(cdir), cfg_path, phase1=phase1, instances=instances, criteria=criteria)
    spec = cfgmod.PRESETS[instances] if isinstance(instances, str) else instances
    for name, *_ in spec:
        replay.prepare(target, name)
        # ROSA 0.6.0 reads every instance's crashes/ right after spawning the fuzzers and
        # aborts if a slow instance (QEMU fork server init) has not created it yet
        for sub in ("crashes", "hangs", "queue"):
            (cdir / "fuzzer-out" / name / sub).mkdir(parents=True, exist_ok=True)
    replay.prepare(target, "replay")
    _log(f"=== {target.name}: campaign start (fuzz={fuzz_seconds}s, phase1={phase1}s, phase2={phase2}s, live={live}) ===")
    watcher = None
    if live:
        watcher = subprocess.Popen(
            [sys.executable, "-m", "rosacov.cli", "live", target.name, "--rosa-out", str(cdir / "rosa-out"),
             "--run", str(cdir / "rosa-out" / "coverage" / "live"), "--watch", live_watch, "--interval", "5"]
            + (["--keep-logs"] if live_keep_logs else []),
            stdout=open(cdir / "live-watcher.out", "a"), stderr=subprocess.STDOUT,
            preexec_fn=lambda: os.nice(19), cwd=str(Path(__file__).resolve().parent.parent))
        _log(f"live watcher started (pid {watcher.pid})")
    with open(cdir / "rosa.log", "w") as lf:
        p = subprocess.run(["timeout", "-s", "INT", "-k", "300", str(fuzz_seconds), _rosa_bin("rosa"), "-n", "-f", str(cfg_path)],
                           stdout=lf, stderr=subprocess.STDOUT, cwd=str(cdir))
    _log(f"=== {target.name}: campaign done, rc={p.returncode} ===")
    if watcher:
        time.sleep(20)
        (cdir / "rosa-out" / "coverage" / "live" / "STOP").touch()
        watcher.wait()
        _log("live watcher stopped")
    ro = cdir / "rosa-out"
    gt = _gt_command(target) if target.gt_binary() else None
    env_arg = ",".join(f"{k}={v}" for k, v in {**({"LD_PRELOAD": target.preload_str()} if target.preload else {}), **target.env_for("main")}.items())
    for sec in cutoffs:
        simdir = cdir / f"sim-{sec}"
        simcfg = cdir / f"config-sim-{sec}.toml"
        text = cfg_path.read_text().replace(f"seconds = {phase1}", f"seconds = {sec}").replace(str(ro), str(simdir))
        simcfg.write_text(text)
        tlim = sec + phase2
        _log(f"simulate cutoff {sec}s (window end {tlim}s)")
        with open(cdir / f"sim-{sec}.log", "w") as lf:
            subprocess.run([_rosa_bin("rosa-simulate"), "-f", "-C", str(ro), str(simcfg)], stdout=lf, stderr=subprocess.STDOUT)
        if gt:
            base = [_rosa_bin("rosa-evaluate"), "-D", "-t", str(tlim), "-p", gt] + (["-e", env_arg] if env_arg else [])
            with open(cdir / f"evalD-{sec}.csv", "w") as fo, open(cdir / f"evalD-{sec}.err", "w") as fe:
                subprocess.run(base + ["-s", str(simdir)], stdout=fo, stderr=fe)
            with open(cdir / f"evalD-{sec}.full", "w") as fo, open(cdir / f"evalD-{sec}.err", "a") as fe:
                subprocess.run(base + [str(simdir)], stdout=fo, stderr=fe)
        st = camp.read_reported(simdir)
        cl = simdir / "clusters"
        (cdir / f"stat-{sec}.txt").write_text(
            f"cutoff_s={sec}\nclusters={len(list(cl.glob('cluster_*.txt'))) if cl.is_dir() else 0}\n"
            f"traces={len(list((simdir / 'traces').glob('*.trace'))) if (simdir / 'traces').is_dir() else 0}\n"
            f"backdoor_inputs={len(st)}\nbackdoor_categories={len(set(st.values()))}\n")
    _log(f"=== {target.name}: ALL DONE ===")
    (cdir / ".DONE").touch()
    uid = os.environ.get("HOST_UID")
    if uid:
        subprocess.run(["chown", "-R", f"{uid}:{uid}", str(cdir)], check=False)
