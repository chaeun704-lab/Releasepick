"""OpenAI Images API — GPT Image 계열 슬라이드 일러스트.

최신 Image API 기준: GPT Image 모델(`gpt-image-2`, `gpt-image-1.5`, …)은
`response_format` 미지원 · 응답에 `b64_json` 기본 포함.
참고: https://developers.openai.com/api/docs/guides/image-generation
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
from urllib.request import urlopen

from openai import OpenAI

from content_filter import append_image_safety, assert_plan_safe
from mofe_mi_logos import load_card_logo_bytes
from render_cards import (
    CARD_HEIGHT,
    CARD_SIZE,
    CARD_WIDTH,
    composite_card_logo,
    get_logo_box,
    section_tone_palette,
)
from template_resources import load_body_template_text, load_cover_template_text

LOGO_POS_EN_LABELS: dict[str, str] = {
    "top_right": "top-right",
    "top_left": "top-left",
    "bottom_center": "bottom-center",
}


LOGO_POS_KO_LABELS: dict[str, str] = {
    "top_right": "우측 상단",
    "top_left": "좌측 상단",
    "bottom_center": "하단 중앙",
}

# 영문 카드 AI 로고에 쓸 공식 영문 기관명 (철자 고정)
ENGLISH_LOGO_WORDMARK = "Ministry of Finance and Economy"
KOREAN_LOGO_WORDMARK = "재정경제부"


def _logo_layout_tail(logo_pos: str, theme_id: str, *, copy_locale: str) -> str:
    """로고 합성 영역을 비워 두도록 지시 (공식 MI PNG는 생성 후 Pillow로 합성)."""
    bx, by, bw, bh = get_logo_box(theme_id, logo_pos)
    x1, y1 = bx + bw, by + bh
    pad = 28
    if copy_locale == "en":
        pos = LOGO_POS_EN_LABELS.get(logo_pos, logo_pos)
        common = (
            "[LOGO ZONE — DO NOT DRAW]\n"
            f"Do NOT draw any government logo, ministry emblem, wordmark, or fake institution text. "
            f"Leave the {pos} zone (x={bx}–{x1}, y={by}–{y1} on {CARD_WIDTH}×{CARD_HEIGHT}) as clean background "
            "matching the surrounding card (no placeholder boxes, no dashed frames, no label text).\n"
            "The official Ministry of Finance and Economy logo will be composited in post-processing.\n"
        )
    else:
        pos = LOGO_POS_KO_LABELS.get(logo_pos, logo_pos)
        common = (
            "[로고 영역 — 그리지 말 것]\n"
            f"정부 로고·재정경제부 엠블럼·기관명 워드마크를 절대 그리지 말 것. "
            f"{pos} 영역(x={bx}~{x1}, y={by}~{y1}, {CARD_WIDTH}×{CARD_HEIGHT})은 주변 배경과 동일하게 비워 둔다. "
            "placeholder·점선·'로고 영역' 문구 금지. 공식 MI 로고는 생성 후 별도 합성된다.\n"
        )
    if logo_pos == "bottom_center":
        safe_y = max(120, by - pad)
        if copy_locale == "en":
            layout = (
                f"[COPY SAFE ZONE]\n"
                f"- Logo at bottom center (y≥{by}); keep title/bullets/art above y≈{safe_y}.\n"
            )
        else:
            layout = (
                f"[문안 안전 영역]\n"
                f"- 로고는 하단 중앙(y≥{by}); 제목·불릿·일러스트는 y≈{safe_y} 위.\n"
            )
    elif logo_pos == "top_right":
        if copy_locale == "en":
            layout = (
                f"[COPY SAFE ZONE]\n"
                f"- Logo top-right (x≥{bx - pad}, y≤{y1 + pad}); copy/art elsewhere.\n"
            )
        else:
            layout = (
                f"[문안 안전 영역]\n"
                f"- 로고 우상단(x≥{bx - pad}, y≤{y1 + pad}); 문안·일러스트는 다른 영역.\n"
            )
    else:
        if copy_locale == "en":
            layout = (
                f"[COPY SAFE ZONE]\n"
                f"- Logo top-left (x≤{x1 + pad}, y≤{y1 + pad}); copy/art elsewhere.\n"
            )
        else:
            layout = (
                f"[문안 안전 영역]\n"
                f"- 로고 좌상단(x≤{x1 + pad}, y≤{y1 + pad}); 문안·일러스트는 다른 영역.\n"
            )
    return common + layout


def build_english_logo_layout_block(logo_pos: str, theme_id: str = "mofe_body") -> str:
    return _logo_layout_tail(logo_pos, theme_id, copy_locale="en")


def build_korean_logo_layout_block(logo_pos: str, theme_id: str = "mofe_body") -> str:
    return _logo_layout_tail(logo_pos, theme_id, copy_locale="ko")


def _apply_official_mi_logo(
    im: Any,
    *,
    copy_locale: str,
    theme_id: str,
    logo_pos: str,
) -> Any:
    """GPT Image 결과 위에 공식 MI PNG 합성."""
    from PIL import Image

    if not isinstance(im, Image.Image):
        return im
    logo_bytes = load_card_logo_bytes(copy_locale)
    if not logo_bytes:
        return im
    return composite_card_logo(
        im,
        logo_bytes,
        theme_id=theme_id,
        logo_position=logo_pos,
        wipe_zone_first=False,
    )

SECTION_TONE_LABELS: dict[str, str] = {
    "neutral": "기본(블루)",
    "green": "녹색",
    "blue": "파랑",
    "purple": "보라",
    "orange": "주황",
}

logger = logging.getLogger(__name__)

# UI 라벨 → `images.generate` 의 model 값 (카드 전면 생성에 사용)
IMAGE_MODEL_OPTIONS: list[tuple[str, str]] = [
    ("GPT Image 2 (gpt-image-2)", "gpt-image-2"),
    ("GPT Image 1.5 (gpt-image-1.5)", "gpt-image-1.5"),
    ("GPT Image 1 (gpt-image-1)", "gpt-image-1"),
    ("GPT Image 1 Mini (gpt-image-1-mini)", "gpt-image-1-mini"),
]


def image_models_for_selectbox() -> list[str]:
    return [label for label, _ in IMAGE_MODEL_OPTIONS]


def resolve_model(label: str) -> str:
    for lab, mid in IMAGE_MODEL_OPTIONS:
        if lab == label:
            return mid
    return IMAGE_MODEL_OPTIONS[0][1]


def _decode_response(resp: Any) -> bytes | None:
    if not resp or not getattr(resp, "data", None):
        return None
    item = resp.data[0]
    b64 = getattr(item, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    url = getattr(item, "url", None)
    if url:
        with urlopen(url, timeout=120) as r:
            return r.read()
    return None


PROMPT_MAX_CHARS = 28_000

# 위 [최우선] 블록의 항목 이름(라벨)이 이미지에 그대로 그려지는 것을 막는다.
_LABEL_GUARD = (
    "\n[문안 표기 규칙 — 매우 중요]\n"
    "위 항목 이름(`시리즈명`, `헤드카피`, `역할`, `슬라이드 제목`, `표지 슬라이드 제목`, "
    "`표지 보조 불릿`, `불릿`, `각주`, `참고` 등)과 콜론(`:`)은 내용 분류용 메타데이터일 뿐이다. "
    "`← 강조:`·`[highlight:]` 표기와 그 뒤 단어 목록도 하이라이트 힌트일 뿐이며 이미지에 그리지 않는다. "
    "이 라벨 단어와 콜론은 절대 이미지에 글자로 그리지 말 것. 라벨 뒤의 실제 값(문안)만 디자인 요소로 렌더링한다.\n"
    "Do NOT render any of these field labels (e.g. '시리즈명:', '헤드카피:', '역할:', '각주:') or their colons as visible text. "
    "Only render the actual copy values, never the field names.\n"
)

# 본문 이미지 생성 시 템플릿 예시 문안이 기획안을 덮어쓰는 것을 막는다.
_CONTENT_FROM_PLAN_GUARD = (
    "\n[문안 출처 규칙 — 매우 중요]\n"
    "이미지에 표시할 모든 한글/영문 카피는 **오직 [최우선] 블록**의 시리즈명·헤드카피·슬라이드 제목·불릿·각주에서만 가져온다.\n"
    "아래 디자인 규격·템플릿 안의 예시 페이지, 샘플 표/카드, "
    "가입·판매·투자·펀드·한입경제·국민성장펀드·소비자물가·2월 소비자물가·2.0%·근원물가·생활물가·"
    "모두의 지역상권·친환경 녹색 소비·에너지는 아끼고·로컬창업·지역상권 회복·녹색 소비 확산·"
    "석유류·장바구니·shopping basket 등 **채워진 데모 문안·수치**는 "
    "레이아웃·색·타이포 참고용일 뿐이며 **절대 이미지에 글자로 그리지 말 것**.\n"
    "템플릿에 적힌 정책명·헤드라인·배지 예시를 복사하지 말고, 반드시 [최우선] 문안만 사용한다.\n"
    "Do NOT copy any sample/demo text from the design spec into the image. "
    "Only render copy from the [최우선] block above.\n"
)

# 템플릿 파일에 포함된 '채워진 예시 페이지' 섹션 — 이미지 프롬프트에서 제거
_BODY_EXAMPLE_SECTION_MARKERS = (
    "## 9. 페이지 시퀀스 가이드",
    "## 10. 체크리스트 (본문 출고 전)",
    "## 11. 페이지별 권장 구성 예시",
    "## 11. 빠른 시작 (HTML/CSS 예시)",
)
_COVER_EXAMPLE_SECTION_MARKERS = (
    "## 11. 카드뉴스 표지 제작 절차",
    "## 12. AI 이미지 생성용 프롬프트",
    "## 13. 카드뉴스 표지 문안 템플릿",
)
# 1장 인포그래픽 템플릿(템플릿-1.txt) — AI 프롬프트·HTML·샘플 문안 섹션
_SINGLE_PAGE_EXAMPLE_SECTION_MARKERS = (
    "## 9. AI 이미지 생성용 프롬프트",
    "## 10. 문안 구성 템플릿",
    "## 11. 빠른 시작 (HTML/CSS",
)


def _strip_template_examples(spec: str, markers: tuple[str, ...]) -> str:
    """채워진 샘플 페이지/제작 예시 섹션을 잘라 레이아웃 가이드만 남긴다."""
    cut_at = len(spec)
    for marker in markers:
        idx = spec.find(marker)
        if idx != -1:
            cut_at = min(cut_at, idx)
    if cut_at < len(spec):
        spec = spec[:cut_at].rstrip()
    return spec


# 레이아웃·템플릿에 박힌 시리즈별 데모 문구 → 플레이스홀더 (프롬프트용)
_TEMPLATE_DEMO_REPLACEMENTS = (
    ("🔑 한입경제 #5", "🔑 [시리즈명]"),
    ("🔑 한입경제 #6", "🔑 [시리즈명]"),
    ("한입경제 #5", "[시리즈명]"),
    ("한입경제 #6", "[시리즈명]"),
    ("국민성장펀드", "[주제명]"),
    ("2026 친환경 녹색 소비·관광", "[정책명 예시]"),
    ("모두의 지역상권 추진 전략 2026", "[정책명 예시]"),
    ("2026 모두의 지역상권 추진 전략", "[정책명 예시]"),
    ("2026 모두의 지역상권 추진전략", "[정책명 예시]"),
    ("모두의 지역상권", "[주제 예시]"),
    ("모두의 '로컬창업'", "[키워드 예시]"),
    ("모두의 ‘로컬창업’", "[키워드 예시]"),
    ("에너지는 아끼고!", "[헤드라인 예시]"),
    ("소비와 관광은 살리고!", "[헤드라인 예시]"),
    ("국민 생활에 힘이 되는", "[헤드라인 예시]"),
    ("정책을 더 쉽게!", "[헤드라인 예시]"),
    ("[녹색 소비 확산]", "[배지 예시]"),
    ("[친환경 관광 활성화]", "[배지 예시]"),
    ("[민생안정 지원]", "[배지 예시]"),
    ("[지역상권 회복]", "[배지 예시]"),
    ("[AI 활용]", "[배지 예시]"),
    ("[소상공인 지원]", "[배지 예시]"),
    ("민·관이 함께 만드는 지속 가능한 대한민국 프로젝트", "[보조 문구 예시]"),
    ("친환경 녹색 소비 촉진", "[주제 예시]"),
    ("친환경 녹색 소비", "[주제 예시]"),
    ("2월 소비자물가", "[주제 라벨]"),
    ("2.0% 상승", "[핵심 수치]"),
    ("2.0%", "[핵심 수치]"),
    ("2.3% 상승", "[보조 수치]"),
    ("근원물가", "[지표 예시]"),
    ("생활물가", "[지표 예시]"),
    ("소비자물가·근원물가", "[차트 제목 예시]"),
    ("석유류 가격", "[키워드 예시]"),
    ("체감물가 안정", "[키워드 예시]"),
    ("아이디어만으로 충분한", "[본문 타이틀 예시]"),
    ("지역상권 혁신 기반을 구축하고", "[마무리 예시]"),
    ("點:창업", "[사이드카피 예시]"),
)


def _strip_markdown_fenced_blocks(spec: str) -> str:
    """``` … ``` 샘플 문안·HTML 블록 제거."""
    return re.sub(r"```[^\n]*\n.*?```", "", spec, flags=re.DOTALL)


def _sanitize_template_demo_copy(spec: str) -> str:
    for old, new in _TEMPLATE_DEMO_REPLACEMENTS:
        spec = spec.replace(old, new)
    return spec


def _finalize_design_spec(spec: str) -> str:
    spec = _strip_markdown_fenced_blocks(spec)
    return _sanitize_template_demo_copy(spec)


def _prepare_body_spec_for_prompt(body_template_full: str) -> str:
    spec = _strip_template_examples(body_template_full, _BODY_EXAMPLE_SECTION_MARKERS)
    return _finalize_design_spec(spec)


def _prepare_cover_spec_for_prompt(cover_template_full: str) -> str:
    spec = _strip_template_examples(cover_template_full, _COVER_EXAMPLE_SECTION_MARKERS)
    return _finalize_design_spec(spec)


# 본문 구성 — 제목 아래 다칸 요약·번호 구분 금지 (표지·본문·1장 공통)
_UNIFIED_CONTENT_LAYOUT_BLOCK = (
    "[본문 구성 규칙 — 매우 중요]\n"
    "- 제목·헤드라인 **바로 아래**에 3칸·3열 요약 블록, **별도 키워드 배지 줄/행**(알약 배지 여러 개), "
    "비교 컬럼으로 내용을 나누지 말 것.\n"
    "- 불릿마다 `1.` `2.` `③` 번호를 새로 붙이거나, 색이 다른 카드 2~3개로 쪼개 **다른 주제처럼** 보이게 하지 말 것.\n"
    "- [최우선]의 **모든 불릿**은 **단일 본문 블록 1개**(하나의 라운드 카드 또는 연속 ■ 불릿 리스트)에 이어서 배치.\n"
    "- 단, **불릿 문장 안**의 핵심 단어(정책명·수치·기한·혜택)는 Bold+액센트색 하이라이트 **필수** — "
    "이것은 배지 줄이 아니라 본문 타이포 강조다.\n"
    "- 슬라이드 제목→헤드라인, 불릿 전체→한 덩어리 본문, 각주→하단 마무리(별도 소제목·번호 섹션 없음).\n"
    "Do NOT create three summary blocks or a separate keyword badge row under the headline. "
    "Do NOT split bullets into numbered sections or multiple topic cards. "
    "DO highlight important words inside bullet sentences (bold + accent color).\n"
)


def _body_keyword_highlight_block(
    section_tone: str,
    theme_id: str = "mofe_body",
    *,
    copy_locale: str = "ko",
) -> str:
    """본문 불릿 내부 키워드 하이라이트 — template2 본문 §3-3·§5."""
    pal = section_tone_palette(theme_id, section_tone)
    accent = pal.get("body_accent", pal["stripe"])
    if copy_locale == "en":
        return (
            "[BODY KEYWORD HIGHLIGHT — REQUIRED]\n"
            f"Inside the single body card, highlight 2~4 important terms per bullet from [최우선] copy only: "
            "policy names, amounts, percentages, deadlines, benefit keywords.\n"
            f"Style: Bold + accent color {accent} (or bottom highlight block in a lighter tint of {accent}). "
            "Pick ONE highlight style for body bullets only — NOT on headlines/titles. "
            "This is inline typography inside bullet text — NOT a separate badge row under the headline.\n"
            "Do NOT leave body bullets as plain single-color text.\n"
        )
    return (
        "[본문 키워드 하이라이트 — 필수]\n"
        "[최우선] 불릿 문장 **안**에서 정책명·수치·%·금액·기한·혜택 핵심어를 불릿당 2~4개 골라 "
        f"**Bold + 액센트색 {accent}** 로 눈에 띄게 강조한다.\n"
        "강조 방식은 **본문 불릿·각주에만** 페이지 전체 1종으로 통일: "
        "(1) 하단 컬러 블록 (2) Bold+액센트색 (3) 액센트 밑줄 중 택1. **제목·헤드라인에는 사용 금지**.\n"
        "헤드라인 아래 **별도 배지 줄**이 아니라, 본문 타이포 인라인 강조다. 본문을 단색 평문만으로 두지 말 것.\n"
        "각주·마무리 문장에도 핵심어 1~2개는 동일 방식으로 강조 가능.\n"
    )


# 제목·헤드라인 타이포 — 형광펜·마커식 강조 금지 (본문 불릿 강조와 분리)
_HEADLINE_TYPO_BLOCK = (
    "[제목·헤드라인 타이포 — 깔끔하게]\n"
    "헤드라인·슬라이드 제목·헤드카피는 **단일 톤 ExtraBold 타이포**로만 처리한다. "
    "굵기·크기·행간·정렬로 위계를 만든다.\n"
    "금지: 형광펜·마커 하이라이트, 글자 뒤 컬러 블록(50% 배경), 컬러 밑줄, "
    "제목 단어마다 다른 색, 키워드 알약 배지.\n"
    "본문 불릿 **내부** 키워드 강조만 Bold+액센트색 허용 — 제목에는 적용하지 않는다.\n"
    "Headlines: clean single-color ExtraBold typography only. "
    "NO highlighter/marker blocks, NO colored underlines, NO per-word accent colors in titles.\n"
)


# template2 시각 규칙 요약 — 데모 문안 없음 (1장 프롬프트용)
_TEMPLATE2_VISUAL_INHERIT_BLOCK = (
    "[template2 표지·본문 시각 규칙 요약 — 문안 없음, 풍부한 캠페인]\n"
    "- 캔버스 1000×1350, Pretendard, 밝은 그라데이션·웜 톤·스카이 톤 배경(섹션 톤 연동)\n"
    "- UI: 시리즈 칩, **깔끔한 ExtraBold 헤드라인**(단일 색·형광펜 금지), "
    "**단일 본문 카드 1개**(컬러 헤더 칩+■ 불릿 연속)\n"
    "- 3D: 클레이 매트 캐릭터+오브젝트 스폿(25~40%), 배경 장식(구름·실루엣·원형 중 1~2종)\n"
    "- 밋밋한 텍스트-only 금지. 본문 분할(3칸 요약·번호 구분)은 금지\n"
)


# 시각 다양화 — 단일 본문 블록 유지하며 디자인 풍부하게
_VISUAL_RICHNESS_BLOCK = (
    "[시각 다양화 — 본문은 한 블록, 디자인은 풍부하게]\n"
    "단일 본문 블록 규칙을 지키면서도 **완성된 template2 캠페인 카드**처럼 보이게 한다. 흰 화면·플랫 문서 금지.\n"
    "- **배경**: 섹션 톤 그라데이션, 은은한 구름·도시 실루엣·옅은 원형/곡선 장식 1~2종(겹치되 과하지 않게).\n"
    "- **헤드라인**: 단일 색 ExtraBold, 행간·크기로만 위계. 형광펜·컬러 박스·밑줄 강조 **금지**.\n"
    "- **단일 본문 카드 꾸미기**: 상단 컬러 헤더 칩(슬라이드 제목), 좌측 액센트 띠, 라운드+부드러운 그림자, "
    "카드 모서리 작은 라인 아이콘 1~2개. **불릿 문장 내부** 핵심어는 Bold+섹션 액센트색 하이라이트(필수).\n"
    "- **3D**: 캐릭터+정책 오브젝트를 **눈에 띄게**(25~40%). 매트 클레이, 자연광·그림자로 깊이감.\n"
    "- **레이어**: 상단 컬러 밴드, 시리즈 칩, 카드와 3D의 전후 겹침으로 입체감.\n"
    "- **시안별 차별**: 선택 시안(A/B/C)의 배경·3D 구도·정렬을 최우선 반영해 후보마다 다르게 보이게.\n"
)


# 전 카드뉴스 공통 — 풍부한 캠페인 (표지·본문·1장)
_BALANCED_CAMPAIGN_DESIGN_BLOCK = (
    "[풍부한 공공 캠페인 디자인 — template2 톤]\n"
    "- 재정경제부 카드뉴스 **완성도 높은 시각 디자인** 필수. 인쇄물·SNS 카드뉴스 수준.\n"
    "- 배경·장식·3D·컬러 밴드·깔끔한 헤드라인 타이포·본문 카드 스타일을 골고루 활용.\n"
    "- 본문 문안은 **단일 블록 1개**에만(3칸 요약·배지 줄·1·2 번호 분할 금지).\n"
    "- 3D·배경·액센트로 **다양하고 풍부**하게, 요소 남발로 **요란**하게는 말 것.\n"
)


# 1장 통합 카드 품질·구도
_SINGLE_PAGE_CAMPAIGN_QUALITY_BLOCK = (
    "[1장 통합 카드 — 풍부한 template2 스타일]\n"
    "- 구성: 시리즈 칩 → **깔끔한 헤드라인**(단일 색 ExtraBold) → **단일 꾸민 본문 카드**(헤더 칩+■ 불릿 전체) → "
    "눈에 띄는 3D 일러스트 → 선언형 마무리.\n"
    "- 3칸 요약·배지 줄·불릿 분할 없음. 시각 위계는 **본문 불릿 내 키워드 하이라이트**로.\n"
)


def _prepare_single_page_spec_for_prompt(template_full: str) -> str:
    """1장 통합 카드 — template-1 가이드 + template2 시각 규칙 요약(전체 원문 병합 금지)."""
    one = _strip_template_examples(template_full, _SINGLE_PAGE_EXAMPLE_SECTION_MARKERS)
    combined = f"{one.rstrip()}\n\n{_TEMPLATE2_VISUAL_INHERIT_BLOCK}"
    return _finalize_design_spec(combined)


def _build_priority_copy_block(
    plan: dict[str, Any],
    slide: dict[str, Any],
    *,
    copy_locale: str = "ko",
    page_index_1based: int | None = None,
    for_cover: bool = True,
    repeat: bool = False,
) -> str:
    bullets = slide.get("bullets") or []
    blines = "\n".join(f"- {b}" for b in bullets) if bullets else (
        "(no bullets)" if copy_locale == "en" else "(불릿 없음)"
    )
    foot = slide.get("footnote") or (
        "(no footnote)" if copy_locale == "en" else "(각주 없음)"
    )
    if repeat:
        header = (
            "[최우선 문안 재확인 — 이미지에 아래 값만 글자로 렌더링]"
            if copy_locale != "en"
            else "[COPY RE-CHECK — render ONLY the values below as visible text]"
        )
    elif page_index_1based is not None and not for_cover:
        header = f"[최우선 — 페이지 {page_index_1based} 반드시 반영할 문안]"
    else:
        header = (
            "[최우선 — 반드시 이미지에 반영할 문안]"
            if copy_locale != "en"
            else "[HIGHEST PRIORITY — copy to render in the image]"
        )
    if page_index_1based is not None and not for_cover:
        role = slide.get("role", "body")
        return (
            f"{header}\n"
            f"역할: {role}\n"
            f"시리즈명(참고): {plan.get('series_title', '')}\n"
            f"헤드카피(참고): {plan.get('head_copy', '')}\n"
            f"슬라이드 제목: {slide.get('title', '')}\n"
            f"불릿:\n{blines}\n"
            f"각주: {foot}\n"
            + _LABEL_GUARD
        )
    return (
        f"{header}\n"
        f"시리즈명: {plan.get('series_title', '')}\n"
        f"헤드카피: {plan.get('head_copy', '')}\n"
        f"표지 슬라이드 제목: {slide.get('title', '')}\n"
        f"표지 보조 불릿:\n{blines}\n"
        f"각주: {foot}\n"
        + _LABEL_GUARD
    )


def _guess_highlight_terms(text: str) -> list[str]:
    """불릿에서 하이라이트 후보 단어를 추출(프롬프트 힌트용)."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    patterns = (
        r"[\d,.]+[%％]",
        r"[\d,.]+\s*억\s*원?",
        r"[\d,.]+\s*만\s*원?",
        r"['\"][^'\"]+['\"]",
        r"\d{4}년",
        r"\d{1,2}월",
        r"~\d+",
        r"최대\s*[\d,.]+[%％pP]?",
    )
    for pat in patterns:
        for m in re.finditer(pat, text):
            term = m.group(0).strip()
            if len(term) >= 2 and term not in seen:
                seen.add(term)
                found.append(term)
    # 2~6글자 한글 명사구(짧은 정책 키워드) — 과도 추출 방지
    for m in re.finditer(r"[가-힣]{2,6}(?:\s[가-힣]{1,4})?", text):
        term = m.group(0).strip()
        if term in seen or len(found) >= 6:
            continue
        if any(x in term for x in ("정부", "지원", "대상", "신청", "확대", "추진", "계획")):
            seen.add(term)
            found.append(term)
    return found[:6]


