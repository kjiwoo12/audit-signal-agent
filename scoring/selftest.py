"""채점기 자체를 검사하기 위한 모범 제출물.

정답지대로 정확히 작성한 조서를 만들어 채점기에 넣는다.
**만점이 안 나오는 채점기로는 아무것도 측정할 수 없다.** 에이전트 점수가 낮게
나왔을 때, 에이전트가 못 한 것인지 채점기가 못 알아본 것인지 가릴 수 없기 때문이다.

여기서 만든 인용은 실제 원천 데이터와 대조를 거친다. 앵커가 하나라도 실재하지
않으면 이 모듈이 먼저 실패한다.
"""

from __future__ import annotations

from agent import evidence

from .answer_key import SIGNALS_BY_ID

# 정답지 각 항목을 '이렇게 쓰면 정답'인 형태로 옮긴 것.
# 앵커는 answer_key 의 첫 번째 값을 쓴다.
MODEL_FINDINGS = [
    {
        "id": "A1",
        "procedure": "cutoff",
        "finding": "기간귀속 오류. FOB 도착지 조건 9건의 매출을 통제 이전 전에 인식했다.",
        "impact_krw": 2_800 * 10**6,
        "impact_note": "FY2024 매출 2,800백만원 과대, 매출채권 동액 과대",
        "evidence": [
            {"dataset": "sales_invoices", "field": "invoice_no", "value": "SI2024-00604"}
        ],
    },
    {
        "id": "A2",
        "procedure": "substance",
        "finding": "대리점 3사에 대한 반품조건부 출하를 총액 매출로 인식했다. 실질은 위탁판매다.",
        "impact_krw": 3_100 * 10**6,
        "impact_note": "익년 반품 3,100백만원. 반품 시점까지 수익 인식 불가",
        "evidence": [
            {"dataset": "credit_notes", "field": "customer_code", "value": "C801"}
        ],
    },
    {
        "id": "A3",
        "procedure": "coherence",
        "finding": "자본적지출에 해당하는 설비 취득 4건을 수선비로 비용처리했다.",
        "impact_krw": 1_450 * 10**6,
        "impact_note": "비용 1,450백만원 과대, 유형자산 과소",
        "evidence": [
            {"dataset": "purchase_orders", "field": "po_no", "value": "PO2024-00147"}
        ],
    },
    {
        "id": "B1",
        "procedure": "divergence",
        "finding": "영업이익과 영업활동현금흐름의 괴리. 차액이 매출채권에 쌓였고 DSO 가 45일에서 78일로 늘었다.",
        "impact_krw": 9_400 * 10**6,
        "impact_note": "매출채권 9,400백만원 증가",
        "evidence": [
            {"dataset": "financial_summary", "field": "item", "value": "영업활동현금흐름"}
        ],
    },
    {
        "id": "B2",
        "procedure": "window",
        "finding": "보고기간 말 단기차입 후 익년 즉시 상환. 재무상태표 외형 보정이다.",
        "impact_krw": 0,
        "impact_note": "손익 영향 없음. 재무상태표 표시의 문제",
        "quantification": [
            {"label": "단기차입금", "value": "5,000백만원"},
            {"label": "기말 현금", "value": "6,800 → 조정 후 1,800백만원"},
        ],
        "evidence": [
            {"dataset": "bank_transactions", "field": "txn_date", "value": "2024-12-27"}
        ],
    },
    {
        "id": "B3",
        "procedure": "manual",
        "finding": "특수관계자에 대한 자금 유출을 미수금으로 계상했다. 실질은 무이자 대여금이다.",
        "impact_krw": 2_400 * 10**6,
        "impact_note": "월 200백만원 × 12회. 이자수익 계상 없음",
        "evidence": [{"dataset": "gl_journal", "field": "account_code", "value": "115"}],
    },
    {
        "id": "C1",
        "procedure": "costing",
        "finding": "제조간접비 배부기준이 실제 원가동인과 불일치한다. 재배부하면 P-C 수익성이 역전된다.",
        "impact_krw": 7_200 * 10**6,
        "impact_note": "P-C 가 간접비 7,200백만원을 과소 부담. GP 31.8% → -8.2%",
        "evidence": [
            {"dataset": "production_cost", "field": "product_code", "value": "P-C"}
        ],
    },
    {
        "id": "C2",
        "procedure": "coherence",
        "finding": "실제로는 적자인 P-C 라인에 설비 증설 투자와 판촉비가 집중되어 있다.",
        "impact_krw": 12_770 * 10**6,
        "impact_note": "P-C 관련 식별된 투입액 합계",
        "evidence": [{"dataset": "gl_journal", "field": "account_code", "value": "702"}],
    },
]

MODEL_REJECTIONS = [
    {
        "candidate": "정상 거래처의 반품 38건",
        "rejection_condition": "제조업 통상 반품률 범위",
        "basis": "정상 거래처 반품률 3.0%. 대리점 68.9% 와 대조된다",
    },
    {
        "candidate": "12월 매출 급증",
        "rejection_condition": "계절성",
        "basis": "자동차 부품업 연말 밀어내기는 통상적. 12월 매출은 타월 평균의 2.59배",
    },
    {
        "candidate": "보고기간 중 차입 6건",
        "rejection_condition": "만기가 기말에 걸쳐 있지 않은 운영자금 거래",
        "basis": "기중 차입 6건은 만기가 보고기간 말과 무관하다",
    },
    {
        "candidate": "출고일이 송장일보다 빠른 건",
        "rejection_condition": "선출고 후 송장 발행",
        "basis": "선출고는 정상 프로세스다. 통제 이전이 먼저 일어난 것이므로 조기인식이 아니다",
    },
]

NARRATIVE = (
    "원가 배부기준이 실제 원가동인과 어긋나 P-C 가 적자임에도 최고 수익 제품으로 "
    "보였고, 그 결과 P-C 라인 증설 투자와 판촉비가 집중되었다. 투자 회수 압박이 "
    "매출 목표로 이어졌고, 그로 인해 기말 조기인식과 반품조건부 출하가 발생했다. "
    "그 매출이 현금으로 회수되지 않아 매출채권이 늘고 DSO 가 78일로 악화되었으며, "
    "영업활동현금흐름 감소를 기말 단기차입으로 가렸다."
)


def perfect_submission() -> dict:
    """정답지대로 작성한 runs.json 을 만든다. 인용은 원천 데이터와 대조한다."""
    runs = {}
    for item in MODEL_FINDINGS:
        signal = SIGNALS_BY_ID[item["id"]]
        f = {k: v for k, v in item.items() if k != "id"}
        f.setdefault("risk_grade", "High")
        f["answer_key_id"] = signal.id
        evidence.verify_finding(f)
        if f["status"] != "발견사항":
            raise AssertionError(
                f"{signal.id} 의 모범답안 인용이 원천 데이터와 대조되지 않는다: "
                f"{f['downgrade_reason']}"
            )
        proc = item["procedure"]
        runs.setdefault(
            proc,
            {"procedure": proc, "findings": [], "rejections": [], "narrative": ""},
        )
        runs[proc]["findings"].append(f)

    runs.setdefault(
        "cutoff", {"procedure": "cutoff", "findings": [], "rejections": [], "narrative": ""}
    )
    runs["cutoff"]["rejections"] = MODEL_REJECTIONS
    runs["coherence"]["narrative"] = NARRATIVE
    return runs
