"""기말 이례거래 검토 — period-end-window-dressing.md 3절의 구현.

보고기간 말 며칠 사이에 들어왔다가 곧바로 나가는 돈을 찾는다.
손익에는 영향이 없으므로 손익계산서만 보면 절대 잡히지 않는다.
"""

from __future__ import annotations

from .loader import (
    PERIOD_END,
    PERIOD_START,
    as_date,
    as_int,
    biz_days_between,
    iso,
    load,
    pct,
    ratio,
    shift_biz_days,
)

CURRENT_ASSETS = ("101", "108", "115", "120", "121", "122")
CURRENT_LIABILITIES = ("301", "305", "310")


def _bank_rows():
    rows = []
    for r in load("bank_transactions"):
        rows.append(
            {
                "account": r["account"],
                "txn_date": as_date(r["txn_date"]),
                "description": r["description"],
                "deposit_krw": as_int(r["deposit_krw"]),
                "withdrawal_krw": as_int(r["withdrawal_krw"]),
                "balance_krw": as_int(r["balance_krw"]),
            }
        )
    rows.sort(key=lambda r: r["txn_date"])
    return rows


def _gl_voucher_for(d, amount, account_code="310"):
    """같은 일자·금액의 GL 전표를 찾는다. 없으면 None (조회범위 밖일 수 있다)."""
    for r in load("gl_journal"):
        if r["account_code"] != account_code:
            continue
        if as_date(r["posting_date"]) != d:
            continue
        if as_int(r["debit_krw"]) == amount or as_int(r["credit_krw"]) == amount:
            return r["voucher_no"]
    return None


def _match_pairs(pre, post, tolerance=0.01):
    """금액이 같고 방향이 반대인 거래 쌍을 매칭한다. 완전일치 우선."""
    pairs = []
    used = set()
    for direction, in_field, out_field in (
        ("차입후상환", "deposit_krw", "withdrawal_krw"),
        ("지급후회수", "withdrawal_krw", "deposit_krw"),
    ):
        opens = [r for r in pre if r[in_field] > 0]
        closes = [r for r in post if r[out_field] > 0]
        for exact_only in (True, False):
            for a in opens:
                if id(a) in used:
                    continue
                amt = a[in_field]
                for b in closes:
                    if id(b) in used:
                        continue
                    other = b[out_field]
                    ok = (
                        other == amt
                        if exact_only
                        else abs(other - amt) <= amt * tolerance
                    )
                    if not ok:
                        continue
                    used.add(id(a))
                    used.add(id(b))
                    pairs.append(
                        {
                            "direction": direction,
                            "match": "완전일치" if exact_only else f"±{tolerance:.0%}",
                            "open_date": iso(a["txn_date"]),
                            "open_description": a["description"],
                            "open_amount_krw": amt,
                            "close_date": iso(b["txn_date"]),
                            "close_description": b["description"],
                            "close_amount_krw": other,
                            "holding_calendar_days": (b["txn_date"] - a["txn_date"]).days,
                            "holding_biz_days": biz_days_between(
                                a["txn_date"], b["txn_date"]
                            ),
                            "open_voucher_no": _gl_voucher_for(a["txn_date"], amt),
                            "close_voucher_no": _gl_voucher_for(b["txn_date"], other),
                        }
                    )
                    break
    return pairs


