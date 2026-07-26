"""에이전트 실행 CLI.

    python -m agent.run                      6개 절차 전부
    python -m agent.run cutoff costing       일부만
    python -m agent.run --dry-run            키 없이, 무엇을 보내는지만 출력
    python -m agent.run --model claude-opus-5
    python -m agent.run --out out/           조서 JSON 저장
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import tools

from . import client as client_mod
from . import orchestrator, prompts, skills, toolbox

# Windows 콘솔 기본 코드페이지(cp949)에서 한글이 깨지는 것을 막는다.
# 키가 없을 때의 안내문은 stderr 로 나가므로 stderr 도 함께 맞춘다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ALL = tools.PARALLEL + [tools.FINAL]


def _short(v, n=70):
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def on_event(procedure, kind, kw):
    if kind == "start":
        print(f"\n── {procedure}  {kw['skill']}")
    elif kind == "tool":
        print(f"   · {kw['name']}({_short(kw['input'])})")
    elif kind == "done":
        print(
            f"   → 발견 {kw['findings']} · 미확인 {kw['hypotheses']} · "
            f"기각 {kw['rejections']} · 도구호출 {kw['tool_calls']} · {kw['stopped']}"
        )


def dry_run(procedures):
    """키 없이 프롬프트와 도구 스키마를 출력한다.

    에이전트에 무엇을 보내는지 눈으로 확인할 수 있어야 한다. 프롬프트를
    보지 않고 결과만 보면, 잘 나온 것이 설계 덕인지 우연인지 알 수 없다.
    """
    print("=" * 78)
    print("DRY RUN — API 호출 없음. 전송할 내용만 출력한다.")
    print("=" * 78)

    print(f"\n[도구 스키마] {len(toolbox.SCHEMAS)}종")
    for s in toolbox.SCHEMAS:
        req = ", ".join(s["input_schema"].get("required", []))
        print(f"  - {s['name']}  (필수: {req})")

    for p in procedures:
        skill = skills.skill_for(p)
        system = prompts.system_for(p, skill)
        kickoff = prompts.kickoff_for(p, skill)
        print("\n" + "=" * 78)
        print(f"[{p}] {skill.title}   system {len(system):,}자 / user {len(kickoff):,}자")
        print("=" * 78)
        print(system)
        print("-" * 78)
        print(kickoff)


def print_workpaper(wp):
    print("\n" + "=" * 78)
    print("조서 요약")
    print("=" * 78)

    print(f"\n발견사항 {len(wp['findings'])}건")
    for f in wp["findings"]:
        amt = f.get("impact_krw")
        amt_s = f"{amt / 1_000_000:,.1f}백만원" if isinstance(amt, int) else "산정 불가"
        print(f"\n  [{f['risk_grade']}] {f['finding']}")
        print(f"      절차 {f['procedure']} · 영향 {amt_s} · 근거등급 {f['evidence_grade']}")
        for e in f["evidence"][:4]:
            print(f"      근거 {e['dataset']}.{e['field']}={e['value']}")

    if wp["unverified_hypotheses"]:
        print(f"\n미확인 가설 {len(wp['unverified_hypotheses'])}건 (근거 대조 실패)")
        for f in wp["unverified_hypotheses"]:
            print(f"  - {f['finding'][:90]}")
            print(f"    사유: {f['downgrade_reason']}")

    print(f"\n기각한 후보 {len(wp['rejected_candidates'])}건")
    for r in wp["rejected_candidates"]:
        print(f"  - {r.get('candidate', '')[:80]}")
        print(f"    ← {r.get('rejection_condition', '')[:80]}")

    u = wp.get("usage") or {}
    if u:
        print(
            f"\n요청 {u.get('requests', 0)}회 · "
            f"입력 {u.get('input_tokens', 0):,} · 출력 {u.get('output_tokens', 0):,} 토큰"
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description="감사 절차 에이전트")
    ap.add_argument("procedures", nargs="*", choices=ALL, help="실행할 절차 (기본: 전부)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 프롬프트만 출력")
    ap.add_argument("--model", default=client_mod.DEFAULT_MODEL)
    ap.add_argument("--max-turns", type=int, default=orchestrator.MAX_TURNS)
    ap.add_argument("--out", help="조서 JSON 을 저장할 디렉터리")
    ap.add_argument("--json", action="store_true", help="조서 JSON 을 표준출력으로")
    ap.add_argument("--html", help="조서를 HTML 문서로 저장할 경로")
    args = ap.parse_args(argv)

    procedures = args.procedures or ALL

    if args.dry_run:
        dry_run(procedures)
        return 0

    try:
        cl = client_mod.Client(model=args.model)
    except client_mod.ApiKeyMissing as e:
        print(e, file=sys.stderr)
        return 2

    # --json 은 표준출력이 JSON 하나여야 파이프로 넘길 수 있다. 진행 상황은 끈다.
    print(f"모델 {args.model} · 절차 {len(procedures)}개", file=sys.stderr if args.json else sys.stdout)
    runs = orchestrator.run_all(
        cl,
        procedures=procedures,
        max_turns=args.max_turns,
        on_event=None if args.json else on_event,
    )
    wp = orchestrator.workpaper(runs, usage=cl.usage)

    if args.json:
        print(json.dumps(wp, ensure_ascii=False, indent=2, default=str))
    else:
        print_workpaper(wp)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "workpaper.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(wp, fh, ensure_ascii=False, indent=2, default=str)
        # 전사기록은 따로 남긴다. 나중에 채점 하네스가 재생해서 쓸 수 있다.
        tpath = os.path.join(args.out, "runs.json")
        with open(tpath, "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=2, default=str)
        print(f"\n저장: {path}, {tpath}")

    if args.html:
        import report

        report.write(wp, args.html)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
