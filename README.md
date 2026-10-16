# rosacov — ROSA 캠페인 + DynamoRIO 커버리지

ROSA로 퍼징하고, 그 입력들을 DynamoRIO(drcov)로 다시 돌려 커버리지를 재는 파이프라인 하나.
대상 정의(`targets.toml`) → ROSA config 생성 → 캠페인 실행(실시간 감시기 선택) → 끝난 뒤 일괄 replay → 지표 → 두 결과 대조.
보고서·표 생성은 범위 밖이고, 결과는 캠페인의 `rosa-out/coverage/` 안에 파일로 남는다.

```
targets.toml ──▶ rosacov config   ──▶ ROSA config (0.5.1 / 0.6 스키마)
             ──▶ rosacov campaign ──▶ rosa → simulate(수집 시간별) → evaluate(ground truth 있을 때)
                                      └ --live: 신고 입력을 즉시 drcov replay → rosa-out/coverage/live/
             ──▶ rosacov replay   ──▶ 끝난 뒤 전체 입력 drcov replay (컨테이너 N개 분할) → rosa-out/coverage/batch/
             ──▶ rosacov metrics  ──▶ 입력별 지표·합집합·요약
             ──▶ rosacov check    ──▶ live vs batch, 또는 아무 두 run 대조
```

## 요구 사항

**호스트**

| 항목 | 요구 | 비고 |
|---|---|---|
| OS | Linux (x86-64). WSL2에서 개발·검증함 | |
| Docker | 컨테이너를 만들 수 있어야 함 | 캠페인·replay는 전부 컨테이너 안에서 돈다 |
| Python | 3.11 이상 | `targets.toml`을 읽는 `tomllib` 때문. 컨테이너 쪽은 3.10도 됨(`launch.sh`가 JSON 사본을 넣어 줌) |
| binutils | `readelf`, `size` | 호스트에서 `rosacov metrics`를 직접 돌릴 때만. Ubuntu: `apt install binutils` |
| 디스크 | 이미지 8.5GB×2 + 아티팩트 이미지 16.6GB + DynamoRIO 1.5GB + 결과 | 일괄 replay는 drcov 원본을 보존하므로 입력 수 × (수백 KB~3MB) |
| 메모리 | 큰 바이너리(수 MB .text)에서 입력당 비트맵 수십 MB를 동시에 들지 않도록 설계됨. 8GB면 충분 | 재계산을 여러 개 동시에 돌리지 말 것 |

numpy 등 외부 파이썬 패키지는 쓰지 않는다. 호스트·컨테이너 모두 표준 라이브러리만.

**Docker 이미지** (크기 때문에 공유하지 않는다. 받는 쪽에서 만든다)

| 이미지 | 어떻게 | 쓰는 대상 |
|---|---|---|
| `plumtrie/rosa-icse25-artifact:0.1.1` | `docker pull` (Docker Hub, 16.6GB). ROSA 0.5.1, 논문 아티팩트 | `*_artifact`, `jq`, `dropbear*` |
| `plumtrie/rosa:0.6.0` | `docker pull` (7.8GB). ROSA 0.6.0 정식 | 아래 두 이미지의 베이스 |
| `rosa-exp:0.6.0` | `docker/build.sh rosa-exp` — 위 베이스 + ROSARUM 빌드 의존성 + ROSA 패치 소켓 하네스 | 호스트 빌드 ROSARUM 6종 |
| `rosa-exp-domoticz:0.6.0` | `docker/build.sh domoticz` — `rosa-exp` + domoticz 라이브러리(`libmosquitto1`, `libusb-0.1-4` 등) | `domoticz` |

빌드는 네트워크가 필요하고 20~30분 걸린다. 캠페인 컨테이너 자체는 항상 `--network none`으로 뜬다.

**DynamoRIO** (1.5GB, 공식 배포판 그대로)

