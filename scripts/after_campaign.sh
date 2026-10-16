#!/bin/bash
# Unattended follow-up for a finished campaign: wait for the container, batch-replay
# every input (logs kept), compare live vs batch, write RESULT.txt.
#   scripts/after_campaign.sh <target> <container> <campaign dir (host)> [shards=8] [cutoff=60]
set -u
RND=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
target=$1; cname=$2; camp=$(cd "$3" && pwd); shards=${4:-8}; cutoff=${5:-60}
log() { echo "[$(date '+%m-%d %H:%M:%S')] $*"; }
log "waiting for $cname"
docker wait "$cname" >/dev/null
docker logs "$cname" > "$camp/container.log" 2>&1
log "campaign container exited; $(grep -c . "$camp/container.log") log lines; DONE=$([ -f "$camp/.DONE" ] && echo yes || echo no)"
n=$(ls "$camp/rosa-out/traces" | grep -vc '\.trace$\|README')
log "inputs: $n ; estimated drcov logs $(python3 -c "print(round($n*2.9/1024,1))") GB ; $(df -h "$RND" | awk 'NR==2{print "free "$4}')"
cov="$camp/rosa-out/coverage"
log "batch replay ($shards shards) -> $cov/batch"
"$RND/scripts/batch.sh" "$target" "$camp" "$cov/batch" "$shards" "$cutoff" 2>&1 | sed 's/^/  /'
log "check live vs batch"
python3 -I "$RND/bin/rosacov" check --campaign "$camp" > "$cov/check-live-vs-batch.txt" 2>&1
cat "$cov/check-live-vs-batch.txt" | sed 's/^/  /'
python3 -I - "$camp" > "$cov/RESULT.txt" 2>&1 <<'PY'
import json, sys
from pathlib import Path
c = Path(sys.argv[1])
def j(p):
    try: return json.loads(p.read_text())
    except Exception as e: return {"error": str(e)}
print("campaign:", c)
for s in sorted(c.glob("stat-*.txt")): print(s.name, s.read_text().replace("\n", " "))
for name in ("live", "batch"):
    s = j(c / "rosa-out" / "coverage" / name / "summary.json"); r = j(c / "rosa-out" / "coverage" / name / "run.json")
    print(f"\n== {name}: inputs {r.get('inputs', r.get('counts', {}).get('processed'))}  counts {s.get('counts')}")
    d = s.get("denominators", {}); print("  denominators", {k: d.get(k) for k in ("program_bytes", "program_functions", "reachable_bytes", "normal_union_bytes", "normal_union_source")})
    for k, u in s.get("unions", {}).items():
        if u.get("inputs"): print(f"  union {k:45s} n={u['inputs']:6d} {u['bytes']:8d}B ({(u.get('program_ratio') or 0)*100:.2f}%) {u.get('functions')} fn")
    dist = s.get("distributions", {})
    for cls, v in dist.items():
        if "bytes" in v: print(f"  {cls:10s} n={v['n']:6d} bytes {v['bytes']['min']}~{v['bytes']['max']} (median {v['bytes']['median']})  bytes_not_in_normal median {v.get('bytes_not_in_normal', {}).get('median')}")
    ip = c / "rosa-out" / "coverage" / name / "inputs.jsonl"
    recs = [json.loads(l) for l in ip.open()] if ip.is_file() else []
    hit = [x for x in recs if any(x.get("markers", {}).values())]
    rep = [x for x in recs if x.get("label", {}).get("reported")]
    print(f"  marker hits {len(hit)} (reported among them {sum(1 for x in hit if x['label'].get('reported'))}); reported {len(rep)}; timed_out {sum(1 for x in recs if x.get('timed_out'))}; replay errors {sum(1 for x in recs if 'error' in x)}")
PY
cat "$cov/RESULT.txt" | sed 's/^/  /'
log "ALL DONE"
