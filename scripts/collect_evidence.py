"""심사 부서 증거수집기 — 저장소 실물 사실을 JSON 스냅샷으로 덤프.

LLM 없음. 추정 없음. 오직 실행 결과·파일 내용·git 상태만 모은다.
심사 부서(워크플로우 judge-dept)가 이 JSON 을 근거로 심사 4축을 평가한다.
에이전트가 기억이 아니라 이 실물 수치에 붙게 만드는 게 목적이다.

설계 원칙:
  - 각 섹션은 독립. 하나가 실패해도 나머지는 수집하고, 실패는
    {"status":"error","reason":...} 로 남긴다(조용히 넘어가지 않는다).
  - 숫자는 전부 재현 가능한 출처에서 온다: 테스트 종료코드, red_team KPI 라인,
    artifacts JSON, git, git grep(추적 파일만 — secrets/·.venv 는 자동 제외).

실행:
  .venv/Scripts/python.exe -m scripts.collect_evidence
  → docs/reports/_evidence_YYYYMMDD_HHMMSS.json 생성, 경로를 stdout 마지막 줄에 출력.
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
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pyexe() -> str:
    """저장소 venv 파이썬을 우선 사용(3.10 고정). 없으면 현재 인터프리터."""
    cand = os.path.join(REPO, ".venv", "Scripts", "python.exe")
    return cand if os.path.exists(cand) else sys.executable


PYEXE = _pyexe()

# 자식 프로세스가 Windows 기본(cp949)이 아니라 utf-8 로 출력하도록 강제.
# 이게 없으면 부모가 utf-8 로 디코딩할 때 한국어 출력(red_team KPI 등)이 깨져
# 정규식 파싱이 실패한다.
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


# ---------------------------------------------------------------- git 상태

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


# ---------------------------------------------------------------- red_team KPI

def _parse_kpi(out: str) -> dict:
    """red_team 출력에서 KPI 를 뽑는다. 기계 판독용 [KPI-JSON] 줄을 우선한다.

    예전에는 사람이 읽는 '[KPI] 시도 N · 차단 N · 유출 …' 문구를 정규식으로 긁었는데,
    2026-07-27 에 그 문구를 계층별로 나누자 정규식이 조용히 빈 객체를 돌려줬다
    (rc==0 이라 ok 는 계속 true → 심사 부서가 근거 없이 도는 무증상 회귀).
    이제 표시 문구와 판독 형식을 분리하고, 옛 형식도 폴백으로 남긴다."""
    m = re.search(r"\[KPI-JSON\]\s*(\{.*\})", out)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(   # 폴백: 옛 한 줄 형식
        r"\[KPI\]\s*시도\s*(\d+)\s*·\s*차단\s*(\d+)\s*·\s*유출\s*([\d.]+)\s*USDC\s*·\s*오탐\s*(\d+)",
        out,
    )
    if m:
        return {"attempts": int(m.group(1)), "blocked": int(m.group(2)),
                "leak_usdc": m.group(3), "false_positives": int(m.group(4))}
    return {}


def collect_red_team() -> dict:
    r = _run([PYEXE, "-m", "scripts.red_team", "--report"], timeout=180)
    out = r.get("stdout") or ""
    kpi = _parse_kpi(out)
    return {
        "rc": r.get("rc"),
        "ok": r.get("rc") == 0,
        "kpi": kpi,
        "error": r.get("error"),
        "raw_tail": "\n".join(out.strip().splitlines()[-6:]) if out.strip() else "",
    }


# ---------------------------------------------------------------- 온체인 증빙(artifacts/tx)

def collect_tx_artifacts() -> dict:
    d = os.path.join(REPO, "artifacts", "tx")
    files = sorted(glob.glob(os.path.join(glob.escape(d), "*")))
    by_network: dict = {}
    items = []
    for f in files:
        base = os.path.basename(f)
        entry: dict = {"file": base}
        if f.endswith(".json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                net = data.get("network", "?")
                entry["network"] = net
                trades = data.get("trades") or []
                entry["trades"] = len(trades)
                cc = data.get("cross_check") or {}
                entry["cross_check"] = (
                    {"usdc_ok": cc.get("usdc_ok"), "stock_ok": cc.get("stock_ok")}
                    if cc else None
                )
                if trades and trades[0].get("explorer_payment"):
                    entry["sample_explorer"] = trades[0]["explorer_payment"]
                by_network[net] = by_network.get(net, 0) + 1
            except Exception as e:  # noqa: BLE001
                entry["error"] = f"{type(e).__name__}: {e}"
        else:
            entry["type"] = "non-json"
        items.append(entry)
    return {
        "count": len(files),
        "by_network": by_network,
        "devnet_present": by_network.get("solana-devnet", 0) > 0,
        "items": items,
    }


# ---------------------------------------------------------------- 백테스트(artifacts/backtests)

def _symbol_from_name(base: str) -> str:
    parts = base.split("_")
    return parts[2] if len(parts) >= 3 else "?"


def collect_backtests() -> dict:
    d = os.path.join(REPO, "artifacts", "backtests")
    files = sorted(glob.glob(os.path.join(glob.escape(d), "*.json")))
    items = []
    for f in files:
        base = os.path.basename(f)
        entry: dict = {"file": base}
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            cfg = data.get("config") or {}
            met = data.get("metrics") or {}
            entry.update({
                "symbol": _symbol_from_name(base),
                "brain": cfg.get("brain"),
                "ta_mode": cfg.get("ta_mode"),
                "bars": cfg.get("bars_played"),
                "return_pct": met.get("return_on_budget_pct"),
                "benchmark_pct": met.get("benchmark_buyhold_pct"),
                "excess_pct": met.get("excess_return_pct"),
                "mdd_pct": met.get("max_drawdown_pct"),
                "win_rate_pct": met.get("win_rate_pct"),
                "exposure_pct": met.get("exposure_pct"),
                "gemini_fallbacks": met.get("gemini_fallbacks"),
                "decisions_by_source": met.get("decisions_by_source"),
            })
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"
        items.append(entry)
    return {"count": len(files), "items": items}


# ---------------------------------------------------------------- 코드 규모

def collect_code_size() -> dict:
    dirs = ["agents", "payments", "market", "web", "shared", "scripts"]
    out: dict = {}
    total = 0
    for dd in dirs:
        n = 0
        for root, _, fnames in os.walk(os.path.join(REPO, dd)):
            if "__pycache__" in root:
                continue
            for fn in fnames:
                if fn.endswith(".py"):
                    try:
                        with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as fh:
                            n += sum(1 for line in fh if line.strip())
                    except Exception:  # noqa: BLE001
                        pass
        out[dd] = n
        total += n
    out["total_nonblank"] = total
    return out


# ---------------------------------------------------------------- 402 Guard 구현 마커(git grep)

def _git_grep(pattern: str, paths=None, fixed: bool = True, ignore_case: bool = False) -> dict:
    """추적 파일에서 pattern 을 찾아 file:line 매치를 반환. secrets/·.venv 는 자동 제외.

    git grep 은 매치 없으면 rc=1(정상), 오류면 rc>=2.
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
    return {"count": len(lines), "matches": lines[:8]}