def analyze(period_end=PERIOD_END, period_start=PERIOD_START, window_biz_days=10):
    """window_biz_days 는 조회 범위다. 판정 기준이 아니다."""
    bank = _bank_rows()
    tb = {
        r["account_code"]: {
            "name": r["account_name"],
            "prior": as_int(r["fy2023_ending_krw"]),
            "current": as_int(r["fy2024_ending_krw"]),
        }
        for r in load("trial_balance")
    }

    pre_from = shift_biz_days(period_end, -window_biz_days)
    post_to = shift_biz_days(period_end, window_biz_days)

    pre = [r for r in bank if pre_from <= r["txn_date"] <= period_end]
    post = [r for r in bank if period_end < r["txn_date"] <= post_to]
    pairs = _match_pairs(pre, post)

    # 전기 패턴: 보고기간 개시 직후의 되돌림 거래.
    # 대응 차입은 전기말에 있어 데이터 범위 밖일 수 있다 — 그 사실을 명시한다.
    data_min = min(r["txn_date"] for r in bank)
    open_to = shift_biz_days(period_start, window_biz_days)
    prior_reversals = [
        {
            "txn_date": iso(r["txn_date"]),
            "description": r["description"],
            "deposit_krw": r["deposit_krw"],
            "withdrawal_krw": r["withdrawal_krw"],
            "counterpart_in_data": r["txn_date"] > data_min,
        }
        for r in bank
        if period_start <= r["txn_date"] <= open_to
        and r["withdrawal_krw"] > 0
        and "차입" in r["description"]
    ]

    # 반증자료 — 보고기간 중간의 차입은 이 절차의 대상이 아니다.
    # 이자 지급은 차입 원금 거래가 아니므로 제외한다.
    mid = [
        {
            "txn_date": iso(r["txn_date"]),
            "description": r["description"],
            "deposit_krw": r["deposit_krw"],
        }
        for r in bank
        if "차입" in r["description"]
        and "이자" not in r["description"]
        and r["deposit_krw"] > 0
        and open_to < r["txn_date"] < pre_from
    ]

    reversed_amount = sum(
        p["open_amount_krw"] for p in pairs if p["direction"] == "차입후상환"
    )

    cash = tb["101"]["current"]
    debt = tb["310"]["current"]
    ca = sum(tb[c]["current"] for c in CURRENT_ASSETS)
    cl = sum(tb[c]["current"] for c in CURRENT_LIABILITIES)

    adj_ca, adj_cl = ca - reversed_amount, cl - reversed_amount
    total_liab = cl  # 이 데이터셋에는 비유동부채가 없다
    equity = tb["401"]["current"] + tb["402"]["current"]

    return {
        "procedure": "period-end-window-dressing",
        "parameters": {
            "period_end": iso(period_end),
            "window_biz_days": window_biz_days,
            "pre_window": [iso(pre_from), iso(period_end)],
            "post_window": [iso(shift_biz_days(period_end, 1)), iso(post_to)],
            "bank_data_range": [iso(data_min), iso(max(r["txn_date"] for r in bank))],
        },
        "matched_pairs": pairs,
        "matched_pair_count": len(pairs),
        "reversed_amount_krw": reversed_amount,
        "balance_sheet_effect": {
            "note": "손익 영향 없음. 재무상태표 표시의 문제다.",
            "현금및현금성자산": {
                "book_krw": cash,
                "adjusted_krw": cash - reversed_amount,
            },
            "단기차입금": {
                "book_krw": debt,
                "adjusted_krw": debt - reversed_amount,
            },
            "유동비율_pct": {
                "book": pct(ca, cl, 1),
                "adjusted": pct(adj_ca, adj_cl, 1),
                # 유동비율이 1을 넘는 상태에서 유동자산·유동부채에 같은 금액을 더하면
                # 비율은 오히려 1에 가까워진다. 방향을 단정하지 말고 이 값을 볼 것.
                "borrowing_improves_ratio": pct(ca, cl, 1) > pct(adj_ca, adj_cl, 1),
            },
            "부채비율_pct": {
                "book": pct(total_liab, equity, 1),
                "adjusted": pct(total_liab - reversed_amount, equity, 1),
            },
        },
        "prior_period_pattern": {
            "reversals_at_period_start": prior_reversals,
            "note": "전기말 차입 거래는 은행 데이터 시작일 이전이라 관측되지 않는다. "
            "상환 측만 확인되므로 반복성 주장 시 근거등급 제약을 명시할 것.",
        },
        "counter_facts": {
            "mid_period_borrowings": mid,
            "mid_period_borrowing_count": len(mid),
            "mid_period_borrowing_amount_krw": sum(r["deposit_krw"] for r in mid),
            "unmatched_period_end_transactions": [
                {
                    "txn_date": iso(r["txn_date"]),
                    "description": r["description"],
                    "deposit_krw": r["deposit_krw"],
                    "withdrawal_krw": r["withdrawal_krw"],
                }
                for r in pre
                if not any(
                    p["open_date"] == iso(r["txn_date"])
                    and p["open_amount_krw"] in (r["deposit_krw"], r["withdrawal_krw"])
                    for p in pairs
                )
            ],
            "note": "보고기간 중간에 발생한 차입은 기말 외형 보정과 무관하다. "
            "이 구분을 못 하면 모든 단기차입이 발견사항이 된다.",
        },
        "notes": [
            "영업일 계산에 공휴일 달력을 적용하지 않았다. 주말만 제외한다.",
            "차기 GL 이 없으므로 상환 전표번호는 조회되지 않을 수 있다.",
        ],
    }
