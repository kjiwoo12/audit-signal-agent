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

3층을 접어 두는 것이 핵심이다. 펼쳐 놓으면 아무도 1층을 읽지 않고, 빼 버리면
아무도 조서를 믿지 않는다.

## 회계를 모르는 사람도 읽을 수 있어야 한다

조서 서식은 그대로 두고, **용어마다 쉬운 말 설명을 붙인다.** 서식을 풀어 버리면
조서가 아니게 되고, 용어만 두면 읽을 사람이 감사인으로 좁혀진다. 둘 다 필요하다.

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
# 회계·개발 용어를 쓰지 않고 쓴다 — 이 표가 이 문서에서 가장 많이 읽히는 곳이다.
GRADE_MEANING = {
    "A": "전표를 원본에서 찾았고, 인용한 문구도 그 줄에 있었다",
    "B": "전표는 찾았으나, 인용한 문구를 그 줄에서 찾지 못했다",
    "C": "원본에서 전표를 찾지 못했다 — 발견사항에서 빼고 아래 '미확인 가설' 로 내렸다",
}

# 화면에는 한국어로 낸다. 처음 읽는 사람에게 High/Medium 은 한 번 더 번역해야 하는 말이다.
RISK_LABEL = {"High": "높음", "Medium": "보통", "Low": "낮음"}
RISK_ORDER = {"High": 0, "Medium": 1, "Low": 2}

PROCEDURE_TITLES = {
    "cutoff": "수익의 기간귀속 검토",
    "substance": "매출 거래의 실질 검토",
    "divergence": "이익과 현금흐름의 괴리 분석",
    "window": "보고기간 말 이례거래 검토",
    "costing": "제조간접비 배부기준의 정합성 검토",
    "coherence": "자원배분 정합성 종합 판단",
}

# 절차 이름을 한 줄로 풀어 쓴 것. 제목만으로는 무엇을 봤는지 알 수 없다.
PROCEDURE_PLAIN = {
    "cutoff": "매출을 올려도 되는 시점이 맞는지 봤다",
    "substance": "겉모습은 매출인데 실제로는 매출이 아닌 거래를 찾았다",
    "divergence": "이익은 늘었는데 현금은 줄었다면 그 차액이 어디 쌓였는지 봤다",
    "window": "결산일 며칠 사이에만 생기고 곧 되돌려진 거래를 찾았다",
    "costing": "제품별 원가를 나누는 기준이 실제와 맞는지 봤다",
    "coherence": "회사가 돈을 가장 많이 쓰는 제품이 실제로 이익을 내는지 봤다",
}


def _e(v) -> str:
    """HTML 이스케이프. 조서 본문에 사용자 데이터가 그대로 들어가므로 전부 통과시킨다."""
    return html.escape("" if v is None else str(v), quote=True)


def _krw(v) -> str:
    if not isinstance(v, (int, float)):
        return '<span class=dim>산정 불가</span>'
    v = int(v)
    if abs(v) >= 100_000_000:
        return f"{v:,}원<span class=unit>약 {v / 100_000_000:,.1f}억원</span>"
    return f"{v:,}원"


def _risk(g: str) -> str:
    return f'<span class="risk r{_e(g)}">위험 {_e(RISK_LABEL.get(g, g))}</span>'


def _row_table(row: dict) -> str:
    """원천 CSV 한 줄을 표로. 드릴다운의 마지막 층이다."""
    if not row:
        return '<p class="none">원본 줄이 조서에 기록되지 않았다.</p>'
    head = "".join(f"<th>{_e(k)}</th>" for k in row)
    body = "".join(f"<td>{_e(v)}</td>" for v in row.values())
    return (
        '<p class=rowcap>인용한 원본 파일의 해당 줄 — 이 조서가 지어낸 값이 아니다</p>'
        f'<div class=scroll><table class=row><thead><tr>{head}</tr></thead>'
        f"<tbody><tr>{body}</tr></tbody></table></div>"
    )