def _unified_slide_layout_map(
    slide: dict[str, Any],
    copy_locale: str,
    *,
    single_page: bool = False,
) -> str:
    """불릿을 단일 본문 블록에 배치 — 3칸 요약·번호 구분 금지."""
    bullets = slide.get("bullets") or []
    if bullets:
        blines_parts: list[str] = []
        for b in bullets:
            terms = _guess_highlight_terms(str(b))
            if copy_locale == "en":
                line = f"  · {b}"
                if terms:
                    line += f"  [highlight: {', '.join(terms)}]"
            else:
                line = f"  · {b}"
                if terms:
                    line += f"  ← 강조: {', '.join(terms)}"
            blines_parts.append(line)
        blines = "\n".join(blines_parts)
    else:
        blines = "  (no bullets)" if copy_locale == "en" else "  (불릿 없음)"
    foot = slide.get("footnote") or (
        "(no footnote)" if copy_locale == "en" else "(각주 없음)"
    )
    foot_terms = _guess_highlight_terms(str(foot)) if foot and foot not in (
        "(no footnote)",
        "(각주 없음)",
    ) else []
    page = "1-PAGE" if single_page else "PAGE"
    if copy_locale == "en":
        return (
            f"\n[{page} — copy placement: single body block only]\n"
            f"- Series chip ← series title\n"
            f"- Headline ← slide title + head copy\n"
            f"- **One unified body block** ← all bullets together (continuous list, NOT split):\n"
            f"{blines}\n"
            f"- Closing ← footnote: {foot}"
            + (f" [highlight: {', '.join(foot_terms)}]" if foot_terms else "")
            + "\n"
            "- FORBIDDEN: 3 summary blocks under headline, separate keyword badge row under headline, "
            "numbered 1/2/3 sections, separate colored cards per bullet.\n"
            "- REQUIRED: bold+accent inline highlights on key terms inside bullet sentences.\n"
        )
    return (
        f"\n[{'1장' if single_page else '본문'} — 문안 배치: 단일 본문 블록]\n"
        "- 시리즈 칩 ← 시리즈명\n"
        "- 헤드라인 ← 슬라이드 제목 + 헤드카피\n"
        f"- **단일 본문 카드 1개**(헤더 칩·그림자·꾸밈 OK) ← 불릿 전체 연속:\n"
        f"{blines}\n"
        f"- 마무리 ← 각주: {foot}"
        + (f" ← 강조: {', '.join(foot_terms)}" if foot_terms else "")
        + "\n"
        "- 금지: 헤드라인 아래 3칸 요약, **별도** 키워드 배지 줄, 1·2·3 번호 구분, 불릿마다 색 다른 카드 분할.\n"
        "- 필수: 불릿·각주 **문장 안** 핵심어 Bold+액센트색 인라인 하이라이트.\n"
    )


