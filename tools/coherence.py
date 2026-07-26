"""자원배분 정합성 — resource-allocation-coherence.md 3절의 구현.

제품별 '실제 수익성'과 '투입 자원'을 나란히 놓고, 앞선 절차들이 낸 금액이
서로 대응하는지 계산한다.

이 모듈은 인과를 주장하지 않는다. 금액이 대응하는지 여부만 낸다.
인과 판단은 Skill 4.2절의 세 요건(금액 대응·시점 정합·방향 정합)으로 LLM이 한다.
"""

from __future__ import annotations

import collections
import re

from .loader import as_date, as_int, growth_pct, load, pct, ratio

CAPEX_KEYWORDS = ("설비", "증설", "금형", "라인", "공사", "로봇", "프레스")
# 자산으로 계상되어야 할 지출이 들어갈 수 있는 비용 계정
EXPENSE_ACCOUNT_PREFIX = ("6", "7")
PROMOTION_ACCOUNT = "702"


def _product_codes():
    return [r["product_code"] for r in load("master_products")]


def _find_product(text, codes):
    for c in codes:
        if re.search(re.escape(c), text):
            return c
    return None


def _capex_by_product(codes):
    """은행 출금 적요에서 제품·라인 식별자가 있는 설비성 지출을 집계한다.

    적요에 식별자가 없으면 배분 추정을 하지 않는다. 미식별로 남긴다.
    """
    identified = collections.Counter()
    items = collections.defaultdict(list)
    unidentified = []
    for r in load("bank_transactions"):
        amt = as_int(r["withdrawal_krw"])
        if not amt:
            continue
        desc = r["description"]
        if not any(k in desc for k in CAPEX_KEYWORDS):
            continue
        p = _find_product(desc, codes)
        rec = {"txn_date": r["txn_date"], "description": desc, "amount_krw": amt}
        if p:
            identified[p] += amt
            items[p].append(rec)
        else:
            unidentified.append(rec)
    return identified, dict(items), unidentified


def _promotion_by_product(codes):
    identified = collections.Counter()
    items = collections.defaultdict(list)
    unidentified = []
    for r in load("gl_journal"):
        if r["account_code"] != PROMOTION_ACCOUNT:
            continue
        amt = as_int(r["debit_krw"])
        if not amt:
            continue
        p = _find_product(r["description"], codes)
        rec = {
            "voucher_no": r["voucher_no"],
            "posting_date": r["posting_date"],
            "description": r["description"],
            "amount_krw": amt,
        }
        if p:
            identified[p] += amt
            items[p].append(rec)
        else:
            unidentified.append(rec)
    return identified, dict(items), unidentified


def _capitalizable_expensed(codes):
    """비용 계정에 계상된 발주 중, 발주 비고가 자산 요건을 기술한 건을 뽑는다.

    판단은 하지 않는다. '비고에 내용연수·생산능력 증가가 적혀 있는데 비용 계정에
    계상되어 있다'는 사실만 낸다.
    """
    gl_by_po = collections.defaultdict(list)
    for r in load("gl_journal"):
        m = re.search(r"PO\d{4}-\d{5}", r["description"])
        if m:
            gl_by_po[m.group(0)].append(r)

    rows = []
    for r in load("purchase_orders"):
        remark = (r["remark"] or "").strip()
        if not remark:
            continue
        if not r["posted_account_code"].startswith(EXPENSE_ACCOUNT_PREFIX):
            continue
        vouchers = [g["voucher_no"] for g in gl_by_po.get(r["po_no"], [])]
        rows.append(
            {
                "po_no": r["po_no"],
                "po_date": r["po_date"],
                "item_description": r["item_description"],
                "amount_krw": as_int(r["amount_krw"]),
                "posted_account_code": r["posted_account_code"],
                "posted_account_name": r["posted_account_name"],
                "remark": remark,
                "gl_voucher_no": sorted(set(vouchers)),
                "product_hint": _find_product(r["item_description"], codes),
            }
        )
    rows.sort(key=lambda x: -x["amount_krw"])
    return rows


