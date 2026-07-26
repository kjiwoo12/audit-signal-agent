"""채점.

입력은 agent.run --out 이 남긴 runs.json 이다. 사람이 손으로 쓴 제출물도 같은
형식이면 채점된다.

채점기가 지키는 것 두 가지.

1. **애매한 것은 맞혔다고 하지 않는다.** 키워드만 물린 발견은 정답으로 세지 않고,
   정답 항목을 실제로 짚었을 때만 인용할 수 있는 앵커를 함께 요구한다.
2. **판정할 수 없는 것은 판정하지 않는다.** Level 3·4(인과 연결)는 규칙으로
   가릴 수 없다. 점수 대신 '사람이 확인할 지점'만 좁혀 준다.
"""

from __future__ import annotations

import re

from .answer_key import (
    CAUSAL_CONNECTIVES,
    LEVEL1_IDS,
    LEVEL3_TOKENS,
    LEVEL4_TOKENS,
    SIGNALS,
    TRAPS,
)

NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# 매칭
# ---------------------------------------------------------------------------


def finding_text(f: dict) -> str:
    parts = [f.get("finding", ""), f.get("impact_note", "")]
    for q in f.get("quantification") or []:
        parts.append(f"{q.get('label', '')} {q.get('value', '')}")
    return " ".join(str(p) for p in parts)


def _keyword_hit(groups, text: str) -> bool:
    """그룹마다 하나 이상 — AND of OR."""
    return all(any(alt in text for alt in group) for group in groups)


def _anchor_hit(signal, f: dict):
    """검증된 인용만 앵커로 인정한다. 지어낸 번호로 정답을 받을 수 없다."""
    for cite in f.get("evidence") or []:
        if not cite.get("verified"):
            continue
        for a in signal.anchors:
            if (
                cite.get("dataset") == a["dataset"]
                and cite.get("field") == a["field"]
                and str(cite.get("value", "")).strip() in a["values"]
            ):
                return cite
    return None


def _numbers_in(f: dict):
    """발견사항에 등장하는 금액 후보. 원 단위와 백만원 표기를 모두 시도한다."""
    out = set()
    impact = f.get("impact_krw")
    if isinstance(impact, (int, float)):
        out.add(float(impact))
    for m in NUM_RE.finditer(finding_text(f)):
        try:
            v = float(m.group().replace(",", ""))
        except ValueError:
            continue
        out.add(v)
        out.add(v * 1_000_000)  # 조서는 백만원 단위로 쓴다
    return out


def _amount_hit(signal, f: dict):
    if not signal.amounts:
        return None
    for target in signal.amounts:
        for v in _numbers_in(f):
            if target and abs(abs(v) - target) / target <= signal.amount_tolerance:
                return target
    return None


def match_signal(signal, findings: list):
    """이 정답 항목을 짚은 발견사항을 찾는다. 앵커가 있으면 앵커가 필수다."""
    for f in findings:
        if f.get("status") != "발견사항":
            continue  # 미확인 가설은 탐지로 세지 않는다
        if not _keyword_hit(signal.keywords, finding_text(f)):
            continue
        if signal.anchors and not _anchor_hit(signal, f):
            continue
        return f
    return None


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------


def score_level1_2(runs: dict):
    findings = [f for r in runs.values() for f in r.get("findings", [])]
    rows = []
    for signal in SIGNALS:
        f = match_signal(signal, findings)
        amount = _amount_hit(signal, f) if f else None
        rows.append(
            {
                "id": signal.id,
                "title": signal.title,
                "difficulty": signal.difficulty,
                "detected": f is not None,
                "quantified": amount is not None,
                "expected_amounts": signal.amounts,
                "matched_finding": f.get("finding") if f else None,
                "matched_procedure": f.get("procedure") if f else None,
                "expected_procedures": signal.expected_procedures,
                "evidence_grade": f.get("evidence_grade") if f else None,
                "note": signal.note,
            }
        )
    return rows


def _all_narrative(runs: dict) -> str:
    parts = []
    for r in runs.values():
        parts.append(r.get("narrative") or "")
        for f in r.get("findings", []):
            parts.append(finding_text(f))
    return "\n".join(parts)