def _evidence_block(ev: dict) -> str:
    grade = ev.get("grade", "?")
    quote = ev.get("quote")
    parts = [
        f'<div class="ev g{_e(grade)}">',
        '<div class=evhead>',
        f'<span class="chip c{_e(grade)}">근거 {_e(grade)}</span>',
        f'<code>{_e(ev.get("dataset"))}.{_e(ev.get("field"))} = {_e(ev.get("value"))}</code>',
        "</div>",
        f'<p class=note>{_e(GRADE_MEANING.get(grade, ""))}</p>',
    ]
    if quote:
        parts.append(f"<p class=quote>인용한 문구 — “{_e(quote)}”</p>")
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
        f"<tr><th>{_e(d.get('label'))}</th><td class=num>{_e(d.get('value'))}</td></tr>"
        for d in items
    )
    return f"<table class=quant><tbody>{rows}</tbody></table>"


def _finding_detail(i: int, f: dict) -> str:
    grade = f.get("risk_grade", "?")
    proc = f.get("procedure", "")
    ev = f.get("evidence") or []
    out = [
        f'<article class=finding id="f{i}">',
        f'<div class=fhead><span class=fnum>{i}</span>{_risk(grade)}</div>',
        f"<p class=body>{_e(f.get('finding'))}</p>",
        "<dl class=meta>",
        # 절차 코드를 함께 둔다. 이 조서를 skills/ 의 어느 절차로 되짚을지가 그 코드다.
        f"<dt>어느 검토에서</dt><dd>{_e(PROCEDURE_TITLES.get(proc, proc))}"
        f' <code class=key>{_e(proc)}</code>'
        f'<span class=plain>{_e(PROCEDURE_PLAIN.get(proc, ""))}</span></dd>',
        f"<dt>금액으로 따지면</dt><dd class=money>{_krw(f.get('impact_krw'))}</dd>",
    ]
    if f.get("impact_note"):
        out.append(f"<dt>그 금액을 어떻게 냈나</dt><dd>{_e(f['impact_note'])}</dd>")
    if f.get("rule"):
        out.append(
            f"<dt>걸린 조건</dt><dd><code>{_e(f['rule'])}</code>"
            "<span class=plain>이 조건에 걸려서 보고된 건이다</span></dd>"
        )
    out.append("</dl>")

    q = _quantification(f.get("quantification"))
    if q:
        out.append("<h4>숫자로 보기</h4>")
        out.append(q)

    out.append(
        f'<details><summary><span class=caret></span>근거 {len(ev)}건 — '
        f"펼치면 원본 데이터까지 나온다</summary>"
        + ("".join(_evidence_block(e) for e in ev) or '<p class="none">근거가 없다.</p>')
        + "</details>"
    )

    out.append("<h4>정상일 가능성은 검토했나</h4>")
    out.append(
        _list_or_none(
            f.get("rejection_checks"),
            "이 엔진은 기각 조건을 검토하지 않는다. 즉 '정상일 수도 있다'를 "
            "따져본 기록이 없다. 비어 있는 것이 사실이므로 비워 두었다.",
        )
    )
    out.append("<h4>다음에 확인할 것</h4>")
    out.append(_list_or_none(f.get("follow_up"), "제시되지 않았다."))
    out.append("<h4>담당자에게 물을 질문</h4>")
    out.append(_list_or_none(f.get("questions_for_management"), "제시되지 않았다."))
    out.append("</article>")
    return "".join(out)


