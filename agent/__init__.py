"""감사 절차 Skill 을 실제 LLM 에이전트로 돌리는 오케스트레이션 계층.

    tools/   3절(결정론적 계산) — 판단하지 않는다
    skills/  절차 정의 7개 절    — 프롬프트의 원본
    agent/   4~7절(판단·기각·근거·산출) 을 LLM 에 맡기는 루프

경계는 도구 스키마로 강제한다. LLM 은 숫자를 만들 수 없고(run_procedure 로만
받는다), 근거는 코드가 원천 데이터와 대조한다(evidence.py).
"""

from . import client, evidence, orchestrator, prompts, skills, toolbox

__all__ = ["client", "evidence", "orchestrator", "prompts", "skills", "toolbox"]