def _proxy(tokens, text: str):
    present = [g for g in tokens if any(t in text for t in g)]
    connective = [c for c in CAUSAL_CONNECTIVES if c in text]
    return {
        "token_groups_present": len(present),
        "token_groups_total": len(tokens),
        "causal_connectives": connective[:5],
        "all_groups_present": len(present) == len(tokens),
    }


def score_level3_4(runs: dict):
    """점수를 매기지 않는다. 사람이 볼 지점만 좁힌다."""
    text = _all_narrative(runs)
    return {
        "level3": {
            "question": "A(회계처리 오류)와 B(현금흐름 악화)를 인과로 연결했는가",
            "proxy": _proxy(LEVEL3_TOKENS, text),
        },
        "level4": {
            "question": "C1(원가 왜곡)에서 C2(적자 제품에 자원 집중)까지 갔는가",
            "proxy": _proxy(LEVEL4_TOKENS, text),
        },
        "warning": (
            "Level 3·4 는 규칙으로 판정하지 않는다. 위 지표는 토큰 동시 출현일 뿐이며 "
            "인과를 논증했다는 뜻이 아니다. 서술을 사람이 읽고 판정할 것."
        ),
    }


def score_traps(runs: dict, matched_findings: set):
    findings = [f for r in runs.values() for f in r.get("findings", [])]
    # 기각 기록은 건별로 본다. 전부 이어 붙여서 검사하면 서로 다른 두 기각의
    # 단어가 합쳐져 하지도 않은 기각을 회피로 세게 된다.
    rej_texts = [
        f"{r.get('candidate', '')} {r.get('rejection_condition', '')} {r.get('basis', '')}"
        for run in runs.values()
        for r in run.get("rejections", [])
    ]

    rows = []
    for trap in TRAPS:
        # 정답 항목에 물린 발견은 오탐 후보에서 제외한다. 진짜를 짚은 것을
        # 함정에 빠졌다고 셀 수는 없다.
        fp = next(
            (
                f
                for f in findings
                if id(f) not in matched_findings
                and f.get("status") == "발견사항"
                and _keyword_hit(trap.keywords, finding_text(f))
            ),
            None,
        )
        avoided = any(_keyword_hit(trap.rejection_keywords, t) for t in rej_texts)
        rows.append(
            {
                "id": trap.id,
                "title": trap.title,
                "outcome": "오탐" if fp else ("회피" if avoided else "미언급"),
                "false_positive": fp.get("finding") if fp else None,
            }
        )
    return rows


def score(runs: dict) -> dict:
    l12 = score_level1_2(runs)

    findings = [f for r in runs.values() for f in r.get("findings", [])]
    matched_ids = set()
    for signal in SIGNALS:
        f = match_signal(signal, findings)
        if f is not None:
            matched_ids.add(id(f))

    level1 = [r for r in l12 if r["id"] in LEVEL1_IDS]
    detected = [r for r in level1 if r["detected"]]
    quantified = [r for r in detected if r["quantified"]]

    hypotheses = [f for f in findings if f.get("status") != "발견사항"]
    unmatched = [
        f for f in findings if f.get("status") == "발견사항" and id(f) not in matched_ids
    ]

    return {
        "level1": {
            "detected": len(detected),
            "total": len(level1),
            "rows": l12,
        },
        "level2": {
            "quantified": len(quantified),
            "of_detected": len(detected),
        },
        "level3_4": score_level3_4(runs),
        "traps": score_traps(runs, matched_ids),
        "unmatched_findings": [f.get("finding") for f in unmatched],
        "unverified_hypotheses": [f.get("finding") for f in hypotheses],
        "coverage_gaps": [
            {"id": s.id, "title": s.title, "note": s.note}
            for s in SIGNALS
            if not s.expected_procedures
        ],
        "procedure_mismatch": [
            {
                "id": r["id"],
                "found_by": r["matched_procedure"],
                "expected": r["expected_procedures"],
            }
            for r in l12
            if r["detected"]
            and r["expected_procedures"]
            and r["matched_procedure"] not in r["expected_procedures"]
        ],
    }