def _summary_table(findings) -> str:
    rows = []
    for i, f in enumerate(findings, 1):
        text = f.get("finding") or ""
        short = text if len(text) <= 120 else text[:119] + "…"
        rows.append(
            "<tr>"
            f"<td class=cnum>{i}</td>"
            f"<td>{_risk(f.get('risk_grade', '?'))}</td>"
            f'<td><a href="#f{i}">{_e(short)}</a></td>'
            f"<td class=num>{_krw(f.get('impact_krw'))}</td>"
            f'<td><span class="chip c{_e(f.get("evidence_grade"))}">'
            f'근거 {_e(f.get("evidence_grade"))}</span></td>'
            "</tr>"
        )
    return (
        "<div class=scroll><table class=summary><thead><tr>"
        "<th></th><th>위험</th><th>무엇을 발견했나</th><th>금액 영향</th><th>근거</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _intro(entity: str, engine: str) -> str:
    """처음 열어 본 사람이 세 줄 안에 무엇을 보고 있는지 알게 한다."""
    return (
        "<section class=intro>"
        "<h2>이 문서는 무엇인가</h2>"
        f"<p>{_e(entity.split(' — ')[0])}의 회계 자료 <strong>11개 파일</strong>을 프로그램이 "
        "서로 대조해서, 문제로 보이는 것을 정리한 <strong>감사조서</strong>다. "
        "감사조서는 감사인이 무엇을 어떻게 확인했는지 남기는 기록 문서를 말한다. "
        "사람이 쓴 것이 아니라 코드가 만들었다.</p>"
        "<ul class=bullets>"
        "<li><strong>파일 하나만 보면 정상인데, 두 파일을 맞춰 보면 어긋나는 것</strong>을 "
        "찾는다. 매출 장부에는 12월 매출인데 창고 기록으로는 1월에 나간 물건 같은 것이다.</li>"
        "<li>금액은 전부 코드가 계산했다. 프로그램이 어림해서 적은 숫자가 아니다.</li>"
        "<li>발견사항마다 <strong>근거</strong>를 펼치면 인용한 원본 데이터 한 줄이 "
        "그대로 나온다. 믿을지 말지는 그 줄을 보고 판단하면 된다.</li>"
        "<li>가상의 회사·거래로 만든 자료다. 실제 기업과 무관하다.</li>"
        "</ul>"
        f"<p class=note>이 조서의 판단 주체: {_e(engine)}</p>"
        "</section>"
    )


def _conclusion(wp: dict) -> str:
    findings = wp.get("findings") or []
    hyp = wp.get("unverified_hypotheses") or []
    rej = wp.get("rejected_candidates") or []

    by_risk: dict = {}
    for f in findings:
        by_risk[f.get("risk_grade", "?")] = by_risk.get(f.get("risk_grade", "?"), 0) + 1
    risk_s = " · ".join(
        f"{RISK_LABEL.get(k, k)} {by_risk[k]}건"
        for k in sorted(by_risk, key=lambda x: RISK_ORDER.get(x, 9))
    )

    by_grade: dict = {}
    for f in findings:
        g = f.get("evidence_grade", "?")
        by_grade[g] = by_grade.get(g, 0) + 1
    grade_s = " · ".join(f"{k} {by_grade[k]}건" for k in sorted(by_grade))

    total = sum(f["impact_krw"] for f in findings if isinstance(f.get("impact_krw"), int))
    unpriced = sum(1 for f in findings if not isinstance(f.get("impact_krw"), int))

    out = [
        "<section>",
        "<h2>결론<span class=lede>몇 건을 찾았고, 금액이 얼마인지</span></h2>",
        "<div class=cards>",
        f'<div class=card><span class=k>문제로 보고한 건수</span>'
        f'<strong>{len(findings)}건</strong><span class=s>{_e(risk_s) or "—"}</span></div>',
        f'<div class=card><span class=k>금액 영향 (단순 합계)</span>'
        f"<strong>{_krw(total)}</strong>"
        f'<span class=s>{"금액을 낼 수 없는 " + str(unpriced) + "건 제외" if unpriced else "전 건 산정"}</span></div>',
        f'<div class=card><span class=k>근거를 확인한 정도</span>'
        f'<strong>{_e(grade_s) or "—"}</strong>'
        f"<span class=s>증거를 못 찾아 내린 것 {len(hyp)}건</span></div>",
        f'<div class=card><span class=k>정상이라 판단한 기록</span>'
        f"<strong>{len(rej)}건</strong>"
        f"<span class=s>검토했으나 문제 아니라고 본 건수</span></div>",
        "</div>",
        '<p class=caveat><strong>이 합계를 그대로 쓰면 안 된다.</strong> 절차 간 '
        "중복을 걷어내지 않은 단순 합계다. 마지막 검토는 앞선 검토의 발견을 이어 붙여 "
        "판단하므로, 앞에서 센 금액을 다시 세게 된다. 겹치는 부분을 걷어내는 일은 "
        "사람이 해야 한다.</p>",
    ]

    narratives = {k: v for k, v in (wp.get("narratives") or {}).items() if (v or "").strip()}
    out.append(
        "<h3>종합 판단<span class=lede>발견들을 원인과 결과로 이어 붙인 설명</span></h3>"
    )
    if narratives:
        for k, v in narratives.items():
            out.append(
                f"<div class=narrative><h4>{_e(PROCEDURE_TITLES.get(k, k))}</h4>"
                f"<p>{_e(v)}</p></div>"
            )
    else:
        out.append(
            '<p class="none">이 조서를 만든 엔진은 서술을 생성하지 않는다. '
            "개별 발견은 아래 표에 있지만, 그것들을 원인과 결과로 이어 붙인 문장은 없다. "
            "빈칸이 아니라 이 엔진의 설계다.</p>"
        )
    out.append("</section>")
    return "".join(out)


def _how_to_read() -> str:
    rows = "".join(
        f'<tr><td><span class="chip c{k}">근거 {k}</span></td><td>{_e(v)}</td></tr>'
        for k, v in GRADE_MEANING.items()
    )
    terms = (
        ("발견사항", "문제가 있다고 판단해 보고한 것"),
        ("미확인 가설", "말은 맞을 수 있으나 증거를 찾지 못해 등급을 내린 것"),
        ("기각", "문제처럼 보였지만 확인해 보니 정상이라고 판단한 것"),
        ("전표", "거래 하나하나에 붙는 번호. 원본 자료를 찾아가는 주소 역할을 한다"),
    )
    term_rows = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in terms)
    return (
        "<section><h2>이 조서를 읽는 방법"
        "<span class=lede>근거를 어디까지 믿을 수 있는지</span></h2>"
        "<p>발견사항에 붙은 전표번호는 <strong>프로그램의 주장이 아니라, 코드가 원본 "
        "파일에서 실제로 찾아본 결과</strong>다. 원본에서 줄을 찾지 못한 인용은 사람이 "
        "보기 전에 자동으로 등급이 내려가고, 그 발견 전체가 아래 '미확인 가설' 로 "
        "분리된다.</p>"
        f"<table class=legend><tbody>{rows}</tbody></table>"
        "<h3>이 문서에 나오는 말</h3>"
        f"<table class=legend><tbody>{term_rows}</tbody></table>"
        "</section>"
    )


