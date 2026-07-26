"""거래의 실질 검토 — substance-over-form-sales.md 3절의 구현.

거래처별로 출하액·반품액·반품률·기말집중도·채권잔액·계약형태·특수관계를 한 줄에 모은다.
어떤 조합이 위탁판매의 징후인지는 Skill 4절이 정한다. 여기서는 세기만 한다.
"""

from __future__ import annotations

import collections
import re

from .loader import (
    PERIOD_END,
    PERIOD_START,
    as_date,
    as_int,
    iso,
    load,
    pct,
    ratio,
)


def _core_name(name: str) -> str:
    """상호에서 법인격·부기 표기를 떼고 식별에 쓸 핵심 토큰만 남긴다.

    '(주)한빛홀딩스' → '한빛홀딩스',  '우성모터스(대리점)' → '우성모터스',
    '한빛정밀(주) 대표이사' → '한빛정밀'
    """
    stripped = re.sub(r"\([^)]*\)", " ", name)
    stripped = stripped.replace("㈜", " ").replace("주식회사", " ")
    tokens = [t for t in stripped.split() if t]
    return tokens[0] if tokens else ""


def _related_party_match(customer_name: str, parties: list[dict]):
    """거래처명과 특수관계자 명세를 부분일치로 대조한다.

    실무에서 이 조인은 깔끔한 FK가 아니라 문자열 포함관계다. 법인격 표기
    ('(주)', '(대리점)')가 양쪽에서 다르게 붙기 때문이다.

    다만 부분일치를 느슨하게 두면 전 거래처가 특수관계자로 잡힌다. 괄호 안을
    먼저 제거하고 핵심 토큰끼리만 비교하며, 2글자 미만 토큰은 대조하지 않는다.
    """
    core = _core_name(customer_name)
    if len(core) < 2:
        return []
    hits = []
    for p in parties:
        pcore = _core_name(p["party_name"])
        if len(pcore) < 2:
            continue
        if core == pcore or core in pcore or pcore in core:
            hits.append(
                {
                    "party_name": p["party_name"],
                    "relation": p["relation"],
                    "note": p["note"],
                    "matched_on": f"{core} ~ {pcore}",
                }
            )
    return hits