```bash
mkdir -p $ROSA_ROOT/TEST/tools && cd $ROSA_ROOT/TEST/tools
curl -LO https://github.com/DynamoRIO/dynamorio/releases/download/cronbuild-11.91.20615/DynamoRIO-Linux-11.91.20615.tar.gz
tar xzf DynamoRIO-Linux-11.91.20615.tar.gz      # -> TEST/tools/DynamoRIO-Linux-11.91.20615/
```

`launch.sh`가 이 디렉터리를 컨테이너의 `/opt/dr/DynamoRIO-Linux-11.91.20615`로 읽기 전용 마운트한다. 다른 버전을 쓰려면
`targets.toml`의 `[defaults] dynamorio`와 `launch.sh`의 `DR=`를 같이 바꾼다. 버전은 커버리지 결과를 호환시키려면 맞추는 것이 좋다.

**대상 바이너리·시드** (대상별로 필요한 것만)

| 대상 | 있어야 하는 것 | 호스트 경로 → 컨테이너 경로 |
|---|---|---|
| ROSARUM 6종 (`poppler`·`linksys`·`proftpd`·`belkin`·`dlink`·`php`) | ROSARUM 호스트 빌드(backdoored + ground-truth), 시드 `TEST/seeds/<이름>/` | `rosarum/targets` → `/root/rosarum/targets`, `TEST` → `/root/TEST` |
| `*_artifact` 3종 | 아티팩트 이미지 안에 다 들어 있음 | — |
| `jq`, `dropbear*` | `TEST/New_Bench/{targets,seeds,dicts}/`, `extract-dropbearkey` | `docker cp`로 반입(`isolate = true`) |
| `domoticz` | `TEST/New_Bench/domotcz/{extracted,seeds}/` | `TEST/New_Bench` → `/root/New_Bench` |

## 설치

```bash
# 1. 저장소 루트. R2D가 <root>/TEST/analysis/coverage/ 아래 있으면 스크립트가 알아서 찾으므로 생략 가능
export ROSA_ROOT=/path/to/ROSA

# 2. 이미지
docker pull plumtrie/rosa-icse25-artifact:0.1.1
docker pull plumtrie/rosa:0.6.0
$ROSA_ROOT/TEST/analysis/coverage/R2D/docker/build.sh          # rosa-exp:0.6.0, rosa-exp-domoticz:0.6.0

# 3. DynamoRIO (위 절)

# 4. 확인
cd $ROSA_ROOT/TEST/analysis/coverage/R2D
bin/rosacov targets                                             # 대상 13종이 나열되면 toml 로드 OK
bin/rosacov targets domoticz                                    # 실행 명령·환경·하네스 확인
scripts/launch.sh domoticz check -- targets domoticz                # 컨테이너 안에서도 같은 출력이면 마운트 OK
```

하네스 `.so`는 따로 빌드할 필요가 없다. `launch.sh`가 컨테이너를 띄울 때 `harness/build.sh`를 먼저 돌려 없거나 오래된 `.so`를
그 컨테이너의 gcc로 만든다(이미지마다 glibc가 달라 호스트에서 빌드한 것을 넣으면 안 된다).

## 디렉터리

```
R2D/
  targets.toml          대상 정의 — 하네스가 정의되는 유일한 곳
  bin/rosacov           진입점
  rosacov/
    targets.py          toml 로드, {instance} 치환, 실행 명령·환경
    config.py           ROSA config 생성 (0.5.1 / 0.6)
    runner.py           캠페인 실행 (rosa, simulate, evaluate, 감시기)
    campaign.py         rosa-out 읽기 (decisions·backdoors·evalD; uid/id 차이 흡수)
    replay.py           입력 하나 replay (drcov / ground truth)
    drcov.py            drcov 로그 → 블록 (세그먼트 preferred_base, 파일 안 다중 덤프)
    elf.py              함수 표 (시작 주소당 하나, C++ demangle), .text 범위
    bitmap.py           주소 비트맵 (슬라이스 칠하기 = 합집합, 정수 AND = 개수)
    metrics.py          입력별 지표·합집합·요약 (일괄·실시간 공용)
    batch.py            일괄 replay + 2패스 지표
    live.py             실시간 감시기
    store.py            결과 디렉터리 읽기·쓰기, 덮어쓰기 금지
    check.py            두 run 대조
    cli.py
  harness/              소켓 하네스 소스 3종 + build.sh (아래)
  docker/               Dockerfile 2종 + build.sh
  scripts/
    launch.sh           컨테이너 하나 띄워 rosacov 명령 실행 (마운트·격리·하네스 빌드)
    batch.sh            끝난 캠페인 일괄 replay N분할 + metrics
    after_campaign.sh   캠페인 종료 대기 → batch.sh → check → RESULT.txt (무인 실행용)
    collect.sh          격리 컨테이너에서 결과 복사
  tests/                레거시 결과와의 동등성 검사
```

