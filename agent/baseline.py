"""LLM 없이 규칙만으로 판단하는 대조군.

`orchestrator.py` 와 같은 계산 결과를 받아 같은 조서 형식으로 제출하지만,
판단하는 주체가 LLM 이 아니라 아래 임계값 표다. API 키도 네트워크도 쓰지 않는다.

**왜 만드는가.** 이 저장소는 "Level 3·4(인과 연결) 때문에 LLM 이 필요하다"고
주장한다. 주장은 측정으로 뒷받침되어야 한다. 규칙으로 어디까지 가는지 먼저 찍어
두면, LLM 실행 성적이 나왔을 때 그 차이가 LLM 이 만든 몫이 된다. 대조군이 없으면
LLM 점수 하나만 놓고 "잘 나왔다"고 말하게 되고, 그 숫자로는 아무것도 알 수 없다.

**세 가지를 의도적으로 하지 않는다.** 규칙 기반의 한계가 드러나야 대조가 성립한다.

1. 기각하지 않는다. `rejection_checks` 는 비어 있고 `emit_rejection` 을 쓰지 않는다.
   임계값 아래로 걸러진 것과 검토 후 기각한 것은 다르다. 전자는 조서에 아무 기록도
   남기지 않는다.
2. 서술하지 않는다. `narrative` 는 빈 문자열이다. 개별 항목을 나열할 수는 있지만
   A 와 B 를 인과로 엮는 문장은 규칙으로 만들 수 없다.
3. 임계값을 튜닝하지 않는다. 점수를 올리려고 임계값을 만지면 대조군이 아니라
   이 데이터셋 전용 정답표가 된다.

**정직하게 밝힐 한계.** 아래 임계값은 이 데이터셋을 본 사람이 정했다. 다른 회사의
자료에 그대로 적용되지 않는다. 임계값 없이 판단하는 것이 LLM 에 기대하는 몫이다.
"""

from __future__ import annotations

import time

import tools

from . import skills
from .toolbox import Toolbox

# 판정 임계값. 코드 안에 숨기지 않고 한곳에 모아 둔다 — 왜 그 건이 걸렸는지
# 설명할 수 없는 임계값은 조서에 쓸 수 없다.
THRESHOLDS = {
    "return_rate_multiple": 2.0,  # 전체 평균 반품률의 몇 배부터 보고할지
    "divergence_pp": 50.0,  # 이익-현금 괴리 %p
}


def _won(v) -> str:
    return f"{int(v):,}원"


# ---------------------------------------------------------------------------
# 규칙. 하나의 규칙 = 하나의 조건 + 하나의 제출.
# 특정 전표번호를 조건으로 쓰는 규칙은 없다. 그렇게 쓰면 규칙이 아니라 정답 복사다.
# ---------------------------------------------------------------------------


def _cutoff(r: dict, emit) -> None:
    c = r["cutoff_candidates"]
    if c["count"] <= 0:
        return
    emit(
        rule="R1 인식일 <= 보고기간말 < 통제이전일 인 송장이 존재",
        finding=(
            f"인도조건상 통제이전일이 보고기간 후인데 매출을 보고기간 안에 인식한 "
            f"송장 {c['count']}건, {_won(c['amount_krw'])}. 매출 기간귀속 오류로 "
            f"당기 매출과 이익이 조기 인식되었다."
        ),
        risk_grade="High",
        impact_krw=c["pretax_impact_krw"],
        impact_note=(
            f"매출 {_won(c['amount_krw'])} − 대응 매출원가 "
            f"{_won(c['estimated_cogs_krw'])} ({c['cogs_basis']})"
        ),
        quantification=[
            {"label": "조기인식 매출액", "value": _won(c["amount_krw"])},
            {"label": "보고기간 매출 대비", "value": f"{c['pct_of_period_revenue']}%"},
            {"label": "세전이익 영향", "value": _won(c["pretax_impact_krw"])},
        ],
        evidence=[
            {
                "dataset": "sales_invoices",
                "field": "invoice_no",
                "value": i["invoice_no"],
                "quote": i["incoterms"],
            }
            for i in c["items"][:3]
        ],
    )