def _single_page_layout_map(slide: dict[str, Any], copy_locale: str) -> str:
    return _unified_slide_layout_map(slide, copy_locale, single_page=True)


# 표지(1페이지) 디자인 시안 3종 — 레이아웃 배치 / 폰트 정렬 / 3D 소스를 달리한다.
# 선택한 시안의 block을 concept_style_block으로 전달하면 표지·본문 전체에 동일 스타일이 적용된다.
DESIGN_VARIANTS: dict[str, dict[str, str]] = {
    "A": {
        "label": "시안 A · 하늘색 중앙형",
        "block": (
            "[디자인 시안 A — 하늘색 중앙 집중형 (최우선 반영)]\n"
            "- 배경: 밝은 스카이블루 그라데이션 + 은은한 흰 구름 1~2개, 상단 섹션 톤 컬러 밴드.\n"
            "- 구도: 가운데 정렬. 헤드라인 크게(단일 색 ExtraBold, 형광펜 금지), 아래 **단일 라운드 본문 카드**(그림자·헤더 칩).\n"
            "- 3D: 화면 중하단에 대표 클레이 캐릭터+오브젝트 1세트(30~40%), 부드러운 그림자.\n"
            "- 톤: 친근한 캠페인 포스터. 본문 불릿은 한 카드 안에만.\n"
        ),
    },
    "B": {
        "label": "시안 B · 웜톤 좌우 분할",
        "block": (
            "[디자인 시안 B — 웜 화이트 · 좌텍스트/우3D (최우선 반영)]\n"
            "- 배경: 웜 오프화이트 #FBF7EE + 민트·퍼플 옅은 원형 장식 2~3개.\n"
            "- 구도: 좌측 정렬 — 칩·헤드라인·**단일 본문 카드**(좌측 액센트 띠+컬러 헤더 칩), "
            "우측에 3D 클레이 그룹(캐릭터+오브젝트).\n"
            "- 3D: 우측 35% 영역, 카드와 겹침으로 깊이감.\n"
            "- 톤: template2 지역상권·웜 시리즈 느낌. 불릿 분할·배지 줄 없음.\n"
        ),
    },
    "C": {
        "label": "시안 C · 대각 밴드·하단 비주얼",
        "block": (
            "[디자인 시안 C — 대각 컬러 밴드 · 하단 3D (최우선 반영)]\n"
            "- 배경: 상단 대각선 섹션 톤 컬러 밴드 + 옅은 도시 실루엣 하단, 그라데이션 전환.\n"
            "- 구도: 헤드라인 좌상단, 중앙 **단일 본문 카드**(라운드+내부 아이콘), "
            "하단 가로 3D 오브젝트·캐릭터 밴드(2~3점).\n"
            "- 3D: 하단 25~35% 비주얼 띠. 정책 오브젝트 리듬.\n"
            "- 톤: 역동적이지만 본문은 한 덩어리 카드.\n"
        ),
    },
}