def _hypotheses(hyp) -> str:
    head = (
        "<h2>미확인 가설"
        "<span class=lede>증거를 찾지 못해 발견사항에서 내린 것</span></h2>"
    )
    if not hyp:
        return (
            f"<section>{head}"
            '<p class="none">근거 대조에 실패해 내려간 발견은 없다. '
            "모든 발견사항이 원본 데이터와 대조되었다.</p></section>"
        )
    items = []
    for f in hyp:
        items.append(
            f"<article class=hyp><p class=body>{_e(f.get('finding'))}</p>"
            f"<p class=reason>내린 이유 — {_e(f.get('downgrade_reason'))}</p>"
            + "".join(_evidence_block(e) for e in (f.get("evidence") or []))
            + "</article>"
        )
    return (
        f'<section>{head.replace("미확인 가설", f"미확인 가설 {len(hyp)}건")}'
        "<p>말은 맞을 수 있으나 근거를 대조하지 못했다. <strong>발견사항이 아니다.</strong> "
        "등급을 내린 것은 코드이며, 프로그램이 스스로 봐준 것이 아니다.</p>"
        + "".join(items)
        + "</section>"
    )


def _rejections(rej) -> str:
    head = (
        "<h2>기각 기록"
        "<span class=lede>확인해 봤는데 정상이었다고 판단한 건</span></h2>"
    )
    if not rej:
        return (
            f"<section>{head}"
            '<p class="none">기각 기록이 없다. 이것은 "검토했고 전부 문제였다"는 뜻이 '
            "아니라, <strong>정상 판정을 기록하지 않았다</strong>는 뜻이다. "
            "기준값에 안 걸려서 조용히 빠진 건과, 들여다보고 정상이라고 판단한 건을 "
            "이 조서로는 구별할 수 없다. 감사에서 이 둘은 전혀 다른 일이다.</p>"
            "</section>"
        )
    items = "".join(
        f"<article class=rej><p class=body>{_e(r.get('candidate'))}</p>"
        f"<p class=reason>정상이라고 본 이유 — {_e(r.get('rejection_condition'))}</p></article>"
        for r in rej
    )
    return (
        f'<section>{head.replace("기각 기록", f"기각 기록 {len(rej)}건")}'
        "<p>문제처럼 보였으나 검토 후 정상으로 판단한 건이다. "
        "<strong>기각도 결과다</strong> — 기록이 없으면 검토했는지 알 수 없다.</p>"
        + items
        + "</section>"
    )