def _substance(r: dict, emit) -> None:
    overall = r["overall"]["return_rate_pct"]
    hits = [
        c
        for c in r["by_customer"]
        if c["return_rate_vs_overall"] >= THRESHOLDS["return_rate_multiple"]
        and c["returned_after_period_end_krw"] > 0
    ]
    if not hits:
        return

    notes = r["credit_notes_by_customer"]
    total_returned = sum(c["returned_after_period_end_krw"] for c in hits)
    names = ", ".join(f"{c['customer_name']} {c['return_rate_pct']}%" for c in hits)

    emit(
        rule=(
            f"R2 반품률이 전체 평균의 {THRESHOLDS['return_rate_multiple']}배 이상이고 "
            f"반품이 보고기간 후에 확정된 거래처"
        ),
        finding=(
            f"거래처 {len(hits)}곳의 반품률이 전체 평균 {overall}% 를 크게 넘고 "
            f"({names}), 반품이 전액 보고기간 후에 확정되었다. 계약형태가 "
            f"'{hits[0]['contract_type']}' 이고 기말월 출고 집중도가 "
            f"{hits[0]['period_end_month_share_pct']}% 여서, 형식은 매출이지만 실질은 "
            f"반품조건부 또는 위탁 거래일 수 있다. 매출 인식 요건 충족 여부를 확인해야 한다."
        ),
        risk_grade="High",
        impact_krw=total_returned,
        impact_note=f"보고기간 후 확정 반품액 합계 {_won(total_returned)}",
        quantification=[
            {"label": "대상 거래처", "value": f"{len(hits)}곳"},
            {"label": "보고기간 후 확정 반품액", "value": _won(total_returned)},
            {"label": "전체 평균 반품률", "value": f"{overall}%"},
        ]
        + [
            {"label": f"{c['customer_name']} 반품률", "value": f"{c['return_rate_pct']}%"}
            for c in hits
        ],
        evidence=[
            {
                "dataset": "credit_notes",
                "field": "customer_code",
                "value": c["customer_code"],
                "quote": (notes.get(c["customer_code"]) or [{}])[0].get("reason", ""),
            }
            for c in hits
        ],
    )


def _divergence(r: dict, emit) -> None:
    d = r["divergence"]
    if d["value_pp"] < THRESHOLDS["divergence_pp"] or not d["opposite_direction"]:
        return

    p = r["profitability_vs_cash"]
    t = r["turnover"]
    rows = [w for w in r["working_capital_attribution"]["rows"] if w["cash_effect_krw"] < 0]
    worst = min(rows, key=lambda w: w["cash_effect_krw"])

    emit(
        rule=f"R3 이익 증감률과 현금흐름 증감률이 반대이고 괴리가 {THRESHOLDS['divergence_pp']}%p 이상",
        finding=(
            f"영업이익은 {p['영업이익']['growth_pct']}% 증가했으나 영업활동현금흐름은 "
            f"{abs(p['영업활동현금흐름']['growth_pct'])}% 감소했다 (괴리 {d['value_pp']}%p). "
            f"현금전환율이 {d['cash_conversion_prior_pct']}% 에서 "
            f"{d['cash_conversion_current_pct']}% 로 떨어지고 DSO 가 "
            f"{t['dso_delta_days']}일 늘었다. 이익-현금 괴리의 차액은 주로 "
            f"{worst['account_name']}({_won(worst['delta_krw'])} 증가, "
            f"{worst['growth_pct']}%)에 쌓여 있다. 이익의 질이 악화되었다."
        ),
        risk_grade="High",
        impact_krw=0,
        impact_note="손익 자체의 오류가 아니라 이익의 질 지표다. 세전이익 영향금액으로 환산하지 않는다.",
        quantification=[
            {"label": "이익-현금 괴리", "value": f"{d['value_pp']}%p"},
            {"label": "현금전환율", "value": f"{d['cash_conversion_prior_pct']}% → {d['cash_conversion_current_pct']}%"},
            {"label": "DSO 증가", "value": f"{t['dso_delta_days']}일"},
            {"label": "매출채권 증가율 / 매출 증가율", "value": f"{t['ar_growth_vs_revenue_growth_multiple']}배"},
            {"label": f"{worst['account_name']} 증가", "value": _won(worst["delta_krw"])},
        ],
        evidence=[
            {"dataset": "financial_summary", "field": "item", "value": "영업활동현금흐름"},
            {"dataset": "trial_balance", "field": "account_code", "value": worst["account_code"]},
        ],
    )


