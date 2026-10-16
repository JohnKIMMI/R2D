"""targets.toml: the one place a target's harness is defined.

Everything that must agree between fuzzing (ROSA config), batch replay and the
live watcher comes from here: binary, arguments, preloads, environment, setup
commands, ground truth, seeds, dictionary, container image. Paths are container
paths; launch.sh mounts the host directories at fixed places.

Example:

  [targets.dlink]
  image        = "plumtrie/rosa-icse25-artifact:0.1.1"
  rosa_version = "0.5.1"
  binary       = "/root/rosarum/authentic/d-link-1.13A-thttpd/backdoored/thttpd"
  args         = ["-D", "-u", "root", "-p", "8080", "-d", "/root/rosarum/authentic/d-link-1.13A-thttpd/example-server"]
  preload      = ["/root/artifact/preloads/desock/desock.so", "/root/artifact/preloads/rlimit/rlimit.so"]
  env          = { }                          # harness env (replay AND fuzzing)
  pre          = []                           # shell commands run once before replay / campaign
  ground_truth = "/root/rosarum/authentic/d-link-1.13A-thttpd/ground-truth/thttpd"
  seeds        = "/root/artifact/seeds/http"
  dictionary   = "/root/artifact/dictionaries/http.dict"
  afl_args     = ["-c", "0", "-a", "ascii"]   # fuzzing only
  afl_env      = { AFL_INST_LIBS = "1" }      # fuzzing only
  markers      = []                           # functions whose coverage is flagged per input
  timeout      = 20                           # replay timeout (s)

`{instance}` in args/pre/env is replaced by the fuzzer instance name ("replay"
for replays), so instances can have private state directories.
"""
import json
import os
import shlex
from pathlib import Path

try:
    import tomllib
except ImportError:          # Python < 3.11 (artifact image): use the exported targets.json
    tomllib = None

DEFAULTS = {
    "image": "rosa-exp:0.6.0",
    "rosa_version": "0.6.0",
    "args": [], "preload": [], "env": {}, "pre": [],
    "ground_truth": "", "ground_truth_args": None,
    "seeds": "", "dictionary": "", "afl_args": [], "afl_env": {},
    "markers": [], "timeout": 20, "instances": None, "isolate": False,
    "notes": "",
}


class Target:
    def __init__(self, name, cfg, globals_):
        self.name = name
        d = dict(DEFAULTS)
        d.update(cfg)
        self.cfg = d
        self.globals = globals_
        if "binary" not in d:
            raise ValueError(f"target {name}: 'binary' is required")

    def __getattr__(self, k):
        try:
            return self.cfg[k]
        except KeyError:
            raise AttributeError(k)

    # -- substitution -----------------------------------------------------------
    def _sub(self, s, instance):
        return s.replace("{instance}", instance)

    def args_for(self, instance="replay"):
        return [self._sub(a, instance) for a in self.cfg["args"]]

    def env_for(self, instance="replay", fuzzing=False):
        env = {k: self._sub(v, instance) for k, v in self.cfg["env"].items()}
        if fuzzing:
            env.update(self.cfg["afl_env"])
        return env

    def pre_for(self, instance="replay"):
        return [self._sub(c, instance) for c in self.cfg["pre"]]

    def preload_str(self):
        return ":".join(self.cfg["preload"])

    def binary_name(self):
        return Path(self.cfg["binary"]).name

    def gt_binary(self):
        return self.cfg["ground_truth"] or None

    def gt_args_for(self, instance="replay"):
        a = self.cfg["ground_truth_args"]
        return self.args_for(instance) if a is None else [self._sub(x, instance) for x in a]

    def dynamorio(self):
        return self.globals.get("dynamorio", "/root/R2D/tools/DynamoRIO-Linux-11.91.20615")

    # -- shell rendering (for scripts and logs) --------------------------------------
    def replay_command(self, instance="replay", ground_truth=False):
        """-> argv list for running the target once natively (no DynamoRIO)."""
        b = self.gt_binary() if ground_truth else self.cfg["binary"]
        return [b] + (self.gt_args_for(instance) if ground_truth else self.args_for(instance))

    def describe(self):
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in self.env_for().items())
        pre = self.preload_str()
        return (f"[{self.name}] image={self.cfg['image']} rosa={self.cfg['rosa_version']}\n"
                f"  {'LD_PRELOAD=' + pre + ' ' if pre else ''}{env}\n"
                f"  {shlex.join(self.replay_command())}\n"
                + (f"  ground truth: {self.gt_binary()}\n" if self.gt_binary() else "  ground truth: none\n")
                + (f"  markers: {self.cfg['markers']}\n" if self.cfg["markers"] else ""))


def load(path=None):
    """-> (globals dict, {name: Target})"""
    p = Path(path or os.environ.get("ROSACOV_TARGETS") or Path(__file__).resolve().parent.parent / "targets.toml")
    if p.suffix == ".json" or tomllib is None:
        if p.suffix != ".json":
            p = p.with_suffix(".json")
        with open(p) as fh:
            doc = json.load(fh)
    else:
        with open(p, "rb") as fh:
            doc = tomllib.load(fh)
    g = doc.get("defaults", {})
    targets = {n: Target(n, c, g) for n, c in doc.get("targets", {}).items()}
    return g, targets


def get(name, path=None):
    g, t = load(path)
    if name not in t:
        raise KeyError(f"unknown target {name!r}; known: {', '.join(sorted(t))}")
    return t[name]
