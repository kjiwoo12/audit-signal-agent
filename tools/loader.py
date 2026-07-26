"""데이터 로딩과 공통 계산 유틸.

이 모듈은 판단하지 않는다. CSV를 읽고, 타입을 맞추고, 날짜를 세는 것까지만 한다.
"""

from __future__ import annotations

import csv
import os
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# 경로 / 보고기간
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "raw")

PERIOD_START = date(2024, 1, 1)
PERIOD_END = date(2024, 12, 31)
PRIOR_START = date(2023, 1, 1)
PRIOR_END = date(2023, 12, 31)

MILLION = 1_000_000


# ---------------------------------------------------------------------------
# 로딩
# ---------------------------------------------------------------------------

_cache: dict[str, list[dict]] = {}


def load(name: str) -> list[dict]:
    """data/raw/{name}.csv 를 dict 리스트로 읽는다. BOM 처리 포함."""
    if name in _cache:
        return _cache[name]
    path = os.path.join(DATA_DIR, name + ".csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} 가 없다. 먼저 `python data/generate.py` 를 실행할 것."
        )
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    _cache[name] = rows
    return rows


def index_by(rows: list[dict], key: str) -> dict[str, dict]:
    """단일 키 인덱스. 중복 키가 있으면 예외를 낸다 (조용한 덮어쓰기 방지)."""
    out: dict[str, dict] = {}
    for r in rows:
        k = r[key]
        if k in out:
            raise ValueError(f"중복 키: {key}={k}")
        out[k] = r
    return out


# ---------------------------------------------------------------------------
# 타입 변환
# ---------------------------------------------------------------------------


def as_int(v) -> int:
    """빈 문자열·None 은 0. 금액은 항상 정수(원) 로 다룬다."""
    if v is None:
        return 0
    s = str(v).strip().replace(",", "")
    if not s:
        return 0
    return int(float(s))


def as_date(v):
    if not v:
        return None
    return date.fromisoformat(str(v).strip()[:10])


# ---------------------------------------------------------------------------
# 영업일
# ---------------------------------------------------------------------------


def is_biz_day(d: date) -> bool:
    return d.weekday() < 5


def shift_biz_days(d: date, n: int) -> date:
    """d 로부터 영업일 n 일 이동. n<0 이면 과거 방향.

    공휴일 달력은 적용하지 않는다. 데이터 생성 로직과 동일한 주말 기준이다.
    """
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    cur = d
    while remaining:
        cur += timedelta(days=step)
        if is_biz_day(cur):
            remaining -= 1
    return cur


def biz_days_between(a: date, b: date) -> int:
    """a 초과 b 이하 구간의 영업일 수. a > b 이면 음수."""
    if a == b:
        return 0
    sign = 1 if b > a else -1
    lo, hi = (a, b) if b > a else (b, a)
    n = 0
    cur = lo
    while cur < hi:
        cur += timedelta(days=1)
        if is_biz_day(cur):
            n += 1
    return n * sign


# ---------------------------------------------------------------------------
# 산술
# ---------------------------------------------------------------------------


def ratio(num, den):
    """0 나눗셈에서 예외 대신 None 을 낸다. None 은 '계산 불가'를 뜻한다."""
    if not den:
        return None
    return num / den


def pct(num, den, digits=1):
    r = ratio(num, den)
    return None if r is None else round(r * 100, digits)


def growth_pct(prior, current, digits=1):
    if not prior:
        return None
    return round((current - prior) / abs(prior) * 100, digits)


def split_exact(total: int, weights: list) -> list[int]:
    """total 을 weights 비율로 나누되 합계가 정확히 total 이 되게 한다.

    배부 결과의 합이 원가 풀과 1원이라도 어긋나면 조서로 쓸 수 없다.
    최대잔여법으로 잔차를 배정한다.
    """
    s = sum(weights)
    if not s:
        raise ValueError("가중치 합계가 0이다. 배부할 수 없다.")
    raw = [total * w / s for w in weights]
    out = [int(x) for x in raw]
    rem = total - sum(out)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in range(rem):
        out[order[i % len(order)]] += 1
    if sum(out) != total:
        raise ArithmeticError("배부 합계 불일치")
    return out


def mn(v):
    """원 -> 백만원. 조서 표기 단위."""
    if v is None:
        return None
    return round(v / MILLION, 1)


def iso(d):
    return d.isoformat() if isinstance(d, date) else d
