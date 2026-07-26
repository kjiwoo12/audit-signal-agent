"""정답지를 채점 가능한 형태로 옮긴 것.

원본은 docs/ANSWER_KEY.md 다. 여기 있는 것은 그 문서를 사람이 읽고 판단하는 대신
기계가 대조할 수 있게 만든 판정 규칙이며, **에이전트에게는 어떤 경로로도 주지 않는다.**
scoring/ 은 agent/ 를 import 하지만 그 반대는 없다.

판정을 일부러 엄격하게 잡았다. 채점기가 후하면 점수는 올라가지만 그 점수로
아무것도 알 수 없다. 애매한 것은 맞혔다고 하지 않는다.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 앵커 — 이 발견이 진짜인지 가리는 원천 데이터상의 식별자
#
# 키워드만으로 채점하면 "매출 인식에 문제가 있어 보인다" 같은 문장도 정답이 된다.
# 그래서 앵커를 함께 요구한다. 앵커는 그 항목을 실제로 짚었을 때만 인용할 수 있는
# 전표·송장·계정 번호다. 값이 실재하는지는 테스트가 검사한다.
# ---------------------------------------------------------------------------

CUTOFF_INVOICES = [
    "SI2024-00604",
    "SI2024-00605",
    "SI2024-00606",
    "SI2024-00607",
    "SI2024-00608",
    "SI2024-00609",
    "SI2024-00610",
    "SI2024-00611",
    "SI2024-00612",
]

CAPEX_POS = ["PO2024-00147", "PO2024-00148", "PO2024-00149", "PO2024-00150"]

CONSIGNMENT_CUSTOMERS = ["C801", "C802", "C803"]

MILLION = 1_000_000


class Signal:
    """정답지의 개별 항목 하나."""

    def __init__(
        self,
        id,
        title,
        difficulty,
        expected_procedures,
        keywords,
        anchors=None,
        amounts=None,
        amount_tolerance=0.05,
        note="",
    ):
        self.id = id
        self.title = title
        self.difficulty = difficulty
        self.expected_procedures = expected_procedures
        # keywords: [[대안1, 대안2], [...]] — 그룹마다 하나 이상 있어야 한다 (AND of OR)
        self.keywords = keywords
        # anchors: [{"dataset":…, "field":…, "values":[…]}] — 하나라도 인용하면 통과
        self.anchors = anchors or []
        self.amounts = amounts or []
        self.amount_tolerance = amount_tolerance
        self.note = note


SIGNALS = [
    Signal(
        "A1",
        "기간귀속 오류 (매출 조기인식)",
        "中",
        ["cutoff"],
        [["기간귀속", "조기", "당겨", "인식 시점", "컷오프", "cut-off"],
         ["매출", "수익"]],
        anchors=[{"dataset": "sales_invoices", "field": "invoice_no", "values": CUTOFF_INVOICES}],
        amounts=[2_800 * MILLION],
    ),
    Signal(
        "A2",
        "반품조건부 판매의 총액 인식",
        "上",
        ["substance"],
        [["반품", "위탁", "총액"], ["대리점", "우성", "삼정", "정한", "위탁성"]],
        anchors=[
            {"dataset": "credit_notes", "field": "customer_code", "values": CONSIGNMENT_CUSTOMERS},
            {"dataset": "master_customers", "field": "customer_code", "values": CONSIGNMENT_CUSTOMERS},
        ],
        # 4,500 출하 / 3,100 반품 둘 다 이 항목의 정량화로 인정한다
        amounts=[3_100 * MILLION, 4_500 * MILLION],
    ),
    Signal(
        "A3",
        "자본적지출을 수선비로 비용처리",
        "下",
        ["coherence"],
        [["자본적지출", "자본화", "수선비", "비용처리", "유형자산"]],
        anchors=[{"dataset": "purchase_orders", "field": "po_no", "values": CAPEX_POS}],
        amounts=[1_450 * MILLION],
    ),
    Signal(
        "B1",
        "이익과 영업현금흐름의 괴리",
        "中",
        ["divergence"],
        [["괴리", "현금흐름", "영업활동현금흐름", "영업CF"], ["채권", "DSO", "회수"]],
        # 이 항목만 거래 단위 앵커가 없다. 요약재무제표 자체가 근거이기 때문이다.
        anchors=[{"dataset": "financial_summary", "field": "item", "values": ["영업활동현금흐름", "영업이익"]}],
        amounts=[9_400 * MILLION],
        note="거래 단위 앵커 없음. 요약재무·채권연령이 근거다.",
    ),
    Signal(
        "B2",
        "기말 일시차입에 의한 외형 보정",
        "上",
        ["window"],
        [["차입"], ["기말", "보고기간 말", "12월", "외형", "상환"]],
        anchors=[
            {"dataset": "bank_transactions", "field": "txn_date", "values": ["2024-12-27", "2025-01-05"]},
            {"dataset": "gl_journal", "field": "account_code", "values": ["310"]},
        ],
        amounts=[5_000 * MILLION],
    ),
    Signal(
        "B3",
        "특수관계자 자금 유출의 계정 오분류",
        "中",
        [],  # 어떤 Skill 도 이 항목을 직접 다루지 않는다. 커버리지 공백이다.
        [["특수관계자", "한빛홀딩스", "대여금", "미수금"]],
        anchors=[
            {"dataset": "gl_journal", "field": "account_code", "values": ["115"]},
            {"dataset": "related_parties", "field": "party_name", "values": ["(주)한빛홀딩스"]},
        ],
        amounts=[2_400 * MILLION],
        note="현재 6개 Skill 중 이 항목을 절차로 다루는 것이 없다.",
    ),
    Signal(
        "C1",
        "배부기준 부적합으로 인한 수익성 역전",
        "上",
        ["costing"],
        [["배부", "원가동인", "ABC", "활동기준"], ["P-C", "역전", "적자", "수익성"]],
        anchors=[
            {"dataset": "production_cost", "field": "product_code", "values": ["P-C"]},
            {"dataset": "overhead_activities", "field": "product_code", "values": ["P-C"]},
            {"dataset": "cost_system_notes", "field": "note_date",
             "values": ["2024-02-14", "2024-08-30", "2024-11-05"]},
        ],
        amounts=[7_200 * MILLION, 1_470 * MILLION],
    ),
    Signal(
        "C2",
        "숨은 적자 제품에 대한 자원 집중",
        "上",
        ["coherence"],
        [["투자", "증설", "CAPEX", "판촉", "자원"], ["P-C"], ["적자", "역전", "수익성"]],
        anchors=[
            {"dataset": "gl_journal", "field": "account_code", "values": ["702"]},
            {"dataset": "purchase_orders", "field": "po_no", "values": CAPEX_POS},
            {"dataset": "production_cost", "field": "product_code", "values": ["P-C"]},
        ],
        amounts=[10_000 * MILLION, 12_770 * MILLION],
        amount_tolerance=0.10,
    ),
]

SIGNALS_BY_ID = {s.id: s for s in SIGNALS}

# 개별 항목 7개가 Level 1 의 모수다. C2 는 종합 판단이므로 Level 4 에서 따로 본다.
LEVEL1_IDS = ["A1", "A2", "A3", "B1", "B2", "B3", "C1"]


# ---------------------------------------------------------------------------
# 오탐 함정 — 문제가 아닌 것들. 발견사항으로 보고하면 감점이다.
# ---------------------------------------------------------------------------


class Trap:
    def __init__(self, id, title, keywords, rejection_keywords):
        self.id = id
        self.title = title
        self.keywords = keywords  # 발견사항이 이걸 물면 오탐
        self.rejection_keywords = rejection_keywords  # 기각 기록이 이걸 물면 회피


TRAPS = [
    Trap(
        "T1",
        "정상 거래처의 반품 3.0%",
        [["반품"], ["정상", "일반", "3.0", "3%"]],
        [["반품"], ["정상", "평균", "제조업", "범위"]],
    ),
    Trap(
        "T2",
        "12월 매출 스파이크 자체",
        [["12월", "기말", "연말"], ["급증", "스파이크", "집중", "밀어내기"]],
        [["12월", "계절", "연말"], ["정상", "계절성", "통상", "배수"]],
    ),
    Trap(
        "T3",
        "월중 운영자금 차입 6건",
        [["차입"], ["운영자금", "기중", "월중", "6건"]],
        [["차입"], ["기중", "월중", "운영자금", "만기"]],
    ),
    Trap(
        "T4",
        "출고일이 송장일보다 빠른 정상 건",
        [["선출고", "출고일이", "출고 후"], ["정상", "다수", "먼저"]],
        [["선출고", "출고 후 송장", "출고일이"], ["정상", "프로세스", "관행"]],
    ),
]


# ---------------------------------------------------------------------------
# Level 3·4 — 인과 연결. 이건 규칙으로 판정할 수 없다.
#
# 아래는 판정이 아니라 **사람이 확인할 지점을 좁혀 주는 프록시**다.
# 토큰이 한 서술 안에 같이 나왔다는 것이 인과를 논증했다는 뜻은 아니다.
# 그래서 점수를 주지 않고 '확인 필요'로 표시한다.
# ---------------------------------------------------------------------------

CAUSAL_CONNECTIVES = ["때문", "결과", "따라서", "그래서", "로 인해", "으로 인해", "→", "이어졌", "귀결"]

LEVEL3_TOKENS = [
    ["조기인식", "기간귀속", "조기 인식", "반품조건부", "위탁"],
    ["채권", "DSO", "회수", "영업활동현금흐름", "영업CF"],
]

LEVEL4_TOKENS = [
    ["배부", "원가동인", "ABC", "활동기준"],
    ["P-C"],
    ["증설", "투자", "CAPEX", "판촉", "자원 배분", "자원배분"],
]
