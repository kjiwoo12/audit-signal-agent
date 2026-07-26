"""감사 절차 Skill 의 3절(결정론적 계산)을 구현한 도구 모음.

각 모듈은 하나의 Skill 에 1:1 대응한다. 모든 함수는 판단 없이 사실만 낸다.
'이것이 발견사항인가'는 Skill 4·5절에서 LLM 이 정한다.
"""

from . import coherence, costing, cutoff, divergence, loader, substance, window

PROCEDURES = {
    "cutoff": cutoff,
    "substance": substance,
    "divergence": divergence,
    "window": window,
    "costing": costing,
    "coherence": coherence,
}

SKILL_OF = {
    "cutoff": "cutoff-revenue-recognition",
    "substance": "substance-over-form-sales",
    "divergence": "earnings-cash-divergence",
    "window": "period-end-window-dressing",
    "costing": "cost-driver-alignment",
    "coherence": "resource-allocation-coherence",
}

# 1~5 는 병렬 실행 가능. 6(coherence)은 앞선 결과를 받아야 하므로 마지막.
PARALLEL = ["cutoff", "substance", "divergence", "window", "costing"]
FINAL = "coherence"


def run_all():
    """1~5 를 돌리고 그 결과를 6 에 넘긴다."""
    out = {name: PROCEDURES[name].analyze() for name in PARALLEL}
    out[FINAL] = coherence.analyze(
        costing_result=out["costing"],
        cutoff_result=out["cutoff"],
        substance_result=out["substance"],
        divergence_result=out["divergence"],
        window_result=out["window"],
    )
    return out


__all__ = [
    "PROCEDURES",
    "SKILL_OF",
    "PARALLEL",
    "FINAL",
    "run_all",
    "loader",
    "cutoff",
    "substance",
    "divergence",
    "window",
    "costing",
    "coherence",
]
