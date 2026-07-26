"""LLM 에 노출하는 도구 4종.

  run_procedure   — 결정론적 계산을 요청한다 (직접 계산 금지의 실현)
  lookup_records  — 원천 CSV 행을 조회한다 (근거 인용용)
  emit_finding    — 발견사항을 제출한다 (근거는 코드가 검증한다)
  emit_rejection  — 후보를 기각한다 (무엇을 기각했는지가 절차의 절반이다)

도구 스키마는 프롬프트다. required 필드를 무엇으로 두는가가 에이전트의 행동을
가장 크게 바꾼다. 기각 조건 검토를 required 로 둔 이유가 그것이다.
"""

from __future__ import annotations

import json

import tools
from tools.loader import load

from . import evidence

MAX_ROWS = 50


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


SCHEMAS = [
    {
        "name": "run_procedure",
        "description": (
            "이 절차의 3절(결정론적 계산)을 실행해 계산 결과를 받는다. "
            "금액·비율·차이·순위는 전부 이 결과에 있다. 직접 계산하지 말 것. "
            "counter_facts 에는 기각 조건 적용에 필요한 반증자료가 함께 들어 있다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "procedure": {
                    "type": "string",
                    "enum": list(tools.PROCEDURES),
                    "description": "실행할 절차. 보통 지금 수행 중인 절차를 넣는다.",
                }
            },
            "required": ["procedure"],
        },
    },
    {
        "name": "lookup_records",
        "description": (
            "원천 CSV 의 행을 그대로 조회한다. 발견사항에 붙일 전표번호·적요 원문을 "
            "확인할 때 쓴다. 집계용이 아니다 — 합계가 필요하면 run_procedure 를 쓴다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "enum": list(evidence.DATASETS),
                    "description": "조회할 파일 (data/raw/{dataset}.csv)",
                },
                "where": {
                    "type": "object",
                    "description": '필드명→값 완전일치. 예: {"account_code":"310"}',
                },
                "contains": {
                    "type": "object",
                    "description": '필드명→부분문자열. 예: {"description":"차입"}',
                },
                "limit": {
                    "type": "integer",
                    "description": f"최대 반환 행 수 (기본 20, 최대 {MAX_ROWS})",
                },
            },
            "required": ["dataset"],
        },
    },
    {
        "name": "emit_finding",
        "description": (
            "발견사항을 조서 형식으로 제출한다. 근거는 코드가 원천 데이터와 대조하며, "
            "대조되지 않으면 '미확인 가설'로 격하된다. 제출 결과를 회신하므로 "
            "격하되었다면 근거를 교체해 다시 제출할 수 있다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "finding": {
                    "type": "string",
                    "description": "발견사항 한두 문장. 무엇이 어떻게 잘못되었는가.",
                },
                "risk_grade": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                    "description": "위험등급. 절차 4절의 기준을 따른다.",
                },
                "impact_krw": {
                    "type": "integer",
                    "description": (
                        "세전이익 영향금액(원). run_procedure 가 낸 값만 쓴다. "
                        "손익 영향이 없으면 0. 산정 불가면 이 필드를 비워 둔다."
                    ),
                },
                "impact_note": {
                    "type": "string",
                    "description": "영향금액 산정 근거 또는 '손익 영향 없음' 등의 설명",
                },
                "quantification": {
                    "type": "array",
                    "description": "조서 정량영향표. 계산 결과에 있는 수치만 옮긴다.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["label", "value"],
                    },
                },
                "evidence": {
                    "type": "array",
                    "description": "원천 데이터 인용. 최소 1건. 코드가 실재를 확인한다.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dataset": {
                                "type": "string",
                                "enum": list(evidence.DATASETS),
                            },
                            "field": {
                                "type": "string",
                                "description": "행을 특정하는 필드 (voucher_no, invoice_no 등)",
                            },
                            "value": {"type": "string"},
                            "quote": {
                                "type": "string",
                                "description": "적요 등 원문 그대로. 요약·의역 금지.",
                            },
                        },
                        "required": ["dataset", "field", "value"],
                    },
                },
                "rejection_checks": {
                    "type": "array",
                    "description": (
                        "절차 5절의 기각 조건 중 검토한 것과, 그것이 이 건에 "
                        "적용되지 않는 이유. 반증자료를 인용할 것."
                    ),
                    "items": {"type": "string"},
                },
                "follow_up": {
                    "type": "array",
                    "description": "추가 확인 절차. 누가 무엇을 하면 되는지 적는다.",
                    "items": {"type": "string"},
                },
                "questions_for_management": {
                    "type": "array",
                    "description": "담당자에게 물을 질문",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "finding",
                "risk_grade",
                "evidence",
                "rejection_checks",
                "follow_up",
                "questions_for_management",
            ],
        },
    },
    {
        "name": "emit_rejection",
        "description": (
            "이상해 보였으나 5절 기각 조건에 따라 기각한 후보를 기록한다. "
            "무엇을 보고했는지만큼 무엇을 기각했는지가 이 절차의 결과다. "
            "기각 기록이 없으면 검토했는지 안 했는지 알 수 없다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate": {
                    "type": "string",
                    "description": "기각한 후보. 무엇이 이상해 보였는가.",
                },
                "rejection_condition": {
                    "type": "string",
                    "description": "적용한 5절 기각 조건",
                },
                "basis": {
                    "type": "string",
                    "description": "기각 근거. counter_facts 의 수치를 인용할 것.",
                },
            },
            "required": ["candidate", "rejection_condition", "basis"],
        },
    },
]