## 하네스

네트워크 서버를 stdin으로 퍼징하기 위한 `LD_PRELOAD` 라이브러리다. `socket`·`bind`·`listen`·`accept`·`recv`·`send` 등을
가로채 "접속이 하나 들어왔고 그 내용은 stdin"이라고 서버를 속인다. 출처는 AFL++ `utils/socket_fuzzing/socketfuzz.c`와
ROSA의 `aflpp-rosa.patch`이고, 그 위에 실험용 동작을 환경 변수로 켜게 더했다. 안 켜면 원본과 같이 동작한다.

| 파일 | 용도 | 더한 것 |
|---|---|---|
| `socketfuzz_plus.c` | ROSARUM 네트워크 대상 (linksys·proftpd·belkin·dlink·php) | 아래 환경 변수 전부, `fdopen(0, "r+")` 강등, lazy-init |
| `socketfuzz_nb.c` | dropbear 실제 서버 (경로 A) | `plus` + `write(0)`·`writev(0)` 무시(응답 쓰기 EBADF 방지), `setsid()` 성공 반환 |
| `socketfuzz_dz.c` | domoticz (boost.asio 서버) | `plus` + `DESOCK_STDIN_PIPE`, `DESOCK_EXIT_ON_CLOSE`, `shutdown()` 훅, **recv/send를 fd 0일 때만** 바꿈(다른 fd는 원래 함수) |

환경 변수 (전부 선택, 설정은 `targets.toml`의 `env`에 둔다):

| 변수 | 뜻 | 쓰는 대상 |
|---|---|---|
| `DESOCK_EXIT_ON_REACCEPT=1` | 두 번째 `accept()`에서 종료. 요청 하나 처리하고 다음 접속을 기다리는 순간 끝낸다 | linksys, proftpd, belkin, php, dropbear |
| `DESOCK_ACCEPT_ONESHOT=1` | 첫 `accept()`만 접속을 주고 그 뒤는 `EAGAIN`. 접속 직후 다음 accept를 거는 서버(asio)용 | dlink(퍼징 시), domoticz |
| `DESOCK_EXIT_ON_CLOSE=1` | 서버가 접속(fd 0)을 `close`/`shutdown` 할 때 종료. `EXIT_ON_REACCEPT`가 너무 이른 서버용 | domoticz |
| `DESOCK_STDIN_PIPE=1` | 첫 `accept()` 때 stdin 내용을 파이프로 옮겨 fd 0을 파이프로 바꾼다. `epoll`이 일반 파일을 거부하는 서버용 | domoticz |
| `DESOCK_NO_DAEMON=1` | 첫 `fork()`만 자식 쪽(0)으로 돌려 데몬화를 건너뜀. 이후 fork는 실제로 함 | dropbear(경로 A) |
| `DESOCK_NO_FORK=1` | 모든 `fork()`를 0으로. 단일 프로세스로 처리 | linksys, proftpd |
| `DESOCK_THTTPD=1` | `poll()` 기반 이벤트 루프용: listen fd를 한 번만 readable로, 접속 fd는 계속 readable로, 처리 뒤 종료 | dlink |
| `DESOCK_POLL_READY=1` | `poll()`의 모든 fd를 readable로 보고 | (실험용) |

