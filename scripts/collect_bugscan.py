"""버그 전담 부서 증거수집기 — 저장소의 '버그 표면'을 JSON 스냅샷으로 덤프.

LLM 없음. 추정 없음. 오직 실행 결과·파일 내용·git·git grep 만 모은다.
버그 부서(워크플로우 bug-dept)가 이 JSON 을 근거로 앱 모듈을 사냥한다.
헌터 에이전트가 기억이 아니라 이 실물 file:line 에 붙게 만드는 게 목적이다.

collect_evidence.py 와 같은 설계 원칙:
  - 각 섹션은 독립. 하나가 실패해도 나머지는 수집하고, 실패는
    {"status":"error","reason":...} 로 남긴다(조용히 넘어가지 않는다).
  - 숫자는 전부 재현 가능한 출처에서 온다: 테스트 종료코드, red_team KPI,
    git, git grep(추적 파일만 — secrets/·.venv 는 자동 제외).

이 수집기는 '판단'하지 않는다 — 위험 마커(bare except·TODO·assert 등)를 세는 것은
버그 확정이 아니라 '여기를 봐라'는 좌표다. 진짜 버그인지는 워크플로우가 코드를 열어 판정한다.

실행:
  .venv/Scripts/python.exe -m scripts.collect_bugscan
  → docs/reports/_bugscan_YYYYMMDD_HHMMSS.json 생성, 경로를 stdout 마지막 줄에 출력.
"""
from __future__ import annotations

import config  # noqa: F401 — cp949 콘솔 인코딩 안전화(모든 진입점 필수)

import glob
import json
import os
import re
import subprocess
import sys
import traceback
from collections import Counter
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 스캔 대상 = 앱 전체 모듈(사용자 확정 2026-07-24). __init__.py 는 제외(트리비얼).
# (dotted, 저장소 상대경로) — coverage_map·module_index 가 공유.
APP_MODULES = [
    ("agents.broker_agent", "agents/broker_agent.py"),
    ("agents.gemini_decider", "agents/gemini_decider.py"),
    ("agents.trading_agent", "agents/trading_agent.py"),
    ("payments.ap2_mandate", "payments/ap2_mandate.py"),
    ("payments.guard", "payments/guard.py"),
    ("payments.x402_solana", "payments/x402_solana.py"),
    ("market.indicators", "market/indicators.py"),
    ("market.price_feed", "market/price_feed.py"),
    ("web.briefing", "web/briefing.py"),
    ("web.engine", "web/engine.py"),
    ("web.events", "web/events.py"),
    ("web.server", "web/server.py"),
    ("web.store", "web/store.py"),
    ("shared.a2a_messages", "shared/a2a_messages.py"),
    ("shared.models", "shared/models.py"),
    ("config", "config.py"),
]

# git grep 대상 경로(추적 파일). config.py 는 단일 파일이라 그대로.
APP_PATHS = ["agents/", "payments/", "market/", "web/", "shared/", "config.py"]

MATCH_CAP = 12  # 마커별 file:line 매치 상한(헌터에게 줄 좌표 수)


def _pyexe() -> str:
    """저장소 venv 파이썬을 우선 사용(3.10 고정). 없으면 현재 인터프리터."""
    cand = os.path.join(REPO, ".venv", "Scripts", "python.exe")
    return cand if os.path.exists(cand) else sys.executable


PYEXE = _pyexe()

