# Ledger Workbook Analysis

![Report preview](docs/assets/report-preview.png)

Excel 매입/매출/재고 장부를 JSON으로 정규화하고, `product_id`별 FIFO 재고원가·매출원가와 날짜 기준 재고수량 대사를 계산하는 분석 도구입니다. 공개 레포에는 샘플 JSON과 미리보기 PNG만 포함하고, 실제 원본/중간산출물/리포트는 Git에서 제외합니다.

## Layout

- `src/charts.py`: pandas `DataFrame`을 받아 Plotly `Figure`를 반환하는 차트 함수
- `examples/dummy_json/`: 공개 가능한 더미 JSON
- `docs/assets/report-preview.png`: 더미 데이터로 만든 보고서 스크린샷
- `private_sources/`: 실제 Excel 원본 보관 위치, Git 제외
- `private_intermediate/`: 실제 변환 JSON, 검증 파일, 전표별 Excel, Git 제외
- `private_reports/`: 실제 HTML/PNG 보고서, Git 제외

## Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Demo Report

데모 분석기간은 `2026-05-10`~`2026-05-20`, 재고 기준일은 `2026-05-25`입니다. 데모에는 복수 매입단가, 기초재고, 후속 매입 소급배정, 미확정 출고, 음수 매입·매출 및 기준일 후 수량대사가 포함되어 있습니다.

```bash
.venv/bin/python analyze_inventory.py \
  --base-dir examples/dummy_json \
  --period-start 2026-05-10 \
  --period-end 2026-05-20 \
  --inventory-date 2026-05-25 \
  --json-out examples/dummy_json/inventory_reconciliation.json \
  --md-out /tmp/inventory_reconciliation.md
```

```bash
.venv/bin/python generate_analysis_report_html.py \
  --input-dir examples/dummy_json \
  --output private_reports/demo_report.html

.venv/bin/python capture_html_a4.py \
  --input private_reports/demo_report.html \
  --output docs/assets/report-preview.png
```

`report_spec.yaml`에서 보고서 제목, Plotly 포함 방식, 차트 제목, 축 이름, 표시 단위를 조정합니다. 기본값은 Plotly JS를 HTML에 inline 포함해서 오프라인으로 열 수 있게 합니다.

## Private Workflow

```bash
.venv/bin/python parse_workbooks.py
.venv/bin/python analyze_validation.py
.venv/bin/python analyze_inventory.py --period-start YYYY-MM-DD --period-end YYYY-MM-DD --inventory-date YYYY-MM-DD
.venv/bin/python export_purchase_voucher_excel.py
.venv/bin/python export_sales_voucher_excel.py
.venv/bin/python parse_sales_voucher_metadata.py
.venv/bin/python generate_analysis_report_html.py
.venv/bin/python capture_html_a4.py
```

기본 입출력은 `private_sources/`, `private_intermediate/`, `private_reports/`를 사용합니다.

- `export_purchase_voucher_excel.py`: `purchase.json`을 `purchase_voucher_groups.xlsx`로 내보내고 전표별 메타데이터 입력 시트를 함께 만듭니다.
- `export_sales_voucher_excel.py`: `sales.json`을 `sales_voucher_groups.xlsx`로 내보내고 전표별 메타데이터 입력 시트를 함께 만듭니다.

## Test

```bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py'
```