하네스를 고르는 순서: stdin 대상이면 preload 없음 → 단순 accept 루프 서버면 `plus` + `EXIT_ON_REACCEPT` → 데몬화·fork가 있으면
`NO_DAEMON`/`NO_FORK` 추가 → `poll` 이벤트 루프면 `THTTPD` → `epoll`(asio 등)이면 `dz` + `STDIN_PIPE` + `ACCEPT_ONESHOT` +
`EXIT_ON_CLOSE`. 새 대상에서 요청이 처리되지 않으면 `strace -f -e trace=accept,accept4,epoll_ctl,read,recvmsg,write,exit_group`으로
어디서 끊기는지 본다. domoticz에서 찾은 세 가지(epoll EPERM, 이른 재accept, 내부 socketpair가 stdin을 먹음)가 전형적이다.

퍼징(AFL++ QEMU 모드)에서는 `AFL_PRELOAD`, replay와 ground truth 실행에서는 `LD_PRELOAD`로 들어간다. `config.py`와
`replay.py`가 각각 알아서 넣으므로 `targets.toml`에는 `preload = [...]`만 적는다.

## 대상 추가

`targets.toml`에 블록 하나. 캠페인·replay·실시간이 전부 이것을 읽는다.

```toml
[targets.domoticz]
image        = "rosa-exp-domoticz:0.6.0"
rosa_version = "0.6.0"
binary       = "/root/New_Bench/domotcz/extracted/domoticz/domoticz"
args         = ["-www", "8080", ..., "-userdata", "/tmp/ud-{instance}"]
preload      = ["/root/TEST/analysis/coverage/R2D/harness/socketfuzz_dz.so"]
env          = { DESOCK_ACCEPT_ONESHOT = "1", DESOCK_EXIT_ON_CLOSE = "1", DESOCK_STDIN_PIPE = "1" }
pre          = ["mkdir -p /tmp/ud-{instance} && ..."]      # 실행 전 한 번 (인스턴스마다)
ground_truth = ""                                           # 없으면 판정 없음
markers      = ["http::server::CWebServer::Cmd_Bypass"]     # 관찰 플래그 (판정 아님)
seeds        = "/root/New_Bench/domotcz/seeds"
dictionary   = "/root/rosa/fuzzers/aflpp/aflpp/dictionaries/http.dict"
afl_args     = ["-c", "0", "-t", "5000+", "-a", "ascii"]
afl_env      = { AFL_FORKSRV_INIT_TMOUT = "60000" }
timeout      = 30                                           # replay 타임아웃(초), SIGTERM 먼저
isolate      = false                                        # true면 bind mount 없이 docker cp
```

경로는 컨테이너 경로다. `launch.sh`가 `$ROSA_ROOT/TEST → /root/TEST`, `$ROSA_ROOT/rosarum/targets → /root/rosarum/targets`,
`$ROSA_ROOT/TEST/New_Bench → /root/New_Bench`, `TEST/tools/DynamoRIO-… → /opt/dr/…`로 마운트한다. `isolate = true`(jq·dropbear:
백도어가 외부로 통신하거나 셸을 실행)면 bind mount 없이 docker cp로 넣고 결과는 `scripts/collect.sh`로 꺼낸다. 네트워크는 항상 없다.
`{instance}`는 퍼저 인스턴스 이름(replay 때는 `replay`)으로 바뀌어 인스턴스마다 상태 디렉터리를 가진다.

새 대상을 넣은 뒤 확인 순서: `bin/rosacov targets <이름>` → 시드 하나로 `scripts/launch.sh <이름> check -- replay <이름> --rosa-out
<traces만 있는 디렉터리>` → `logs/<id>/*.log`가 0바이트가 아닌지 → `metrics`로 함수가 잡히는지.

## 실행 방법

설치(위)가 끝났다는 전제다. 모든 명령은 `$ROSA_ROOT/TEST/analysis/coverage/R2D`에서 실행한다.

**1. 대상 확인**

