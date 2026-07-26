"""채점 CLI.

    python -m agent.run --out out/          # 먼저 에이전트를 돌려 runs.json 을 만든다
    python -m scoring.run out/runs.json     # 채점
    python -m scoring.run --self-test       # 채점기가 만점을 줄 수 있는지 확인
    python -m scoring.run out/runs.json --json
"""

from __future__ import annotations

import argparse
import json
import sys

from . import score as scorer
from .selftest import perfect_submission

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

MARK = {True: "O", False: "X"}


def render(result: dict) -> None:
    l1 = result["level1"]
    l2 = result["level2"]

    print("=" * 74)
    print("채점 결과")
    print("=" * 74)

    print(f"\nLevel 1  개별 항목 탐지   {l1['detected']} / {l1['total']}")
    print(f"Level 2  금액 정량화      {l2['quantified']} / {l2['of_detected']} (탐지된 것 중)")

    print("\n  항목  난이도  탐지  정량  근거  절차")
    print("  " + "-" * 66)
    for r in l1["rows"]:
        print(
            f"  {r['id']:<4}  {r['difficulty']:^4}   {MARK[r['detected']]}    "
            f"{MARK[r['quantified']]}    {r['evidence_grade'] or '-':^3}   "
            f"{r['matched_procedure'] or '-':<10} {r['title']}"
        )

    if result["procedure_mismatch"]:
        print("\n  다른 절차에서 잡힌 항목 (절차 배치를 다시 볼 것)")
        for m in result["procedure_mismatch"]:
            print(f"    {m['id']}: {m['found_by']} 에서 잡힘, 예상은 {m['expected']}")

    print("\n오탐 함정")
    for t in result["traps"]:
        print(f"  [{t['outcome']}] {t['id']} {t['title']}")
        if t["false_positive"]:
            print(f"        → {t['false_positive'][:70]}")

    if result["unmatched_findings"]:
        print(f"\n정답지에 없는 발견 {len(result['unmatched_findings'])}건")
        print("  (오탐일 수도, 정답지가 놓친 것일 수도 있다. 사람이 볼 것)")
        for f in result["unmatched_findings"]:
            print(f"  - {f[:80]}")

    if result["unverified_hypotheses"]:
        print(f"\n근거 대조 실패로 격하된 것 {len(result['unverified_hypotheses'])}건")
        for f in result["unverified_hypotheses"]:
            print(f"  - {f[:80]}")

    if result["coverage_gaps"]:
        print("\n절차가 커버하지 않는 정답 항목")
        for g in result["coverage_gaps"]:
            print(f"  - {g['id']} {g['title']}")
            print(f"    {g['note']}")

    l34 = result["level3_4"]
    print("\nLevel 3 · 4  인과 연결 — 점수 없음")
    for k in ("level3", "level4"):
        p = l34[k]["proxy"]
        print(f"  {k}: {l34[k]['question']}")
        print(
            f"    토큰군 {p['token_groups_present']}/{p['token_groups_total']} 출현"
            f" · 인과 접속어 {p['causal_connectives'] or '없음'}"
        )
    print(f"  {l34['warning']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="정답지 기준 채점")
    ap.add_argument("runs", nargs="?", help="agent.run --out 이 남긴 runs.json")
    ap.add_argument("--self-test", action="store_true", help="만점 가능한지 확인")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        runs = perfect_submission()
        print("SELF TEST — 정답지대로 작성한 제출물을 채점한다.")
        print("채점기가 만점을 줄 수 없다면 그 채점기로는 아무것도 측정할 수 없다.\n")
    elif args.runs:
        with open(args.runs, encoding="utf-8") as fh:
            runs = json.load(fh)
    else:
        ap.error("runs.json 경로 또는 --self-test 가 필요하다")

    result = scorer.score(runs)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        render(result)

    if args.self_test:
        l1 = result["level1"]
        ok = l1["detected"] == l1["total"]
        print(f"\n결과: {'통과' if ok else '실패'} — 만점 {l1['detected']}/{l1['total']}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