def _window(r: dict, emit) -> None:
    eff = r["balance_sheet_effect"]
    for p in r["matched_pairs"]:
        ev = [
            {
                "dataset": "bank_transactions",
                "field": "txn_date",
                "value": p["open_date"],
                "quote": p["open_description"],
            }
        ]
        if p.get("open_voucher_no"):
            # 전표 적요는 계산 결과에 없다. 없는 인용은 붙이지 않는다 (근거등급 B).
            ev.append(
                {"dataset": "gl_journal", "field": "voucher_no", "value": p["open_voucher_no"]}
            )

        emit(
            rule="R4 보고기간 말 전후에 금액이 일치하고 방향이 반대인 거래쌍이 존재",
            finding=(
                f"보고기간 말 직전 {p['open_date']} 에 {_won(p['open_amount_krw'])}을 "
                f"차입하고 {p['close_date']} 에 동일 금액을 상환했다. 보유기간 "
                f"{p['holding_biz_days']}영업일. 기말 현금및현금성자산과 단기차입금이 "
                f"각각 그만큼 과대 표시되어 재무상태표 외형이 보정되었다. "
                f"손익 영향은 없다."
            ),
            risk_grade="Medium",
            impact_krw=0,
            impact_note=eff["note"],
            quantification=[
                {"label": "차입 후 상환 금액", "value": _won(p["open_amount_krw"])},
                {"label": "보유기간", "value": f"{p['holding_biz_days']}영업일"},
                {
                    "label": "현금및현금성자산 조정",
                    "value": f"{_won(eff['현금및현금성자산']['book_krw'])} → {_won(eff['현금및현금성자산']['adjusted_krw'])}",
                },
                {
                    "label": "부채비율 조정",
                    "value": f"{eff['부채비율_pct']['book']}% → {eff['부채비율_pct']['adjusted']}%",
                },
            ],
            evidence=ev,
        )


def _costing(r: dict, emit) -> None:
    flipped = set(r["sign_flip"])
    basis = r["current_basis"]["name"]
    notes = r["cost_system_notes"]

    for prod in r["profitability"]:
        if prod["product_code"] not in flipped:
            continue  # 순위만 바뀐 제품은 보고하지 않는다. 부호가 뒤집힌 것만 본다.

        ev = [
            {
                "dataset": "production_cost",
                "field": "product_code",
                "value": prod["product_code"],
                "quote": basis,
            }
        ]
        if notes:
            ev.append(
                {
                    "dataset": "cost_system_notes",
                    "field": "note_date",
                    "value": notes[0]["note_date"],
                    "quote": notes[0]["note"],
                }
            )

        emit(
            rule="R5 배부기준을 활동기준으로 바꾸면 제품 매출총이익의 부호가 바뀜",
            finding=(
                f"제조간접비를 {basis} 기준으로 일괄 배부하고 있으나 활동별 원가동인은 "
                f"기계시간·셋업횟수 등으로 갈린다. 활동기준으로 재배부하면 "
                f"{prod['product_name']}({prod['product_code']})의 매출총이익률이 "
                f"{prod['current_gp_pct']}% 에서 {prod['abc_gp_pct']}% 로 부호가 뒤집힌다 "
                f"(간접비 배부액 차이 {_won(prod['oh_delta_krw'])}). 원가동인과 배부기준이 "
                f"맞지 않아 제품별 수익성이 왜곡되어 있다."
            ),
            risk_grade="High",
            impact_krw=r["financial_statement_impact"]["amount_krw"],
            impact_note=r["financial_statement_impact"]["reason"],
            quantification=[
                {"label": "간접비 배부액 차이", "value": _won(prod["oh_delta_krw"])},
                {
                    "label": "매출총이익률",
                    "value": f"{prod['current_gp_pct']}% → {prod['abc_gp_pct']}%",
                },
                {
                    "label": "수익성 순위",
                    "value": f"{prod['current_gp_rank']}위 → {prod['abc_gp_rank']}위",
                },
                {"label": "제조간접비 총액", "value": _won(r["overhead"]["total_krw"])},
            ],
            evidence=ev,
        )