```bash
bin/rosacov targets                               # 대상 목록
bin/rosacov targets domoticz                      # 실행 명령·환경·하네스·ground truth
scripts/launch.sh domoticz check -- targets domoticz  # 컨테이너 안에서도 같은 출력이면 마운트 OK
```

**2. 캠페인 실행** (컨테이너 하나, 백그라운드)

```bash
scripts/launch.sh domoticz rosa-domoticz --detach -- \
    campaign domoticz --dir /root/TEST/campaigns/domoticz-1 --fuzz 18600 --phase2 14400 --live
docker logs -f rosa-domoticz                      # 진행 상황; rosa.log는 캠페인 디렉터리 안
```

| 인자 | 뜻 |
|---|---|
| `--dir` | 캠페인 디렉터리, 컨테이너 경로(`/root/TEST/…` = 호스트 `$ROSA_ROOT/TEST/…`). 없으면 만든다 |
| `--fuzz` | 퍼징 초. Phase 2 평가 구간을 4시간으로 두려면 최대 수집 시간(3,600) + 14,400 이상 |
| `--phase2` | Phase 2 평가 구간 초 (기본 14,400) |
| `--phase1` | 실제 실행의 Phase 1 수집 시간 초 (기본 60). 300·900·1800·3600초는 끝난 뒤 모의 실행으로 재계산 |
| `--instances` | `six`(기본, explore/fast/exploit × libs/bin) 또는 `one` |
| `--live` | 신고 입력이 나올 때마다 커버리지를 재서 `rosa-out/coverage/live/`에 쌓음. `--watch traces`면 모든 입력 |

끝나면 수집 시간별 `sim-<초>/`, ground truth가 있으면 `evalD-<초>.{csv,full}`, `stat-<초>.txt`, `.DONE`이 생긴다.

**3. 끝난 뒤 전체 입력 커버리지** → `rosa-out/coverage/batch/`

```bash
scripts/batch.sh domoticz $ROSA_ROOT/TEST/campaigns/domoticz-1 - 8 60
#                 대상     캠페인 디렉터리(호스트)              run(-=기본) 분할 수  레이블 수집 시간
```

컨테이너 8개로 나눠 replay(원본 drcov 로그 보존)하고 지표를 한 번 계산한다. 마지막 인자는 신고 여부·군집 레이블을 어느
`sim-<초>/`에서 가져올지다. 2와 3을 무인으로 이으려면 캠페인을 띄운 직후:

```bash
nohup scripts/after_campaign.sh domoticz rosa-domoticz $ROSA_ROOT/TEST/campaigns/domoticz-1 8 60 > after.log 2>&1 &
```

캠페인 종료를 기다렸다가 3을 돌리고, 실시간과 대조해 `rosa-out/coverage/{check-live-vs-batch.txt,RESULT.txt}`를 쓴다.

**4. 결과 보기**

```bash
bin/rosacov check --campaign $ROSA_ROOT/TEST/campaigns/domoticz-1     # 실시간 vs 일괄
cd $ROSA_ROOT/TEST/campaigns/domoticz-1/rosa-out/coverage/batch
python3 -c "import json;s=json.load(open('summary.json'));print({k:(v['inputs'],v['bytes'],v['functions']) for k,v in s['unions'].items() if v['inputs']})"
head -c 600 inputs.jsonl
```

파일 형식은 아래 「결과」 절.

**그 밖에**

```bash
bin/rosacov config domoticz --out-dir /root/TEST/campaigns/x          # ROSA config만 만들어 보기
bin/rosacov live domoticz --rosa-out <rosa-out> [--watch traces] [--once]   # 감시기만 따로 (컨테이너 안)
bin/rosacov metrics domoticz --campaign <dir> --cutoff 300 \
    --run <dir>/rosa-out/coverage/batch-300 --logs <dir>/rosa-out/coverage/batch/logs
                                                                       # 같은 로그에 다른 수집 시간 레이블
bin/rosacov check <run A> <run B>                                      # 아무 두 run 대조
```