def design_variant_ids() -> list[str]:
    return list(DESIGN_VARIANTS.keys())


def design_variant_label(variant_id: str) -> str:
    v = DESIGN_VARIANTS.get(variant_id)
    return v["label"] if v else str(variant_id)


def design_variant_block(variant_id: str | None) -> str:
    if not variant_id:
        return ""
    v = DESIGN_VARIANTS.get(variant_id)
    return v["block"] if v else ""


def _clip_middle(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    h = max_len // 2 - 40
    t = max_len - h - 40
    return s[:h] + "\n\n...[중간 생략]...\n\n" + s[-t:]


def build_theme_description(
    *,
    theme_id: str,
    section_tone: str,
    body_layout: str,
    font_scale: float,
    use_custom_title: bool,
    use_custom_body: bool,
    title_color: str,
    body_color: str,
    logo_pos: str,
) -> str:
    parts = [
        f"YAML_테마={theme_id}",
        f"섹션톤={section_tone}",
        f"본문레이아웃={'흰카드(B)' if body_layout == 'white_card' else '여백(A)'}",
        f"폰트배율={font_scale}",
        f"로고위치={logo_pos}",
    ]
    if use_custom_title:
        parts.append(f"제목색지정={title_color}")
    if use_custom_body:
        parts.append(f"본문색지정={body_color}")
    return " | ".join(parts)


def _custom_text_color_directives(
    title_color: str | None,
    body_color: str | None,
    copy_locale: str,
    *,
    body_accent: str | None = None,
) -> str:
    """사용자가 고른 제목/본문 글자색을 섹션 톤보다 우선 적용하도록 강제하는 지시문."""
    lines: list[str] = []
    accent_note_ko = (
        f" 단, 불릿·각주 **내부** 정책명·수치·기한 등 핵심 키워드는 섹션 액센트 {body_accent}로 "
        "Bold 하이라이트는 유지한다(단색 평문만 금지)."
        if body_accent
        else " 단, 불릿·각주 내부 핵심 키워드 Bold+액센트색 하이라이트는 유지한다."
    )
    accent_note_en = (
        f" Still highlight 2~4 key terms per bullet in accent {body_accent} (bold) — not plain single-color body."
        if body_accent
        else " Still highlight key terms inside bullets with bold accent color."
    )
    if copy_locale == "en":
        if title_color:
            lines.append(
                f"[USER TITLE TEXT COLOR — HIGHEST PRIORITY] Render ALL title/headline glyphs strictly in {title_color}. "
                "This overrides the section tone color for text: the section tone applies only to bands, chips, "
                "and accent shapes, NOT to the title text color."
            )
        if body_color:
            lines.append(
                f"[USER BODY TEXT COLOR — HIGHEST PRIORITY] Default body/bullet text in {body_color}."
                + accent_note_en
            )
    else:
        if title_color:
            lines.append(
                f"[제목 글자색 — 최우선] 제목·헤드라인의 '글자색'은 반드시 사용자 지정 {title_color}로 렌더한다. "
                "섹션 톤 색은 상단 띠·배지·강조 도형에만 적용하고, 제목 글자색에는 적용하지 않는다."
            )
        if body_color:
            lines.append(
                f"[본문 글자색 — 최우선] 본문·불릿의 기본 '글자색'은 사용자 지정 {body_color}."
                + accent_note_ko
            )
    return ("\n".join(lines) + "\n") if lines else ""


def build_section_tone_prompt_block(
    section_tone: str,
    theme_id: str = "mofe_body",
    *,
    for_cover: bool,
    copy_locale: str = "ko",
    title_color: str | None = None,
    body_color: str | None = None,
) -> str:
    """섹션 컬러 톤을 hex·지시문으로 명시 (표지는 템플릿 기본 하늘색 예시보다 우선).

    title_color/body_color가 주어지면 글자색은 섹션 톤보다 우선해 사용자 지정색으로 강제한다.
    """
    pal = section_tone_palette(theme_id, section_tone)
    stripe = pal["stripe"]
    body_accent = pal.get("body_accent", stripe)
    # 사용자가 제목색을 지정하면 그 색이 섹션 톤 팔레트 제목색을 대체한다.
    title_c = title_color or pal["title_on_page"]
    label = SECTION_TONE_LABELS.get(section_tone, section_tone)
    custom = _custom_text_color_directives(
        title_color, body_color, copy_locale, body_accent=body_accent
    )
    if copy_locale == "en":
        if for_cover:
            return (
                "[COVER COLOR TONE — OVERRIDES default sky-blue examples in the spec below]\n"
                f"User-selected section tone: {label} ({section_tone})\n"
                f"Top color band, series chip, body card header chip — accent: {stripe}\n"
                f"Headline text color: {title_c}\n"
                "Rich campaign: gradient background, clouds OR skyline OR decorative shapes, "
                "clean single-color ExtraBold headline (no marker/highlighter), "
                "one styled unified body card (header chip), "
                "prominent 3D clay. All bullets in ONE block — no 3-column summary, no numbered split. "
                "Match inner pages. "
                + custom
            )
        return (
            "[BODY COLOR TONE — REQUIRED]\n"
            f"Section tone: {label} — top stripe/accent: {stripe}, title text: {title_c}, "
            f"body keyword accent: {body_accent}\n"
            "Rich visual design: gradient background, clean single-color headlines, styled single body card "
            "with header chip, spot 3D clay. Highlight key terms inside bullets (bold + accent). "
            "One block for all bullets — no split sections. "
            + custom
        )
    if for_cover:
        return (
            "[표지 컬러 톤 — 최우선 · 아래 표지 템플릿의 하늘색/블루 예시보다 이 설정이 우선]\n"
            f"사용자 선택 섹션 톤: {label} ({section_tone})\n"
            f"상단 컬러 밴드·시리즈 칩·본문 카드 헤더 칩·강조(주색): {stripe}\n"
            f"표지 제목·헤드라인 글자색: {title_c}\n"
            "배경 그라데이션·구름/실루엣·장식 도형으로 풍부하게. 헤드라인은 단일 색 ExtraBold(형광펜·컬러박스 금지). "
            "단일 본문 카드(헤더 칩·그림자·좌측 띠). 눈에 띄는 3D 클레이. "
            "불릿은 단일 블록만 — 3칸 요약·번호 분할 금지.\n"
            "배경은 섹션 톤과 어울리는 밝은 그라데이션 또는 웜 오프화이트. "
            "구름·도시 실루엣은 **하나만** 은은하게(겹침 금지).\n"
            "3D 클레이 일러스트를 반드시 포함(텍스트만 밋밋한 화면 금지). "
            "액센트 색 2종 이내. 불릿별 분리 카드·1·2·3 번호 구분 금지.\n"
            "녹색 톤이면 하늘색·네이비 기본 캠페인 배경을 쓰지 말 것.\n"
            "본문 페이지와 동일한 섹션 컬러로 시리즈 전체 톤을 통일한다.\n"
            + custom
        )
    return (
        "[본문 컬러 톤 — 필수 반영]\n"
        f"섹션 톤: {label} — 상단 띠·액센트: {stripe}, 제목 글자색: {title_c}, "
        f"본문 키워드 강조색: {body_accent}\n"
        "풍부한 본문 디자인: 그라데이션·장식·단일 꾸민 카드·스폿 3D. "
        "불릿 문장 **내부** 핵심어 Bold+강조색 하이라이트 필수. 불릿 단일 블록 유지.\n"
        + custom
    )


def _campaign_prompt_blocks(
    section_tone: str,
    theme_id: str,
    *,
    copy_locale: str,
) -> str:
    hl = _body_keyword_highlight_block(section_tone, theme_id, copy_locale=copy_locale)
    return (
        f"\n\n{_BALANCED_CAMPAIGN_DESIGN_BLOCK}\n\n{_HEADLINE_TYPO_BLOCK}\n\n{_VISUAL_RICHNESS_BLOCK}\n\n{hl}\n\n"
        f"{_UNIFIED_CONTENT_LAYOUT_BLOCK}\n"
    )


def build_first_card_image_prompt(
    plan: dict[str, Any],
    slide: dict[str, Any],
    *,
    cover_template_full: str,
    theme_desc: str,
    copy_locale: str = "ko",
    section_tone: str = "blue",
    theme_id: str = "mofe_body",
    logo_pos: str = "bottom_center",
    concept_style_block: str = "",
    single_page_template: bool = False,
    title_color: str | None = None,
    body_color: str | None = None,
) -> str:
    priority = _build_priority_copy_block(
        plan, slide, copy_locale=copy_locale, for_cover=True
    )
    if single_page_template:
        priority += _single_page_layout_map(slide, copy_locale)
    priority_repeat = _build_priority_copy_block(
        plan, slide, copy_locale=copy_locale, for_cover=True, repeat=True
    )
    if single_page_template:
        if copy_locale == "en":
            tail = (
                "\n[작업 지시]\n"
                "Create ONE finished single-page policy card-news image using the **same design system** "
                "as MOEF multi-page cover + body (template2): **rich campaign design** — "
                "gradient background, decorative layers, clean single-color ExtraBold headline, "
                "bold+accent highlights on key terms inside body bullets only, "
                "one styled unified body card (header chip, shadow), prominent 3D clay. "
                "Apply selected design variant A/B/C style strongly. "
                "NO three summary blocks under headline. NO numbered 1/2/3 sections. "
                "【COPY PRIORITY】Render ALL visible copy ONLY from [최우선]. "
                "Do NOT render sample/demo text from the design spec. "
                "Do NOT use white statistics-only infographic (giant %, comparison columns, line charts). "
                "Apply [표지 컬러 톤] hex for bands and badges. "
                "Leave the logo zone empty per [LOGO ZONE — DO NOT DRAW]. Single professional image."
                + build_english_logo_layout_block(logo_pos, theme_id)
            )
        else:
            tail = (
                "\n[작업 지시]\n"
                "재정경제부 1장 통합 카드뉴스. **풍부한 template2 캠페인 디자인** "
                "(그라데이션 배경, 깔끔한 단색 헤드라인, 꾸민 단일 본문 카드, 불릿 내 키워드 강조, 눈에 띄는 3D). "
                "시안 A/B/C 구도를 강하게 반영. "
                "3칸 요약·배지 줄·1·2 분할 **없음**, 불릿은 단일 카드 안에만.\n"
                "【문안 우선】오직 [최우선]의 시리즈명·헤드카피·슬라이드 제목·불릿·각주만 한글로 선명하게 배치한다. "
                "디자인 규격 속 예시·데모 문안은 절대 렌더링하지 않는다.\n"
                "흰 배경 통계 인포그래피(거대 %·2열 비교·차트만) 스타일은 사용하지 않는다. "
                "[표지 컬러 톤] hex를 상단·배지·강조에 반드시 적용한다. 로고 영역은 비워 둔다."
                + build_korean_logo_layout_block(logo_pos, theme_id)
            )
        spec_label = (
            "[1장 통합 카드 디자인 규격 — template2 표지·본문 상속, 문안은 [최우선]만 사용]"
        )
    elif copy_locale == "en":
        tail = (
            "\n[작업 지시]\n"
            "Create ONE finished policy card-news COVER image. "
            "【COPY PRIORITY】Render ALL visible copy ONLY from [최우선] / [COPY RE-CHECK]. "
            "Do NOT render sample/demo text from the design spec (e.g. regional commerce, green consumption). "
            "Follow [표지 디자인 규격] for layout, colors, typography, 3D elements. "
            "Leave the logo zone empty per [LOGO ZONE — DO NOT DRAW]. Single professional image."
            + build_english_logo_layout_block(logo_pos, theme_id)
        )
        spec_label = "[표지 디자인 규격 — 레이아웃·구도만 참고, 문안은 [최우선]만 사용]"
    else:
        tail = (
            "\n[작업 지시]\n"
            "재정경제부 정책 카드뉴스 '표지' 1장을 생성한다.\n"
            "【문안 우선】오직 [최우선]·[최우선 문안 재확인] 문안만 배치. 데모 문안 금지.\n"
            "【시각】풍부한 template2 캠페인(그라데이션·장식·꾸민 단일 카드·3D). "
            "3블록 요약·번호 분할 금지. 시안 구도 반영. "
            "[표지 컬러 톤] 반영. 로고 영역 비움."
            + build_korean_logo_layout_block(logo_pos, theme_id)
        )
        spec_label = "[표지 디자인 규격 — 레이아웃·구도만 참고, 문안은 [최우선]만 사용]"
    tone_block = build_section_tone_prompt_block(
        section_tone,
        theme_id,
        for_cover=True,
        copy_locale=copy_locale,
        title_color=title_color,
        body_color=body_color,
    )
    balanced_block = _campaign_prompt_blocks(section_tone, theme_id, copy_locale=copy_locale)
    if single_page_template:
        cover_spec = _prepare_single_page_spec_for_prompt(cover_template_full)
        visual_block = f"\n\n{_SINGLE_PAGE_CAMPAIGN_QUALITY_BLOCK}\n"
    else:
        cover_spec = _prepare_cover_spec_for_prompt(cover_template_full)
        visual_block = ""
    content_guard = _CONTENT_FROM_PLAN_GUARD
    spec = _clip_middle(
        cover_spec,
        PROMPT_MAX_CHARS
        - len(priority)
        - len(priority_repeat)
        - len(content_guard)
        - len(balanced_block)
        - len(visual_block)
        - len(tone_block)
        - len(theme_desc)
        - len(tail)
        - 120,
    )
    concept_part = f"\n\n{concept_style_block.strip()}\n" if concept_style_block.strip() else ""
    body = _clip_middle(
        f"{priority}{content_guard}{balanced_block}{visual_block}\n\n{tone_block}{concept_part}\n\n{spec_label}\n"
        f"{spec}\n\n[현재 디자인 설정]\n{theme_desc}\n{priority_repeat}{tail}",
        PROMPT_MAX_CHARS + 2000,
    )
    return append_image_safety(body)


def build_body_card_image_prompt(
    plan: dict[str, Any],
    slide: dict[str, Any],
    page_index_1based: int,
    *,
    body_template_full: str,
    theme_desc: str,
    copy_locale: str = "ko",
    section_tone: str = "blue",
    theme_id: str = "mofe_body",
    logo_pos: str = "bottom_center",
    concept_style_block: str = "",
    title_color: str | None = None,
    body_color: str | None = None,
) -> str:
    priority = _build_priority_copy_block(
        plan,
        slide,
        copy_locale=copy_locale,
        page_index_1based=page_index_1based,
        for_cover=False,
    )
    priority_repeat = _build_priority_copy_block(
        plan,
        slide,
        copy_locale=copy_locale,
        page_index_1based=page_index_1based,
        for_cover=False,
        repeat=True,
    )
    priority += _unified_slide_layout_map(slide, copy_locale, single_page=False)
    if copy_locale == "en":
        tail = (
            "\n[작업 지시]\n"
            "Create ONE finished policy card-news inner or closing page. "
            "【COPY PRIORITY】Render ALL visible copy ONLY from [최우선] / [COPY RE-CHECK]. "
            "Do NOT render sample/demo text from the design spec. "
            "Follow [본문 디자인 규격] for layout, palette, typography, 3D illustration. "
            "Leave the logo zone empty per [LOGO ZONE — DO NOT DRAW]. Single finished image."
            + build_english_logo_layout_block(logo_pos, theme_id)
        )
    else:
        tail = (
            "\n[작업 지시]\n"
            "재정경제부 카드뉴스 본문(또는 맺음) 페이지 1장을 생성한다.\n"
            "【문안 우선】오직 [최우선]·[최우선 문안 재확인] 문안만 배치. 데모 문안 금지.\n"
            "【시각】풍부한 본문(배경·장식·단일 꾸민 카드·3D). 불릿 문장 내 핵심어 Bold+액센트 하이라이트 필수. "
            "시안 구도 반영. 3칸 요약·번호·불릿 분할 카드 금지. "
            "로고 영역 비움. 표지와 동일 톤."
            + build_korean_logo_layout_block(logo_pos, theme_id)
        )
    tone_block = build_section_tone_prompt_block(
        section_tone,
        theme_id,
        for_cover=False,
        copy_locale=copy_locale,
        title_color=title_color,
        body_color=body_color,
    )
    balanced_block = _campaign_prompt_blocks(section_tone, theme_id, copy_locale=copy_locale)
    body_spec = _prepare_body_spec_for_prompt(body_template_full)
    spec = _clip_middle(
        body_spec,
        PROMPT_MAX_CHARS
        - len(priority)
        - len(priority_repeat)
        - len(_CONTENT_FROM_PLAN_GUARD)
        - len(balanced_block)
        - len(tone_block)
        - len(theme_desc)
        - len(tail)
        - 80,
    )
    concept_part = f"\n\n{concept_style_block.strip()}\n" if concept_style_block.strip() else ""
    body = _clip_middle(
        f"{priority}{_CONTENT_FROM_PLAN_GUARD}{balanced_block}\n\n{tone_block}{concept_part}\n\n"
        f"[본문 디자인 규격 — 레이아웃·색·타이포만 참고, 문안은 [최우선]만 사용]\n{spec}\n\n"
        f"[현재 디자인 설정]\n{theme_desc}\n{priority_repeat}{tail}",
        PROMPT_MAX_CHARS + 2000,
    )
    return append_image_safety(body)


def generate_image_png_bytes(client: OpenAI, model: str, prompt: str) -> bytes | None:
    """Images API로 PNG 바이트 생성 (프롬프트는 호출부에서 완성)."""
    p = prompt[:31000]
    base_kw: dict[str, Any] = {
        "model": model,
        "prompt": p,
        "n": 1,
        "output_format": "png",
        "moderation": "auto",
        "size": "1024x1024",
        "quality": "medium",
    }
    if model.startswith("gpt-image-2"):
        try:
            resp = client.images.generate(**base_kw)
        except Exception as exc:
            logger.warning("Image API error (%s): %s", model, exc)
            try:
                resp = client.images.generate(
                    model=model,
                    prompt=p,
                    n=1,
                    size="auto",
                    quality="auto",
                    output_format="png",
                    moderation="auto",
                )
            except Exception as exc2:
                logger.warning("Image API retry failed (%s): %s", model, exc2)
                return None
    else:
        try:
            resp = client.images.generate(**base_kw)
        except Exception as exc:
            logger.warning("Image API error (%s): %s", model, exc)
            return None
    return _decode_response(resp)


def generate_plan_card_jpegs(
    client: OpenAI,
    model: str,
    plan_dict: dict[str, Any],
    *,
    theme_desc: str,
    out_dir: Path,
    cover_template_full: str,
    body_template_full: str,
    progress_callback: Any | None = None,
    jpeg_size: tuple[int, int] = CARD_SIZE,
    quality: int = 92,
    copy_locale: str = "ko",
    section_tone: str = "blue",
    theme_id: str = "mofe_body",
    logo_pos: str = "bottom_center",
    concept_style_block: str = "",
    single_page_template: bool = False,
    title_color: str | None = None,
    body_color: str | None = None,
) -> list[Path]:
    """슬라이드별 전면 카드 이미지를 생성해 JPEG로 저장한다.

    title_color/body_color가 주어지면 글자색을 섹션 톤보다 우선해 강제 적용한다.
    """
    from io import BytesIO

    from PIL import Image

    if single_page_template:
        if not cover_template_full:
            from template_catalog import load_one_page_template_text

            cover_template_full = load_one_page_template_text()
    else:
        if not cover_template_full:
            cover_template_full = load_cover_template_text()
        if not body_template_full:
            body_template_full = load_body_template_text()

    assert_plan_safe(plan_dict)
    out_dir.mkdir(parents=True, exist_ok=True)
    slides = plan_dict.get("slides") or []
    paths: list[Path] = []
    total = len(slides)
    for i, slide in enumerate(slides):
        if i == 0:
            prompt = build_first_card_image_prompt(
                plan_dict,
                slide,
                cover_template_full=cover_template_full,
                theme_desc=theme_desc,
                copy_locale=copy_locale,
                section_tone=section_tone,
                theme_id=theme_id,
                logo_pos=logo_pos,
                concept_style_block=concept_style_block,
                single_page_template=single_page_template,
                title_color=title_color,
                body_color=body_color,
            )
        else:
            prompt = build_body_card_image_prompt(
                plan_dict,
                slide,
                i + 1,
                body_template_full=body_template_full,
                theme_desc=theme_desc,
                copy_locale=copy_locale,
                section_tone=section_tone,
                theme_id=theme_id,
                logo_pos=logo_pos,
                concept_style_block=concept_style_block,
                title_color=title_color,
                body_color=body_color,
            )
        png_bytes = generate_image_png_bytes(client, model, prompt)
        if not png_bytes:
            raise RuntimeError(f"페이지 {i + 1} 이미지 생성 실패 (모델: {model})")
        im = Image.open(BytesIO(png_bytes)).convert("RGB")
        im = im.resize(jpeg_size, Image.Resampling.LANCZOS)
        im = _apply_official_mi_logo(
            im, copy_locale=copy_locale, theme_id=theme_id, logo_pos=logo_pos
        )
        role = slide.get("role", "body")
        fname = f"{i + 1:02d}_{role}.jpg"
        p = out_dir / fname
        im.save(p, format="JPEG", quality=quality, optimize=True)
        paths.append(p)
        if progress_callback is not None:
            progress_callback(i + 1, total)
    return paths


def generate_cover_variant_jpegs(
    client: OpenAI,
    model: str,
    plan_dict: dict[str, Any],
    *,
    theme_desc: str,
    out_dir: Path,
    cover_template_full: str,
    variant_ids: list[str] | None = None,
    jpeg_size: tuple[int, int] = CARD_SIZE,
    quality: int = 92,
    copy_locale: str = "ko",
    section_tone: str = "blue",
    theme_id: str = "mofe_body",
    logo_pos: str = "bottom_center",
    single_page_template: bool = False,
    title_color: str | None = None,
    body_color: str | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Path]:
    """표지(1페이지)만 디자인 시안별로 생성한다. 반환: {variant_id: jpeg_path}."""
    from io import BytesIO

    from PIL import Image

    if single_page_template and not cover_template_full:
        from template_catalog import load_one_page_template_text

        cover_template_full = load_one_page_template_text()
    elif not cover_template_full:
        cover_template_full = load_cover_template_text()

    assert_plan_safe(plan_dict)
    slides = plan_dict.get("slides") or []
    if not slides:
        raise RuntimeError("기획안에 슬라이드가 없습니다.")
    slide0 = slides[0]
    if not variant_ids:
        variant_ids = design_variant_ids()
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    total = len(variant_ids)
    for idx, vid in enumerate(variant_ids):
        prompt = build_first_card_image_prompt(
            plan_dict,
            slide0,
            cover_template_full=cover_template_full,
            theme_desc=theme_desc,
            copy_locale=copy_locale,
            section_tone=section_tone,
            theme_id=theme_id,
            logo_pos=logo_pos,
            concept_style_block=design_variant_block(vid),
            single_page_template=single_page_template,
            title_color=title_color,
            body_color=body_color,
        )
        png_bytes = generate_image_png_bytes(client, model, prompt)
        if not png_bytes:
            raise RuntimeError(f"시안 {vid} 표지 생성 실패 (모델: {model})")
        im = Image.open(BytesIO(png_bytes)).convert("RGB").resize(
            jpeg_size, Image.Resampling.LANCZOS
        )
        im = _apply_official_mi_logo(
            im, copy_locale=copy_locale, theme_id=theme_id, logo_pos=logo_pos
        )
        p = out_dir / f"cover_variant_{vid}.jpg"
        im.save(p, format="JPEG", quality=quality, optimize=True)
        result[vid] = p
        if progress_callback is not None:
            progress_callback(idx + 1, total)
    return result
