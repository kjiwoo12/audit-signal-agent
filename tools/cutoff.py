"""기간귀속 검토 — cutoff-revenue-recognition.md 3절의 구현.

판매 시스템의 인식일과 물류 시스템의 통제이전일을 대조한다.
이 모듈은 '몇 건이 며칠 어긋났는가'까지만 낸다. 의도적 조기인식인지,
프로세스 지연인지는 Skill 4절에서 LLM이 판단한다.
"""

from __future__ import annotations

import collections

from .loader import (
    PERIOD_END,
    as_date,
    as_int,
    iso,
    load,
    pct,
    ratio,
    shift_biz_days,
)

# 인도조건별 통제 이전 시점. 이 대응이 절차 전체의 전제다.
CONTROL_DATE_RULE = {
    "FOB 도착지": "arrival_date",
    "FOB 선적지": "actual_ship_date",
}


def _cogs_ratio_by_product() -> dict[str, float]:
    """제품별 매출원가율. 조기인식분의 대응 원가 추정에 쓴다."""
    out = {}
    for r in load("production_cost"):
        rev = as_int(r["revenue_krw"])
        cost = as_int(r["total_cost_krw"])
        out[r["product_code"]] = ratio(cost, rev) or 0.0
    return out


def analyze(period_end=PERIOD_END, period_end_window_biz_days=10):
    """기간귀속 오류 후보를 추출한다.

    period_end_window_biz_days 는 '기말 근처'의 조회 범위일 뿐 판정 기준이 아니다.
    무엇을 보고할지는 Skill 5절이 정한다.
    """
    invoices = load("sales_invoices")
    ship_by_invoice = {r["invoice_no"]: r for r in load("shipments")}
    cogs_ratio = _cogs_ratio_by_product()

    window_start = shift_biz_days(period_end, -period_end_window_biz_days)

    joined, unjoined = [], []
    unknown_incoterms = set()

    for inv in invoices:
        sh = ship_by_invoice.get(inv["invoice_no"])
        if sh is None:
            unjoined.append(inv["invoice_no"])
            continue

        terms = inv["incoterms"]
        field = CONTROL_DATE_RULE.get(terms)
        if field is None:
            unknown_incoterms.add(terms)
            continue

        rev_date = as_date(inv["revenue_date"])
        ctrl_date = as_date(sh[field])
        joined.append(
            {
                "invoice_no": inv["invoice_no"],
                "shipment_no": sh["shipment_no"],
                "customer_code": inv["customer_code"],
                "product_code": inv["product_code"],
                "amount_krw": as_int(inv["amount_krw"]),
                "incoterms": terms,
                "revenue_date": rev_date,
                "control_basis": field,
                "control_date": ctrl_date,
                "gap_days": (ctrl_date - rev_date).days,
            }
        )

    # gap_days > 0 : 통제 이전보다 먼저 인식 (조기인식 방향)
    early = [r for r in joined if r["gap_days"] > 0]
    # gap_days < 0 : 출고가 인식보다 빠름 — 이 절차의 대상이 아니다 (반증자료)
    late = [r for r in joined if r["gap_days"] < 0]

    # 기간귀속 오류 후보: 인식은 당기, 통제 이전은 차기
    candidates = [
        r
        for r in early
        if r["revenue_date"] <= period_end < r["control_date"]
    ]
    candidates.sort(key=lambda r: -r["amount_krw"])

    period_revenue = sum(
        as_int(r["amount_krw"])
        for r in invoices
        if as_date(r["revenue_date"]) <= period_end
    )
    cand_amount = sum(r["amount_krw"] for r in candidates)
    cand_cogs = sum(
        round(r["amount_krw"] * cogs_ratio.get(r["product_code"], 0.0))
        for r in candidates
    )

    # 월별 분포: 기말 집중인지 연중 분산인지를 LLM이 보게 한다
    early_by_month = collections.Counter(
        r["revenue_date"].strftime("%Y-%m") for r in early
    )
    gap_hist = collections.Counter(r["gap_days"] for r in early)

    # 반증자료 1 — 12월 매출 급증 자체는 발견사항이 아니다
    monthly_revenue = collections.Counter()
    for r in invoices:
        d = as_date(r["revenue_date"])
        if d <= period_end:
            monthly_revenue[d.strftime("%Y-%m")] += as_int(r["amount_krw"])
    months = sorted(monthly_revenue)
    dec = monthly_revenue[months[-1]] if months else 0
    avg_ex_dec = (
        sum(monthly_revenue[m] for m in months[:-1]) / max(len(months) - 1, 1)
        if months
        else 0
    )

    return {
        "procedure": "cutoff-revenue-recognition",
        "parameters": {
            "period_end": iso(period_end),
            "period_end_window_biz_days": period_end_window_biz_days,
            "period_end_window_start": iso(window_start),
            "control_date_rule": CONTROL_DATE_RULE,
        },
        "coverage": {
            "invoices": len(invoices),
            "joined": len(joined),
            "unjoined_invoice_no": unjoined,
            "unknown_incoterms": sorted(unknown_incoterms),
            "shipment_data_max_date": iso(
                max(as_date(r["arrival_date"]) for r in load("shipments"))
            ),
        },
        "early_recognition": {
            "count": len(early),
            "amount_krw": sum(r["amount_krw"] for r in early),
            "gap_days_histogram": dict(sorted(gap_hist.items())),
            "by_month": dict(sorted(early_by_month.items())),
            "within_period": {
                "count": len(early) - len(candidates),
                "amount_krw": sum(r["amount_krw"] for r in early)
                - sum(r["amount_krw"] for r in candidates),
                "note": "인식일과 통제이전일이 같은 보고기간 안에 있다. "
                "기간 내에서 상계되므로 재무제표 영향이 없다. "
                "다만 인식 시점 운영이 인도조건과 일관되지 않다는 관찰은 남는다.",
            },
        },
        "cutoff_candidates": {
            "definition": "인식일 <= 보고기간말 < 통제이전일",
            "count": len(candidates),
            "amount_krw": cand_amount,
            "pct_of_period_revenue": pct(cand_amount, period_revenue, 2),
            "estimated_cogs_krw": cand_cogs,
            "pretax_impact_krw": cand_amount - cand_cogs,
            "cogs_basis": "제품별 total_cost/revenue (전부원가 기준 근사)",
            "items": [
                {
                    **r,
                    "revenue_date": iso(r["revenue_date"]),
                    "control_date": iso(r["control_date"]),
                }
                for r in candidates
            ],
        },
        "counter_facts": {
            "ship_before_invoice": {
                "count": len(late),
                "amount_krw": sum(r["amount_krw"] for r in late),
                "note": "선출고 후 송장 발행. 이 절차의 대상이 아니다.",
            },
            "period_end_month_revenue": {
                "month": months[-1] if months else None,
                "amount_krw": dec,
                "avg_other_months_krw": round(avg_ex_dec),
                "multiple": round(ratio(dec, avg_ex_dec) or 0, 2),
                "note": "매출 급증 자체는 발견사항이 아니다. 계절성 여부를 먼저 볼 것.",
            },
        },
        "notes": [
            "조회 기간을 보고기간으로 자르면 이 절차는 아무것도 찾지 못한다. "
            "shipments 는 차기 도착분까지 포함해 읽었다.",
            "대응 매출원가는 전부원가율 근사다. 변동원가만 되돌리는 경우 영향금액이 달라진다.",
        ],
    }
