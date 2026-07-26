"""조서 JSON → 단일 HTML 파일.

    python -m report out/baseline/workpaper.json -o docs/report/baseline.html

## 왜 렌더러가 별도 계층인가

조서의 **내용**은 `agent/` 가 만든다. 여기서는 그 내용을 한 글자도 바꾸지 않고
**읽는 순서만** 정한다. 렌더러가 요약하거나 보태기 시작하면, 화면에 보이는 것과
채점기가 채점한 것이 갈라진다.

## 세 층으로 나누는 이유

경영진과 실무자가 같은 문서를 읽지만 필요한 깊이가 다르다.

    1층  결론          몇 건, 얼마, 무엇이 가장 위험한가
    2층  발견사항 요약표  건별 한 줄
    3층  근거 드릴다운   원천 CSV 행 원문까지

3층을 접어 두는 것이 핵심이다. 펼쳐 놓으면 아무도 1층을 읽지 않고, 없애면
아무도 조서를 믿지 않는다.

## 감추지 않는다

기각 0건, 서술 없음, 근거등급 B — 비어 있거나 약한 항목을 공백으로 처리하지
않고 그 사실을 문장으로 적는다. 조서에서 빈칸은 "검토하지 않았음"과 "검토했으나
해당 없음"을 구별해 주지 못한다.

의존성 없음. 자바스크립트도 쓰지 않는다 (`<details>` 만으로 드릴다운을 만든다).
출력은 결정론적이다 — 같은 조서를 넣으면 항상 같은 바이트가 나온다.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys

# 근거등급의 뜻. agent/evidence.py 가 매기는 등급과 1:1 로 대응한다.
GRADE_MEANING = {
    "A": "전표가 실재하고 인용한 원문도 그 행에 있다",
    "B": "전표는 실재하나 인용한 원문을 그 행에서 찾지 못했다",
    "C": "원천 데이터에서 행을 찾지 못했다 — 미확인 가설로 격하",
}

RISK_ORDER = {"High": 0, "Medium": 1, "Low": 2}

PROCEDURE_TITLES = {
    "cutoff": "수익의 기간귀속 검토",
    "substance": "매출 거래의 실질 검토",
    "divergence": "이익과 현금흐름의 괴리 분석",
    "window": "보고기간 말 이례거래 검토",
    "costing": "제조간접비 배부기준의 정합성 검토",
    "coherence": "자원배분 정합성 종합 판단",
}


def _e(v) -> str:
    """HTML 이스케이프. 조서 본문에 사용자 데이터가 그대로 들어가므로 전부 통과시킨다."""
    return html.escape("" if v is None else str(v), quote=True)


def _krw(v) -> str:
    if not isinstance(v, (int, float)):
        return "산정 불가"
    v = int(v)
    if abs(v) >= 100_000_000:
        return f"{v:,}원 <span class=unit>({v / 100_000_000:,.1f}억원)</span>"
    return f"{v:,}원"


def _row_table(row: dict) -> str:
    """원천 CSV 한 행을 표로. 드릴다운의 마지막 층이다."""
    if not row:
        return '<p class="none">원천 행이 조서에 기록되지 않았다.</p>'
    head = "".join(f"<th>{_e(k)}</th>" for k in row)
    body = "".join(f"<td>{_e(v)}</td>" for v in row.values())
    return f'<div class=scroll><table class=row><thead><tr>{head}</tr></thead><tbody><tr>{body}</tr></tbody></table></div>'


def _evidence_block(ev: dict) -> str:
    grade = ev.get("grade", "?")
    ok = "확인" if ev.get("verified") else "미확인"
    quote = ev.get("quote")
    parts = [
        f'<div class="ev g{_e(grade)}">',
        f'<div class=evhead><code>{_e(ev.get("dataset"))}.{_e(ev.get("field"))}'
        f' = {_e(ev.get("value"))}</code>',
        f'<span class="badge b{_e(grade)}">근거 {_e(grade)}</span>'
        f'<span class="badge bn">{ok}</span></div>',
    ]
    if quote:
        parts.append(f"<p class=quote>인용: “{_e(quote)}”</p>")
    note = ev.get("note")
    if note:
        parts.append(f"<p class=note>{_e(note)}</p>")
    if ev.get("matched_rows") is not None:
        parts.append(f'<p class=note>대조된 행 {_e(ev["matched_rows"])}개</p>')
    parts.append(_row_table(ev.get("row") or {}))
    parts.append("</div>")
    return "".join(parts)


def _list_or_none(items, empty_msg: str) -> str:
    if not items:
        return f'<p class="none">{_e(empty_msg)}</p>'
    lis = "".join(f"<li>{_e(x)}</li>" for x in items)
    return f"<ul>{lis}</ul>"


def _quantification(items) -> str:
    if not items:
        return ""
    rows = "".join(
        f"<tr><th>{_e(d.get('label'))}</th><td>{_e(d.get('value'))}</td></tr>" for d in items
    )
    return f"<table class=quant>{rows}</table>"


def _finding_detail(i: int, f: dict) -> str:
    grade = f.get("risk_grade", "?")
    proc = f.get("procedure", "")
    title = PROCEDURE_TITLES.get(proc, proc)
    out = [
        f'<article class=finding id="f{i}">',
        f'<h3><span class="badge r{_e(grade)}">{_e(grade)}</span> 발견사항 {i}</h3>',
        f"<p class=body>{_e(f.get('finding'))}</p>",
        "<dl class=meta>",
        f"<dt>절차</dt><dd>{_e(title)} <code>{_e(proc)}</code></dd>",
        f"<dt>추정 영향금액</dt><dd>{_krw(f.get('impact_krw'))}</dd>",
    ]
    if f.get("impact_note"):
        out.append(f"<dt>산정 근거</dt><dd>{_e(f['impact_note'])}</dd>")
    if f.get("rule"):
        out.append(f"<dt>적용 규칙</dt><dd><code>{_e(f['rule'])}</code></dd>")
    out.append(f"<dt>근거등급</dt><dd>{_e(f.get('evidence_grade'))}</dd>")
    out.append("</dl>")

    q = _quantification(f.get("quantification"))
    if q:
        out.append("<h4>정량화</h4>")
        out.append(q)

    ev = f.get("evidence") or []
    out.append(
        f"<details><summary>근거 {len(ev)}건 — 원천 데이터까지 펼쳐 보기</summary>"
        + ("".join(_evidence_block(e) for e in ev) or '<p class="none">근거가 없다.</p>')
        + "</details>"
    )

    out.append("<h4>기각 조건 검토</h4>")
    out.append(
        _list_or_none(
            f.get("rejection_checks"),
            "이 엔진은 기각 조건을 검토하지 않는다. 비어 있는 것이 사실이므로 비워 두었다.",
        )
    )
    out.append("<h4>추가 확인 절차</h4>")
    out.append(_list_or_none(f.get("follow_up"), "제시되지 않았다."))
    out.append("<h4>담당자에게 물을 질문</h4>")
    out.append(_list_or_none(f.get("questions_for_management"), "제시되지 않았다."))
    out.append("</article>")
    return "".join(out)


def _summary_table(findings) -> str:
    rows = []
    for i, f in enumerate(findings, 1):
        g = f.get("risk_grade", "?")
        text = f.get("finding") or ""
        short = text if len(text) <= 110 else text[:109] + "…"
        rows.append(
            f"<tr>"
            f'<td><span class="badge r{_e(g)}">{_e(g)}</span></td>'
            f'<td><a href="#f{i}">{_e(short)}</a></td>'
            f"<td class=num>{_krw(f.get('impact_krw'))}</td>"
            f'<td><span class="badge b{_e(f.get("evidence_grade"))}">'
            f'{_e(f.get("evidence_grade"))}</span></td>'
            f"<td><code>{_e(f.get('procedure'))}</code></td>"
            f"</tr>"
        )
    return (
        "<div class=scroll><table class=summary><thead><tr>"
        "<th>위험</th><th>발견사항</th><th>추정 영향금액</th><th>근거</th><th>절차</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _conclusion(wp: dict) -> str:
    findings = wp.get("findings") or []
    hyp = wp.get("unverified_hypotheses") or []
    rej = wp.get("rejected_candidates") or []

    by_risk = {}
    for f in findings:
        by_risk[f.get("risk_grade", "?")] = by_risk.get(f.get("risk_grade", "?"), 0) + 1
    risk_s = " · ".join(
        f"{k} {by_risk[k]}건" for k in sorted(by_risk, key=lambda x: RISK_ORDER.get(x, 9))
    )

    by_grade = {}
    for f in findings:
        g = f.get("evidence_grade", "?")
        by_grade[g] = by_grade.get(g, 0) + 1
    grade_s = " · ".join(f"{k} {by_grade[k]}건" for k in sorted(by_grade))

    total = sum(f["impact_krw"] for f in findings if isinstance(f.get("impact_krw"), int))
    unpriced = sum(1 for f in findings if not isinstance(f.get("impact_krw"), int))

    out = [
        "<section class=conclusion>",
        "<h2>결론</h2>",
        "<div class=cards>",
        f'<div class=card><span class=k>발견사항</span><strong>{len(findings)}건</strong>'
        f"<span class=s>{_e(risk_s) or '—'}</span></div>",
        f'<div class=card><span class=k>추정 영향금액 단순 합계</span><strong>{_krw(total)}</strong>'
        f'<span class=s>{"산정 불가 " + str(unpriced) + "건 제외" if unpriced else "전 건 산정"}</span></div>',
        f'<div class=card><span class=k>근거등급</span><strong>{_e(grade_s) or "—"}</strong>'
        f"<span class=s>미확인 가설 {len(hyp)}건</span></div>",
        f'<div class=card><span class=k>기각 기록</span><strong>{len(rej)}건</strong>'
        f"<span class=s>검토 후 정상 판정한 건수</span></div>",
        "</div>",
        '<p class=caveat><strong>합계를 그대로 쓰지 마십시오.</strong> 절차 간 중복을 '
        "조정하지 않은 단순 합계다. 6번 절차(자원배분 정합성)는 앞선 절차의 발견을 인과로 "
        "엮으므로 같은 금액이 두 번 집계될 수 있다. 중복 조정은 사람이 판단해야 한다.</p>",
    ]

    narratives = {k: v for k, v in (wp.get("narratives") or {}).items() if (v or "").strip()}
    out.append("<h3>종합 판단</h3>")
    if narratives:
        for k, v in narratives.items():
            out.append(
                f"<div class=narrative><h4>{_e(PROCEDURE_TITLES.get(k, k))}</h4>"
                f"<p>{_e(v)}</p></div>"
            )
    else:
        out.append(
            '<p class="none">이 조서를 만든 엔진은 서술을 생성하지 않는다. '
            "개별 발견은 위 표에 있으나, 그것을 인과로 엮은 문장은 없다. "
            "빈칸이 아니라 이 엔진의 설계다.</p>"
        )
    out.append("</section>")
    return "".join(out)


def _how_to_read() -> str:
    rows = "".join(
        f"<tr><td><span class='badge b{k}'>{k}</span></td><td>{_e(v)}</td></tr>"
        for k, v in GRADE_MEANING.items()
    )
    return (
        "<section class=howto><h2>이 조서를 읽는 방법</h2>"
        "<p>발견사항에 붙은 전표번호는 <strong>에이전트의 주장이 아니라 코드가 대조한 "
        "결과</strong>다. 원천 CSV 에서 행을 찾지 못한 인용은 사람이 보기 전에 "
        "자동으로 등급이 내려가고, 그 발견 전체가 미확인 가설로 분리된다.</p>"
        f"<table class=legend><tbody>{rows}</tbody></table>"
        "<p class=note>각 발견사항의 <em>근거 n건</em>을 펼치면 인용된 원천 행을 그대로 "
        "볼 수 있다. 조서를 믿을지 말지는 그 행을 보고 판단하면 된다.</p></section>"
    )


def _hypotheses(hyp) -> str:
    if not hyp:
        return (
            "<section><h2>미확인 가설</h2>"
            '<p class="none">근거 대조에 실패해 격하된 발견은 없다. '
            "모든 발견사항이 원천 데이터와 대조되었다.</p></section>"
        )
    items = []
    for f in hyp:
        items.append(
            f"<article class=hyp><p class=body>{_e(f.get('finding'))}</p>"
            f"<p class=reason>격하 사유: {_e(f.get('downgrade_reason'))}</p>"
            + "".join(_evidence_block(e) for e in (f.get("evidence") or []))
            + "</article>"
        )
    return (
        f"<section><h2>미확인 가설 {len(hyp)}건</h2>"
        "<p>말은 맞을 수 있으나 근거를 대조하지 못했다. <strong>발견사항이 아니다.</strong> "
        "코드가 등급을 내린 것이며 에이전트가 스스로 내린 것이 아니다.</p>"
        + "".join(items)
        + "</section>"
    )


def _rejections(rej) -> str:
    if not rej:
        return (
            "<section><h2>기각 기록</h2>"
            '<p class="none">기각 기록이 없다. 이것은 "검토했고 전부 문제였다"는 뜻이 '
            "아니라, <strong>정상 판정을 기록하지 않았다</strong>는 뜻이다. "
            "임계값 아래로 걸러진 항목과 검토 후 기각한 항목을 이 조서로는 구별할 수 "
            "없다.</p></section>"
        )
    items = "".join(
        f"<article class=rej><p class=body>{_e(r.get('candidate'))}</p>"
        f"<p class=reason>기각 근거: {_e(r.get('rejection_condition'))}</p></article>"
        for r in rej
    )
    return (
        f"<section><h2>기각 기록 {len(rej)}건</h2>"
        "<p>문제처럼 보였으나 검토 후 정상으로 판단한 항목이다. "
        "<strong>기각도 결과다</strong> — 기록이 없으면 검토했는지 알 수 없다.</p>"
        + items
        + "</section>"
    )


def _run_meta(wp: dict) -> str:
    meta = wp.get("run_meta") or {}
    rows = "".join(
        f"<tr><td><code>{_e(k)}</code></td><td>{_e(PROCEDURE_TITLES.get(k, ''))}</td>"
        f"<td class=num>{_e(v.get('turns'))}</td>"
        f"<td class=num>{_e(v.get('tool_calls'))}</td>"
        f"<td class=num>{_e(v.get('elapsed_sec'))}s</td>"
        f"<td>{_e(v.get('stopped'))}</td></tr>"
        for k, v in meta.items()
    )
    extra = []
    if wp.get("thresholds"):
        th = " · ".join(f"{k}={v}" for k, v in wp["thresholds"].items())
        extra.append(
            f"<p class=note>임계값 {_e(th)} — 이 데이터셋을 본 사람이 정한 값이다. "
            "다른 회사 자료에 그대로 적용되지 않는다.</p>"
        )
    u = wp.get("usage") or {}
    if u:
        extra.append(
            f"<p class=note>API 요청 {_e(u.get('requests', 0))}회 · "
            f"입력 {int(u.get('input_tokens', 0)):,} · "
            f"출력 {int(u.get('output_tokens', 0)):,} 토큰</p>"
        )
    return (
        "<section><h2>실행 정보</h2>"
        "<div class=scroll><table class=summary><thead><tr>"
        "<th>절차</th><th></th><th>턴</th><th>도구호출</th><th>소요</th><th>종료 사유</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>" + "".join(extra) + "</section>"
    )


CSS = """
:root{--ink:#16191d;--dim:#5b6472;--line:#dfe3e8;--bg:#fff;--soft:#f6f7f9;
--hi:#b4232a;--mid:#b56b12;--lo:#4a6fa5;--ok:#1f7a4d;--warn:#8a6d1f}
*{box-sizing:border-box}
body{margin:0;background:var(--soft);color:var(--ink);
font:15px/1.65 -apple-system,"Segoe UI","Malgun Gothic",sans-serif;
-webkit-text-size-adjust:100%}
.page{max-width:960px;margin:0 auto;padding:0 20px 80px}
header.doc{background:var(--bg);border-bottom:3px solid var(--ink);padding:28px 0 20px;margin-bottom:28px}
header.doc .page{padding-bottom:0}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:14px;margin:0}
.engine{display:inline-block;margin-top:12px;padding:4px 10px;border:1px solid var(--line);
border-radius:4px;background:var(--soft);font-size:13px;color:var(--dim)}
section{background:var(--bg);border:1px solid var(--line);border-radius:6px;
padding:22px 24px;margin-bottom:20px}
h2{margin:0 0 14px;font-size:18px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{margin:22px 0 8px;font-size:16px}
h4{margin:16px 0 6px;font-size:13px;color:var(--dim);text-transform:none;
letter-spacing:.02em;font-weight:600}
p{margin:8px 0}
code{font:13px/1.5 ui-monospace,"Cascadia Mono",Consolas,monospace;
background:var(--soft);padding:1px 5px;border-radius:3px}
.unit{color:var(--dim);font-size:.9em}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:4px 0 16px}
.card{border:1px solid var(--line);border-radius:5px;padding:12px 14px;background:var(--soft)}
.card .k{display:block;font-size:12px;color:var(--dim);margin-bottom:4px}
.card strong{display:block;font-size:19px;line-height:1.3}
.card .s{display:block;font-size:12px;color:var(--dim);margin-top:4px}
.caveat{border-left:3px solid var(--warn);background:#fdfaf2;padding:10px 14px;font-size:14px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
thead th{background:var(--soft);font-size:12px;color:var(--dim);white-space:nowrap}
table.summary a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--line)}
table.summary a:hover{border-bottom-color:var(--ink)}
table.quant{max-width:520px}
table.quant th{width:45%;font-weight:500;color:var(--dim)}
table.row{font-size:13px;white-space:nowrap}
table.legend td:first-child{width:70px}
.badge{display:inline-block;padding:1px 7px;border-radius:3px;font-size:12px;
font-weight:600;white-space:nowrap;border:1px solid transparent}
.rHigh{background:#fdeceb;color:var(--hi);border-color:#f3cfcd}
.rMedium{background:#fdf4e6;color:var(--mid);border-color:#f0dfc2}
.rLow{background:#eef2f8;color:var(--lo);border-color:#d3ddeb}
.bA{background:#eaf6ef;color:var(--ok);border-color:#c9e6d5}
.bB{background:#fdf4e6;color:var(--mid);border-color:#f0dfc2}
.bC{background:#fdeceb;color:var(--hi);border-color:#f3cfcd}
.bn{background:var(--soft);color:var(--dim);border-color:var(--line);font-weight:500}
.finding{border-top:1px solid var(--line);padding-top:18px;margin-top:22px}
.finding:first-of-type{border-top:0}
.finding h3{margin:0 0 8px;display:flex;align-items:center;gap:8px}
.body{font-size:15px;line-height:1.7}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:12px 0;font-size:14px}
dl.meta dt{color:var(--dim);font-size:13px}
dl.meta dd{margin:0}
details{margin:12px 0;border:1px solid var(--line);border-radius:5px;background:var(--soft)}
summary{cursor:pointer;padding:9px 13px;font-size:14px;font-weight:600;user-select:none}
summary:hover{background:#eef0f3}
details>*:not(summary){margin-left:13px;margin-right:13px}
details>*:last-child{margin-bottom:13px}
.ev{background:var(--bg);border:1px solid var(--line);border-left-width:3px;
border-radius:4px;padding:10px 12px;margin:10px 0}
.ev.gA{border-left-color:var(--ok)}.ev.gB{border-left-color:var(--mid)}
.ev.gC{border-left-color:var(--hi)}
.evhead{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:6px}
.quote{margin:4px 0;font-size:14px}
.note{color:var(--dim);font-size:13px;margin:3px 0}
.none{color:var(--dim);font-size:14px;background:var(--soft);border:1px dashed var(--line);
border-radius:4px;padding:9px 12px;margin:6px 0}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
.narrative{border-left:3px solid var(--lo);padding:2px 14px;margin:12px 0}
.narrative h4{margin-top:6px}
.hyp,.rej{border-top:1px solid var(--line);padding-top:14px;margin-top:16px}
.reason{color:var(--hi);font-size:14px}
footer{color:var(--dim);font-size:13px;text-align:center;padding:0 20px}
@media (max-width:640px){
 .page{padding:0 14px 60px}section{padding:16px 15px;border-radius:0;
 margin-left:-14px;margin-right:-14px}
 dl.meta{grid-template-columns:1fr;gap:2px}
 dl.meta dd{margin-bottom:8px}
 h1{font-size:20px}
}
@media print{
 body{background:#fff}.page{max-width:none}
 section{border:0;border-top:1px solid var(--line);border-radius:0;
 padding:12px 0;margin:0;break-inside:avoid}
 details{border:0;background:none}details>*:not(summary){margin-left:0}
 summary{padding-left:0}details[open] summary{font-weight:600}
 .scroll{overflow:visible}a{color:inherit;text-decoration:none}
}
"""


def _split_entity(entity: str) -> tuple[str, str]:
    """`(주)한빛정밀 — 자동차 부품 제조업, 보고기간 FY2024 …` 를 이름과 나머지로 나눈다.

    조서 제목은 회사명까지다. 업종·보고기간은 제목이 아니라 머리글에 들어간다.
    """
    for sep in (" — ", " - ", ", "):
        if sep in entity:
            head, rest = entity.split(sep, 1)
            return head.strip(), rest.strip()
    return entity.strip(), ""


def render(wp: dict, title: str | None = None) -> str:
    """조서 dict → 완결된 HTML 문자열. 외부 파일을 참조하지 않는다."""
    entity = wp.get("entity") or "감사 대상"
    engine = wp.get("engine") or "LLM 에이전트"
    procs = wp.get("procedures_run") or []
    name, detail = _split_entity(entity)
    doc_title = title or f"{name} 감사조서"
    findings = wp.get("findings") or []

    sub = " · ".join(
        x for x in (detail, f"감사 절차 {len(procs)}개", f"발견사항 {len(findings)}건") if x
    )

    body = [
        '<header class=doc><div class=page>',
        f"<h1>{_e(doc_title)}</h1>",
        f"<p class=sub>{_e(sub)}</p>",
        f"<span class=engine>판단 엔진: {_e(engine)}</span>",
        "</div></header>",
        "<div class=page>",
        _conclusion(wp),
        _how_to_read(),
        "<section><h2>발견사항 요약</h2>",
        _summary_table(findings) if findings else '<p class="none">발견사항이 없다.</p>',
        "</section>",
    ]

    if findings:
        body.append("<section><h2>발견사항 상세</h2>")
        body.extend(_finding_detail(i, f) for i, f in enumerate(findings, 1))
        body.append("</section>")

    body.append(_hypotheses(wp.get("unverified_hypotheses")))
    body.append(_rejections(wp.get("rejected_candidates")))
    body.append(_run_meta(wp))
    body.append("</div>")
    body.append(
        "<footer>가상의 회사·인물·거래로 구성된 합성 데이터입니다. "
        "실제 기업의 재무정보나 감사 결과와 무관합니다.</footer>"
    )

    return (
        "<!doctype html>\n<html lang=ko>\n<head>\n<meta charset=utf-8>\n"
        '<meta name=viewport content="width=device-width,initial-scale=1">\n'
        f"<title>{_e(doc_title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


def write(wp: dict, path: str, title: str | None = None) -> str:
    """조서를 HTML 파일로 쓴다. 상위 디렉터리는 만들어 준다."""
    doc = render(wp, title=title)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    # newline="\n" — 같은 조서가 OS 에 따라 다른 파일이 되면 커밋해 둘 수 없다.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc)
    print(f"저장: {path} ({len(doc):,}자)")
    return doc


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="조서 JSON 을 HTML 로 렌더링한다")
    ap.add_argument("workpaper", help="agent.run 또는 agent.baseline 이 저장한 workpaper.json")
    ap.add_argument("-o", "--out", help="출력 HTML 경로 (기본: 표준출력)")
    ap.add_argument("--title", help="문서 제목")
    args = ap.parse_args(argv)

    with open(args.workpaper, encoding="utf-8") as fh:
        wp = json.load(fh)

    if not args.out:
        print(render(wp, title=args.title))
        return 0

    write(wp, args.out, title=args.title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
