"""Run one input through the target the way the campaign ran it.

  replay_cov(target, input, logdir)   DynamoRIO drcov -> logs in logdir; returns ReplayResult
  replay_gt(target, input)            ground-truth build; True when the marker fired

`timeout` sends SIGTERM first (drcov writes its log only on a clean exit) and
SIGKILL a few seconds later. A run that hit the timeout is reported as such: its
coverage is "what happened before the timeout", not a full run.

Pre-commands (targets.toml `pre`) are run once per process via `prepare()`.
"""
import os
import subprocess
from pathlib import Path

MARKER = b"***BACKDOOR TRIGGERED***"
_prepared = set()


class ReplayResult:
    __slots__ = ("ok", "timed_out", "returncode", "seconds")

    def __init__(self, ok, timed_out, returncode, seconds):
        self.ok, self.timed_out, self.returncode, self.seconds = ok, timed_out, returncode, seconds


def prepare(target, instance="replay"):
    key = (target.name, instance)
    if key in _prepared:
        return
    for cmd in target.pre_for(instance):
        subprocess.run(["bash", "-c", cmd], check=False)
    _prepared.add(key)


def _env(target, instance, ground_truth=False):
    env = dict(os.environ)
    env.update(target.env_for(instance))
    pre = target.preload_str()
    if pre:
        env["LD_PRELOAD"] = pre
    return env


def replay_cov(target, input_path, logdir, instance="replay"):
    prepare(target, instance)
    logdir = Path(logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    dr = Path(target.dynamorio()) / "bin64" / "drrun"
    t = int(target.timeout)
    cmd = ["timeout", "-k", "5", str(t), str(dr), "-follow_children", "-t", "drcov",
           "-dump_text", "-logdir", str(logdir), "--"] + target.replay_command(instance)
    import time
    t0 = time.time()
    with open(input_path, "rb") as fin:
        p = subprocess.run(cmd, stdin=fin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=_env(target, instance))
    dt = time.time() - t0
    timed_out = p.returncode in (124, 137)
    ok = any(logdir.glob("*.log"))
    return ReplayResult(ok, timed_out, p.returncode, round(dt, 3))


def replay_gt(target, input_path, instance="replay"):
    """-> True / False, or None when the target has no ground truth"""
    if not target.gt_binary():
        return None
    prepare(target, instance)
    t = max(5, int(target.timeout) // 2)
    cmd = ["timeout", "-k", "3", str(t)] + target.replay_command(instance, ground_truth=True)
    with open(input_path, "rb") as fin:
        p = subprocess.run(cmd, stdin=fin, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           env=_env(target, instance, ground_truth=True))
    return MARKER in (p.stderr or b"")