def analyze(costing_result=None, cutoff_result=None, substance_result=None,
            divergence_result=None, window_result=None):
    """앞선 절차 결과를 받으면 금액 대응까지 계산한다. 없으면 자원배분표만 낸다."""
    codes = _product_codes()
    pc = {r["product_code"]: r for r in load("production_cost")}
    products = {r["product_code"]: r for r in load("master_products")}

    capex, capex_items, capex_unid = _capex_by_product(codes)
    promo, promo_items, promo_unid = _promotion_by_product(codes)
    reclass = _capitalizable_expensed(codes)

    abc = {}
    if costing_result:
        abc = {r["product_code"]: r for r in costing_result["profitability"]}

    capex_total = sum(capex.values()) + sum(r["amount_krw"] for r in capex_unid)
    promo_total = sum(promo.values()) + sum(r["amount_krw"] for r in promo_unid)

    # 비용 처리된 자본적지출도 해당 라인의 실제 투입액이다
    reclass_by_product = collections.Counter()
    for r in reclass:
        if r["product_hint"]:
            reclass_by_product[r["product_hint"]] += r["amount_krw"]

    rows = []
    for code in codes:
        rev = as_int(pc[code]["revenue_krw"])
        a = abc.get(code, {})
        invested = capex[code] + promo[code] + reclass_by_product[code]
        rows.append(
            {
                "product_code": code,
                "product_name": products[code]["product_name"],
                "segment": products[code]["segment"],
                "launch_year": as_int(products[code]["launch_year"]),
                "revenue_krw": rev,
                "current_gp_pct": a.get("current_gp_pct"),
                "abc_gp_pct": a.get("abc_gp_pct"),
                "abc_gp_krw": a.get("abc_gp_krw"),
                "capex_krw": capex[code],
                "promotion_krw": promo[code],
                "capitalizable_expensed_krw": reclass_by_product[code],
                "total_identified_investment_krw": invested,
                "investment_share_pct": pct(invested, capex_total + promo_total, 1),
                "investment_to_revenue_pct": pct(invested, rev, 1),
            }
        )

    def _rank(items, keyfn):
        """동점은 같은 순위를 준다. 투입액이 0인 제품끼리 임의 순위를 매기면
        존재하지 않는 정합성 불일치가 만들어진다."""
        out, prev, prev_rank = {}, object(), 0
        for i, it in enumerate(sorted(items, key=lambda x: -(keyfn(x) or 0))):
            v = keyfn(it)
            rank = prev_rank if v == prev else i + 1
            out[it["product_code"]] = rank
            prev, prev_rank = v, rank
        return out

    prof_rank = _rank(rows, lambda x: x["abc_gp_pct"]) if abc else {}
    inv_rank = _rank(rows, lambda x: x["total_identified_investment_krw"])

    for r in rows:
        has_investment = r["total_identified_investment_krw"] > 0
        r["investment_rank"] = inv_rank[r["product_code"]] if has_investment else None
        r["abc_profitability_rank"] = prof_rank.get(r["product_code"])
        if not has_investment or r["abc_profitability_rank"] is None:
            # 식별된 투입액이 없으면 정합성을 판단할 근거가 없다. 추정하지 않는다.
            r["rank_misaligned"] = None
        else:
            r["rank_misaligned"] = r["investment_rank"] != r["abc_profitability_rank"]

    fs = {r["item"]: r for r in load("financial_summary")}
    icf_p = as_int(fs["투자활동현금흐름"]["fy2023_krw"])
    icf_c = as_int(fs["투자활동현금흐름"]["fy2024_krw"])

    # --- 절차 간 금액 대응 (인과가 아니라 대응이다) -------------------------
    corr = []
    if cutoff_result and substance_result and divergence_result:
        early = cutoff_result["cutoff_candidates"]["amount_krw"]
        returned_after = sum(
            r["returned_after_period_end_krw"] for r in substance_result["by_customer"]
        )
        ar_delta = next(
            r["delta_krw"]
            for r in divergence_result["working_capital_attribution"]["rows"]
            if r["account_code"] == "108"
        )
        corr.append(
            {
                "left": "기간귀속 오류액 + 보고기간 후 반품확정액",
                "left_krw": early + returned_after,
                "components_krw": {
                    "기간귀속": early,
                    "후속반품확정": returned_after,
                },
                "right": "매출채권 증가액",
                "right_krw": ar_delta,
                "coverage_pct": pct(early + returned_after, ar_delta, 1),
            }
        )
    if window_result and divergence_result:
        rev_amt = window_result["reversed_amount_krw"]
        ocf = divergence_result["profitability_vs_cash"]["영업활동현금흐름"]
        corr.append(
            {
                "left": "기말 일시차입 되돌림액",
                "left_krw": rev_amt,
                "right": "영업활동현금흐름 감소액",
                "right_krw": ocf["prior"] - ocf["current"],
                "coverage_pct": pct(rev_amt, ocf["prior"] - ocf["current"], 1),
            }
        )

    return {
        "procedure": "resource-allocation-coherence",
        "prerequisites_supplied": {
            "cost-driver-alignment": costing_result is not None,
            "cutoff-revenue-recognition": cutoff_result is not None,
            "substance-over-form-sales": substance_result is not None,
            "earnings-cash-divergence": divergence_result is not None,
            "period-end-window-dressing": window_result is not None,
        },
        "resource_allocation": rows,
        "rank_misalignment": [
            r["product_code"] for r in rows if r.get("rank_misaligned")
        ],
        "investment_detail": {
            "capex_by_product": capex_items,
            "capex_unidentified": capex_unid,
            "promotion_by_product": promo_items,
            "promotion_unidentified": promo_unid,
            "capex_total_krw": capex_total,
            "promotion_total_krw": promo_total,
        },
        "capitalizable_expensed": {
            "note": "발주 비고가 자산 요건을 기술하는데 비용 계정에 계상된 건. "
            "자본화 여부의 판단은 하지 않는다. 사실만 제시한다.",
            "count": len(reclass),
            "amount_krw": sum(r["amount_krw"] for r in reclass),
            "items": reclass,
        },
        "investing_cash_flow": {
            "prior_krw": icf_p,
            "current_krw": icf_c,
            "growth_pct": growth_pct(abs(icf_p), abs(icf_c)),
        },
        "cross_procedure_correspondence": corr,
        "notes": [
            "제품 귀속은 적요 문자열의 식별자에 의존한다. 식별자가 없는 건은 "
            "unidentified 로 남기고 배분 추정을 하지 않는다.",
            "이 표는 자원배분의 결과를 보여줄 뿐 경영진의 의도를 판단하지 않는다.",
        ],
    }
