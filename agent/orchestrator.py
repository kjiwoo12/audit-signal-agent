"""절차별 에이전트 루프와 6개 절차의 오케스트레이션.

1~5 는 서로 의존하지 않는다. 각각 독립된 대화로 돌린다.
6 은 앞선 다섯의 판단 결과를 받아 인과로 엮는다. 그래서 마지막이다.

한 절차 = 한 대화다. 여섯 절차를 한 대화에 몰아넣지 않는 이유는,
앞 절차의 결론이 뒤 절차의 판단을 오염시키기 때문이다. 기간귀속에서
"매출을 당겨 인식했다"고 결론 낸 대화에서 원가 절차를 이어 돌리면,
원가 판단이 그 결론에 맞춰 기울어진다.
"""

from __future__ import annotations

import time

import tools

from . import prompts, skills
from .toolbox import Toolbox

MAX_TURNS = 16


def _text_of(content) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


def run_procedure(
    client,
    procedure: str,
    precomputed: dict | None = None,
    prior_context: str | None = None,
    max_turns: int = MAX_TURNS,
    on_event=None,
) -> dict:
    """절차 하나를 에이전트로 수행한다."""
    skill = skills.skill_for(procedure)
    box = Toolbox(procedure, precomputed=precomputed)
    system = prompts.system_for(procedure, skill)
    messages = [
        {
            "role": "user",
            "content": prompts.kickoff_for(procedure, skill, prior_context),
        }
    ]

    def emit(kind, **kw):
        if on_event:
            on_event(procedure, kind, kw)

    emit("start", skill=skill.title)
    started = time.time()
    turns = 0
    stopped = "완료"

    for turns in range(1, max_turns + 1):
        resp = client.messages(system=system, messages=messages, tools=box.schemas())
        messages.append({"role": "assistant", "content": resp["content"]})

        uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
        if not uses:
            if resp.get("stop_reason") == "max_tokens":
                stopped = "max_tokens 로 잘림"
            break

        results = []
        for u in uses:
            emit("tool", name=u["name"], input=u["input"])
            out, is_err = box.dispatch(u["name"], u["input"])
            block = {
                "type": "tool_result",
                "tool_use_id": u["id"],
                "content": out,
            }
            if is_err:
                block["is_error"] = True
            results.append(block)
        messages.append({"role": "user", "content": results})
    else:
        # 도구 호출이 끝나지 않은 채 상한에 걸렸다. 잘린 결과임을 남긴다.
        stopped = f"턴 상한({max_turns}) 도달"

    run = {
        "procedure": procedure,
        "skill": skill.name,
        "skill_title": skill.title,
        "findings": box.findings,
        "rejections": box.rejections,
        "narrative": _text_of(messages[-1]["content"])
        if messages[-1]["role"] == "assistant"
        else "",
        "tool_calls": box.calls,
        "turns": turns,
        "stopped": stopped,
        "elapsed_sec": round(time.time() - started, 1),
        "summary": box.summary(),
    }
    emit("done", **run["summary"], stopped=stopped)
    return run


def run_all(client, procedures=None, max_turns: int = MAX_TURNS, on_event=None) -> dict:
    """6개 절차 전체. 1~5 를 돌린 뒤 그 판단 결과를 6 에 넘긴다."""
    wanted = procedures or (tools.PARALLEL + [tools.FINAL])
    parallel = [p for p in wanted if p in tools.PARALLEL]
    runs = {}

    # 계산 결과는 절차 간에 공유한다. 같은 CSV 를 여섯 번 다시 읽을 이유가 없고,
    # 6번 절차는 어차피 앞선 계산 결과를 입력으로 받아야 한다.
    computed: dict = {}

    for p in parallel:
        runs[p] = run_procedure(
            client, p, precomputed=computed, max_turns=max_turns, on_event=on_event
        )

    if tools.FINAL in wanted:
        # 6번은 앞선 다섯의 '판단'을 받는다. 계산 결과만으로는 인과를 엮을 수 없다.
        missing = [p for p in tools.PARALLEL if p not in computed]
        for p in missing:
            computed[p] = tools.PROCEDURES[p].analyze()
        prior = prompts.prior_findings_digest(runs) if runs else "(선행 절차 미실행)"
        runs[tools.FINAL] = run_procedure(
            client,
            tools.FINAL,
            precomputed=computed,
            prior_context=prior,
            max_turns=max_turns,
            on_event=on_event,
        )

    return runs


def workpaper(runs: dict, usage: dict | None = None) -> dict:
    """조서 형태로 정리한다. 렌더링은 하지 않는다 — 여기서는 구조만 만든다."""
    findings, hypotheses, rejections = [], [], []
    for run in runs.values():
        for f in run["findings"]:
            (findings if f["status"] == "발견사항" else hypotheses).append(f)
        rejections.extend(run["rejections"])

    order = {"High": 0, "Medium": 1, "Low": 2}
    findings.sort(key=lambda f: order.get(f.get("risk_grade"), 9))

    return {
        "entity": prompts.COMPANY,
        "procedures_run": list(runs),
        "findings": findings,
        "unverified_hypotheses": hypotheses,
        "rejected_candidates": rejections,
        "narratives": {k: v["narrative"] for k, v in runs.items()},
        "run_meta": {
            k: {
                "turns": v["turns"],
                "stopped": v["stopped"],
                "elapsed_sec": v["elapsed_sec"],
                "tool_calls": len(v["tool_calls"]),
            }
            for k, v in runs.items()
        },
        "usage": usage or {},
    }
