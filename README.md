# Ledger Workbook Analysis

![Report preview](docs/assets/report-preview.png)

Excel 매입/매출/재고 장부를 JSON으로 정규화하고, 제품 ID 기준 대사와 Plotly HTML 보고서를 만드는 작은 분석 도구입니다. 공개 레포에는 샘플 JSON과 미리보기 PNG만 포함하고, 실제 원본/중간산출물/리포트는 Git에서 제외합니다.

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
.venv/bin/python analyze_inventory.py
.venv/bin/python parse_sales_voucher_metadata.py
.venv/bin/python generate_analysis_report_html.py
.venv/bin/python capture_html_a4.py
```

기본 입출력은 `private_sources/`, `private_intermediate/`, `private_reports/`를 사용합니다.

## Test

```bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py'
```