격리 대상(`isolate = true`: jq·dropbear)은 bind mount가 없으므로 캠페인이 끝난 뒤 `scripts/collect.sh <컨테이너> /root/TEST/campaigns/<이름> <호스트 경로>`로 꺼낸다.

`campaign --live`는 감시기를 `nice 19`로 rosa 옆에 띄운다. 감시기는 `rosa-out/backdoors/`를 5초마다 보고, 새 신고 입력의
`decisions/` 파일이 생기면 replay해 `rosa-out/coverage/live/`에 쌓는다. rosa가 끝나면 20초 뒤 `STOP`을 놓는다.

## 결과 — `rosa-out/coverage/<live|batch>/` (일괄·실시간 같은 모양)

| 파일 | 내용 |
|---|---|
| `run.json` | 대상, 바이너리, `mode` (batch/live), ROSA 버전, 수집 시간, 건수, 시각 |
| `inputs.jsonl` | 입력당 한 줄 (아래) |
| `blocks.jsonl.gz` | 입력당 블록 목록 `{"id", "processes", "modules": {경로: [[주소, 크기], …]}}`. 라이브러리 포함. 모든 지표의 원재료 |
| `unions/*.bin` | 비트맵(주소당 1바이트): `all`, `reported`, `unreported`, `phase1`, `tp`, `fp`, `backdoor`, `benign`, `marker_<함수>`, (선택) `cluster_<id>` |
| `summary.json` | 분모, 건수, 합집합별 바이트·비율·함수 수·함수별 `[밟은 바이트, 크기]`, 분포 |
| `logs/<id>/` | drcov 원본. **일괄은 보존, 실시간은 파싱 후 삭제**(블록 수가 로그 선언값과 같을 때만) |
| `live.log` | (실시간) 묶음마다 한 줄: 시각, 건수, 소요, 누계, 신고 합집합 |

`inputs.jsonl` 한 줄:

| 키 | 뜻 |
|---|---|
| `id`, `seconds`, `category` | 입력 id, ROSA가 판정한 시각, 신고 범주(`<지문>_<군집>`) |
| `label` | `reported`, `phase`("1"/"2"), `cluster`, `verdict`(tp/fp — 그 수집 시간의 신고에 대한 판정), `backdoor`(ground truth — 입력의 성질이라 어느 수집 시간에서든 평가된 결과를 씀), `result`, `ground_truth` |
| `bytes`, `blocks`, `functions_covered`, `program_ratio`, `text_ratio` | 대상 바이너리 기준. `bytes`는 함수 안 바이트, 겹치는 블록은 한 번 |
| `functions` | `{이름: [밟은 바이트, 함수 크기]}`, 밟은 바이트 순 |
| `markers` | `{함수: true/false}` — 관찰이지 판정이 아님 |
| `reachable_ratio`, `normal_union_ratio`, `bytes_not_in_normal` | 합집합 대비 (일괄만; "normal" = ground truth 있으면 benign, 없으면 unreported) |
| `timed_out`, `replay_seconds`, `parse_ok`, `processes`, `modules` | replay 상태, 모듈별 블록 수 |

함수 표는 **시작 주소당 하나**(C++ 이름은 demangle). 옛 파이프라인은 심볼 이름당 하나라서 별칭(C++ 생성자 C1/C2 등)을 두 번
셌다. `--legacy-functions`가 옛 규칙이고 비교용이다.

빠르게 보기:

```bash
python3 -c "import json;s=json.load(open('summary.json'));print({k:(v['inputs'],v['bytes'],v['functions']) for k,v in s['unions'].items() if v['inputs']})"
head -c 600 inputs.jsonl
```

## 덮어쓰기 금지

- 실시간과 일괄은 다른 디렉터리(`rosa-out/coverage/live/`, `rosa-out/coverage/batch/`).
- `metrics`는 `summary.json`이 있으면 거부. `--force`면 파생 파일(`summary.json`, `inputs.jsonl`, `blocks.jsonl.gz`, `unions/`, `run.json`)만 `prev-<시각>/`로 옮기고 `logs/`는 그대로 둔다.
- `scripts/batch.sh`는 `summary.json`이 있는 run 디렉터리를 거부한다.

