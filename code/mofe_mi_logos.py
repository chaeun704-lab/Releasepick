"""재정경제부 공식 MI 로고 (카드뉴스 합성용)."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image

from render_cards import trim_logo_rgba

CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent
MI_DIR = ROOT / "themes" / "재정경제부 MI"

KO_MI_LOGO_PATH = MI_DIR / "국문-좌우조합.png"
EN_MI_LOGO_PATH = MI_DIR / "영문-좌우조합.png"


def _trimmed_png_bytes(path: Path) -> bytes | None:
    try:
        raw = path.read_bytes()
        im = trim_logo_rgba(Image.open(BytesIO(raw)))
        buf = BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except OSError:
        return None


@lru_cache(maxsize=4)
def _cached_trimmed(path_str: str, mtime_ns: int) -> bytes | None:
    return _trimmed_png_bytes(Path(path_str))


def _load_trimmed(path: Path) -> bytes | None:
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return _trimmed_png_bytes(path)
    return _cached_trimmed(str(path.resolve()), mtime_ns)


def load_korean_mi_logo_bytes() -> bytes | None:
    return _load_trimmed(KO_MI_LOGO_PATH)


def load_english_mi_logo_bytes() -> bytes | None:
    return _load_trimmed(EN_MI_LOGO_PATH)


def load_card_logo_bytes(copy_locale: str = "ko") -> bytes | None:
    """한국어 카드 → 국문 MI, 영어 카드 → 영문 MI (여백 자동 트림)."""
    if copy_locale == "en":
        return load_english_mi_logo_bytes()
    return load_korean_mi_logo_bytes()


def trim_mi_logo_file(path: Path, *, pad: int = 2) -> tuple[int, int] | None:
    """디스크의 MI PNG 여백 제거 후 덮어쓰기. 반환: (이전 크기, 이후 크기)."""
    try:
        im = Image.open(path)
        old = im.size
        trimmed = trim_logo_rgba(im, pad=pad)
        trimmed.save(path, format="PNG", optimize=True)
        return old, trimmed.size
    except OSError:
        return None


def trim_all_mi_logo_files() -> dict[str, tuple[tuple[int, int], tuple[int, int]]]:
    out: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {}
    for p in (KO_MI_LOGO_PATH, EN_MI_LOGO_PATH):
        result = trim_mi_logo_file(p)
        if result:
            out[p.name] = result
    _cached_trimmed.cache_clear()
    return out