def _coherence(r: dict, emit) -> None:
    cap = r["capitalizable_expensed"]
    if cap["count"] > 0:
        emit(
            rule="R6 발주 비고가 자산 요건을 기술하는데 비용 계정에 계상됨",
            finding=(
                f"내용연수·생산능력 증가를 비고에 기술한 발주 {cap['count']}건, "
                f"{_won(cap['amount_krw'])}이 수선비·소모품비 등 비용 계정으로 "
                f"계상되어 있다. 자본적지출을 수익적지출로 처리해 당기 비용이 "
                f"과대, 자산이 과소 계상되었을 수 있다."
            ),
            risk_grade="Medium",
            impact_krw=cap["amount_krw"],
            impact_note="비용 처리액 전액. 자본화 요건 충족 여부는 확인이 필요하다.",
            quantification=[
                {"label": "비용 계상된 발주", "value": f"{cap['count']}건"},
                {"label": "금액", "value": _won(cap["amount_krw"])},
            ],
            evidence=[
                {
                    "dataset": "purchase_orders",
                    "field": "po_no",
                    "value": i["po_no"],
                    "quote": i["remark"],
                }
                for i in cap["items"]
            ],
        )

    misaligned = set(r["rank_misalignment"])
    detail = r["investment_detail"]
    for a in r["resource_allocation"]:
        if a["product_code"] not in misaligned:
            continue

        ev = []
        promo = (detail["promotion_by_product"].get(a["product_code"]) or [])[:1]
        capex = (detail["capex_by_product"].get(a["product_code"]) or [])[:1]
        for x in promo:
            ev.append(
                {
                    "dataset": "gl_journal",
                    "field": "voucher_no",
                    "value": x["voucher_no"],
                    "quote": x["description"],
                }
            )
        for x in capex:
            ev.append(
                {
                    "dataset": "bank_transactions",
                    "field": "txn_date",
                    "value": x["txn_date"],
                    "quote": x["description"],
                }
            )

        emit(
            rule="R7 활동기준 수익성 순위와 투자 집중 순위가 어긋남",
            finding=(
                f"{a['product_name']}({a['product_code']})은 활동기준 수익성이 "
                f"{a['abc_profitability_rank']}위(매출총이익률 {a['abc_gp_pct']}%)인데 "
                f"투자·판촉 집중도는 {a['investment_rank']}위다. 이 제품에 투입된 "
                f"설비투자는 {_won(a['capex_krw'])}, 판촉비는 {_won(a['promotion_krw'])}이며 "
                f"식별된 투입액 합계 {_won(a['total_identified_investment_krw'])}은 "
                f"매출의 {a['investment_to_revenue_pct']}% 에 해당한다. "
                f"왜곡된 원가 정보 위에서 자원배분이 이루어지고 있다."
            ),
            risk_grade="Medium",
            impact_krw=0,
            impact_note="회계처리 오류가 아니라 경영 의사결정의 문제다. 재무제표 수정 대상이 아니다.",
            quantification=[
                {"label": "설비투자", "value": _won(a["capex_krw"])},
                {"label": "판촉비", "value": _won(a["promotion_krw"])},
                {"label": "투자액 / 매출", "value": f"{a['investment_to_revenue_pct']}%"},
                {
                    "label": "수익성 순위 vs 투자 순위",
                    "value": f"{a['abc_profitability_rank']}위 vs {a['investment_rank']}위",
                },
            ],
            evidence=ev,
        )


RULES = {
    "cutoff": _cutoff,
    "substance": _substance,
    "divergence": _divergence,
    "window": _window,
    "costing": _costing,
    "coherence": _coherence,
}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def run_procedure(procedure: str, precomputed: dict | None = None, on_event=None) -> dict:
    """절차 하나를 규칙으로 수행한다. `orchestrator.run_procedure` 와 같은 형식을 낸다."""
    skill = skills.skill_for(procedure)
    box = Toolbox(procedure, precomputed=precomputed)
    started = time.time()

    def emit(rule: str, **payload) -> None:
        # 규칙은 기각 조건을 검토하지 않는다. 비어 있는 것이 사실이므로 비워 둔다.
        payload.setdefault("rejection_checks", [])
        payload.setdefault("follow_up", [])
        payload.setdefault("questions_for_management", [])
        payload["rule"] = rule
        out, is_err = box.dispatch("emit_finding", payload)
        if on_event:
            on_event(procedure, "finding", {"rule": rule, "result": out, "is_error": is_err})

    if on_event:
        on_event(procedure, "start", {"skill": skill.title})

    out, is_err = box.dispatch("run_procedure", {"procedure": procedure})
    if is_err:
        raise RuntimeError(f"{procedure} 계산 실패: {out}")
    RULES[procedure](box.precomputed[procedure], emit)

    run = {
        "procedure": procedure,
        "skill": skill.name,
        "skill_title": skill.title,
        "findings": box.findings,
        "rejections": box.rejections,
        # 규칙은 서술하지 않는다. 개별 항목은 나열할 수 있어도 인과로 엮을 수 없다.
        "narrative": "",
        "tool_calls": box.calls,
        "turns": 0,
        "stopped": "규칙 실행 완료 (LLM 미사용)",
        "elapsed_sec": round(time.time() - started, 2),
        "summary": box.summary(),
    }
    if on_event:
        on_event(procedure, "done", dict(run["summary"], stopped=run["stopped"]))
    return run


