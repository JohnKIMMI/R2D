"""Generate a ROSA config file from targets.toml.

Two schemas:
  0.5.1 (paper artifact)   extra_args has "-Q" (qemu) and "-r" (runtime traces, main only),
                           [seed_conditions] seconds = N
  0.6.x                    mode = "qemu", input = "stdin", max_syscall_id, strace_timeout_seconds,
                           env ROSA_AFL_DUMP_RUNTIME_TRACES = "1", [phase_one] seconds = N,
                           plus the detection criteria at the top

Instances: the "six" preset reproduces the New_Bench layout (explore/fast/exploit x
with/without AFL_INST_LIBS); "one" is a single main instance. Both keep the
paper's -c 0 (CmpLog) and -a ascii where the target asks for it.
"""
import json
from pathlib import Path

CRITERIA = {
    "cluster_formation_criterion": "edges-only",
    "cluster_formation_edge_tolerance": 0,
    "cluster_formation_syscall_tolerance": 0,
    "cluster_selection_criterion": "edges-and-syscalls",
    "oracle_criterion": "syscalls-only",
}
AFL_FUZZ = "/root/rosa/fuzzers/aflpp/aflpp/afl-fuzz"
BASE_ENV = {"AFL_SKIP_CPUFREQ": "1", "AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES": "1",
            "AFL_COMPCOV_LEVEL": "2", "AFL_NO_ARITH": "1"}

PRESETS = {
    "one": [("main", True, "explore", True, True)],
    "six": [("main", True, "explore", True, True),
            ("fast-libs", False, "fast", True, True),
            ("exploit-libs", False, "exploit", False, True),
            ("explore-bin", False, "explore", True, False),
            ("fast-bin", False, "fast", True, False),
            ("exploit-bin", False, "exploit", False, False)],
}


def _toml_str(s):
    return json.dumps(s)      # JSON string escaping is valid TOML for our paths


def _toml_list(xs):
    return "[" + ", ".join(_toml_str(x) for x in xs) + "]"


def generate(target, out_dir, phase1=60, instances="six", criteria=None, schema=None):
    """-> config text. out_dir: container path of the campaign directory (rosa-out/fuzzer-out go inside)."""
    schema = schema or ("0.5.1" if str(target.rosa_version).startswith("0.5") else "0.6")
    spec = PRESETS[instances] if isinstance(instances, str) else instances
    lines = [f'output_dir = {_toml_str(out_dir + "/rosa-out")}']
    if schema != "0.5.1":
        for k, v in {**CRITERIA, **(criteria or {})}.items():
            lines.append(f"{k} = {_toml_str(v) if isinstance(v, str) else v}")
    if schema == "0.5.1":
        lines += ["", "[seed_conditions]", f"seconds = {phase1}"]
    for name, is_main, power, ascii_, inst_libs in spec:
        # target.afl_args minus "-a ascii" (re-added per instance below); "-c 0" always first
        args, skip = [], False
        for a in target.afl_args:
            if skip:
                skip = False; continue
            if a == "-a":
                skip = True; continue
            args.append(a)
        if "-c" in args:
            i = args.index("-c"); del args[i:i + 2]
        afl_args = ["-c", "0"] + args
        if target.dictionary:
            afl_args += ["-x", target.dictionary]
        afl_args += ["-p", power]
        if ascii_ and "-a" in target.afl_args:
            afl_args += ["-a", "ascii"]
        if schema == "0.5.1":
            afl_args = ["-Q"] + afl_args + (["-r"] if is_main else [])
        env = dict(BASE_ENV)
        env.update(target.env_for(name))
        env.update({k: v for k, v in target.afl_env.items() if k != "AFL_INST_LIBS"})
        if inst_libs and target.afl_env.get("AFL_INST_LIBS", "1") == "1":
            env["AFL_INST_LIBS"] = "1"
        if target.preload and "AFL_PRELOAD" not in env:
            env["AFL_PRELOAD"] = target.preload_str()
        if schema != "0.5.1":
            env["ROSA_AFL_DUMP_RUNTIME_TRACES"] = "1"
        if schema == "0.5.1":
            env.setdefault("AFL_SYNC_TIME", "1")
        lines += ["", "[[fuzzers]]", "[fuzzers.backend]", 'kind = "afl++"', f"name = {_toml_str(name)}"]
        if schema != "0.5.1":
            lines.append('mode = "qemu"')
        lines += [f"is_main = {'true' if is_main else 'false'}",
                  f"afl_fuzz = {_toml_str(AFL_FUZZ)}",
                  f"input_dir = {_toml_str(target.seeds)}",
                  f"output_dir = {_toml_str(out_dir + '/fuzzer-out')}",
                  "target = [", f"    {_toml_str(target.binary)},"]
        lines += [f"    {_toml_str(a)}," for a in target.args_for(name)]
        lines.append("]")
        if schema != "0.5.1":
            lines += ['input = "stdin"']
        lines.append(f"extra_args = {_toml_list(afl_args)}")
        if schema != "0.5.1":
            lines += ["max_syscall_id = 500", "strace_timeout_seconds = 10"]
        lines.append("[fuzzers.backend.env]")
        for k, v in env.items():
            lines.append(f"{k} = {_toml_str(v)}")
    if schema != "0.5.1":
        lines += ["", "[phase_one]", f"seconds = {phase1}"]
    return "\n".join(lines) + "\n"


def write(target, out_dir, path, **kw):
    text = generate(target, out_dir, **kw)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
    return text
