# 릴리즈픽 (ReleasePick)

> **생성형 AI 기반 정책 보도자료 → 카드뉴스 인포그래픽 자동 생성 웹서비스**
> 재정경제부 혁신정책담당관·규제개혁법무담당관 (이주호 · 박진영 · 신채은)

보도자료 PDF/HWPX 한 건을 업로드하면, **AI가 핵심 문구를 추출 → 기획안을 작성 → 정부 디자인 가이드 준수 카드뉴스(한국어·영어) JPEG**를 자동 생성하고 **Instagram에 바로 발행**까지 이어지는 Streamlit 기반 MVP 입니다.

평균 **2일 이상**, **건당 약 300만 원** 들던 외주 제작 흐름을 **15~20분, 비용 0원**으로 단축하는 것이 목표입니다.

---

## 1. 기획서 ↔ 구현 매핑

| 기획서 워크플로우 | 구현 모듈 / 단계 | 상태 |
|---|---|---|
| ① 공식 홈페이지 보도자료 PDF 자동 크롤링 | [`code/press_release.py`](code/press_release.py) — mofe.go.kr RSS + 상세 페이지에서 PDF·HWPX·HWP 첨부 메타 자동 수집 (Step 1) | ✅ |
| ② AI 분석 및 기획 (핵심 문구 추출 + 디자인 기획) | [`code/plan_llm.py`](code/plan_llm.py) — OpenAI `gpt-*` 호출 → [`code/models.py`](code/models.py)의 `CardNewsPlan` (Pydantic) JSON 산출. [`code/pdf_extract.py`](code/pdf_extract.py) · [`code/hwpx_extract.py`](code/hwpx_extract.py) 로 본문 추출 (Step 2) | ✅ |
| ③ 스타일 적용 (규격화 템플릿 매칭) | [`themes/`](themes/) (`mofe_body.yaml`, `template1`, `template2/템플릿1·2`) + [`code/template_catalog.py`](code/template_catalog.py) · [`code/template_resources.py`](code/template_resources.py) · [`code/image_gen.py`](code/image_gen.py) · [`code/render_cards.py`](code/render_cards.py) (Step 3·4) | ✅ |
| ④ 담당자 직접 텍스트 · 디자인 미세 조정 | Step 2에서 페이지별 제목·불릿·각주 인라인 편집(1차 승인 후 최대 2회 수정). Step 4에서 표지/완성 시안 **A·B·C 3종 생성 → 선택** (`generate_cover_variant_jpegs`) | ✅ |
| ⑤ 최종 승인 (내부 회람) | `plan_phase` 상태 머신: `draft → post_first → locked` ([`code/state.py`](code/state.py)). 확정 전에는 카드 생성 비활성화 | ✅ |
| ⑥ 다운로드 및 SNS 실시간 자동 배포 | Step 5 ZIP 패키지 ([`code/package_export.py`](code/package_export.py) — PPTX 기획안 + `jpeg/ko/`, `jpeg/en/`) + Step 6 Instagram 자동 발행 ([`code/supabase_storage.py`](code/supabase_storage.py) → [`code/buffer_publish.py`](code/buffer_publish.py)) | ✅ |
| 다국어 (영문) 외신 홍보 | [`plan_llm.translate_plan_to_english`](code/plan_llm.py) + GPT Image 영문 세트 (로고 워드마크 `Ministry of Finance and Economy`) | ✅ |
| 맞춤형 디자인 툴 (템플릿·MI·폰트) | 1000×1350 캔버스, 맑은 고딕 / 맑은 고딕 Bold, `themes/mofe_body.yaml` 섹션 톤 5종(neutral·green·blue·purple·orange), 본문 레이아웃 2종(여백형·흰 카드형), 캐릭터 PNG 업로드 | ✅ |
| 정서·이미지 안전 검토 (기획서 비포함, 자체 강화) | [`code/content_filter.py`](code/content_filter.py) — 일본·식민지 잔재, 북한, 정치 이념(보수·진보), 젠더·세대·지역 갈등, 혐오 표현을 LLM 기획·이미지 프롬프트·생성 전에 차단 | ✅ (자체 추가) |
| 세션 복구 | [`code/job_store.py`](code/job_store.py) — `data/jobs.sqlite` 에 단계별 스냅샷 저장, 새로고침/세션 복귀 시 복원 | ✅ (자체 추가) |

---

## 2. 사용자 플로우 (Streamlit 6단계)

[`code/app.py`](code/app.py) 는 얇은 라우터이고 실제 화면은 [`code/views/`](code/views/) 에 있습니다.