def run_all(procedures=None, on_event=None) -> dict:
    """6개 절차 전체. 계산 결과는 절차 간에 공유한다."""
    wanted = procedures or (tools.PARALLEL + [tools.FINAL])
    computed: dict = {}
    runs = {}

    for p in [x for x in wanted if x in tools.PARALLEL]:
        runs[p] = run_procedure(p, precomputed=computed, on_event=on_event)

    if tools.FINAL in wanted:
        for p in tools.PARALLEL:
            if p not in computed:
                computed[p] = tools.PROCEDURES[p].analyze()
        runs[tools.FINAL] = run_procedure(
            tools.FINAL, precomputed=computed, on_event=on_event
        )

    return runs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    """python -m agent.baseline [절차...] [--out DIR] [--json]"""
    import argparse
    import json
    import os

    from . import orchestrator
    from .run import ALL, on_event, print_workpaper  # stdout 인코딩 설정도 함께 온다

    ap = argparse.ArgumentParser(
        description="규칙 기반 대조군 — LLM·API 키·네트워크를 쓰지 않는다"
    )
    # choices= 를 쓰지 않는다. 위치인자에 nargs="*" 와 choices 를 함께 주면 인자를
    # 생략했을 때 기본값 [] 자체가 choices 검사에 걸려 3.12 미만에서 죽는다.
    # 목록은 metavar 로 보여주고 검사는 아래에서 직접 한다.
    ap.add_argument(
        "procedures",
        nargs="*",
        metavar="{%s}" % ",".join(ALL),
        help="실행할 절차 (기본: 전부)",
    )
    ap.add_argument("--out", help="조서 JSON 을 저장할 디렉터리")
    ap.add_argument("--json", action="store_true", help="조서 JSON 을 표준출력으로")
    ap.add_argument("--html", help="조서를 HTML 문서로 저장할 경로")
    args = ap.parse_args(argv)

    unknown = [p for p in args.procedures if p not in ALL]
    if unknown:
        ap.error("알 수 없는 절차: %s (가능: %s)" % (", ".join(unknown), ", ".join(ALL)))

    procedures = args.procedures or ALL

    # --json 은 표준출력이 JSON 하나여야 파이프로 넘길 수 있다. 진행 상황은 끈다.
    if not args.json:
        print("규칙 기반 대조군 (LLM 미사용) · 절차 %d개" % len(procedures))
        print("임계값: " + ", ".join(f"{k}={v}" for k, v in THRESHOLDS.items()))

    def events(procedure, kind, kw):
        if kind == "finding":
            print(f"   · {kw['rule']}")
        else:
            on_event(procedure, kind, kw)

    runs = run_all(procedures=procedures, on_event=None if args.json else events)
    wp = orchestrator.workpaper(runs)
    wp["engine"] = "규칙 기반 대조군 (LLM 미사용)"
    wp["thresholds"] = dict(THRESHOLDS)

    if args.json:
        print(json.dumps(wp, ensure_ascii=False, indent=2, default=str))
    else:
        print_workpaper(wp)
        print(
            "\n기각 0건 · 서술 없음은 버그가 아니라 이 대조군의 설계다.\n"
            "규칙은 임계값 아래를 조용히 버리고, 개별 항목을 인과로 엮지 않는다."
        )

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for name, obj in (("workpaper.json", wp), ("runs.json", runs)):
            path = os.path.join(args.out, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
            print(f"저장: {path}")

    if args.html:
        import report

        report.write(wp, args.html)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
