"""CLI. 절차를 실행해 계산 결과를 낸다.

    python -m tools.run                 # 6개 절차 전부, 요약 출력
    python -m tools.run costing         # 특정 절차만
    python -m tools.run --json          # 에이전트에 넘길 원본 JSON
    python -m tools.run --out out/      # 절차별 JSON 파일로 저장

출력에 발견사항·위험등급은 없다. 여기까지가 Python 의 몫이다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import FINAL, PARALLEL, PROCEDURES, SKILL_OF, coherence, run_all
from .loader import mn

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BAR = "─" * 68


def _h(title, skill):
    return f"\n{BAR}\n{title}\n  skills/{skill}.md\n{BAR}"


def fmt_cutoff(r):
    c = r["cutoff_candidates"]
    e = r["early_recognition"]
    cf = r["counter_facts"]
    lines = [
        f"조인: 송장 {r['coverage']['invoices']}건 중 {r['coverage']['joined']}건 매칭"
        f" (물류 데이터 {r['coverage']['shipment_data_max_date']}까지 조회)",
        f"인식일이 통제이전일보다 앞선 건: {e['count']}건 / {mn(e['amount_krw'])}백만원",
        f"  그중 기간 내 상계(재무제표 영향 없음): {e['within_period']['count']}건 /"
        f" {mn(e['within_period']['amount_krw'])}백만원",
        f"기간귀속 후보(인식 당기·통제이전 차기): {c['count']}건 / {mn(c['amount_krw'])}백만원"
        f" (당기매출의 {c['pct_of_period_revenue']}%)",
        f"  대응 매출원가 추정 {mn(c['estimated_cogs_krw'])} → 세전이익 영향 {mn(c['pretax_impact_krw'])}백만원",
        "  상위 5건:",
    ]
    for it in c["items"][:5]:
        lines.append(
            f"    {it['invoice_no']}  인식 {it['revenue_date']} / 통제이전 {it['control_date']}"
            f" ({it['gap_days']}일)  {it['incoterms']}  {mn(it['amount_krw'])}백만원"
        )
    lines += [
        "  [반증자료]",
        f"    선출고 후 송장: {cf['ship_before_invoice']['count']}건 — 대상 아님",
        f"    {cf['period_end_month_revenue']['month']} 매출 {mn(cf['period_end_month_revenue']['amount_krw'])}"
        f" = 타월 평균의 {cf['period_end_month_revenue']['multiple']}배 — 그 자체는 발견사항 아님",
    ]
    return "\n".join(lines)


def fmt_substance(r):
    o = r["overall"]
    lines = [
        f"전체 반품률 {o['return_rate_pct']}% (출하 {mn(o['shipped_krw'])} / 반품 {mn(o['returned_krw'])}백만원)",
        "거래처별 (반품률 순):",
        "  코드   거래처            계약형태  반품률   배수  기말월비중  채권/출하  특수관계",
    ]
    for c in r["by_customer"][:6]:
        rp = c["related_parties"][0]["relation"] if c["related_parties"] else "-"
        lines.append(
            f"  {c['customer_code']}  {c['customer_name']:<16.16} {c['contract_type']:<7}"
            f" {str(c['return_rate_pct']):>6}% {str(c['return_rate_vs_overall']):>5}x"
            f" {str(c['period_end_month_share_pct']):>8}%"
            f" {str(c['ar_to_shipped_ratio']):>8}  {rp:.24}"
        )
    lines.append("반품 사유별:")
    for reason, v in r["return_reasons"].items():
        lines.append(f"  {reason:<16} {v['count']:>3}건  {mn(v['amount_krw']):>9}백만원")
    return "\n".join(lines)


def fmt_divergence(r):
    p = r["profitability_vs_cash"]
    d = r["divergence"]
    t = r["turnover"]
    lines = [
        f"영업이익      {mn(p['영업이익']['prior']):>9} → {mn(p['영업이익']['current']):>9}"
        f"  ({p['영업이익']['growth_pct']:+}%)",
        f"영업활동현금흐름 {mn(p['영업활동현금흐름']['prior']):>9} → {mn(p['영업활동현금흐름']['current']):>9}"
        f"  ({p['영업활동현금흐름']['growth_pct']:+}%)",
        f"괴리 {d['value_pp']:+}%p, 부호 반대: {d['opposite_direction']}"
        f", 현금전환율 {d['cash_conversion_prior_pct']}% → {d['cash_conversion_current_pct']}%",
        f"DSO {t['dso_prior_days']}일 → {t['dso_current_days']}일 ({t['dso_delta_days']:+}일)"
        f", 채권증가율 / 매출증가율 = {t['ar_growth_vs_revenue_growth_multiple']}배",
        f"재무활동현금흐름 부호 전환: {p['재무활동현금흐름']['sign_flipped']}"
        f" ({mn(p['재무활동현금흐름']['prior'])} → {mn(p['재무활동현금흐름']['current'])})",
        "차액이 쌓인 곳 (현금효과 나쁜 순):",
    ]
    for w in r["working_capital_attribution"]["rows"][:4]:
        lines.append(
            f"  {w['account_name']:<8} {mn(w['prior_krw']):>9} → {mn(w['current_krw']):>9}"
            f"  현금효과 {mn(w['cash_effect_krw']):>+9}백만원"
        )
    for u in r["unavailable"]:
        lines.append(f"  [계산 불가] {u['item']} — {u['reason']}")
    return "\n".join(lines)


def fmt_window(r):
    lines = [
        f"조회: 기말 전후 {r['parameters']['window_biz_days']}영업일"
        f" (은행 데이터 {r['parameters']['bank_data_range'][0]}~{r['parameters']['bank_data_range'][1]})",
        f"매칭된 거래 쌍: {r['matched_pair_count']}건, 되돌림액 {mn(r['reversed_amount_krw'])}백만원",
    ]
    for p in r["matched_pairs"]:
        lines.append(
            f"  {p['open_date']} \"{p['open_description']}\" {mn(p['open_amount_krw'])}"
            f" → {p['close_date']} 상환 (보유 {p['holding_biz_days']}영업일, {p['match']})"
        )
        lines.append(f"    전표 {p['open_voucher_no']} / 상환전표 {p['close_voucher_no']}")
    b = r["balance_sheet_effect"]
    lines += [
        f"  현금     {mn(b['현금및현금성자산']['book_krw'])} → 조정 후 {mn(b['현금및현금성자산']['adjusted_krw'])}",
        f"  단기차입금 {mn(b['단기차입금']['book_krw'])} → 조정 후 {mn(b['단기차입금']['adjusted_krw'])}",
        f"  유동비율  {b['유동비율_pct']['book']}% → 조정 후 {b['유동비율_pct']['adjusted']}%"
        f"  (차입이 비율을 개선했는가: {b['유동비율_pct']['borrowing_improves_ratio']})",
        "  [전기 패턴]",
    ]
    for x in r["prior_period_pattern"]["reversals_at_period_start"]:
        lines.append(
            f"    {x['txn_date']} \"{x['description']}\" {mn(x['withdrawal_krw'])}백만원"
            f" (대응 차입 데이터 내 존재: {x['counterpart_in_data']})"
        )
    cf = r["counter_facts"]
    lines.append(
        f"  [반증자료] 보고기간 중간 차입 {cf['mid_period_borrowing_count']}건 /"
        f" {mn(cf['mid_period_borrowing_amount_krw'])}백만원 — 대상 아님"
    )
    return "\n".join(lines)


def fmt_costing(r):
    cb = r["current_basis"]
    oh = r["overhead"]
    lines = [
        f"현행 배부기준: {cb['name']} (비례관계 검증 {cb['proportionality_verified']},"
        f" 최대편차 {cb['proportionality_max_deviation_pct']}%)",
        f"간접비 총액 {mn(oh['total_krw'])}백만원, 총원가의 {oh['share_of_total_cost_pct']}%"
        f", 재배부 합계 일치 {oh['reallocation_ties_out']}",
        "배부기준 구성비 vs 동인 구성비 (%):",
    ]
    mixkeys = [k for k in r["basis_vs_driver_mix"][0] if k.endswith("_share_pct")]
    lines.append("  제품  " + "  ".join(k.replace("_share_pct", "") for k in mixkeys))
    for m in r["basis_vs_driver_mix"]:
        lines.append(
            f"  {m['product_code']}   "
            + "  ".join(f"{m[k]:>6}" for k in mixkeys)
            + f"   최대격차 {m['max_gap_pp']}%p"
        )
    lines.append("제품별 수익성:")
    lines.append("  제품  매출      현행GP%  ABC GP%  차이(%p)  배부차이     순위  부호전환")
    for p in r["profitability"]:
        lines.append(
            f"  {p['product_code']}   {mn(p['revenue_krw']):>8}"
            f"  {p['current_gp_pct']:>7}  {p['abc_gp_pct']:>7}  {p['gp_pct_delta_pp']:>+8}"
            f"  {mn(p['oh_delta_krw']):>+9}  {p['current_gp_rank']}→{p['abc_gp_rank']}"
            f"  {'예' if p['sign_flipped'] else '-'}"
        )
    lines.append(f"재무제표 영향: {r['financial_statement_impact']['reason']}")
    lines.append("담당자 메모:")
    for n in r["cost_system_notes"]:
        lines.append(f"  {n['note_date']} {n['author']}: {n['note'][:60]}…")
    return "\n".join(lines)


def fmt_coherence(r):
    lines = ["자원배분 정합성:", "  제품  ABC GP%   CAPEX     판촉비   비용처리자본지출  투입계  투입순위/수익순위"]
    for x in r["resource_allocation"]:
        lines.append(
            f"  {x['product_code']}   {str(x['abc_gp_pct']):>7}"
            f"  {mn(x['capex_krw']):>8}  {mn(x['promotion_krw']):>8}"
            f"  {mn(x['capitalizable_expensed_krw']):>14}"
            f"  {mn(x['total_identified_investment_krw']):>8}"
            f"   {x['investment_rank']} / {x['abc_profitability_rank']}"
            f"{'  ← 어긋남' if x.get('rank_misaligned') else ''}"
        )
    ce = r["capitalizable_expensed"]
    lines.append(f"비용 계정 계상 발주 중 자산요건 기술 건: {ce['count']}건 / {mn(ce['amount_krw'])}백만원")
    for it in ce["items"]:
        lines.append(
            f"  {it['po_no']} {it['item_description']} {mn(it['amount_krw'])}"
            f" → {it['posted_account_name']} / 전표 {','.join(it['gl_voucher_no']) or '-'}"
        )
        lines.append(f"    비고: {it['remark']}")
    if r["cross_procedure_correspondence"]:
        lines.append("절차 간 금액 대응 (인과 아님, 대응 여부만):")
        for c in r["cross_procedure_correspondence"]:
            lines.append(
                f"  {c['left']} {mn(c['left_krw'])} vs {c['right']} {mn(c['right_krw'])}"
                f" → {c['coverage_pct']}% 설명"
            )
    return "\n".join(lines)


FORMATTERS = {
    "cutoff": fmt_cutoff,
    "substance": fmt_substance,
    "divergence": fmt_divergence,
    "window": fmt_window,
    "costing": fmt_costing,
    "coherence": fmt_coherence,
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="감사 절차의 결정론적 계산을 실행한다.")
    ap.add_argument(
        "procedure",
        nargs="?",
        choices=sorted(PROCEDURES),
        help="생략하면 6개 절차를 모두 실행한다.",
    )
    ap.add_argument("--json", action="store_true", help="원본 JSON 을 stdout 에 출력")
    ap.add_argument("--out", metavar="DIR", help="절차별 JSON 파일로 저장")
    args = ap.parse_args(argv)

    if args.procedure and args.procedure != FINAL:
        results = {args.procedure: PROCEDURES[args.procedure].analyze()}
    elif args.procedure == FINAL:
        base = {n: PROCEDURES[n].analyze() for n in PARALLEL}
        results = {
            FINAL: coherence.analyze(
                costing_result=base["costing"],
                cutoff_result=base["cutoff"],
                substance_result=base["substance"],
                divergence_result=base["divergence"],
                window_result=base["window"],
            )
        }
    else:
        results = run_all()

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for name, res in results.items():
            path = os.path.join(args.out, f"{name}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(res, fh, ensure_ascii=False, indent=2)
            print(f"저장: {path}")
        return 0

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    for name in list(PARALLEL) + [FINAL]:
        if name not in results:
            continue
        print(_h(results[name]["procedure"], SKILL_OF[name]))
        print(FORMATTERS[name](results[name]))
    print(
        f"\n{BAR}\n"
        "위 출력에 발견사항·위험등급·결론은 없다. 계산은 여기까지이고,\n"
        "무엇을 보고하고 무엇을 기각할지는 각 Skill 의 4·5절에서 LLM 이 정한다.\n"
        f"{BAR}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