```
[Step 1] 보도자료 선택
  └─ "최신 보도자료 불러오기" → mofe.go.kr RSS 5건 표시
     → 행에서 PDF / HWPX 첨부 1건 선택 → 본문 자동 추출
     → 총 페이지 수(1~12) 슬라이더, 멀티페이지 템플릿 변형(v1·v2) 선택

[Step 2] 내용 분석 · 기획안
  └─ "AI 분석 시작" → OpenAI 호출 → CardNewsPlan(JSON) 생성
     → 안전 필터 자동 검사 → 페이지별 제목·불릿·각주 인라인 편집
     → "1차 승인" 후 최대 2회 수정 → "기획 확정 →" (잠금)

[Step 3] 디자인 설정
  └─ 템플릿 YAML, 섹션 컬러 톤(5색 스와치), 로고 위치(상우/상좌/하중),
     캐릭터 PNG, 제목/본문 색상, 본문 레이아웃(여백형 A · 흰 카드형 B),
     폰트 크기 배율(0.75~1.25), 이미지 생성 모델(GPT Image 계열) 선택
     ※ 디자인 컨셉은 페이지 수에 맞춰 자동 추천

[Step 4] 카드 이미지 생성 (JPEG)
  └─ ① "표지 시안 3종 (A/B/C)" 생성 (1장이면 결과물 후보 3종)
     ② 시안 선택
     ③ "한국어 카드 생성" → 1000×1350 JPEG (page 1~N)
     ※ 안전 필터 통과한 기획만 API 호출

[Step 5] 결과 확인 · 산출물
  └─ 썸네일 레일 + 미리보기 + 페이지별 재렌더 (최대 2회)
     → "영문 세트 번역·생성" → `jpeg/en/` 추가 생성
     → "ZIP 다운로드" — `기획안_최종.pptx` + `jpeg/ko/`, `jpeg/en/`

[Step 6] SNS 업로드 (선택)
  └─ "캡션 초안 생성" → OpenAI 가 한국어 캡션 초안
     → Supabase Storage 에 카드 임시 호스팅
     → Buffer GraphQL API 로 Instagram 캐러셀 발행 (최대 10장)
     → 발행 후 Supabase 임시 파일 정리
```

각 단계 진입 가능 여부는 [`code/views/editor.py`](code/views/editor.py) 의 `_next_disabled(step)` 가 게이트합니다.

---

## 3. 폴더 구조

```
Releasepick/
├─ README.md                        ← 본 문서
├─ .env-sample                      ← 환경변수 템플릿
├─ requirements.txt                 ← 의존성 (streamlit / openai / pymupdf / pillow / pydantic / pptx / requests / bs4)
├─ design.md                        ← 디자인 가이드 (담당자·디자이너용 톤앤매너)
├─ MERGE_GUIDE_SNS_UPLOAD.md        ← Section 6(SNS 업로드) 머지 가이드
├─ logo.png                         ← 한국어 기본 로고 (직접 렌더 + AI 참조)
├─ 01.jpg / 02.jpg / 03.jpg         ← 기획서 첨부 샘플
│
├─ code/
│  ├─ app.py                        ← Streamlit 멀티페이지 라우터 (얇은 shell)
│  ├─ views/
│  │   ├─ landing.py                ← 홈 (히어로 + bento 피처 그리드)
│  │   ├─ editor.py                 ← Step 1~4, 6 + 단계 푸터
│  │   └─ result_view.py            ← Step 5 결과 확인 + 영문 번역 + ZIP
│  ├─ ui/
│  │   ├─ theme.py                  ← 전역 CSS / 디자인 토큰
│  │   ├─ components.py             ← top_app_bar, step_nav, pill, feature_card …
│  │   └─ assets.py                 ← logo / hero mock data URI
│  │
│  ├─ press_release.py              ← mofe.go.kr RSS + 첨부 메타 크롤러 (재시도·세션)
│  ├─ pdf_extract.py                ← PyMuPDF 기반 PDF 본문 추출
│  ├─ hwpx_extract.py               ← HWPX (OWPML) 본문 추출 (표준 라이브러리만)
│  │
│  ├─ plan_llm.py                   ← OpenAI 기반 CardNewsPlan 생성 + EN 번역
│  ├─ caption_llm.py                ← Instagram 한국어 캡션 초안 생성
│  ├─ content_filter.py             ← 정서·이미지 안전 필터 (기획·프롬프트·산출 3중)
│  ├─ models.py                     ← Pydantic CardNewsPlan / SlidePlan
│  │
│  ├─ template_catalog.py           ← 1페이지·멀티페이지 디자인 컨셉 카탈로그·추천
│  ├─ template_resources.py         ← 멀티페이지 표지/본문 템플릿 변형 로드
│  ├─ template_thumbnails.py        ← 컨셉 썸네일 생성
│  ├─ image_gen.py                  ← GPT Image (OpenAI Images API) 카드 생성
│  │                                   - 표지 시안 3종 (variant A/B/C)
│  │                                   - 한국어 → 영문 세트 (로고 워드마크 자동 교체)
│  ├─ render_cards.py               ← Pillow 기반 직접 렌더 (테마 YAML, 섹션 톤, 로고 박스)
│  ├─ english_logo.py               ← 영문 카드용 로고 비트맵 합성
│  │
│  ├─ export_plan_pptx.py           ← python-pptx 로 기획안 PPTX 빌드 (검수용)
│  ├─ package_export.py             ← PPTX + JPEG → ZIP 패키징
│  │
│  ├─ supabase_storage.py           ← Supabase Storage 업로드/삭제 (서비스 롤 키)
│  ├─ buffer_publish.py             ← Buffer GraphQL `createPost` 로 Instagram 발행
│  │
│  ├─ state.py                      ← session_state defaults / hydrate / persist / guard
│  └─ job_store.py                  ← SQLite (`data/jobs.sqlite`) 스냅샷 저장·복구
│
├─ themes/                          ← 디자인 정본
│  ├─ mofe_body.yaml                ← 본문 색·타이포·섹션 톤·로고 박스
│  ├─ template1/                    ← 1페이지 포스터형 (`템플릿-1.txt`)
│  └─ template2/                    ← 멀티페이지 (2~5+ 페이지)
│      ├─ # Multi-Page Card News (2~5 Pages).txt
│      ├─ 템플릿1/                  ← v1 「재정경제부 1」 표지·본문
│      └─ 템플릿2/                  ← v2 「재정경제부2 (모모페페 캐릭터)」 표지·본문
│
└─ data/
   ├─ jobs.sqlite                   ← 세션 스냅샷
   └─ logo_en_mofe.png              ← 영문 로고 워드마크
```

