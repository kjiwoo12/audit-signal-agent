"""(주)한빛정밀 FY2024 합성 회계 데이터셋 생성기.

의도적으로 심어둔 이상징후 3종(회계처리 오류 / 현금흐름 / 원가배분 왜곡)이
서로 인과관계를 갖도록 구성되어 있다. 심어둔 내용의 전체 목록은
docs/ANSWER_KEY.md 를 참고할 것. 시드가 고정되어 있어 실행 결과는 항상 동일하다.

사용법:  python data/generate.py
출력:    data/raw/*.csv
"""

import csv
import os
import random
import sys
from datetime import date, timedelta

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SEED = 20260725
random.seed(SEED)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "raw")

# 금액 단위는 전부 '원'. 백만원 단위 설계값에 1e6을 곱해서 사용한다.
M = 1_000_000


def w(name, header, rows):
    """CSV 한 개를 쓴다."""
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  {name:28s} {len(rows):>6,} rows")


def split_exact(total, weights):
    """total을 weights 비율로 나누되 합계가 정확히 total이 되게 한다."""
    s = sum(weights)
    out = [int(total * x / s) for x in weights]
    out[-1] += total - sum(out)
    return out


def biz_day(d):
    """주말이면 다음 월요일로 밀어준다."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# 0. 설계 상수 (단위: 백만원)
# ---------------------------------------------------------------------------

PRODUCTS = {
    "P-A": {"name": "정밀기어", "seg": "성숙/노동집약"},
    "P-B": {"name": "베어링", "seg": "성숙/혼합"},
    "P-C": {"name": "EV 구동부품", "seg": "신규/자본집약"},
}

REV_2024 = {"P-A": 42_000, "P-B": 28_000, "P-C": 18_000}          # 합 88,000
REV_2023 = {"P-A": 40_000, "P-B": 27_000, "P-C": 9_000}           # 합 76,000

DM_2024 = {"P-A": 18_000, "P-B": 12_600, "P-C": 9_900}            # 직접재료비
DL_2024 = {"P-A": 7_560, "P-B": 3_920, "P-C": 1_080}              # 직접노무비
OH_POOL_2024 = 15_000                                              # 제조간접비 총액

# 원가동인 실제 사용량 — 현행 배부기준(직접노무비)과 어긋나는 지점
DRIVERS = {
    "기계시간":       {"P-A": 30_000, "P-B": 24_000, "P-C": 66_000},
    "셋업횟수":       {"P-A": 120, "P-B": 90, "P-C": 390},
    "검사재작업건수": {"P-A": 400, "P-B": 300, "P-C": 1_300},
    "자재출고건수":   {"P-A": 1_200, "P-B": 900, "P-C": 900},
}

# 활동별 간접비 풀 (합계 = OH_POOL_2024)
ACTIVITY_POOLS = [
    ("설비감가상각·전력", "기계시간", 8_400),
    ("셋업·금형교체", "셋업횟수", 3_000),
    ("품질검사·재작업", "검사재작업건수", 2_400),
    ("자재취급", "자재출고건수", 1_200),
]

# 심어둔 이상징후 규모 (단위: 백만원)
A1_CUTOFF = 2_800        # 12월 조기 매출인식 (실제 출고는 익년 1월)
A2_RETURN = 4_500        # 반품조건부 대량출하 → 그중 3,100이 익년 1~2월 반품
A2_RETURNED = 3_100
A3_CAPEX_EXPENSED = 1_450  # 자본적지출을 수선비로 비용처리
B2_WINDOW_2024 = 5_000   # 기말 일시차입 (12/27 유입 → 익년 1/5 상환)
B2_WINDOW_2023 = 3_000
B3_RELATED_MONTHLY = 200  # 특수관계자 월 송금 × 12회

ACCOUNTS = {
    "101": "현금및현금성자산", "108": "매출채권", "115": "미수금",
    "120": "원재료", "121": "재공품", "122": "제품", "131": "선급금",
    "201": "기계장치", "202": "금형", "209": "감가상각누계액",
    "301": "매입채무", "305": "미지급금", "310": "단기차입금",
    "402": "이익잉여금",
    "501": "매출", "502": "매출환입",
    "601": "매출원가",
    "611": "급여(제조)", "612": "감가상각비(제조)", "613": "전력비(제조)",
    "614": "수선비(제조)", "615": "외주가공비(제조)", "616": "소모품비(제조)",
    "701": "급여(판관)", "702": "판매촉진비", "703": "지급수수료",
    "705": "운반비", "706": "접대비", "801": "이자비용",
}

STAFF = ["김민서", "이준호", "박서연", "정우진", "최지아"]
APPROVERS = ["한동석(재무팀장)", "오세훈(관리본부장)"]

gl_rows = []
_voucher_seq = {}


def voucher(d):
    key = d.strftime("%Y%m")
    _voucher_seq[key] = _voucher_seq.get(key, 0) + 1
    return f"JV{d.strftime('%Y%m')}-{_voucher_seq[key]:04d}"


def je(d, lines, memo, dept="재무팀", inputter=None, approver=None):
    """분개 한 건을 gl_rows에 추가한다. lines = [(계정코드, 차변, 대변), ...]"""
    v = voucher(d)
    inputter = inputter or random.choice(STAFF)
    approver = approver or random.choice(APPROVERS)
    for code, dr, cr in lines:
        gl_rows.append([
            v, d.isoformat(), code, ACCOUNTS[code], dr, cr, memo,
            dept, inputter, approver,
        ])
    return v


# ---------------------------------------------------------------------------
# 1. 마스터
# ---------------------------------------------------------------------------

CUSTOMERS = [
    ("C001", "현대모비스", "국내/1차벤더", "일반"),
    ("C002", "만도", "국내/1차벤더", "일반"),
    ("C003", "HL만도터보", "국내/1차벤더", "일반"),
    ("C004", "LG마그나", "국내/1차벤더", "일반"),
    ("C005", "SL미러텍", "국내/2차벤더", "일반"),
    ("C006", "대성정공", "국내/2차벤더", "일반"),
    ("C007", "Bosch Korea", "해외/1차벤더", "일반"),
    ("C008", "Denso Kyushu", "해외/1차벤더", "일반"),
    ("C009", "동아오토모티브", "국내/2차벤더", "일반"),
    ("C010", "세종테크", "국내/2차벤더", "일반"),
    # A2: 반품조건부 대량출하 대상 대리점 3곳
    ("C801", "우성모터스(대리점)", "국내/대리점", "위탁성"),
    ("C802", "삼정오토파츠(대리점)", "국내/대리점", "위탁성"),
    ("C803", "정한상사(대리점)", "국내/대리점", "위탁성"),
]

REGULAR_CUST = [c[0] for c in CUSTOMERS if not c[0].startswith("C8")]
DEALER_CUST = ["C801", "C802", "C803"]


def build_masters():
    w("master_products.csv",
      ["product_code", "product_name", "segment", "launch_year"],
      [[k, v["name"], v["seg"], 2011 if k != "P-C" else 2023]
       for k, v in PRODUCTS.items()])

    w("master_customers.csv",
      ["customer_code", "customer_name", "channel", "contract_type", "credit_term_days"],
      [[c[0], c[1], c[2], c[3], 60 if c[0].startswith("C8") else 45]
       for c in CUSTOMERS])

    w("related_parties.csv",
      ["party_name", "relation", "note"],
      [["(주)한빛홀딩스", "최대주주(대표이사 지분 78%)", "당사 지분 41% 보유"],
       ["한빛정밀(주) 대표이사", "임원", ""],
       ["우성모터스(대리점)", "대표이사 배우자가 지분 60% 보유", "대리점 계약"]])


# ---------------------------------------------------------------------------
# 2. 매출 (판매 시스템) + 물류(출고) + 반품
# ---------------------------------------------------------------------------

MONTH_WEIGHT = [0.075, 0.070, 0.082, 0.080, 0.083, 0.086,
                0.070, 0.078, 0.088, 0.085, 0.086, 0.117]  # 12월 스파이크

invoices = []      # 판매 시스템 원장
shipments = []     # 물류 시스템 (실제 출고)
credit_notes = []  # 반품/매출취소


def build_sales():
    inv_no = 0
    ship_no = 0

    # ---- (1) 정상 매출: 제품별 총액을 월별로 배분 -------------------------
    # A1(조기인식)과 A2(반품조건부)에 쓸 금액은 12월에 따로 심으므로 제외
    seeded = A1_CUTOFF + A2_RETURN
    normal_total = sum(REV_2024.values()) - seeded

    per_product = split_exact(
        normal_total, [REV_2024[p] for p in PRODUCTS])

    for p, ptotal in zip(PRODUCTS, per_product):
        per_month = split_exact(ptotal, MONTH_WEIGHT)
        for mi, mtotal in enumerate(per_month, start=1):
            n = random.randint(14, 20)
            weights = [random.uniform(0.5, 1.8) for _ in range(n)]
            amounts = split_exact(mtotal * M, weights)
            for amt in amounts:
                inv_no += 1
                ship_no += 1
                day = random.randint(1, 28)
                d = biz_day(date(2024, mi, day))
                cust = random.choice(REGULAR_CUST)
                # 정상 건: 출고일 = 인식일 (또는 1~2일 이내)
                sd = biz_day(d - timedelta(days=random.choice([0, 0, 1])))
                invoices.append([
                    f"SI2024-{inv_no:05d}", d.isoformat(), cust, p,
                    amt, "FOB 도착지", f"SO2024-{inv_no:05d}",
                    (d - timedelta(days=random.randint(10, 25))).isoformat(),
                ])
                shipments.append([
                    f"SH2024-{ship_no:05d}", f"SI2024-{inv_no:05d}",
                    sd.isoformat(),
                    biz_day(sd + timedelta(days=random.choice([1, 1, 2]))).isoformat(),
                    random.choice(["1공장창고", "2공장창고", "물류센터"]),
                ])

    # ---- (2) A1: 12월말 조기 매출인식 (실제 출고는 익년 1월) --------------
    n = 9
    weights = [random.uniform(0.7, 1.5) for _ in range(n)]
    amounts = split_exact(A1_CUTOFF * M, weights)
    for i, amt in enumerate(amounts):
        inv_no += 1
        ship_no += 1
        d = date(2024, 12, random.choice([27, 28, 30, 31]))
        # 실제 출고는 익년 1월 초 — 인도조건이 FOB 도착지이므로 통제이전 전
        sd = date(2025, 1, random.choice([2, 3, 6, 7]))
        p = random.choice(["P-A", "P-B", "P-C"])
        cust = random.choice(REGULAR_CUST)
        invoices.append([
            f"SI2024-{inv_no:05d}", d.isoformat(), cust, p,
            amt, "FOB 도착지", f"SO2024-{inv_no:05d}",
            (d - timedelta(days=random.randint(8, 20))).isoformat(),
        ])
        shipments.append([
            f"SH2025-{ship_no:05d}", f"SI2024-{inv_no:05d}",
            sd.isoformat(),
            biz_day(sd + timedelta(days=2)).isoformat(),
            random.choice(["1공장창고", "2공장창고"]),
        ])

    # ---- (3) A2: 대리점 3곳에 12월 대량출하 → 익년 1~2월 대량반품 --------
    dealer_amounts = split_exact(A2_RETURN * M, [1.1, 1.0, 0.9])
    returned_amounts = split_exact(A2_RETURNED * M, [1.1, 1.0, 0.9])
    cn_no = 0
    for cust, ship_amt, ret_amt in zip(DEALER_CUST, dealer_amounts, returned_amounts):
        # 대량출하: 대리점당 4건
        parts = split_exact(ship_amt, [random.uniform(0.8, 1.3) for _ in range(4)])
        for amt in parts:
            inv_no += 1
            ship_no += 1
            d = date(2024, 12, random.choice([18, 20, 23, 26, 27]))
            invoices.append([
                f"SI2024-{inv_no:05d}", d.isoformat(), cust, "P-C",
                amt, "FOB 도착지", f"SO2024-{inv_no:05d}",
                (d - timedelta(days=random.randint(3, 9))).isoformat(),
            ])
            shipments.append([
                f"SH2024-{ship_no:05d}", f"SI2024-{inv_no:05d}",
                d.isoformat(),
                biz_day(d + timedelta(days=2)).isoformat(), "물류센터",
            ])
        # 반품: 대리점당 3건, 익년 1~2월
        rparts = split_exact(ret_amt, [random.uniform(0.8, 1.2) for _ in range(3)])
        for amt in rparts:
            cn_no += 1
            rd = date(2025, random.choice([1, 1, 2]), random.randint(8, 25))
            credit_notes.append([
                f"CN2025-{cn_no:04d}", rd.isoformat(), cust, "P-C", amt,
                random.choice(["미판매 재고 반품", "판매부진 반품", "재고조정 반품"]),
            ])

    # ---- (4) 정상 거래처의 소액 반품 (대조군: 반품률 약 3%) ---------------
    normal_return_base = sum(
        r[4] for r in invoices if not r[2].startswith("C8"))
    target = int(normal_return_base * 0.03)
    parts = split_exact(target, [random.uniform(0.4, 1.6) for _ in range(38)])
    for amt in parts:
        cn_no += 1
        mi = random.randint(2, 12)
        rd = biz_day(date(2024, mi, random.randint(3, 26)))
        credit_notes.append([
            f"CN2024-{cn_no:04d}", rd.isoformat(),
            random.choice(REGULAR_CUST), random.choice(list(PRODUCTS)), amt,
            random.choice(["규격불량 반품", "수량초과 반품", "치수불량 반품"]),
        ])

    invoices.sort(key=lambda r: (r[1], r[0]))
    credit_notes.sort(key=lambda r: (r[1], r[0]))

    w("sales_invoices.csv",
      ["invoice_no", "revenue_date", "customer_code", "product_code",
       "amount_krw", "incoterms", "order_no", "order_date"], invoices)
    w("shipments.csv",
      ["shipment_no", "invoice_no", "actual_ship_date", "arrival_date",
       "warehouse"], shipments)
    w("credit_notes.csv",
      ["credit_note_no", "issue_date", "customer_code", "product_code",
       "amount_krw", "reason"], credit_notes)


# ---------------------------------------------------------------------------
# 3. 원가 시스템 (배부 왜곡의 핵심)
# ---------------------------------------------------------------------------

def build_cost():
    # 현행 배부: 직접노무비 기준 단일 배부율
    dl_total = sum(DL_2024.values())
    current_oh = {p: round(OH_POOL_2024 * DL_2024[p] / dl_total)
                  for p in PRODUCTS}
    diff = OH_POOL_2024 - sum(current_oh.values())
    current_oh["P-A"] += diff

    # 활동기준 배부: 활동별 원가동인 실제 사용량 기준
    abc_oh = {p: 0 for p in PRODUCTS}
    activity_rows = []
    for act, driver, pool in ACTIVITY_POOLS:
        usage = DRIVERS[driver]
        tot = sum(usage.values())
        alloc = {p: round(pool * usage[p] / tot) for p in PRODUCTS}
        alloc["P-C"] += pool - sum(alloc.values())
        for p in PRODUCTS:
            abc_oh[p] += alloc[p]
            activity_rows.append([
                act, driver, pool * M, p, usage[p], tot, alloc[p] * M,
            ])

    rows = []
    for p in PRODUCTS:
        rev = REV_2024[p] * M
        dm, dl = DM_2024[p] * M, DL_2024[p] * M
        oh = current_oh[p] * M
        rows.append([
            p, PRODUCTS[p]["name"], 2024, rev, dm, dl,
            "직접노무비", oh, dm + dl + oh, rev - (dm + dl + oh),
            DRIVERS["기계시간"][p], DRIVERS["셋업횟수"][p],
            DRIVERS["검사재작업건수"][p], DRIVERS["자재출고건수"][p],
        ])
    w("production_cost.csv",
      ["product_code", "product_name", "fiscal_year", "revenue_krw",
       "direct_material_krw", "direct_labor_krw", "oh_allocation_basis",
       "allocated_oh_krw", "total_cost_krw", "gross_profit_krw",
       "machine_hours", "setup_count", "inspection_rework_count",
       "material_issue_count"], rows)

    w("overhead_activities.csv",
      ["activity", "cost_driver", "activity_pool_krw", "product_code",
       "driver_usage", "driver_total", "abc_allocated_krw"], activity_rows)

    # 원가 담당자가 남긴 메모 — 배부기준이 관행이라는 단서
    w("cost_system_notes.csv",
      ["note_date", "author", "note"],
      [["2024-02-14", "원가팀 박서연",
        "제조간접비 배부기준은 2011년 원가계산규정 제정 이후 직접노무비 기준을 계속 적용 중. "
        "P-C 라인 도입 시 배부기준 재검토 안건이 올라왔으나 시스템 변경 부담으로 보류됨."],
       ["2024-08-30", "원가팀 박서연",
        "P-C 라인 셋업 횟수가 월 30회를 넘어 A/B 라인 대비 3배 이상. "
        "다품종 시생산이 많아 금형 교체가 빈번함."],
       ["2024-11-05", "품질팀 정우진",
        "P-C 재작업률 9.2% (A 1.3%, B 1.1%). 재작업 공수는 공통 제조경비로 처리되고 있음."]],
      )
    return current_oh, abc_oh


# ---------------------------------------------------------------------------
# 4. 은행 거래내역 (현금흐름 이상징후)
# ---------------------------------------------------------------------------

bank_rows = []


def build_bank():
    balance = 4_500 * M  # FY2023 기말 현금 (3,000의 일시차입 포함)

    def add(d, memo, dep, wit, acct="신한 110-***-4471"):
        nonlocal balance
        balance += dep - wit
        bank_rows.append([acct, d.isoformat(), memo, dep, wit, balance])

    # B2 전년도 일시차입의 상환 (기초에 바로 빠져나감) — 반복 패턴의 증거
    add(date(2024, 1, 4), "단기차입금 상환 (기업은행)", 0, B2_WINDOW_2023 * M)

    # 월별 정상 거래
    for mi in range(1, 13):
        # 매출채권 회수
        for _ in range(random.randint(9, 14)):
            d = biz_day(date(2024, mi, random.randint(2, 27)))
            add(d, f"매출채권 회수 ({random.choice([c[1] for c in CUSTOMERS[:10]])})",
                random.randint(380, 1_650) * M, 0)
        # 매입채무 지급
        for _ in range(random.randint(7, 11)):
            d = biz_day(date(2024, mi, random.randint(5, 26)))
            add(d, "매입채무 지급 (원재료)", 0, random.randint(320, 1_400) * M)
        # 급여
        add(biz_day(date(2024, mi, 25)), "급여 지급", 0,
            random.randint(1_180, 1_320) * M)
        # 이자
        add(biz_day(date(2024, mi, 20)), "차입금 이자", 0,
            random.randint(58, 92) * M)
        # B3: 특수관계자 월 정기송금 — 적요가 모호함
        add(biz_day(date(2024, mi, 10)), "운영자금 대체 (한빛홀딩스)", 0,
            B3_RELATED_MONTHLY * M)
        # 운영자금 차입 (영업현금 부족을 차입으로 메움)
        if mi in (3, 5, 7, 9, 10, 11):
            add(biz_day(date(2024, mi, 15)), "단기차입금 차입 (신한은행)",
                random.choice([1_500, 2_000, 2_500]) * M, 0)
        # CAPEX — P-C 라인 집중 투자
        if mi in (2, 4, 6, 8, 9, 11):
            add(biz_day(date(2024, mi, 18)),
                "설비대금 지급 (P-C 라인 증설)", 0,
                random.choice([1_200, 1_600, 2_200]) * M)

    # B2: 기말 일시차입 → 익년 즉시 상환 (window dressing)
    add(date(2024, 12, 27), "단기차입금 차입 (기업은행, 만기 2025-01-05)",
        B2_WINDOW_2024 * M, 0)
    add(date(2025, 1, 5), "단기차입금 상환 (기업은행)", 0, B2_WINDOW_2024 * M)

    bank_rows.sort(key=lambda r: r[1])
    # 정렬 후 잔액 재계산
    bal = 4_500 * M
    for r in bank_rows:
        bal += r[3] - r[4]
        r[5] = bal

    w("bank_transactions.csv",
      ["account", "txn_date", "description", "deposit_krw", "withdrawal_krw",
       "balance_krw"], bank_rows)


# ---------------------------------------------------------------------------
# 5. 구매 발주 (자본적지출의 비용처리 단서)
# ---------------------------------------------------------------------------

CAPEX_DISGUISED = [
    ("금형 신규 제작 (P-C 하우징 3종)", 620, "614"),
    ("프레스 설비 증설 (600T 1기)", 480, "614"),
    ("자동화 로봇 추가 도입 (2축 1기)", 220, "614"),
    ("P-C 라인 레이아웃 변경 공사", 130, "614"),
]


def build_purchases():
    rows = []
    po = 0
    for mi in range(1, 13):
        for _ in range(random.randint(10, 15)):
            po += 1
            d = biz_day(date(2024, mi, random.randint(2, 27)))
            item = random.choice([
                "원재료 - 특수강 SCM440", "원재료 - 베어링강 STB2",
                "원재료 - 알루미늄 빌렛", "부재료 - 절삭유",
                "소모품 - 절삭공구", "외주가공 - 열처리",
                "외주가공 - 표면처리", "소모품 - 연마석",
            ])
            acct = "615" if "외주가공" in item else ("616" if "소모품" in item else "120")
            rows.append([
                f"PO2024-{po:05d}", d.isoformat(), item,
                random.randint(120, 980) * M, acct, ACCOUNTS[acct], "",
            ])
    # A3: 실질이 자본적지출인데 수선비로 계정처리된 건
    for i, (item, amt, acct) in enumerate(CAPEX_DISGUISED):
        po += 1
        d = biz_day(date(2024, [5, 7, 9, 11][i], random.randint(8, 22)))
        rows.append([
            f"PO2024-{po:05d}", d.isoformat(), item, amt * M, acct,
            ACCOUNTS[acct], "내용연수 5년 이상 / 취득 후 생산능력 증가 예상",
        ])
    rows.sort(key=lambda r: (r[1], r[0]))
    w("purchase_orders.csv",
      ["po_no", "po_date", "item_description", "amount_krw",
       "posted_account_code", "posted_account_name", "remark"], rows)
    return rows


# ---------------------------------------------------------------------------
# 6. 총계정원장 — 위 서브시스템과 tie-out 되게 생성
# ---------------------------------------------------------------------------

def build_gl(purchase_rows):
    # (1) 매출 인식: 매출채권 / 매출
    for r in invoices:
        d = date.fromisoformat(r[1])
        cust = next(c[1] for c in CUSTOMERS if c[0] == r[2])
        je(d, [("108", r[4], 0), ("501", 0, r[4])],
           f"{r[0]} 매출 인식 ({cust} / {r[3]})", dept="영업관리팀")

    # (2) 반품: 매출환입 / 매출채권
    for r in credit_notes:
        d = date.fromisoformat(r[1])
        if d.year != 2024:
            continue  # 익년 반품은 FY2024 GL에 없음 (A2가 숨는 이유)
        je(d, [("502", r[4], 0), ("108", 0, r[4])],
           f"{r[0]} 매출환입 ({r[5]})", dept="영업관리팀")

    # (3) 은행 거래 대응 전표
    for acct, dstr, memo, dep, wit, _bal in bank_rows:
        d = date.fromisoformat(dstr)
        if d.year != 2024:
            continue
        if dep:
            if "차입" in memo:
                je(d, [("101", dep, 0), ("310", 0, dep)], memo)
            else:
                je(d, [("101", dep, 0), ("108", 0, dep)], memo, dept="영업관리팀")
        else:
            if "매입채무" in memo:
                je(d, [("301", wit, 0), ("101", 0, wit)], memo, dept="구매팀")
            elif "급여" in memo:
                je(d, [("611", int(wit * 0.62), 0), ("701", wit - int(wit * 0.62), 0),
                       ("101", 0, wit)], memo, dept="인사팀")
            elif "이자" in memo:
                je(d, [("801", wit, 0), ("101", 0, wit)], memo)
            elif "상환" in memo:
                je(d, [("310", wit, 0), ("101", 0, wit)], memo)
            elif "설비대금" in memo:
                je(d, [("201", wit, 0), ("101", 0, wit)], memo, dept="생산기술팀")
            elif "한빛홀딩스" in memo:
                # B3: 실질 대여금인데 '미수금'으로 계상, 이자 미수취
                je(d, [("115", wit, 0), ("101", 0, wit)], memo,
                   inputter="김민서", approver="오세훈(관리본부장)")
            else:
                je(d, [("305", wit, 0), ("101", 0, wit)], memo)

    # (4) 구매 전표
    for po, dstr, item, amt, acct, _an, _rm in purchase_rows:
        d = date.fromisoformat(dstr)
        je(d, [(acct, amt, 0), ("301", 0, amt)], f"{po} {item}", dept="구매팀")

    # (5) 월별 제조원가 대체 및 매출원가
    cogs_total = sum(DM_2024.values()) + sum(DL_2024.values()) + OH_POOL_2024
    monthly_cogs = split_exact(cogs_total * M, MONTH_WEIGHT)
    for mi, amt in enumerate(monthly_cogs, start=1):
        d = date(2024, mi, 28)
        je(biz_day(d), [("601", amt, 0), ("122", 0, amt)],
           f"{mi}월 제품 매출원가 대체", dept="원가팀")

    # (6) 월별 제조경비 (감가상각·전력)
    for mi in range(1, 13):
        d = biz_day(date(2024, mi, 28))
        dep_amt = random.randint(410, 470) * M
        pow_amt = random.randint(190, 240) * M
        je(d, [("612", dep_amt, 0), ("209", 0, dep_amt)],
           f"{mi}월 감가상각비 (제조)", dept="원가팀")
        je(d, [("613", pow_amt, 0), ("305", 0, pow_amt)],
           f"{mi}월 전력비", dept="원가팀")

    # (7) 판관비 — P-C 판매촉진비 집중 (숨은 적자 제품에 자원 투입)
    for mi in range(1, 13):
        d = biz_day(date(2024, mi, random.randint(10, 25)))
        pc = random.randint(180, 320) * M
        je(d, [("702", pc, 0), ("305", 0, pc)],
           f"{mi}월 판매촉진비 (P-C 신규라인 프로모션)", dept="마케팅팀")
        for _ in range(random.randint(2, 4)):
            dd = biz_day(date(2024, mi, random.randint(3, 27)))
            amt = random.randint(60, 240) * M
            code = random.choice(["703", "705", "706"])
            je(dd, [(code, amt, 0), ("305", 0, amt)],
               f"{mi}월 {ACCOUNTS[code]}", dept="관리팀")

    gl_rows.sort(key=lambda r: (r[1], r[0]))
    w("gl_journal.csv",
      ["voucher_no", "posting_date", "account_code", "account_name",
       "debit_krw", "credit_krw", "description", "department",
       "prepared_by", "approved_by"], gl_rows)


# ---------------------------------------------------------------------------
# 7. 시산표 / 매출채권 연령분석 / 요약 재무제표
# ---------------------------------------------------------------------------

def build_tb_and_ar():
    tb = [
        # 계정코드, 계정명, FY2023 기말, FY2024 기말  (단위: 백만원)
        ("101", "현금및현금성자산", 4_500, 6_800),
        ("108", "매출채권", 9_400, 18_800),
        ("115", "미수금", 310, 2_710),
        ("120", "원재료", 3_200, 4_100),
        ("121", "재공품", 2_400, 3_600),
        ("122", "제품", 5_100, 7_900),
        ("201", "기계장치", 41_000, 52_400),
        ("202", "금형", 8_600, 9_100),
        ("209", "감가상각누계액", -19_800, -25_100),
        ("301", "매입채무", 8_900, 10_200),
        ("305", "미지급금", 3_100, 3_800),
        ("310", "단기차입금", 12_000, 21_000),
        ("401", "자본금", 5_000, 5_000),
        ("402", "이익잉여금", 25_610, 31_310),
    ]
    w("trial_balance.csv",
      ["account_code", "account_name", "fy2023_ending_krw", "fy2024_ending_krw"],
      [[c, n, a * M, b * M] for c, n, a, b in tb])

    # 매출채권 연령분석 — 대리점 3곳이 장기 미회수로 몰려 있음
    ar = []
    remaining = 18_800
    for code, name, ch, _ct in [(c[0], c[1], c[2], c[3]) for c in CUSTOMERS]:
        if code in DEALER_CUST:
            continue
        amt = random.randint(700, 1_500)
        remaining -= amt
        b0 = int(amt * random.uniform(0.55, 0.75))
        b1 = int(amt * random.uniform(0.15, 0.30))
        b2 = amt - b0 - b1
        ar.append([code, name, ch, amt * M, b0 * M, b1 * M, b2 * M, 0])
    dealer_share = split_exact(remaining, [1.1, 1.0, 0.9])
    for code, amt in zip(DEALER_CUST, dealer_share):
        name = next(c[1] for c in CUSTOMERS if c[0] == code)
        # 전액 12월 출하분 → 30일 이내지만 익년 반품으로 회수되지 않음
        ar.append([code, name, "국내/대리점", amt * M, amt * M, 0, 0, 0])
    w("ar_aging.csv",
      ["customer_code", "customer_name", "channel", "balance_krw",
       "days_0_30_krw", "days_31_60_krw", "days_61_90_krw", "over_90_krw"], ar)

    # 요약 손익 / 현금흐름 (경영진 보고용 — 겉으로 보이는 그림)
    w("financial_summary.csv",
      ["item", "fy2023_krw", "fy2024_krw"],
      [[k, a * M, b * M] for k, a, b in [
          ("매출액", 76_000, 88_000),
          ("매출원가", 59_300, 68_060),
          ("매출총이익", 16_700, 19_940),
          ("판매비와관리비", 9_900, 11_040),
          ("영업이익", 6_800, 8_900),
          ("당기순이익", 5_100, 6_400),
          ("영업활동현금흐름", 6_100, 1_200),
          ("투자활동현금흐름", -4_800, -11_400),
          ("재무활동현금흐름", -600, 8_500),
      ]])


# ---------------------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"(주)한빛정밀 FY2024 합성 데이터셋 생성 (seed={SEED})")
    build_masters()
    build_sales()
    current_oh, abc_oh = build_cost()
    build_bank()
    purchase_rows = build_purchases()
    build_gl(purchase_rows)
    build_tb_and_ar()

    print("\n[참고] 제품별 매출총이익 — 현행 배부 vs 활동기준 배부 (백만원)")
    print(f"  {'제품':<6}{'매출':>9}{'현행GP':>9}{'현행%':>8}{'ABC GP':>10}{'ABC%':>8}")
    for p in PRODUCTS:
        rev = REV_2024[p]
        cur = rev - (DM_2024[p] + DL_2024[p] + current_oh[p])
        abc = rev - (DM_2024[p] + DL_2024[p] + abc_oh[p])
        print(f"  {p:<6}{rev:>9,}{cur:>9,}{cur/rev*100:>7.1f}%"
              f"{abc:>10,}{abc/rev*100:>7.1f}%")
    print("\n  → P-C는 현행 기준으로는 최고 수익 제품이지만, 실제 원가동인 기준으로는 적자.")


if __name__ == "__main__":
    main()