## 검증

- `tests/test_core.py`, `tests/test_metrics.py`: 옛 drcov 로그·블록·지표와 입력 단위로 동일한지.
- `tests/legacy_recompute.py`: 옛 14개 캠페인(34,827 입력)을 새 코드로 전부 재계산해 옛 `metrics/`와 입력 단위로 동일함을 확인했다
  (`../runs-legacy/ACCEPTANCE.md`). 이 테스트들은 옛 데이터가 있는 원래 머신에서만 돈다.
- 실시간 vs 일괄: `rosacov check`. 같은 입력도 replay마다 블록 몇 개가 다를 수 있고(thttpd 타이머, domoticz 스레드 등),
  check는 그것(블록 수가 다름)과 파이프라인 버그(블록 수 같은데 수치 다름)를 구분해 센다.

## 알려진 것 · 막히면

| 증상 | 원인 | 대응 |
|---|---|---|
| `rosa` 가 시작 몇 초 만에 `invalid crashes directory` | ROSA 0.6.0이 fuzzer를 띄운 직후 모든 인스턴스의 `crashes/`를 읽는데 느린 인스턴스(QEMU 포크 서버 초기화)가 아직 못 만듦 | `runner.py`가 디렉터리를 미리 만든다. 직접 rosa를 띄울 때는 `fuzzer-out/<인스턴스>/{crashes,hangs,queue}`를 먼저 만들 것 |
| ROSA 상태 줄 `Edge coverage` 99%대, 추적이 수십 개뿐 | QEMU edge id 테이블이 64K 고정이라 큰 바이너리는 기동만으로 포화. 추적 해시가 syscall만으로 정해져 중복 제거에 다 걸림 | ROSA 쪽 제한(`AFL_MAP_SIZE` 무효). 결과 해석 시 명시 |
| AFL이 시작 단계(dry run)에서 전체 중단 | 시드 하나가 1,000ms 초과 | `afl_args`에 `-t 2000+`, `afl_env`에 `AFL_IGNORE_SEED_PROBLEMS`·`AFL_IGNORE_TIMEOUTS` |
| drcov 로그가 0바이트 | 프로세스가 SIGKILL로 죽음(drcov는 정상 종료 때만 씀), 또는 서버가 끝나지 않음 | `timeout`은 TERM 먼저(`replay.py`가 그렇게 함). 종료 조건 환경 변수 확인. dropbear 경로 A는 원래 안 됨 |
| 서버가 요청을 안 읽고 바로 끝남 | `epoll`이 stdin(일반 파일)을 거부 / 재accept에서 종료 / 내부 소켓이 stdin을 먹음 | `socketfuzz_dz` + `DESOCK_STDIN_PIPE`·`ACCEPT_ONESHOT`·`EXIT_ON_CLOSE`. strace로 확인 |
| 컨테이너가 만든 파일을 호스트에서 못 지움 | root 소유 | `HOST_UID`를 넘기므로 rosacov가 끝날 때 chown 한다. 중간에 죽었으면 `docker run --rm -v <dir>:/c alpine chown -R $(id -u) /c` |
| `metrics`가 메모리를 다 씀 | 입력별 비트맵을 동시에 들고 있지 않도록 2패스 구조다. 그래도 재계산을 여러 개 병렬로 돌리면 큰 바이너리에서 터진다 | 한 번에 하나 |
| 호스트에서 `tomllib` 없음 | Python < 3.11 | 3.11 이상 설치, 또는 `bin/rosacov targets --export targets.json` 뒤 `ROSACOV_TARGETS=targets.json` |

`metrics --cutoff C`는 `sim-C/`·`evalD-C.full`의 레이블로 합집합을 만든다. 생략하면 캠페인 자신의 판정을 쓴다.