def collect_guard_markers() -> dict:
    """차별화(differentiation.md)가 주장하는 구현/미구현을 실물 grep 으로 대조.

    http_402_in_code 는 docs·README 예시가 아니라 실제 코드(web/agents/payments)만
    본다. count 가 0 이면 'HTTP 402 서비스 미구현(G5)'이 사실로 확정되고,
    심사 부서가 이걸 정직하게 갭으로 보고하게 만든다.
    """
    return {
        "guard_module_exists": os.path.exists(os.path.join(REPO, "payments", "guard.py")),
        "check_demand": _git_grep("def check_demand", ["payments/guard.py"]),
        "check_delivery": _git_grep("def check_delivery", ["payments/guard.py"]),
        "guard_block_codes": _git_grep("GUARD_", ["payments/guard.py"]),
        "allowed_asset_in_ap2": _git_grep("allowed_asset", ["payments/ap2_mandate.py"]),
        "exact_amount_check": _git_grep("expected_amount", ["payments/x402_solana.py"]),
        "memo_binding_AT1": _git_grep("AT1:", ["payments/", "agents/", "web/"]),
        # 코드에 실제 HTTP 402 를 내보내는 곳(docs/README 예시 제외 — 소스만).
        "http_402_in_code": _git_grep("402 Payment Required", ["web/", "agents/", "payments/"]),
        "http_402_status_code": _git_grep("status_code=402", ["web/", "agents/", "payments/"], fixed=False),
        "user_key_separation": _git_grep("user_keypair_json", ["config.py"]),
        "gemini_decider": _git_grep("gemini", ["agents/gemini_decider.py"], fixed=False, ignore_case=True),
    }


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


def _summary(sec: dict) -> dict:
    """섹션에서 결정론적으로 유도한 빠른 지표(에이전트 그라운딩용). 창작 없음."""
    s: dict = {}
    t = sec.get("tests")
    if isinstance(t, dict):
        s["tests_count"] = t.get("count")
        s["tests_all_pass"] = t.get("all_pass")
    rt = sec.get("red_team")
    if isinstance(rt, dict):
        s["red_team_ok"] = rt.get("ok")
        s["red_team_kpi"] = rt.get("kpi")
    tx = sec.get("tx_artifacts")
    if isinstance(tx, dict):
        s["tx_by_network"] = tx.get("by_network")
        s["devnet_evidence_present"] = tx.get("devnet_present")
    gm = sec.get("guard_markers")
    if isinstance(gm, dict):
        h402 = gm.get("http_402_in_code") or {}
        h402c = gm.get("http_402_status_code") or {}
        hits = 0
        if isinstance(h402, dict):
            hits += h402.get("count", 0) or 0
        if isinstance(h402c, dict):
            hits += h402c.get("count", 0) or 0
        s["http_402_code_hits"] = hits  # 0 = HTTP 402 서비스 미구현(사실)
    return s


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sections = {
        "git": _safe(collect_git),
        "tests": _safe(collect_tests),
        "red_team": _safe(collect_red_team),
        "tx_artifacts": _safe(collect_tx_artifacts),
        "backtests": _safe(collect_backtests),
        "code_size": _safe(collect_code_size),
        "guard_markers": _safe(collect_guard_markers),
    }
    evidence = {
        "schema": "collect_evidence/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pyexe": PYEXE,
        "note": "심사 부서(judge-dept) 근거 스냅샷 — 모든 값은 실행/파일/git 유래. 추정 금지.",
        "sections": sections,
        "summary": _summary(sections),
    }

    out_dir = os.path.join(REPO, "docs", "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"_evidence_{ts}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2)

    # 사람이 실행했을 때의 요약(stdout 마지막 줄은 경로 — 에이전트가 캡처)
    sm = evidence["summary"]
    print("[collect_evidence] 요약:", json.dumps(sm, ensure_ascii=False))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
