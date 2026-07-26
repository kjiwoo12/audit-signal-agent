"""인용 검증.

README 4.2 를 코드로 만든 부분이다.

에이전트는 그럴듯한 전표번호를 만들어낼 능력이 충분하다. 그래서 인용을 믿지 않고
원천 CSV 와 대조한다. 대조되지 않는 근거가 하나라도 있으면 그 발견사항은
**발견사항이 아니라 미확인 가설**로 격하된다. 격하는 LLM 이 아니라 여기서 정한다.

이 모듈도 판단하지 않는다. "이 인용이 실재하는가"만 본다.
그 발견이 옳은가는 다른 문제다.
"""

from __future__ import annotations

from tools.loader import load

# 데이터셋별로 행을 특정할 수 있는 필드. 여기 없는 필드로 인용하면 검증 불가로 본다.
CITABLE_FIELDS = {
    "sales_invoices": ["invoice_no", "order_no", "customer_code", "product_code"],
    "shipments": ["shipment_no", "invoice_no"],
    "credit_notes": ["credit_note_no", "customer_code", "product_code"],
    "gl_journal": ["voucher_no", "account_code", "posting_date"],
    "bank_transactions": ["txn_date"],
    "purchase_orders": ["po_no", "posted_account_code"],
    "production_cost": ["product_code"],
    "overhead_activities": ["activity", "product_code"],
    "cost_system_notes": ["note_date", "author"],
    "ar_aging": ["customer_code"],
    "master_customers": ["customer_code"],
    "master_products": ["product_code"],
    "related_parties": ["party_name"],
    "trial_balance": ["account_code"],
    "financial_summary": ["item"],
}

DATASETS = tuple(CITABLE_FIELDS)

# 인용의 근거등급. Skill 6절이 요구하는 수준과 대응한다.
GRADE_A = "A"  # 원문 인용까지 있고 실재가 확인됨
GRADE_B = "B"  # 실재는 확인됐으나 원문 인용이 없음
GRADE_C = "C"  # 대조 불가


def verify_citation(item: dict) -> dict:
    """근거 1건을 원천 데이터와 대조한다."""
    out = {
        "dataset": item.get("dataset", ""),
        "field": item.get("field", ""),
        "value": str(item.get("value", "")),
        "quote": item.get("quote", "") or "",
        "verified": False,
        "matched_rows": 0,
        "grade": GRADE_C,
        "note": "",
    }

    ds, field, value = out["dataset"], out["field"], out["value"]

    if ds not in CITABLE_FIELDS:
        out["note"] = f"모르는 데이터셋: {ds}"
        return out
    if field not in CITABLE_FIELDS[ds]:
        out["note"] = (
            f"{ds} 에서 행을 특정할 수 없는 필드다: {field}. "
            f"사용 가능: {', '.join(CITABLE_FIELDS[ds])}"
        )
        return out

    rows = [r for r in load(ds) if str(r.get(field, "")).strip() == value.strip()]
    out["matched_rows"] = len(rows)
    if not rows:
        out["note"] = f"{ds}.{field} = {value} 인 행이 원천 데이터에 없다"
        return out

    out["verified"] = True
    out["row"] = rows[0]

    quote = out["quote"].strip()
    if not quote:
        out["grade"] = GRADE_B
        out["note"] = "실재 확인. 원문 인용이 없어 근거등급 B"
        return out

    # 원문 인용은 실제로 그 행 어딘가에 있어야 한다. 지어낸 적요를 걸러낸다.
    joined = " ".join(str(v) for v in rows[0].values())
    if quote in joined:
        out["grade"] = GRADE_A
        out["note"] = "실재·원문 모두 확인"
    else:
        out["grade"] = GRADE_B
        out["note"] = f"행은 실재하나 인용문이 그 행에 없다: {quote!r}"
    return out


def verify_finding(finding: dict) -> dict:
    """발견사항 전체를 검증하고 필요하면 등급을 격하한다."""
    checked = [verify_citation(e) for e in finding.get("evidence") or []]
    finding["evidence"] = checked

    ok = [c for c in checked if c["verified"]]
    bad = [c for c in checked if not c["verified"]]

    finding["evidence_grade"] = (
        GRADE_C
        if not ok
        else GRADE_A
        if all(c["grade"] == GRADE_A for c in ok)
        else GRADE_B
    )

    if not checked:
        finding["status"] = "미확인 가설"
        finding["downgrade_reason"] = "근거 인용이 없다"
    elif bad:
        finding["status"] = "미확인 가설"
        finding["downgrade_reason"] = "원천 데이터와 대조되지 않는 근거: " + "; ".join(
            f"{c['dataset']}.{c['field']}={c['value']} ({c['note']})" for c in bad
        )
    else:
        finding["status"] = "발견사항"
        finding["downgrade_reason"] = None

    return finding


def feedback_for(finding: dict) -> str:
    """제출 직후 에이전트에게 돌려줄 문장.

    격하 사실을 알려주면 에이전트가 근거를 교체해 다시 제출할 수 있다.
    조용히 격하하면 에이전트는 자기가 무엇을 잘못했는지 모른 채 끝난다.
    """
    lines = [
        f"제출됨: {finding['status']} (근거등급 {finding['evidence_grade']})",
        f"  {finding.get('finding', '')[:120]}",
    ]
    for c in finding["evidence"]:
        mark = "확인" if c["verified"] else "확인 실패"
        lines.append(f"  [{mark}] {c['dataset']}.{c['field']}={c['value']} — {c['note']}")
    if finding["status"] == "미확인 가설":
        lines.append(
            "이 발견은 미확인 가설로 격하되었다. "
            "실재하는 전표·송장·거래 번호로 근거를 교체해 다시 제출할 수 있다. "
            "찾을 수 없다면 격하된 상태로 두라. 지어낸 번호를 붙이는 것보다 낫다."
        )
    return "\n".join(lines)
