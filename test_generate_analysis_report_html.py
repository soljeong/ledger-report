#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generate_analysis_report_html import (
    generate_report,
    load_report_spec,
    load_sources,
    render_amount_balance_chart,
    render_report_html,
)


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class GenerateAnalysisReportHtmlTests(unittest.TestCase):
    def test_renders_analysis_overview_and_key_amount_reconciliation(self) -> None:
        sources = load_sources(EXAMPLE_DIR)
        html = render_report_html(sources)
        principal_section = html.split('id="principal-title"', 1)[1]
        amount_table_html = html.split('<table class="amount-table">', 1)[1].split("</table>", 1)[0]

        self.assertIn("<title>매입/매출 재고 분석 보고서</title>", html)
        self.assertIn("요약", html)
        self.assertIn("금액 대사", html)
        self.assertEqual(html.count('class="report-page'), 4)
        self.assertIn("size: A4 portrait;", html)
        self.assertIn("--page-width: 794px;", html)
        self.assertIn('id="amount-balance-chart"', html)
        self.assertIn("밸런스 블록 차트", html)
        self.assertIn("양쪽 합계 1,303,500원", html)
        self.assertIn("매출 + 재고", html)
        self.assertIn("대사 잔여 + 매입", html)
        self.assertIn("추정 매출원가 = 매출수량 × 현재 평균원가", html)
        self.assertIn("대사 잔여금액 = 총 매출금액 + 현재 재고금액 - 총 매입금액", html)
        self.assertIn("주간 매출", html)
        self.assertIn("주간 재고금액 흐름: 매입은 위, 출고는 아래", html)
        self.assertIn("원청별 매출", html)
        self.assertIn("Plotly.newPlot", html)
        self.assertIn("plotly.js", html)
        self.assertNotIn('src="https://cdn.plot.ly', html)
        self.assertIn("매출원가", html)
        self.assertIn("마진율", html)
        self.assertIn("표시 단위: 원", html)
        self.assertNotIn("매입 전표 목록", html)
        self.assertNotIn("총 139개 매입 전표", html)
        self.assertNotIn("매출 거래처별 TOP", html)
        self.assertIn("2026-05-01 ~ 2026-06-19", html)
        self.assertIn("거래 규모", html)
        self.assertIn("매입 상세 4건", html)
        self.assertIn("매출 상세 4건", html)
        self.assertIn("분석 대상", html)
        self.assertIn("매입처 2곳 / 매출처 2곳", html)
        self.assertIn("재고 품목 4개", html)
        self.assertIn('class="summary-list"', html)
        self.assertNotIn('class="cards"', html)
        self.assertNotIn("재고 검증", html)
        self.assertNotIn("보류 항목", html)
        self.assertNotIn("계산 음수재고 2건", html)
        self.assertNotIn("매출만 있는 등록번호 1개", html)

        self.assertIn("1,045,000", html)
        self.assertIn("627,000", html)
        self.assertIn("676,500", html)
        self.assertIn("368,500", amount_table_html)
        self.assertIn("258,500", html)
        self.assertNotIn("최종매입가", html)

        self.assertIn("4/27", html)
        self.assertIn("5/4", html)
        self.assertIn("462,000", html)
        self.assertIn("286,000", html)
        self.assertIn("38.1%", html)

        self.assertIn("Sample Robotics", html)
        self.assertIn("528,000", html)
        self.assertIn("319,000", html)
        self.assertIn("39.6%", html)
        self.assertNotIn("원가 미확인 행수", html)
        self.assertNotIn("매출 품목별 TOP", html)
        self.assertIn("제품별 단가", html)
        self.assertNotIn('<th class="money">수량</th>', principal_section)
        amount_labels = [
            "총 매입금액",
            "총 매출금액",
            "추정 매출원가",
            "현재 재고금액",
            "대사 잔여금액",
        ]
        self.assertEqual(
            [amount_table_html.index(label) for label in amount_labels],
            sorted(amount_table_html.index(label) for label in amount_labels),
        )
        self.assertNotIn("<th>이익</th>", amount_table_html)

        self.assertNotIn("품목별 분석", html)
        self.assertNotIn("전표별 매출 요약", html)

    def test_amount_balance_chart_uses_nonnegative_guard(self) -> None:
        html = render_amount_balance_chart(2_000, 500, 500, -1_000)

        self.assertNotIn('id="amount-balance-chart"', html)
        self.assertIn("모두 0 이상", html)
        self.assertIn("대사 잔여금액", html)

    def test_report_spec_controls_chart_text_and_plotlyjs_mode(self) -> None:
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")
        spec["plotly"]["include_plotlyjs"] = "cdn"
        spec["charts"]["weekly_purchase_sales"]["title"] = "주간 테스트 제목"
        spec["charts"]["weekly_purchase_sales"]["x_axis_title"] = "테스트 X축"
        spec["charts"]["weekly_purchase_sales"]["y_axis_title"] = "테스트 Y축"
        spec["charts"]["weekly_purchase_sales"]["unit_label"] = "테스트 단위"
        html = render_report_html(load_sources(EXAMPLE_DIR), spec)

        self.assertIn("주간 테스트 제목", html)
        self.assertIn("테스트 X축", html)
        self.assertIn("테스트 Y축", html)
        self.assertIn("표시 단위: 테스트 단위", html)
        self.assertIn('src="https://cdn.plot.ly', html)

    def test_writes_report_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            generate_report(EXAMPLE_DIR, output_path)

            html = output_path.read_text(encoding="utf-8")
            self.assertIn("매입/매출 재고 분석 보고서", html)
            self.assertIn("금액 대사", html)
            self.assertIn('id="amount-balance-chart"', html)


if __name__ == "__main__":
    unittest.main()