def analyze(period_start=PERIOD_START, period_end=PERIOD_END):
    invoices = load("sales_invoices")
    credits = load("credit_notes")
    customers = load("master_customers")
    parties = load("related_parties")
    aging = {r["customer_code"]: r for r in load("ar_aging")}

    ship_by_cust = collections.Counter()
    last_month_by_cust = collections.Counter()
    invoice_no_by_cust = collections.defaultdict(list)
    last_month = period_end.strftime("%Y-%m")

    for r in invoices:
        d = as_date(r["revenue_date"])
        if not (period_start <= d <= period_end):
            continue
        c = r["customer_code"]
        amt = as_int(r["amount_krw"])
        ship_by_cust[c] += amt
        invoice_no_by_cust[c].append(r["invoice_no"])
        if d.strftime("%Y-%m") == last_month:
            last_month_by_cust[c] += amt

    # 반품은 보고기간 종료 후까지 포함해서 집계한다. 당기 데이터만 보면 안 보인다.
    ret_by_cust = collections.Counter()
    ret_after_by_cust = collections.Counter()
    reasons_by_cust = collections.defaultdict(collections.Counter)
    reason_amt_by_cust = collections.defaultdict(collections.Counter)
    cn_by_cust = collections.defaultdict(list)

    for r in credits:
        c = r["customer_code"]
        amt = as_int(r["amount_krw"])
        d = as_date(r["issue_date"])
        ret_by_cust[c] += amt
        if d > period_end:
            ret_after_by_cust[c] += amt
        reasons_by_cust[c][r["reason"]] += 1
        reason_amt_by_cust[c][r["reason"]] += amt
        cn_by_cust[c].append(
            {"credit_note_no": r["credit_note_no"], "issue_date": r["issue_date"],
             "amount_krw": amt, "reason": r["reason"], "product_code": r["product_code"]}
        )

    total_ship = sum(ship_by_cust.values())
    total_ret = sum(ret_by_cust.values())
    overall_rate = pct(total_ret, total_ship, 2)

    rows = []
    for cu in customers:
        c = cu["customer_code"]
        ship = ship_by_cust[c]
        ret = ret_by_cust[c]
        rate = pct(ret, ship, 2)
        ag = aging.get(c, {})
        rp = _related_party_match(cu["customer_name"], parties)
        rows.append(
            {
                "customer_code": c,
                "customer_name": cu["customer_name"],
                "channel": cu["channel"],
                "contract_type": cu["contract_type"],
                "credit_term_days": as_int(cu["credit_term_days"]),
                "shipped_krw": ship,
                "returned_krw": ret,
                "returned_after_period_end_krw": ret_after_by_cust[c],
                "return_rate_pct": rate,
                "return_rate_vs_overall": (
                    round(rate / overall_rate, 1)
                    if rate is not None and overall_rate
                    else None
                ),
                "period_end_month_share_pct": pct(last_month_by_cust[c], ship, 1),
                "ar_balance_krw": as_int(ag.get("balance_krw")),
                "ar_days_0_30_krw": as_int(ag.get("days_0_30_krw")),
                "ar_days_31_60_krw": as_int(ag.get("days_31_60_krw")),
                "ar_days_61_90_krw": as_int(ag.get("days_61_90_krw")),
                "ar_over_90_krw": as_int(ag.get("over_90_krw")),
                "ar_over_60_share_pct": pct(
                    as_int(ag.get("days_61_90_krw")) + as_int(ag.get("over_90_krw")),
                    as_int(ag.get("balance_krw")),
                    1,
                ),
                "ar_to_shipped_ratio": (
                    round(ratio(as_int(ag.get("balance_krw")), ship) or 0, 3)
                    if ship
                    else None
                ),
                "related_parties": rp,
                "invoice_count": len(invoice_no_by_cust[c]),
                "return_reasons": dict(reasons_by_cust[c]),
                "return_reason_amount_krw": dict(reason_amt_by_cust[c]),
            }
        )

    rows.sort(key=lambda r: -(r["return_rate_pct"] or 0))

    # 반품 사유는 금액 집계로는 절대 구별되지 않는다. 원문을 그대로 넘긴다.
    reason_totals = collections.Counter()
    reason_counts = collections.Counter()
    for r in credits:
        reason_totals[r["reason"]] += as_int(r["amount_krw"])
        reason_counts[r["reason"]] += 1

    return {
        "procedure": "substance-over-form-sales",
        "parameters": {
            "period_start": iso(period_start),
            "period_end": iso(period_end),
            "credit_note_data_max_date": iso(
                max(as_date(r["issue_date"]) for r in credits)
            ),
        },
        "overall": {
            "shipped_krw": total_ship,
            "returned_krw": total_ret,
            "return_rate_pct": overall_rate,
            "customer_count": len(customers),
        },
        "return_reasons": {
            reason: {"count": reason_counts[reason], "amount_krw": amt}
            for reason, amt in reason_totals.most_common()
        },
        "by_customer": rows,
        "credit_notes_by_customer": {c: v for c, v in cn_by_cust.items()},
        "counter_facts": {
            "note": "반품의 존재 자체는 발견사항이 아니다. 제조업 통상 반품률 구간과 "
            "반품 사유(품질하자 vs 미판매)를 함께 볼 것.",
            "customers_at_or_below_overall_rate": [
                r["customer_code"]
                for r in rows
                if r["return_rate_pct"] is not None
                and overall_rate is not None
                and r["return_rate_pct"] <= overall_rate
            ],
        },
        "notes": [
            "특수관계자 조인은 문자열 부분일치다. 표기가 다르면 누락될 수 있으므로 "
            "결과를 그대로 신뢰하지 말고 명세 원문을 함께 볼 것.",
            "채권 연령은 당기말 시점 1개뿐이다. 전기 대비 연령 이동은 계산할 수 없다.",
        ],
    }
