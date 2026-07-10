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

## Analysis Result Excel

파싱·정규화 JSON과 재고대사 계산 JSON만으로 열람용 `analysis_result.xlsx`를 새로 생성할 수 있습니다. 이 생성기는 ERP 원본 Excel이나 HTML을 다시 열지 않고, 외부 Excel 링크·데이터 연결·Excel 수식도 만들지 않습니다.

```bash
.venv/bin/python generate_analysis_result_excel.py \
  --input-dir examples/dummy_json \
  --output /tmp/analysis_result.xlsx
```

필수 입력은 `purchase.json`, `sales.json`, `inventory.json`, `inventory_reconciliation.json`입니다. `sales_voucher_metadata.json`은 선택 입력으로, 없으면 `전표메타` 시트는 빈 표로 남기고 원청 분석은 `(원청 없음)`으로 표시하며 결과 상태를 `completed_with_warnings`로 기록합니다.

HTML과 Excel은 `build_report_data()`를 함께 사용합니다. 따라서 분석 기간, 매출 공급가액, FIFO 매출원가, 매출총이익, 주간 손익, 원청·거래처·품목별 표시 집계를 각각 따로 계산하지 않습니다. FIFO·부가세·재고대사는 `inventory_reconciliation.json`의 계산 결과를 표시할 뿐 재구현하지 않습니다.

생성 순서는 다음과 같습니다.

1. `요약`
2. `매입`
3. `매출`
4. `재고`
5. `전표메타`
6. `손익분석`
7. `재고분석`
8. `매출분석`
9. `검증결과`
10. `실행정보`

`요약`에는 실제 Excel 차트 객체로 기간별 매입 원가·매출 공급가액, 손익, 재고금액, 상위 거래처·품목 매출, 검증 유형별 건수를 표시합니다. 각 차트는 해당 분석 시트의 셀 범위만 참조하며 PNG/HTML 캡처를 사용하지 않습니다.

대사 불일치, 미확정 원가·수량, 개별 행 오류, 선택 전표 메타 누락과 차트용 데이터 부족은 비치명적입니다. 파일을 계속 만들고 `completed_with_warnings`, `검증결과`, `실행정보`에 남깁니다. 필수 JSON 누락·읽기 실패·핵심 계산 결과(분석 기간·손익·기말 FIFO 재고) 누락·출력 실패만 치명적 오류입니다. 저장은 같은 디렉터리의 임시 `.xlsx`를 재열어 검증한 뒤 원자적으로 최종 경로와 교체합니다. 피벗·슬라이서, 대용량 행 분할, 원본 ERP 연결, Excel 수식 재계산, HTML 시각화의 완전한 복제는 현재 범위에 포함하지 않습니다.

## 수량 대사·기초재고 계약

장부금액, 장부수량, FIFO 원가는 서로 독립된 스트림입니다. 날짜·`product_id`·숫자 수량이 모두 유효한 거래만 장부수량과 FIFO에 쓰고, FIFO 매입 원가에는 추가로 숫자 `unit_price`가 필요합니다. 따라서 단가가 빠진 매입은 장부수량에는 남지만 FIFO 원가층에는 들어가지 않습니다.

재고 시트의 각 행은 `inventory_validations`에 `product_id_valid`, `stock_quantity_valid`, `inventory_reconciliation_eligible`, `inventory_validation_status`로 공개됩니다. `stock_quantity`가 누락되거나 비수치이면 원본값과 오류를 보존하고 0으로 바꾸지 않습니다. 정상 재고 행만 합산하되, 같은 품목에 오류 행이 하나라도 있으면 해당 품목의 수량 대사는 `validation_error`입니다.

- `quantity_difference_count`와 `quantity_reconciliation_mismatch_count`: 비교 가능한 장부·재고 수량의 실제 차이만 센다(`inventory_more`, `ledger_more`, `inventory_negative_stock`). `ledger_only`, `inventory_only`, `validation_error`는 제외한다.
- `quantity_validation_error_count`: 날짜, `product_id`, 수량 때문에 장부수량 스트림에 들어가지 못한 거래 행 수다. 기준일 이후 행도 입력 검증 건수에는 남지만 현재 기준일 대사에는 영향을 주지 않는다.
- `global_quantity_validation_error_count`: 기준일 대사에 영향을 주지만 품목에 귀속할 수 없는 거래 오류 행 수다. 날짜를 판정할 수 없고 `product_id`도 없는 행도 여기에 포함한다.
- `inventory_snapshot_validation_error_count`: 유효하지 않은 재고 스냅샷 행 수다. 한 행에 `product_id`와 `stock_quantity` 오류가 함께 있어도 이 건수는 한 번만 센다.
- `quantity_reconciliation_validation_error_count`: 현재 대사의 검증 오류 위치 수다. 품목별 `validation_error`는 품목당 한 번, 전역 거래·재고 스냅샷 오류는 행당 한 번 센다. 위 세 입력 오류 건수와 중복될 수 있으므로 합계로 사용하지 않는다. 전체 상태는 `quantity_reconciliation_validation_status`의 `valid` 또는 `validation_error`다.

기초 장부수량은 `opening_stock_quantity`(=`opening_signed_stock_quantity`)이며, 이는 시작일 전 매입수량에서 매출수량을 뺀 값입니다. `opening_normal_stock_quantity`과 `opening_negative_stock_quantity`는 그 부호를 분리한 값이고, `opening_costed_layer_quantity`과 `opening_stock_amount`는 단가까지 확인된 FIFO 원가층만 나타냅니다.

## Private Workflow

```bash
.venv/bin/python parse_workbooks.py
.venv/bin/python analyze_validation.py
.venv/bin/python analyze_inventory.py --period-start YYYY-MM-DD --period-end YYYY-MM-DD --inventory-date YYYY-MM-DD
.venv/bin/python export_purchase_voucher_excel.py
.venv/bin/python export_sales_voucher_excel.py
.venv/bin/python parse_sales_voucher_metadata.py
.venv/bin/python generate_analysis_report_html.py
.venv/bin/python generate_analysis_result_excel.py
.venv/bin/python capture_html_a4.py
```

기본 입출력은 `private_sources/`, `private_intermediate/`, `private_reports/`를 사용합니다.

- `export_purchase_voucher_excel.py`: `purchase.json`을 `purchase_voucher_groups.xlsx`로 내보내고 전표별 메타데이터 입력 시트를 함께 만듭니다.
- `export_sales_voucher_excel.py`: `sales.json`을 `sales_voucher_groups.xlsx`로 내보내고 전표별 메타데이터 입력 시트를 함께 만듭니다.

## Test

```bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py'
```