---

## 4. 환경 설정

### 4.0 사전 요구사항

| 항목 | 권장 | 비고 |
|---|---|---|
| OS | Windows 10/11 (1차 타깃) · macOS 13+ · Ubuntu 22.04+ | 한글 폰트는 OS별로 경로가 다름 — §4.3 참조 |
| Python | **3.11 ~ 3.12** | pydantic v2 / streamlit 1.28+ 호환. 3.13 은 일부 휠 미배포 가능 |
| 패키지 매니저 | [`uv`](https://github.com/astral-sh/uv) 권장 (pip 도 가능) | Windows: `winget install astral-sh.uv` · macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| 디스크 | ≥ 1 GB (가상환경 · SQLite 스냅샷 · 카드 JPEG 캐시) | `data/jobs.sqlite` 는 세션마다 누적 — 주기적 정리 권장 (§11.3) |
| 네트워크 | OpenAI / mofe.go.kr / (선택) Supabase · Buffer 아웃바운드 HTTPS | 사내 프록시 환경에서는 `HTTPS_PROXY` 환경변수 설정 필요 |
| 한글 폰트 | Windows 기본 포함 (`malgun.ttf`) · macOS/Linux 는 별도 설치 필요 | §4.3 / §5.2 트러블슈팅 |

### 4.1 필수 — OpenAI

저장소 루트 `Releasepick/.env` 에 다음 키를 설정합니다 (없으면 Step 2 부터 오류).

```env
OPENAI_API_KEY=sk-...
```

### 4.2 선택 — Step 6 Instagram 자동 발행

아래 5개 키가 **모두** 채워져야 Step 6 가 활성화됩니다. 하나라도 비면 화면 자체가 비활성화되어 기존 1~5단계는 그대로 동작합니다 ([`editor.py:96-104`](code/views/editor.py#L96-L104)).

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_BUCKET=
BUFFER_API_KEY=
BUFFER_CHANNEL_ID=              # Buffer 채널 설정 URL 의 hex 24자리
BUFFER_ORGANIZATION_ID=         # 선택
```

캡션 톤은 [`code/caption_llm.py`](code/caption_llm.py) 의 `DEFAULT_CAPTION_SYSTEM_PROMPT` 를 편집해 조정합니다. 머지/회귀 관련 주의사항은 [`MERGE_GUIDE_SNS_UPLOAD.md`](MERGE_GUIDE_SNS_UPLOAD.md) 참조.

### 4.3 디자인 커스터마이즈

- 카드 해상도: **1000 × 1350 px** (비율 **20:27**, 세로형 카드뉴스)
- 한글 폰트: Windows `malgun.ttf` / `malgunbd.ttf` 자동 탐색 ([`render_cards.py:16-17`](code/render_cards.py#L16-L17))
- 본문 정본: [`themes/mofe_body.yaml`](themes/mofe_body.yaml)
- 1페이지 가이드: [`themes/template1/템플릿-1.txt`](themes/template1/템플릿-1.txt)
- 멀티페이지 가이드: [`themes/template2/# Multi-Page Card News (2~5 Pages).txt`](themes/template2/)
- 톤앤매너 레퍼런스: [`design.md`](design.md)

---

## 5. 실행

### 5.1 첫 실행 (Windows / PowerShell)

```powershell
# 1) 저장소 클론 + 진입
git clone <repo-url> Releasepick
cd Releasepick

# 2) 가상환경 + 의존성
uv venv                              # .venv 생성 (Python 3.11+ 자동 선택)
uv pip install -r requirements.txt

# 3) 환경변수
copy .env-sample .env
notepad .env                         # OPENAI_API_KEY 최소 1개 필수

# 4) 앱 실행
cd code
uv run python -m streamlit run app.py
```

### 5.1.a macOS / Linux

```bash
git clone <repo-url> Releasepick && cd Releasepick
uv venv && uv pip install -r requirements.txt
cp .env-sample .env && ${EDITOR:-vi} .env
cd code && uv run python -m streamlit run app.py
```

> Windows 에서 `uv run streamlit ...` 직접 호출은 trampoline 오류가 발생할 수 있어 `python -m streamlit` 형태를 권장합니다.

브라우저에서 `http://localhost:8501` → 「홈」 카드의 **"보도자료 업로드하고 시작하기"** 버튼으로 진입.

### 5.2 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `OPENAI_API_KEY not set` (Step 2 진입 시 오류) | `.env` 미생성 또는 키 누락 | `.env-sample` 복사 후 키 채우기. 가상환경 활성화 상태에서 실행했는지 확인 |
| Streamlit 가 열리지만 화면이 빈 칸 | `streamlit run` 을 `code/` 가 아닌 루트에서 실행 | `cd code` 후 `python -m streamlit run app.py` |
| 한글 글자가 □ 로 깨짐 (Pillow 직접 렌더 경로) | 시스템에 `malgun.ttf` 가 없음 (주로 macOS/Linux) | NanumGothic 등 한글 TTF 설치 후 [`render_cards.py:16-17`](code/render_cards.py#L16-L17) 의 폰트 후보에 경로 추가 |
| Step 1 RSS 가 503/ConnectionReset | mofe.go.kr WAF 일시 차단 | 「새로고침」 버튼으로 캐시 클리어 후 30초~1분 대기 (백오프 재시도 자동) |
| Step 4 이미지 생성이 멈춤 | OpenAI 응답 지연 또는 안전 필터 차단 | 터미널 stderr 확인 (`ContentFilterError` 면 기획 텍스트 수정) |
| Step 6 가 비활성화 | Supabase/Buffer 5개 키 중 누락 | [`editor.py:96-104`](code/views/editor.py#L96-L104) 확인 후 `.env` 보강 |
| 새로고침 후 단계가 1로 돌아감 | `data/jobs.sqlite` 가 비어 있거나 `session_id` 가 새로 발급됨 | 같은 브라우저 세션에서 새로고침해야 복원됨 — 시크릿 모드 변경 X |
| `uv run streamlit` Windows trampoline 오류 | uv 의 console_scripts 트램폴린 버그 | `python -m streamlit run app.py` 로 우회 |
| 사내 프록시에서 OpenAI 호출 실패 | HTTPS 프록시 미설정 | PowerShell 에서 `$env:HTTPS_PROXY="http://proxy:8080"` 후 재실행 |

---

## 6. 데이터 흐름과 아키텍처 포인트

### 6.1 보도자료 수집 (Step 1)

[`press_release.py`](code/press_release.py) 는 mofe.go.kr 의 공식 RSS (`detailRssTagService.do?bbsId=MOSFBBS_000000000028`) 에서 최근 N건의 제목·nttId·발행일·작성자를 수집한 뒤, 각 상세 페이지에서 첨부 목록을 BeautifulSoup 으로 파싱합니다.

- **첨부 우선순위**: PDF > HWPX > HWP. HWP 만 있는 게시물은 UI 에서 "불가" 칩으로 비활성화.
- **WAF 우회**: 정상 브라우저 헤더 풀세트(`Accept`, `Accept-Language`, `Sec-Fetch-*` 등) + Session keep-alive + `ConnectionReset/Timeout` 시 3회 백오프 재시도 (1.5 / 3.0 / 6.0초).
- **캐싱**: `st.cache_data` 로 RSS / 상세 페이지 응답을 캐시하고, 「새로고침」 버튼이 모든 cache 를 `.clear()` 합니다.

### 6.2 본문 추출

- **PDF**: [`pdf_extract.py`](code/pdf_extract.py) 가 PyMuPDF 로 텍스트 레이어 추출. **스캔 전용 PDF 는 본문이 비어 있을 수 있어** 사용자에게 명시적으로 안내합니다.
- **HWPX**: [`hwpx_extract.py`](code/hwpx_extract.py) 가 ZIP 내부 `Contents/section*.xml` 에서 `hp:t` 요소만 순회 — 외부 hwp 의존성 0개.

### 6.3 LLM 기획 (Step 2)

[`plan_llm.py`](code/plan_llm.py) 는 OpenAI Chat Completions 에 다음 3 요소를 합성한 system prompt 를 전달합니다.

1. JSON Schema 강제 블록 (`series_title`, `head_copy`, `slides[role/title/bullets/footnote]`)
2. **선택된 페이지 수**에 따른 슬라이드 역할 규칙 (1장이면 cover 단독, 2장이면 cover + closing, 3장 이상이면 cover + body × N + closing)
3. **현재 선택된 템플릿 변형**의 표지·본문 원문 (`load_cover_template_text`, `load_body_template_text`) — LLM 이 톤·레이아웃 규칙까지 따르도록 합니다
4. [`content_filter.PLAN_EDITOR_SAFETY_RULES`](code/content_filter.py) — 안전 규칙

응답은 Pydantic `CardNewsPlan` 으로 검증 → `st.session_state.plan_dict` 에 저장 → SQLite 스냅샷 저장 (`persist()`).

`plan_phase` 상태 머신:
- `draft` — 자유 편집 + "페이지 편집 반영"
- 「1차 승인」 → `post_first` — 추가 수정 2회 카운트
- 「기획 확정 →」 → `locked` — Step 4 로 진행 가능

### 6.4 안전 필터 (3중)

[`content_filter.py`](code/content_filter.py) 가 차단하는 카테고리:
- 일본 문화/관광/식민지 잔재 (기모노·도리이·후지산·일제강점기·신사 등)
- 북한·분단·주체사상
- 정치 이념 (보수·진보·좌우), 젠더·세대·지역 갈등
- 혐오·차별·선동 표현

적용 지점:
1. **LLM 기획 생성 시** system prompt 에 `PLAN_EDITOR_SAFETY_RULES` 주입
2. **기획안 저장/편집 직후** `assert_plan_safe()` → 위반 시 사용자에게 표시 후 중단 (`ContentFilterError`)
3. **이미지 프롬프트** 마지막에 `IMAGE_VISUAL_SAFETY_BLOCK` (영문 negative prompt) 강제 추가 ([`image_gen.append_image_safety`](code/image_gen.py))

### 6.5 이미지 생성 (Step 4)

[`image_gen.py`](code/image_gen.py) — OpenAI Images API (`gpt-image-*` 계열) 호출. 두 단계:

1. **표지 시안 3종 (variant A/B/C)** — `generate_cover_variant_jpegs`
   - 동일 기획 1페이지를 레이아웃·정렬·3D 소스만 달리 생성
   - 1장 카드뉴스 모드에서는 이것이 곧 **최종 후보**(추가 API 호출 없음)
2. **선택된 시안 스타일을 본문 N장에 일관 적용** — `generate_plan_card_jpegs`
   - 페이지당 1~2분 / 1회 호출
   - 영문 세트는 Step 5 에서 추가로 N회 호출 (요금 2배)

로고는 두 가지 경로:
- **GPT Image 가 카드 안에 직접 그림** — 한국어는 「재정경제부」 워드마크 + 삼태극, 영문은 「Ministry of Finance and Economy」 워드마크. 위치는 `top_right` / `top_left` / `bottom_center` 중 선택.
- **Pillow 직접 렌더 경로** ([`render_cards.py`](code/render_cards.py)) — `logo.png` / `data/logo_en_mofe.png` 합성. 현재 메인 흐름은 GPT Image 경로지만 직접 렌더는 폴백·검수용으로 유지.

### 6.6 결과 산출 (Step 5)

[`package_export.py`](code/package_export.py) → ZIP 구조:

```
└─ release-pick-export.zip
   ├─ 기획안_최종.pptx        ← export_plan_pptx.build_plan_pptx_bytes
   └─ jpeg/
       ├─ ko/01_cover.jpg, 02_body.jpg, …
       └─ en/01_cover.jpg, 02_body.jpg, …
```

PPTX 는 텍스트 검수용이며 카드 픽셀과 1:1 대응하지 않습니다 (개정 텍스트 확인용).

### 6.7 SNS 자동 발행 (Step 6, 선택)

```
Supabase Storage (private bucket)            Buffer GraphQL
       ▲                                          ▲
       │ ① upload_card_images()                   │ ② createPost(input)
       │   service-role key + signed URL          │   channelId + media[] + caption
       └────────── 한국어 카드 JPEG ──────────────┘
                       │
                       ▼
                Instagram (Buffer 채널 연결)
```

[`supabase_storage.py`](code/supabase_storage.py) 는 service-role 키로 Storage 에 카드를 업로드하고 외부 접근용 URL 을 반환. [`buffer_publish.py`](code/buffer_publish.py) 는 GraphQL `mutation CreatePost` 를 호출해 Instagram 캐러셀(최대 10장)을 즉시 발행합니다. 발행 직후 "Supabase 임시 파일 정리" 버튼으로 호스팅 잔여물을 삭제합니다.

### 6.8 세션 복구

[`state.persist()`](code/state.py) 는 모든 단계에서 `st.session_state` 의 직렬화 가능한 키를 모아 [`job_store.save_snapshot`](code/job_store.py) 로 `data/jobs.sqlite` 에 저장합니다. 새로고침/세션 복귀 시 `try_hydrate()` 가 같은 `session_id` 의 스냅샷을 복원하므로, 이미지 생성 도중 새로고침해도 직전 상태부터 이어 작업 가능합니다.

---

## 7. 도입 효과 (기획서 대비 검증)

| 항목 | Before (기획서) | After (구현) |
|---|---|---|
| 카드뉴스 제작 비율 | 보도자료 143건 중 10건 (7%) | 보도자료 1건당 약 15~20분 → 사실상 모든 보도자료 처리 가능 |
| 1건 제작 소요 시간 | 외주·내부 모두 최소 2~3일 | 본문 추출 ~1분 + 기획 ~30초 + 카드 N장 × 1~2분 + 영문 세트 N분 ≈ **15~20분** |
| 1건 제작 비용 | 외주 약 3백만 원 | OpenAI API 종량 과금 (보통 수십~수백원 / 건) ≈ **사실상 0원** |
| 일관된 브랜딩 | 외주마다 편차 | 전용 템플릿(v1/v2) + 톤 5색 + 로고 위치 표준화 |
| 다국어 (영문) | 별도 외주 | 동일 흐름에서 자동 번역 + AI 로고 워드마크 교체 |
| 회람·검토 | 이메일 왕복 | 화면 내 1차 승인 + 최대 2회 수정 상태 머신 |
| SNS 적시성 | 수작업 업로드 | Buffer API 로 Instagram 캐러셀 즉시 발행 |

---

## 8. 제한 사항 / 주의

- **텍스트 추출 가능한 PDF** 권장. 스캔 전용 PDF·이미지 PDF 는 본문이 비어 추출 실패합니다.
- **HWP (구버전)** 는 첨부 목록에서 감지되지만 추출은 **불가**. HWPX 또는 PDF 첨부가 있는 게시물만 선택 가능.
- 카드뉴스 페이지 수는 **1~12** 지원. Instagram 캐러셀 한도(10장)를 넘으면 Step 6 가 차단합니다.
- GPT Image 호출은 **페이지당** 발생하며, 영문 세트 생성 시 **거의 2배**의 API 비용이 발생합니다.
- 안전 필터를 통과하지 않은 기획은 **API 호출 없이 중단** — 위반 항목과 가이드가 화면에 표시됩니다.
- 세션 상태는 메모리 + SQLite 스냅샷. 다른 PC 에서 같은 세션 이어받기는 지원하지 않습니다.

---

## 9. 확장 가이드

운영 중 자주 발생하는 5가지 확장 시나리오에 대한 변경 지점만 모았습니다. 모든 변경은 **한 모듈로 격리**되도록 설계되어 있어 다른 단계의 회귀 위험이 낮습니다.

### 9.1 새 부처 / 다른 RSS 추가

대상: 다른 정부 부처(예: 행정안전부, 과학기술정보통신부)로 확장.

1. [`code/press_release.py`](code/press_release.py) 상단 상수 (`RSS_URL`, `DETAIL_URL_TEMPLATE`, `BASE_URL`) 를 부처별 dict 로 분리.
2. Step 1 UI ([`code/views/editor.py`](code/views/editor.py)) 에 부처 선택 셀렉트 추가 → 선택값을 `fetch_recent_releases(agency=...)` 인자로 전달.
3. 영문 로고 워드마크는 [`code/english_logo.py`](code/english_logo.py) 의 `MINISTRY_WORDMARK` 상수와 [`image_gen.py`](code/image_gen.py) 의 영문 프롬프트 두 군데 동시 수정 (예: `Ministry of the Interior and Safety`).
4. (선택) 부처 로고 PNG 를 `data/logo_<agency>.png` 로 추가 후 [`render_cards.py`](code/render_cards.py) 의 로고 박스 경로 분기.

### 9.2 새 카드 템플릿(디자인 변형) 추가

대상: 「재정경제부 3」 같은 신규 디자인 변형.

1. [`themes/template2/템플릿3/`](themes/template2/) 디렉토리 생성 → 표지/본문 원문 txt 작성 (v1/v2 와 동일 구조).
2. [`code/template_catalog.py`](code/template_catalog.py) 의 카탈로그에 신규 컨셉 등록 (이름, 추천 페이지 수 범위, 썸네일 키).
3. [`code/template_resources.py`](code/template_resources.py) 의 `load_cover_template_text` / `load_body_template_text` 분기 추가.
4. (선택) [`code/template_thumbnails.py`](code/template_thumbnails.py) 로 미리보기 썸네일 사전 생성.

### 9.3 LLM 모델 교체 (gpt-5-mini → gpt-5 / 클로드 등)

| 위치 | 호출 | 교체 포인트 |
|---|---|---|
| 기획 생성 | [`code/plan_llm.py`](code/plan_llm.py) | `MODEL` 상수 + `client.chat.completions.create(model=...)` |
| 영문 번역 | 동일 파일 `translate_plan_to_english` | 동일 상수 사용 |
| 캡션 생성 | [`code/caption_llm.py`](code/caption_llm.py) | `CAPTION_MODEL` 상수 |
| 이미지 생성 | [`code/image_gen.py`](code/image_gen.py) | `IMAGE_MODEL` 상수 (`gpt-image-1` 계열만 호환) |

다른 LLM 공급자(Anthropic 등)로 바꾸려면 `openai.OpenAI` 클라이언트 호출을 추상화한 어댑터를 [`code/plan_llm.py`](code/plan_llm.py) 에 도입한 뒤 위 4 군데를 일괄 위임하는 방식을 권장합니다. Pydantic 스키마(`CardNewsPlan`) 는 그대로 재사용 가능합니다.

### 9.4 안전 필터 키워드 / 카테고리 추가

[`code/content_filter.py`](code/content_filter.py) 한 파일만 수정합니다.

1. `BANNED_TERMS` (한국어 키워드 dict) 에 카테고리·키워드 추가.
2. `IMAGE_VISUAL_SAFETY_BLOCK` (영문 negative prompt) 에 대응 영문 표현 추가.
3. `PLAN_EDITOR_SAFETY_RULES` (system prompt) 에 새 규칙 1줄 추가.
4. 단위 테스트가 없으므로 `assert_plan_safe()` 가 위반 케이스에서 `ContentFilterError` 를 던지는지 REPL 로 1회 검증.

### 9.5 새 SNS 채널 추가 (X, Threads, Facebook)

현재 Step 6 는 Buffer GraphQL `createPost` 한 진입점만 사용 — Buffer 채널 ID 만 바꾸면 같은 코드로 X/Threads/Facebook 발행이 가능합니다.

- Buffer 가 지원하지 않는 채널(예: 카카오톡 채널)을 추가하려면 [`code/buffer_publish.py`](code/buffer_publish.py) 옆에 `kakao_publish.py` 같은 새 모듈을 만들고, [`code/views/editor.py`](code/views/editor.py) Step 6 의 채널 토글에 분기 추가.
- 이미지 호스팅은 [`code/supabase_storage.py`](code/supabase_storage.py) 의 `upload_card_images()` / `cleanup_card_images()` 인터페이스를 재사용 가능.

---

## 10. 운영 · 디버깅 · 로깅

### 10.1 로그 / 디버그

- Streamlit 기본 로그: 실행 터미널 stdout/stderr. Python 예외는 모두 화면 상단 빨간 배너로 표시.
- 추가 디버그: 환경변수 `STREAMLIT_LOGGER_LEVEL=debug` 후 재실행.
- LLM 응답 원본 확인: [`code/plan_llm.py`](code/plan_llm.py) `_call_openai()` 의 `response` 객체에 브레이크포인트 — 토큰 사용량(`response.usage`)도 동시에 확인 가능.

### 10.2 비용 모니터링

- 1건당 대략 비용: 기획 LLM ~$0.01, 카드 이미지 GPT Image N장 × ~$0.04, 영문 세트 추가 N장 — 합계 보통 **$0.3 ~ $1.0 / 건**.
- 정확한 모니터링은 [OpenAI Usage 대시보드](https://platform.openai.com/usage) 에서 일자별 토큰·이미지 호출량 확인.
- 비용 절감 팁: Step 4 에서 영문 세트는 발행 직전에만 생성 / 페이지 수를 슬라이더로 미리 제한 / Step 4 표지 시안 3종 단계에서 불만족 시 재생성하지 말고 Step 2 기획 텍스트부터 수정.

### 10.3 SQLite 스냅샷 관리

`data/jobs.sqlite` 는 세션마다 누적되어 GB 단위로 커질 수 있습니다.

```powershell
# 주기적 정리 — 7일 이상 된 스냅샷 삭제
cd c:\Users\sangh\Releasepick
uv run python -c "from code.job_store import purge_old; purge_old(days=7)"

# 완전 초기화 (모든 진행중 세션 손실)
del data\jobs.sqlite
```

> `purge_old` 가 없으면 `job_store.py` 에 7~15줄로 추가 가능 — 현재 자동 정리는 비활성.

### 10.4 재현 가능한 버그 리포트

1. 사용한 보도자료 PDF/HWPX 파일명 + nttId.
2. 어느 Step 에서 발생했는지.
3. 터미널 마지막 50줄 (`OPENAI_API_KEY` 등 비밀값 마스킹).
4. (가능하면) `data/jobs.sqlite` 사본 또는 `session_id`.

---

## 11. 로드맵 · 알려진 이슈

### 11.1 로드맵 (작성 시점 기준)

- [ ] HWP(구버전) 본문 추출 — 외부 변환기(예: `hwp5txt`) 도입 검토
- [ ] 스캔 PDF OCR — Tesseract 또는 OpenAI Vision fallback
- [ ] 부처 다중 지원 (§9.1) — 행정안전부 / 과학기술정보통신부
- [ ] 카드뉴스 시안 비교 뷰(A/B/C 동시 비교 → 1클릭 채택)
- [ ] 비용 표시 패널 — 토큰·이미지 호출 누적치를 사이드바에 실시간 표시
- [ ] 로컬 폰트 자동 다운로드 — macOS/Linux 첫 실행 시 NanumGothic 자동 설치

### 11.2 알려진 이슈

| 이슈 | 영향 | 회피책 |
|---|---|---|
| mofe.go.kr WAF 가 ConnectionReset 으로 응답하는 시간대 존재 | Step 1 RSS 로딩 실패 | 30초~1분 대기 후 새로고침 — 자동 백오프 재시도 3회 |
| GPT Image 가 한국어 글자를 깨뜨릴 때가 있음 | 카드 본문 글자 깨짐 | Step 3 「Pillow 직접 렌더 경로」 강제 사용 또는 페이지별 재생성 (최대 2회) |
| Streamlit 1.36+ 에서 `st.cache_data` 의 SHA 충돌 경고 | 콘솔 노이즈 | 무시 가능 — 기능엔 영향 없음 |
| Buffer 의 Instagram 캐러셀 최대 10장 한도 | 11장 이상 카드 발행 불가 | Step 6 가 자동 차단. 페이지 수를 10 이하로 설정 |

---

## 12. 기여 · 라이선스

### 12.1 기여 절차

1. 이슈 또는 사내 채널에 변경 의도 공유 → 영향 범위 합의.
2. **feature 브랜치**에서 작업 (`feature/<short-desc>`). main 직접 푸시 금지.
3. PR 본문에 (a) 변경 요약 (b) 영향 단계(Step 1~6) (c) 테스트 방법 명시.
4. Step 6 (SNS 업로드) 관련 변경은 반드시 [`MERGE_GUIDE_SNS_UPLOAD.md`](MERGE_GUIDE_SNS_UPLOAD.md) 의 회귀 체크리스트 통과.
5. 안전 필터 관련 변경은 §9.4 의 REPL 검증 결과를 PR 에 첨부.

### 12.2 커밋 메시지 컨벤션

`<type>: <subject>` — type ∈ `feat / fix / docs / refactor / chore / ui / safety`. 본문은 한국어 권장 (담당자 회람용).

### 12.3 라이선스

내부 시연 / 행정 활용 목적의 비공개 프로젝트입니다. 외부 공개·재배포 전에는 보도자료 원본(저작권)·로고 사용권·OpenAI 출력물 라이선스 검토가 선행되어야 합니다.

---

## 13. 참고 문서

- [`design.md`](design.md) — 카드뉴스 디자인 톤앤매너 (담당자/디자이너용)
- [`MERGE_GUIDE_SNS_UPLOAD.md`](MERGE_GUIDE_SNS_UPLOAD.md) — Section 6 (Instagram 업로드) 머지·회귀 방지 가이드
- [`themes/template1/템플릿-1.txt`](themes/template1/) — 1페이지 포스터형 정본
- [`themes/template2/# Multi-Page Card News (2~5 Pages).txt`](themes/template2/) — 멀티페이지 정본
