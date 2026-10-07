# Maintainers · 개발과 문서 검증

**목적:** 사용자 guide와 구현·계약·실측 기록을 함께 유지합니다.

## 개발 환경

```bash
python -m pip install -r requirements.txt -e .
python -m pytest -q
python -m compileall -q xlayer_telemetry examples
git ls-files -z '*.sh' | xargs -0 -r -n 1 bash -n
```

**정상 결과:** 관련 CPU regression과 syntax 검사가 통과합니다. Synthetic·VM 결과를 실제 GPU/VERL·3FS cluster 검증으로 표현하지 않습니다.

## 문서와 UI 검증

```bash
bash scripts/docs.sh build
python -m pytest -q tests/test_documentation_links.py tests/test_documentation_diagrams.py
CHROME_PATH=/path/to/chromium XLAYER_DOCS_SITE=artifacts/docs-site npm test --prefix scripts/docs-browser
```

**정상 결과:** strict Sphinx build에 warning이 없고 link·viewport·diagram 확대·geometry가 통과합니다. Browser dependency 설치는 [Script 안내](https://github.com/daegyu94/xlayer-telemetry/blob/main/scripts/README.md#documentation-diagrams)를 따릅니다.

## D2 원본과 SVG

```bash
python scripts/render_diagrams.py --install
# 원본을 수정한 뒤 재생성합니다.
python scripts/render_diagrams.py
python scripts/render_diagrams.py --check
```

**정상 결과:** pinned D2 0.9.0이 원본·공통 style과 SVG 일치를 확인합니다. Source와 SVG를 같이 commit하고 generated site·binary·run data는 Git에 넣지 않습니다.

| 그림 계층 | 본문 preview | 사용 |
| --- | --- | --- |
| Small | 최대 600px, 원본보다 확대하지 않음 | 짧은 concept flow |
| Medium | 최대 780px | Workflow / 일반 architecture |
| Large | 최대 960px, 본문 폭 이내 | Cross-layer detail / topology |

- Node label은 1–3줄; 자세한 설명은 caption·본문으로 이동.
- Thin neutral border, primary blue/indigo, optional dashed + label.
- 본문은 compact preview; 그림의 mouse·keyboard 접근을 유지.
- Desktop 1440px·laptop 1280px·narrow viewport에서 실제 크기 확인.

## 문서 역할

| 역할 | 본문 패턴 |
| --- | --- |
| Tutorial | 처음부터 성공 기준까지 하나의 경로 |
| Task | 얻는 것 → 준비 → Configure → Start → Verify → 다음 |
| Concept | Mental model·용어·해석 경계 |
| Reference | Source·unit·scope·schema·구현 계약 |
| Validation record | 당시 환경·결과·미실행·한계 |

## 기록과 디자인 근거

[검증 기록](validation/README.md) · [Dashboard UI review](grafana-ui-ux-review.md) · [Documentation review](documentation-ux-review.md) · [실환경 기록](real-verl-demo.md)
