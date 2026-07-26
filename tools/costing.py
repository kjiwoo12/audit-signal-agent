"""원가동인 정합성 검토 — cost-driver-alignment.md 3절의 구현.

제조간접비를 현행 배부기준과 활동기준(ABC)으로 각각 배부해 제품별 수익성을 비교한다.

여기서 계산이 틀리면 절차 전체가 무의미하다. 재배부 합계가 간접비 총액과
1원이라도 어긋나면 예외를 던지고 멈춘다 — 틀린 숫자를 내놓는 것보다 낫다.
"""

from __future__ import annotations

import collections

from .loader import as_int, load, pct, ratio, split_exact

# production_cost.csv 의 동인 사용량 컬럼과 activity 의 cost_driver 명 대응
DRIVER_COLUMN = {
    "기계시간": "machine_hours",
    "셋업횟수": "setup_count",
    "검사재작업건수": "inspection_rework_count",
    "자재출고건수": "material_issue_count",
}
BASIS_COLUMN = {
    "직접노무비": "direct_labor_krw",
    "직접재료비": "direct_material_krw",
    "기계시간": "machine_hours",
}


def analyze():
    pc = load("production_cost")
    acts = load("overhead_activities")
    notes = load("cost_system_notes")

    products = [r["product_code"] for r in pc]
    by_code = {r["product_code"]: r for r in pc}

    basis_name = pc[0]["oh_allocation_basis"]
    if len(set(r["oh_allocation_basis"] for r in pc)) != 1:
        raise ValueError("제품별 배부기준이 서로 다르다. 전제를 다시 확인할 것.")
    basis_col = BASIS_COLUMN.get(basis_name)
    if basis_col is None:
        raise ValueError(f"알 수 없는 배부기준: {basis_name}")

    oh_total = sum(as_int(r["allocated_oh_krw"]) for r in pc)

    # --- 1) 현행 배부기준의 비례관계 검증 -----------------------------------
    basis_values = {p: as_int(by_code[p][basis_col]) for p in products}
    recomputed = dict(
        zip(products, split_exact(oh_total, [basis_values[p] for p in products]))
    )
    current_alloc = {p: as_int(by_code[p]["allocated_oh_krw"]) for p in products}
    max_dev = max(abs(recomputed[p] - current_alloc[p]) for p in products)
    # 원장 금액은 백만원 단위로 반올림되어 있다. 원 단위 재계산과는 반올림 차이가 남는다.
    # 배부 로직이 실제로 그 기준을 따랐는지만 확인하면 되므로 풀 대비 0.1% 를 허용한다.
    max_dev_pct = pct(max_dev, oh_total, 4)

    # --- 2) 배부기준 구성비 vs 동인 구성비 ---------------------------------
    driver_totals = {
        d: sum(as_int(by_code[p][col]) for p in products)
        for d, col in DRIVER_COLUMN.items()
    }
    basis_total = sum(basis_values.values())
    mix = []
    for p in products:
        row = {
            "product_code": p,
            "product_name": by_code[p]["product_name"],
            f"{basis_name}_share_pct": pct(basis_values[p], basis_total, 1),
        }
        for d, col in DRIVER_COLUMN.items():
            row[f"{d}_share_pct"] = pct(
                as_int(by_code[p][col]), driver_totals[d], 1
            )
        row["max_gap_pp"] = round(
            max(
                abs((row[f"{d}_share_pct"] or 0) - (row[f"{basis_name}_share_pct"] or 0))
                for d in DRIVER_COLUMN
            ),
            1,
        )
        mix.append(row)

    # --- 3) 활동기준 재배부 -------------------------------------------------
    pools = collections.OrderedDict()
    for r in acts:
        pools.setdefault(
            r["activity"],
            {
                "cost_driver": r["cost_driver"],
                "pool_krw": as_int(r["activity_pool_krw"]),
                "usage": {},
                "driver_total": as_int(r["driver_total"]),
            },
        )["usage"][r["product_code"]] = as_int(r["driver_usage"])

    pool_sum = sum(v["pool_krw"] for v in pools.values())
    if pool_sum != oh_total:
        raise ArithmeticError(
            f"활동 풀 합계({pool_sum})가 간접비 총액({oh_total})과 다르다. 계산 중단."
        )

    abc_alloc = {p: 0 for p in products}
    activity_detail = []
    for name, v in pools.items():
        usage_sum = sum(v["usage"].get(p, 0) for p in products)
        if usage_sum != v["driver_total"]:
            raise ArithmeticError(
                f"활동 '{name}' 의 동인 사용량 합계가 driver_total 과 다르다."
            )
        alloc = split_exact(v["pool_krw"], [v["usage"].get(p, 0) for p in products])
        for p, a in zip(products, alloc):
            abc_alloc[p] += a
        activity_detail.append(
            {
                "activity": name,
                "cost_driver": v["cost_driver"],
                "activity_pool_krw": v["pool_krw"],
                "driver_total": v["driver_total"],
                "allocation": {
                    p: {"driver_usage": v["usage"].get(p, 0), "allocated_krw": a}
                    for p, a in zip(products, alloc)
                },
            }
        )

    if sum(abc_alloc.values()) != oh_total:
        raise ArithmeticError("ABC 재배부 합계가 간접비 총액과 불일치. 계산 중단.")

    # --- 4) 두 기준의 제품별 수익성 ----------------------------------------
    rows = []
    for p in products:
        r = by_code[p]
        rev = as_int(r["revenue_krw"])
        dm = as_int(r["direct_material_krw"])
        dl = as_int(r["direct_labor_krw"])
        cur_gp = rev - dm - dl - current_alloc[p]
        abc_gp = rev - dm - dl - abc_alloc[p]
        rows.append(
            {
                "product_code": p,
                "product_name": r["product_name"],
                "revenue_krw": rev,
                "direct_material_krw": dm,
                "direct_labor_krw": dl,
                "current_oh_krw": current_alloc[p],
                "abc_oh_krw": abc_alloc[p],
                "oh_delta_krw": abc_alloc[p] - current_alloc[p],
                "current_gp_krw": cur_gp,
                "abc_gp_krw": abc_gp,
                "current_gp_pct": pct(cur_gp, rev, 1),
                "abc_gp_pct": pct(abc_gp, rev, 1),
                "gp_pct_delta_pp": round(
                    (pct(abc_gp, rev, 1) or 0) - (pct(cur_gp, rev, 1) or 0), 1
                ),
                "sign_flipped": (cur_gp >= 0) != (abc_gp >= 0),
            }
        )

    cur_rank = {
        r["product_code"]: i + 1
        for i, r in enumerate(sorted(rows, key=lambda x: -(x["current_gp_pct"] or 0)))
    }
    abc_rank = {
        r["product_code"]: i + 1
        for i, r in enumerate(sorted(rows, key=lambda x: -(x["abc_gp_pct"] or 0)))
    }
    for r in rows:
        p = r["product_code"]
        r["current_gp_rank"] = cur_rank[p]
        r["abc_gp_rank"] = abc_rank[p]
        r["rank_changed"] = cur_rank[p] != abc_rank[p]

    total_cost = sum(
        as_int(by_code[p]["total_cost_krw"]) for p in products
    )

    return {
        "procedure": "cost-driver-alignment",
        "current_basis": {
            "name": basis_name,
            "source_column": "oh_allocation_basis",
            "proportionality_max_deviation_krw": max_dev,
            "proportionality_max_deviation_pct": max_dev_pct,
            "proportionality_verified": max_dev_pct is not None and max_dev_pct <= 0.1,
            "tolerance_note": "원장 금액이 백만원 단위로 반올림되어 있어 원 단위 "
            "재계산과 반올림 차이가 남는다. 허용 오차 0.1%.",
        },
        "overhead": {
            "total_krw": oh_total,
            "share_of_total_cost_pct": pct(oh_total, total_cost, 1),
            "activity_pool_sum_krw": pool_sum,
            "reallocation_ties_out": True,
        },
        "basis_vs_driver_mix": mix,
        "activity_based_reallocation": activity_detail,
        "profitability": rows,
        "rank_reversal": [r["product_code"] for r in rows if r["rank_changed"]],
        "sign_flip": [r["product_code"] for r in rows if r["sign_flipped"]],
        "cost_system_notes": [
            {"note_date": n["note_date"], "author": n["author"], "note": n["note"]}
            for n in notes
        ],
        "financial_statement_impact": {
            "amount_krw": 0,
            "reason": "총 매출원가와 총 매출총이익은 변하지 않는다. 제품 간 배분만 바뀐다.",
        },
        "notes": [
            "연 1회 단일 배부다. 월별 배부율 변동은 반영하지 않았다.",
            "담당자 메모(cost_system_notes.csv)는 원문 그대로 넘긴다. "
            "정량 결과를 회사 내부 인식으로 뒷받침하는 근거이며 수치로는 얻을 수 없다.",
        ],
    }
