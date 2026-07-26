"""이익-현금 괴리 분석 — earnings-cash-divergence.md 3절의 구현.

이익이 늘었는데 현금이 줄었다면 그 차액은 어딘가에 자산으로 쌓여 있다.
이 모듈은 '어느 계정에 얼마가 쌓였는지'까지 계산한다. 그 계정이 왜 늘었는지는
다른 절차의 몫이다.
"""

from __future__ import annotations

from .loader import as_date, as_int, growth_pct, load, pct, ratio

# 운전자본으로 볼 계정. 이익-현금 차액의 소재를 여기서 찾는다.
WORKING_CAPITAL = {
    "108": ("매출채권", "자산"),
    "115": ("미수금", "자산"),
    "120": ("원재료", "자산"),
    "121": ("재공품", "자산"),
    "122": ("제품", "자산"),
    "301": ("매입채무", "부채"),
    "305": ("미지급금", "부채"),
}
INVENTORY_CODES = ("120", "121", "122")


def _summary() -> dict[str, dict]:
    return {
        r["item"]: {"prior": as_int(r["fy2023_krw"]), "current": as_int(r["fy2024_krw"])}
        for r in load("financial_summary")
    }


def _tb() -> dict[str, dict]:
    return {
        r["account_code"]: {
            "name": r["account_name"],
            "prior": as_int(r["fy2023_ending_krw"]),
            "current": as_int(r["fy2024_ending_krw"]),
        }
        for r in load("trial_balance")
    }


def analyze():
    fs = _summary()
    tb = _tb()

    def g(item):
        v = fs[item]
        return v["prior"], v["current"]

    rev_p, rev_c = g("매출액")
    cogs_p, cogs_c = g("매출원가")
    op_p, op_c = g("영업이익")
    ni_p, ni_c = g("당기순이익")
    ocf_p, ocf_c = g("영업활동현금흐름")
    icf_p, icf_c = g("투자활동현금흐름")
    fcf_p, fcf_c = g("재무활동현금흐름")

    op_growth = growth_pct(op_p, op_c)
    ocf_growth = growth_pct(ocf_p, ocf_c)

    ar_p, ar_c = tb["108"]["prior"], tb["108"]["current"]
    inv_p = sum(tb[c]["prior"] for c in INVENTORY_CODES)
    inv_c = sum(tb[c]["current"] for c in INVENTORY_CODES)

    dso_p = ratio(ar_p, ratio(rev_p, 365))
    dso_c = ratio(ar_c, ratio(rev_c, 365))
    inv_days_p = ratio(inv_p, ratio(cogs_p, 365))
    inv_days_c = ratio(inv_c, ratio(cogs_c, 365))

    wc_rows = []
    for code, (name, side) in WORKING_CAPITAL.items():
        p, c = tb[code]["prior"], tb[code]["current"]
        delta = c - p
        # 자산 증가는 현금 유출(-), 부채 증가는 현금 유입(+)
        cash_effect = -delta if side == "자산" else delta
        wc_rows.append(
            {
                "account_code": code,
                "account_name": name,
                "side": side,
                "prior_krw": p,
                "current_krw": c,
                "delta_krw": delta,
                "growth_pct": growth_pct(p, c),
                "cash_effect_krw": cash_effect,
            }
        )
    wc_rows.sort(key=lambda r: r["cash_effect_krw"])

    # 은행 거래에서 차입 관련 건을 세어 재무활동 전환의 실체를 확인한다
    bank = load("bank_transactions")
    borrow_rows = [r for r in bank if "차입" in r["description"]]
    borrowings = [
        {
            "txn_date": r["txn_date"],
            "description": r["description"],
            "deposit_krw": as_int(r["deposit_krw"]),
            "withdrawal_krw": as_int(r["withdrawal_krw"]),
        }
        for r in borrow_rows
    ]

    st_debt = tb["310"]
    ar_growth = growth_pct(ar_p, ar_c)
    rev_growth = growth_pct(rev_p, rev_c)

    return {
        "procedure": "earnings-cash-divergence",
        "profitability_vs_cash": {
            "매출액": {"prior": rev_p, "current": rev_c, "growth_pct": rev_growth},
            "영업이익": {"prior": op_p, "current": op_c, "growth_pct": op_growth},
            "당기순이익": {"prior": ni_p, "current": ni_c, "growth_pct": growth_pct(ni_p, ni_c)},
            "영업활동현금흐름": {"prior": ocf_p, "current": ocf_c, "growth_pct": ocf_growth},
            "투자활동현금흐름": {"prior": icf_p, "current": icf_c, "growth_pct": growth_pct(icf_p, icf_c)},
            "재무활동현금흐름": {
                "prior": fcf_p,
                "current": fcf_c,
                "sign_flipped": (fcf_p < 0) != (fcf_c < 0),
            },
        },
        "divergence": {
            "definition": "영업이익 증감률 − 영업활동현금흐름 증감률 (%p)",
            "value_pp": (
                None
                if op_growth is None or ocf_growth is None
                else round(op_growth - ocf_growth, 1)
            ),
            "opposite_direction": (
                op_growth is not None
                and ocf_growth is not None
                and (op_growth > 0) != (ocf_growth > 0)
            ),
            "cash_conversion_prior_pct": pct(ocf_p, op_p, 1),
            "cash_conversion_current_pct": pct(ocf_c, op_c, 1),
        },
        "turnover": {
            "dso_prior_days": None if dso_p is None else round(dso_p, 1),
            "dso_current_days": None if dso_c is None else round(dso_c, 1),
            "dso_delta_days": (
                None if dso_p is None or dso_c is None else round(dso_c - dso_p, 1)
            ),
            "inventory_days_prior": None if inv_days_p is None else round(inv_days_p, 1),
            "inventory_days_current": None if inv_days_c is None else round(inv_days_c, 1),
            "ar_growth_vs_revenue_growth_multiple": (
                None
                if not rev_growth
                else round(ar_growth / rev_growth, 2)
            ),
        },
        "working_capital_attribution": {
            "note": "cash_effect_krw 가 음수인 계정에 이익-현금 차액이 쌓여 있다.",
            "rows": wc_rows,
            "total_cash_effect_krw": sum(r["cash_effect_krw"] for r in wc_rows),
        },
        "financing": {
            "단기차입금": {
                "prior": st_debt["prior"],
                "current": st_debt["current"],
                "delta_krw": st_debt["current"] - st_debt["prior"],
            },
            "bank_borrowing_transactions": borrowings,
            "borrowing_transaction_count": len(borrowings),
        },
        "unavailable": [
            {
                "item": "채권 연령 분포의 전기 대비 변화",
                "reason": "ar_aging.csv 는 당기말 시점 1개만 존재한다. 전기 연령 자료 없음.",
                "consequence": "연령 이동 추세를 근거로 쓸 수 없다. 추정하지 않는다.",
            }
        ],
        "notes": [
            "이 절차는 단독으로 결론을 내지 않는다. 차액의 소재를 특정해서 "
            "다른 절차로 넘기는 것이 역할이다.",
            "재고자산은 원재료+재공품+제품 합계다.",
        ],
    }