class Toolbox:
    """도구 실행과 결과 수집. 절차 하나당 하나."""

    def __init__(self, procedure: str, precomputed: dict | None = None):
        self.procedure = procedure
        # `or {}` 로 쓰면 빈 dict 가 새 dict 로 바뀌어 공유가 끊긴다.
        # 첫 절차의 계산 결과가 뒤 절차에 전달되지 않는다.
        self.precomputed = {} if precomputed is None else precomputed
        self.findings: list[dict] = []
        self.rejections: list[dict] = []
        self.calls: list[dict] = []

    def schemas(self) -> list:
        return SCHEMAS

    # ------------------------------------------------------------------
    # 디스패치
    # ------------------------------------------------------------------

    def dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        """(도구 결과 문자열, 오류 여부)"""
        handler = {
            "run_procedure": self._run_procedure,
            "lookup_records": self._lookup_records,
            "emit_finding": self._emit_finding,
            "emit_rejection": self._emit_rejection,
        }.get(name)

        if handler is None:
            return f"모르는 도구: {name}", True

        try:
            out = handler(args or {})
            err = False
        except Exception as e:  # 도구 오류는 대화를 끊지 않고 모델에게 돌려준다
            out, err = f"{type(e).__name__}: {e}", True

        self.calls.append({"tool": name, "input": args, "is_error": err})
        return out, err

    # ------------------------------------------------------------------

    def _run_procedure(self, args: dict) -> str:
        name = args.get("procedure") or self.procedure
        if name not in tools.PROCEDURES:
            return f"모르는 절차: {name}. 가능: {', '.join(tools.PROCEDURES)}"

        if name in self.precomputed:
            return _dump(self.precomputed[name])

        if name == tools.FINAL:
            result = tools.coherence.analyze(
                costing_result=self.precomputed.get("costing"),
                cutoff_result=self.precomputed.get("cutoff"),
                substance_result=self.precomputed.get("substance"),
                divergence_result=self.precomputed.get("divergence"),
                window_result=self.precomputed.get("window"),
            )
        else:
            result = tools.PROCEDURES[name].analyze()

        self.precomputed[name] = result
        return _dump(result)

    def _lookup_records(self, args: dict) -> str:
        ds = args.get("dataset")
        if ds not in evidence.DATASETS:
            return f"모르는 데이터셋: {ds}. 가능: {', '.join(evidence.DATASETS)}"

        rows = load(ds)
        where = args.get("where") or {}
        contains = args.get("contains") or {}

        cols = set(rows[0]) if rows else set()
        unknown = [f for f in list(where) + list(contains) if f not in cols]
        if unknown:
            return f"{ds} 에 없는 컬럼: {unknown}. 사용 가능: {sorted(cols)}"

        def keep(r):
            for f, v in where.items():
                if str(r.get(f, "")).strip() != str(v).strip():
                    return False
            for f, v in contains.items():
                if str(v) not in str(r.get(f, "")):
                    return False
            return True

        hit = [r for r in rows if keep(r)]
        limit = min(int(args.get("limit") or 20), MAX_ROWS)
        return _dump(
            {
                "dataset": ds,
                "matched": len(hit),
                "returned": min(len(hit), limit),
                "rows": hit[:limit],
                "note": (
                    "일부만 반환했다. 조건을 좁힐 것."
                    if len(hit) > limit
                    else "전부 반환했다."
                ),
            }
        )

    def _emit_finding(self, args: dict) -> str:
        finding = dict(args)
        finding["procedure"] = self.procedure
        evidence.verify_finding(finding)
        self.findings.append(finding)
        return evidence.feedback_for(finding)

    def _emit_rejection(self, args: dict) -> str:
        rec = dict(args)
        rec["procedure"] = self.procedure
        self.rejections.append(rec)
        return f"기각 기록됨: {rec.get('candidate', '')[:100]}"

    # ------------------------------------------------------------------

    def summary(self) -> dict:
        confirmed = [f for f in self.findings if f["status"] == "발견사항"]
        return {
            "findings": len(confirmed),
            "hypotheses": len(self.findings) - len(confirmed),
            "rejections": len(self.rejections),
            "tool_calls": len(self.calls),
        }