# 자식 프로세스가 Windows 기본(cp949)이 아니라 utf-8 로 출력하도록 강제.
# 없으면 부모가 utf-8 로 디코딩할 때 한국어 출력(red_team KPI 등)이 깨져 파싱 실패.
_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _run(args, timeout: int = 180) -> dict:
    """서브프로세스 실행 → {rc, stdout, stderr} 또는 {rc:None, error}. 절대 예외로 죽지 않음."""
    try:
        p = subprocess.run(
            args, cwd=REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=_ENV,
        )
        return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except subprocess.TimeoutExpired:
        return {"rc": None, "error": f"timeout>{timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"rc": None, "error": f"{type(e).__name__}: {e}"}


def _git_grep(pattern: str, paths=None, fixed: bool = True, ignore_case: bool = False) -> dict:
    """추적 파일에서 pattern 을 찾아 file:line 매치를 반환. secrets/·.venv 는 자동 제외.

    git grep 은 매치 없으면 rc=1(정상), 오류면 rc>=2. (collect_evidence.py 와 동일 계약)
    """
    args = ["git", "grep", "-n", "-I"]
    if fixed:
        args.append("-F")
    if ignore_case:
        args.append("-i")
    args += ["-e", pattern]
    if paths:
        args += ["--"] + paths
    r = _run(args, timeout=30)
    if r.get("rc") not in (0, 1):
        return {"error": (r.get("stderr") or r.get("error") or "").strip()}
    lines = [ln for ln in (r.get("stdout") or "").splitlines() if ln.strip()]
    return {"count": len(lines), "matches": lines[:MATCH_CAP]}


def _merge_greps(patterns, paths=None) -> dict:
    """여러 고정문자열 grep 을 합쳐 {count, matches, by_pattern} 로. 회귀 없는 결정론."""
    total = 0
    matches: list = []
    by_pattern: dict = {}
    err = None
    for p in patterns:
        g = _git_grep(p, paths)
        if g.get("error"):
            err = g["error"]
            by_pattern[p] = {"error": g["error"]}
            continue
        c = g.get("count", 0)
        by_pattern[p] = c
        total += c
        matches.extend(g.get("matches", []))
    out = {"count": total, "matches": matches[:MATCH_CAP], "by_pattern": by_pattern}
    if err:
        out["partial_error"] = err
    return out


# ---------------------------------------------------------------- git 상태 + churn

def collect_git() -> dict:
    out: dict = {}
    plans = {
        "branch": ["rev-parse", "--abbrev-ref", "HEAD"],
        "head": ["rev-parse", "HEAD"],
        "status_short": ["status", "--short"],
        "recent_commits": ["log", "--oneline", "-8"],
    }
    for key, args in plans.items():
        r = _run(["git"] + args, timeout=30)
        if r.get("rc") == 0:
            out[key] = (r.get("stdout") or "").strip()
        else:
            out[key] = {"error": (r.get("stderr") or r.get("error") or "").strip()}
    sc = out.get("status_short")
    out["clean"] = (sc == "")  # 빈 문자열 = 워킹트리 clean
    rc = out.get("recent_commits")
    if isinstance(rc, str):
        out["recent_commits"] = [ln for ln in rc.splitlines() if ln.strip()]

    # churn — 최근 20커밋에서 앱 .py 파일별 변경 빈도(자주 바뀐 곳 = 버그 잦은 곳).
    r = _run(["git", "log", "--pretty=format:", "--name-only", "-n", "20", "--"] + APP_PATHS, timeout=30)
    churn: list = []
    if r.get("rc") == 0:
        files = [ln.strip() for ln in (r.get("stdout") or "").splitlines() if ln.strip().endswith(".py")]
        churn = [{"file": f, "touches": n} for f, n in Counter(files).most_common(12)]
    else:
        churn = {"error": (r.get("stderr") or r.get("error") or "").strip()}
    out["recently_changed_files"] = churn
    return out


# ---------------------------------------------------------------- 테스트 6종(동적 탐색)

def collect_tests() -> dict:
    # glob.escape: 저장소 경로의 대괄호([Google Cloud X Solana])가 문자클래스로
    # 오해석되지 않게 베이스 경로를 이스케이프한다(파일명 패턴만 와일드카드).
    base = glob.escape(os.path.join(REPO, "scripts"))
    files = sorted(glob.glob(os.path.join(base, "test_*.py")))
    modules = []
    all_pass = True
    for f in files:
        mod = "scripts." + os.path.splitext(os.path.basename(f))[0]
        r = _run([PYEXE, "-m", mod], timeout=180)
        rc = r.get("rc")
        out = r.get("stdout") or ""
        ok = (rc == 0)
        modules.append({
            "module": mod,
            "rc": rc,
            "pass": ok,
            "ok_count": len(re.findall(r"\[OK", out)),
            "fail_count": len(re.findall(r"\[FAIL", out)),
            "summary": (out.strip().splitlines()[-1] if out.strip() else ""),
            "error": r.get("error"),
        })
        all_pass = all_pass and ok
    return {"count": len(files), "all_pass": all_pass, "modules": modules}


# ---------------------------------------------------------------- red_team KPI(현재 기준선)

def collect_red_team() -> dict:
    r = _run([PYEXE, "-m", "scripts.red_team", "--report"], timeout=180)
    out = r.get("stdout") or ""
    kpi = {}
    m = re.search(
        r"\[KPI\]\s*시도\s*(\d+)\s*·\s*차단\s*(\d+)\s*·\s*유출\s*([\d.]+)\s*USDC\s*·\s*오탐\s*(\d+)",
        out,
    )
    if m:
        kpi = {
            "attempts": int(m.group(1)),
            "blocked": int(m.group(2)),
            "leak_usdc": m.group(3),
            "false_positives": int(m.group(4)),
        }
    return {
        "rc": r.get("rc"),
        "ok": r.get("rc") == 0,
        "kpi": kpi,
        "error": r.get("error"),
        "raw_tail": "\n".join(out.strip().splitlines()[-4:]) if out.strip() else "",
    }


# ---------------------------------------------------------------- 테스트 커버리지 맵

def _read_text(rel: str) -> str:
    try:
        with open(os.path.join(REPO, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:  # noqa: BLE001
        return ""


def collect_coverage_map() -> dict:
    """앱 모듈별로 '어떤 test_* 가 임포트해 건드리나' 를 결정론적으로 대조.

    referenced_in_tests = 그 모듈을 임포트하는 test 파일 목록(dotted 경로 또는 'import <stem>').
    dedicated_test_file = test_<stem>.py 파일이 실재하는가.
    uncovered = 어떤 테스트도 참조하지 않는 모듈(진짜 사각지대).
    ※ '참조됨' 은 '잘 테스트됨' 이 아니다 — 전용 테스트가 없는데 남이 임포트만 한 경우도 있다.
      그래서 두 신호를 함께 준다(헌터가 코드를 열어 판정).
    """
    base = glob.escape(os.path.join(REPO, "scripts"))
    test_files = sorted(glob.glob(os.path.join(base, "test_*.py")))
    # 각 테스트 파일을 한 번만 읽어 텍스트로 보관(재읽기 방지).
    test_text = {os.path.basename(f): _read_text(os.path.relpath(f, REPO)) for f in test_files}
    test_stems = {os.path.splitext(bn)[0].replace("test_", "") for bn in test_text}  # guard, store, ...

    modules = []
    uncovered = []
    for dotted, path in APP_MODULES:
        stem = dotted.split(".")[-1]
        needles = (dotted, f"import {stem}", f"{stem} import")
        referenced = sorted(
            bn for bn, txt in test_text.items() if any(nd in txt for nd in needles)
        )
        dedicated = (f"test_{stem}.py" in test_text)
        entry = {
            "module": dotted,
            "path": path,
            "referenced_in_tests": referenced,
            "dedicated_test_file": dedicated,
        }
        modules.append(entry)
        if not referenced:
            uncovered.append(dotted)
    return {
        "app_module_count": len(APP_MODULES),
        "test_file_count": len(test_text),
        "modules": modules,
        "uncovered": uncovered,
        "uncovered_count": len(uncovered),
    }


# ---------------------------------------------------------------- 위험 마커(git grep)

def collect_risk_markers() -> dict:
    """'여기를 봐라' 좌표. 마커 = 버그 확정이 아니라 점검 지점(헌터가 열어 판정).

    전부 고정문자열 grep(정규식 이식성 함정 회피). 추적 파일만, secrets/·.venv 자동 제외.
    """
    return {
        # bare except: "except:" 부분문자열은 오직 맨몸 except 만 잡는다
        # ("except Exception:"·"except ValueError:" 에는 "except:" 부분문자열이 없다).
        "bare_except": _git_grep("except:", APP_PATHS),
        # 광범위 예외 포착 — 삼킴 여부는 헌터가 확인(로깅·재던짐 있으면 정상).
        "broad_except": _git_grep("except Exception", APP_PATHS),
        # 미해결 표식.
        "todo_fixme": _merge_greps(["TODO", "FIXME", "HACK", "XXX"], APP_PATHS),
        # 억눌린 린트 — 무언가를 알고도 넘긴 지점일 수 있음.
        "noqa": _git_grep("# noqa", APP_PATHS),
        # 런타임 assert — python -O 로 스트립되면 사라지는 방어(가드에 있으면 위험).
        "runtime_assert": _git_grep("assert ", APP_PATHS),
        # 돈은 Decimal/정수 base units 여야 한다 — payments 안의 float( 는 정밀도 위험.
        "float_in_payments": _git_grep("float(", ["payments/"]),
    }


# ---------------------------------------------------------------- 모듈 인덱스(팬아웃 지도)

_DEF_RE = re.compile(r"^\s*(?:async\s+def|def)\s+\w+", re.MULTILINE)
_CLASS_RE = re.compile(r"^\s*class\s+\w+", re.MULTILINE)


def collect_module_index() -> dict:
    """앱 파일별 비공백 라인·함수·클래스 수. 헌터가 어디에 표면이 많은지 가늠하는 지도."""
    dirs_map = {"agents": [], "payments": [], "market": [], "web": [], "shared": [], "root": []}
    total_files = 0
    total_nonblank = 0
    for dotted, path in APP_MODULES:
        txt = _read_text(path)
        if not txt:
            continue
        nonblank = sum(1 for line in txt.splitlines() if line.strip())
        funcs = len(_DEF_RE.findall(txt))
        classes = len(_CLASS_RE.findall(txt))
        top = path.split("/")[0] if "/" in path else "root"
        dirs_map.setdefault(top, []).append({
            "file": path, "nonblank": nonblank, "funcs": funcs, "classes": classes,
        })
        total_files += 1
        total_nonblank += nonblank
    # 빈 그룹 제거
    dirs_map = {k: v for k, v in dirs_map.items() if v}
    return {"dirs": dirs_map, "total_files": total_files, "total_nonblank": total_nonblank}


# ---------------------------------------------------------------- 조립·요약·기록

def _safe(fn) -> dict:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {
            "status": "error",
            "reason": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc().splitlines()[-3:],
        }


def _marker_count(rm: dict, key: str):
    v = rm.get(key)
    if isinstance(v, dict):
        return v.get("count")
    return None


def _summary(sec: dict) -> dict:
    """섹션에서 결정론적으로 유도한 빠른 지표(헌터 그라운딩용). 창작 없음."""
    s: dict = {}
    t = sec.get("tests")
    if isinstance(t, dict):
        s["tests_count"] = t.get("count")
        s["tests_all_pass"] = t.get("all_pass")
    rt = sec.get("red_team")
    if isinstance(rt, dict):
        s["red_team_ok"] = rt.get("ok")
        s["red_team_kpi"] = rt.get("kpi")
    cm = sec.get("coverage_map")
    if isinstance(cm, dict):
        s["uncovered_count"] = cm.get("uncovered_count")
        s["uncovered"] = cm.get("uncovered")
    rm = sec.get("risk_markers")
    if isinstance(rm, dict):
        s["risk_marker_totals"] = {
            k: _marker_count(rm, k)
            for k in ("bare_except", "broad_except", "todo_fixme",
                      "noqa", "runtime_assert", "float_in_payments")
        }
    mi = sec.get("module_index")
    if isinstance(mi, dict):
        s["app_file_count"] = mi.get("total_files")
        s["app_nonblank_total"] = mi.get("total_nonblank")
    return s


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sections = {
        "git": _safe(collect_git),
        "tests": _safe(collect_tests),
        "red_team": _safe(collect_red_team),
        "coverage_map": _safe(collect_coverage_map),
        "risk_markers": _safe(collect_risk_markers),
        "module_index": _safe(collect_module_index),
    }
    evidence = {
        "schema": "collect_bugscan/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pyexe": PYEXE,
        "note": "버그 부서(bug-dept) 근거 스냅샷 — 모든 값은 실행/파일/git 유래. 마커는 '점검 지점'이지 버그 확정이 아니다. 추정 금지.",
        "scope": "앱 전체 모듈(agents·payments·market·web·shared·config) · 렌즈 4종 · 심각도 전부",
        "sections": sections,
        "summary": _summary(sections),
    }

    out_dir = os.path.join(REPO, "docs", "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"_bugscan_{ts}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2)

    # 사람이 실행했을 때의 요약(stdout 마지막 줄은 경로 — 에이전트가 캡처)
    print("[collect_bugscan] 요약:", json.dumps(evidence["summary"], ensure_ascii=False))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