def _run_meta(wp: dict) -> str:
    meta = wp.get("run_meta") or {}
    rows = "".join(
        f"<tr><td>{_e(PROCEDURE_TITLES.get(k, k))}"
        f'<span class=plain>{_e(PROCEDURE_PLAIN.get(k, ""))}</span></td>'
        f"<td class=num>{_e(v.get('tool_calls'))}</td>"
        f"<td class=num>{_e(v.get('elapsed_sec'))}초</td>"
        f"<td>{_e(v.get('stopped'))}</td></tr>"
        for k, v in meta.items()
    )
    extra = []
    if wp.get("thresholds"):
        th = " · ".join(f"{k}={v}" for k, v in wp["thresholds"].items())
        extra.append(
            f"<p class=note><strong>기준값</strong> {_e(th)} — 이 자료를 미리 본 사람이 "
            "정한 값이다. 다른 회사 자료에 그대로 적용되지 않는다.</p>"
        )
    u = wp.get("usage") or {}
    if u:
        extra.append(
            f"<p class=note>API 요청 {_e(u.get('requests', 0))}회 · "
            f"입력 {int(u.get('input_tokens', 0)):,} · "
            f"출력 {int(u.get('output_tokens', 0)):,} 토큰</p>"
        )
    return (
        "<section><h2>실행 정보<span class=lede>각 검토가 실제로 무엇을 했나</span></h2>"
        "<div class=scroll><table class=summary><thead><tr>"
        "<th>검토</th><th>데이터 조회</th><th>소요</th><th>종료 사유</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>" + "".join(extra) + "</section>"
    )


