"""멀티페이지 카드뉴스 템플릿 원문 로드 (표지 / 본문) — themes/template2 변형 선택."""

from __future__ import annotations

from pathlib import Path

from english_logo import load_english_logo_bytes as _load_english_logo_bytes_legacy
from mofe_mi_logos import load_english_mi_logo_bytes, load_korean_mi_logo_bytes

CODE_DIR = Path(__file__).resolve().parent
ROOT9 = CODE_DIR.parent
TEMPLATE2_DIR = ROOT9 / "themes" / "template2"

# 멀티페이지(2장 이상) 디자인 변형. 사용자가 단추로 선택한다.
MULTIPAGE_TEMPLATE_VARIANTS: dict[str, dict[str, object]] = {
    "v1": {
        "label": "재정경제부 1",
        "cover": TEMPLATE2_DIR / "템플릿1" / "템플릿(표지).txt",
        "body": TEMPLATE2_DIR / "템플릿1" / "템플릿(본문).txt",
    },
    "v2": {
        "label": "재정경제부2(모모페페 캐릭터)",
        "cover": TEMPLATE2_DIR / "템플릿2" / "multipage_momopepe_front.txt",
        "body": TEMPLATE2_DIR / "템플릿2" / "multipage_momopepe_body.txt",
    },
}
DEFAULT_MULTIPAGE_VARIANT = "v1"

# 하위 호환용 (기본 변형 v1)
COVER_TEMPLATE_PATH = MULTIPAGE_TEMPLATE_VARIANTS["v1"]["cover"]
BODY_TEMPLATE_PATH = MULTIPAGE_TEMPLATE_VARIANTS["v1"]["body"]


def multipage_variant_labels() -> dict[str, str]:
    """변형 id → 단추 라벨."""
    return {k: str(v["label"]) for k, v in MULTIPAGE_TEMPLATE_VARIANTS.items()}


def _variant(variant: str | None) -> dict[str, object]:
    return MULTIPAGE_TEMPLATE_VARIANTS.get(
        variant or DEFAULT_MULTIPAGE_VARIANT,
        MULTIPAGE_TEMPLATE_VARIANTS[DEFAULT_MULTIPAGE_VARIANT],
    )


def load_cover_template_text(variant: str | None = DEFAULT_MULTIPAGE_VARIANT) -> str:
    path = _variant(variant)["cover"]
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def load_body_template_text(variant: str | None = DEFAULT_MULTIPAGE_VARIANT) -> str:
    path = _variant(variant)["body"]
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def load_english_logo_bytes(
    ko_logo_path: Path | None = None,
    *,
    ko_logo_bytes: bytes | None = None,
) -> bytes | None:
    """영문 카드뉴스 합성용 — `themes/재정경제부 MI/영문-좌우조합.png`."""
    data = load_english_mi_logo_bytes()
    if data:
        return data
    return _load_english_logo_bytes_legacy(ko_logo_path, ko_logo_bytes=ko_logo_bytes)


def load_korean_logo_bytes() -> bytes | None:
    """한국어 카드뉴스 합성용 — `themes/재정경제부 MI/국문-좌우조합.png`."""
    return load_korean_mi_logo_bytes()