CSS = """
:root{
 --ink:#15181c;--ink2:#565f6b;--dim:#8b939e;
 --line:#e7e9ec;--hair:#f0f1f3;--paper:#fff;--bg:#f4f4f2;
 --accent:#1f4b73;
 --hi:#a02c2c;--mid:#96681a;--lo:#3c6291;--ok:#1e6b45;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
 font:16px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI Variable Text","Segoe UI",
 "Malgun Gothic","Apple SD Gothic Neo",sans-serif;
 font-variant-numeric:tabular-nums;letter-spacing:-.003em}
.page{max-width:880px;margin:0 auto;padding:0 28px 96px}

/* 머리글 — 조서 표지 */
header.doc{background:var(--paper);border-bottom:1px solid var(--line);padding:52px 0 30px}
header.doc .page{padding-bottom:0}
.eyebrow{font-size:12px;letter-spacing:.13em;color:var(--dim);margin:0 0 14px;
 text-transform:uppercase}
h1{margin:0;font-size:31px;line-height:1.28;font-weight:660;letter-spacing:-.022em}
.sub{color:var(--ink2);font-size:15px;margin:10px 0 0}
.stamp{display:inline-flex;align-items:center;gap:7px;margin-top:20px;padding:5px 12px 5px 10px;
 border:1px solid var(--line);border-radius:100px;font-size:13px;color:var(--ink2);
 background:var(--bg)}
.stamp::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--ok)}

/* 본문 구획 */
section{background:var(--paper);border:1px solid var(--line);border-radius:10px;
 padding:34px 38px;margin:22px 0}
section.intro{background:linear-gradient(180deg,#fcfcfb,var(--paper));
 border-color:#dfe2e6}
h2{margin:0 0 20px;font-size:20px;font-weight:650;letter-spacing:-.02em;
 display:flex;flex-wrap:wrap;align-items:baseline;gap:0 12px;
 padding-bottom:14px;border-bottom:1px solid var(--hair)}
h3{margin:34px 0 12px;font-size:16.5px;font-weight:650;letter-spacing:-.015em;
 display:flex;flex-wrap:wrap;align-items:baseline;gap:0 10px}
h4{margin:22px 0 7px;font-size:12px;font-weight:650;color:var(--dim);
 letter-spacing:.07em}
.lede{font-size:13.5px;font-weight:400;color:var(--dim);letter-spacing:0}
p{margin:11px 0}
strong{font-weight:650}
code{font:13.5px/1.5 ui-monospace,"Cascadia Mono",Consolas,monospace;
 background:var(--bg);border:1px solid var(--hair);padding:1px 6px;border-radius:4px;
 letter-spacing:0}
code.key{font-size:12px;color:var(--dim);background:none;border:0;padding:0 0 0 2px}
.dim{color:var(--dim)}
.unit{display:block;font-size:12.5px;color:var(--dim);font-weight:400;letter-spacing:0}
.num,.money{text-align:right;font-variant-numeric:tabular-nums}
.cnum{color:var(--dim);font-size:13px;text-align:right;width:28px}
.plain{display:block;font-size:13px;color:var(--dim);letter-spacing:0;margin-top:2px}
ul.bullets{margin:14px 0 0;padding-left:0;list-style:none}
ul.bullets li{position:relative;padding-left:18px;margin:9px 0;color:var(--ink2)}
ul.bullets li::before{content:"";position:absolute;left:2px;top:11px;width:5px;height:5px;
 border-radius:50%;background:var(--dim)}
ul.bullets li strong{color:var(--ink)}

/* 결론 카드 */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));
 gap:1px;background:var(--line);border:1px solid var(--line);border-radius:8px;
 overflow:hidden;margin:6px 0 20px}
.card{background:var(--paper);padding:16px 18px 17px}
.card .k{display:block;font-size:12px;color:var(--dim);letter-spacing:.02em;margin-bottom:7px}
.card strong{display:block;font-size:21px;font-weight:650;line-height:1.25;letter-spacing:-.02em}
.card .s{display:block;font-size:12.5px;color:var(--ink2);margin-top:6px}
.caveat{border-left:2px solid var(--mid);background:#fdfbf6;padding:13px 16px;
 font-size:14.5px;color:var(--ink2);border-radius:0 5px 5px 0}
.caveat strong{color:var(--ink)}

/* 표 */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;
 margin-left:-2px;padding-left:2px}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th,td{text-align:left;padding:12px 14px 12px 0;border-bottom:1px solid var(--hair);
 vertical-align:top}
thead th{font-size:11.5px;color:var(--dim);letter-spacing:.06em;font-weight:650;
 padding-bottom:9px;white-space:nowrap;border-bottom-color:var(--line)}
tbody tr:last-child td{border-bottom:0}
table.summary a{color:var(--ink);text-decoration:none;
 border-bottom:1px solid #d8dce1;padding-bottom:1px}
table.summary a:hover{border-bottom-color:var(--accent);color:var(--accent)}
table.summary tbody tr:hover{background:#fafafa}
table.quant{max-width:460px;margin:8px 0 0}
table.quant th{font-weight:400;color:var(--ink2);width:60%}
table.row{font-size:13px;white-space:nowrap;background:var(--paper)}
table.row thead th{padding-right:22px}
table.row td{padding-right:22px;color:var(--ink2)}
table.legend th{width:110px;font-weight:650}
table.legend td:first-child{width:96px}
.rowcap{font-size:12px;color:var(--dim);margin:12px 0 4px}

/* 위험등급 · 근거등급 */
.risk{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;
 white-space:nowrap}
.risk::before{content:"";width:7px;height:7px;border-radius:50%;flex:none}
.rHigh{color:var(--hi)}.rHigh::before{background:var(--hi)}
.rMedium{color:var(--mid)}.rMedium::before{background:var(--mid)}
.rLow{color:var(--lo)}.rLow::before{background:var(--lo)}
.chip{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:650;
 white-space:nowrap;letter-spacing:.01em;border:1px solid}
.cA{background:#f0f8f3;color:var(--ok);border-color:#cfe7d9}
.cB{background:#fdf7ec;color:var(--mid);border-color:#eddfc6}
.cC{background:#fdf0ef;color:var(--hi);border-color:#f0d3d1}

/* 발견사항 */
.finding{padding-top:30px;margin-top:30px;border-top:1px solid var(--hair)}
.finding:first-of-type{border-top:0;padding-top:8px;margin-top:0}
.fhead{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.fnum{display:inline-flex;align-items:center;justify-content:center;width:25px;height:25px;
 border-radius:50%;background:var(--ink);color:#fff;font-size:12.5px;font-weight:650}
.body{font-size:16.5px;line-height:1.68;letter-spacing:-.01em;margin:0}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:11px 22px;
 margin:18px 0 0;font-size:14.5px;padding:16px 0 2px;border-top:1px solid var(--hair)}
dl.meta dt{color:var(--dim);font-size:13px;padding-top:1px}
dl.meta dd{margin:0}
dl.meta dd.money{text-align:left;font-weight:650}

/* 드릴다운 */
details{margin:20px 0 0;border:1px solid var(--line);border-radius:8px;overflow:hidden}
summary{cursor:pointer;padding:13px 16px;font-size:14px;font-weight:600;
 list-style:none;display:flex;align-items:center;gap:9px;background:#fbfbfa}
summary::-webkit-details-marker{display:none}
summary:hover{background:#f6f6f5}
.caret{width:0;height:0;flex:none;border-left:5px solid var(--dim);
 border-top:4px solid transparent;border-bottom:4px solid transparent;
 transition:transform .15s}
details[open] summary{border-bottom:1px solid var(--line)}
details[open] .caret{transform:rotate(90deg)}
details>*:not(summary){margin-left:16px;margin-right:16px}
details>*:last-child{margin-bottom:16px}
.ev{border-left:2px solid var(--line);padding:0 0 0 16px;margin:18px 0}
.ev.gA{border-left-color:var(--ok)}
.ev.gB{border-left-color:var(--mid)}
.ev.gC{border-left-color:var(--hi)}
.evhead{display:flex;flex-wrap:wrap;align-items:center;gap:9px;margin-bottom:7px}
.quote{margin:6px 0;font-size:14.5px;color:var(--ink2)}
.note{color:var(--ink2);font-size:13.5px;margin:5px 0}
.none{color:var(--ink2);font-size:14.5px;background:#fafaf9;border:1px solid var(--hair);
 border-left:2px solid var(--dim);border-radius:0 6px 6px 0;padding:13px 16px;margin:8px 0}
ul:not(.bullets){margin:8px 0;padding-left:20px;color:var(--ink2)}
ul:not(.bullets) li{margin:5px 0}
.narrative{border-left:2px solid var(--accent);padding:0 0 0 18px;margin:16px 0}
.narrative h4{margin-top:0;color:var(--accent)}
.hyp,.rej{padding-top:20px;margin-top:20px;border-top:1px solid var(--hair)}
.reason{color:var(--hi);font-size:14px;margin:7px 0}
footer{color:var(--dim);font-size:13px;text-align:center;padding:0 28px;line-height:1.7}

@media (max-width:680px){
 .page{padding:0 16px 64px}
 header.doc{padding:34px 0 22px}
 h1{font-size:25px}
 section{padding:24px 20px;margin:14px 0;border-radius:8px}
 dl.meta{grid-template-columns:1fr;gap:3px}
 dl.meta dt{margin-top:10px}
 dl.meta dd.money{text-align:left}
 .cards{grid-template-columns:1fr 1fr}
 details>*:not(summary){margin-left:14px;margin-right:14px}
 .body{font-size:16px}
}
@media (max-width:400px){.cards{grid-template-columns:1fr}}

@media print{
 body{background:#fff;font-size:10.5pt}
 .page{max-width:none;padding:0}
 header.doc{border-bottom:2px solid var(--ink);padding:0 0 12px}
 .stamp{border:0;padding-left:0;background:none}
 section{border:0;border-top:1px solid var(--line);border-radius:0;padding:14px 0;
  margin:0;break-inside:avoid;background:none}
 section.intro{background:none}
 details{border:0;border-radius:0}
 details>*:not(summary){margin-left:0;margin-right:0}
 summary{padding:8px 0;background:none;font-weight:650}
 .caret{display:none}
 .scroll{overflow:visible}
 a{color:inherit;text-decoration:none}
 .cards{border:0;gap:0}
 .card{border:1px solid var(--line);padding:8px 10px}
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

    body = [
        "<header class=doc><div class=page>",
        "<p class=eyebrow>Audit Working Paper</p>",
        f"<h1>{_e(doc_title)}</h1>",
        f"<p class=sub>{_e(detail)}</p>" if detail else "",
        f"<span class=stamp>검토 {len(procs)}건 · 발견사항 {len(findings)}건</span>",
        "</div></header>",
        "<div class=page>",
        _intro(entity, engine),
        _conclusion(wp),
        _how_to_read(),
        "<section><h2>발견사항 요약"
        "<span class=lede>한 줄씩. 누르면 아래 자세한 설명으로 간다</span></h2>",
        _summary_table(findings) if findings else '<p class="none">발견사항이 없다.</p>',
        "</section>",
    ]

    if findings:
        body.append(
            "<section><h2>발견사항 상세"
            "<span class=lede>건별 근거와 금액 산정 과정</span></h2>"
        )
        body.extend(_finding_detail(i, f) for i, f in enumerate(findings, 1))
        body.append("</section>")

    body.append(_hypotheses(wp.get("unverified_hypotheses")))
    body.append(_rejections(wp.get("rejected_candidates")))
    body.append(_run_meta(wp))
    body.append("</div>")
    body.append(
        "<footer>가상의 회사·인물·거래로 구성된 합성 데이터입니다.<br>"
        "실제 기업의 재무정보나 감사 결과와 무관합니다.</footer>"
    )

    return (
        "<!doctype html>\n<html lang=ko>\n<head>\n<meta charset=utf-8>\n"
        '<meta name=viewport content="width=device-width,initial-scale=1">\n'
        f"<title>{_e(doc_title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        + "\n".join(x for x in body if x)
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
