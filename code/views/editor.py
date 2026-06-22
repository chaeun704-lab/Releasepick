"""Editor page: 5-step workflow (stitch _2 layout + stitch _3 result step)."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from buffer_publish import BufferError, create_instagram_post
from caption_llm import (
    MAX_CAPTION_LEN,
    generate_instagram_caption,
)
from content_filter import ContentFilterError
from image_gen import (
    build_theme_description,
    design_variant_block,
    design_variant_ids,
    design_variant_label,
    generate_cover_variant_jpegs,
    generate_plan_card_jpegs,
    image_models_for_selectbox,
    resolve_model,
)
import press_release
from hwpx_extract import HwpxExtractError, extract_text_from_hwpx_bytes
from pdf_extract import extract_text_from_pdf_bytes
from press_release import (
    Attachment,
    PressFetchError,
    PressItem,
    pick_best_attachment,
)
from plan_llm import generate_plan, translate_plan_to_english
from render_cards import list_theme_ids
from supabase_storage import (
    SupabaseStorageError,
    UploadedAsset,
    delete_objects,
    upload_card_images,
)
from template_catalog import (
    concept_image_block,
    concept_plan_block,
    concepts_for_pages,
    get_concept,
    load_one_page_template_text,
    page_group,
    recommend_concept_id,
)
from template_resources import (
    DEFAULT_MULTIPAGE_VARIANT,
    load_body_template_text,
    load_cover_template_text,
    multipage_variant_labels,
)
from template_thumbnails import generate_thumbnails_for_pages

from state import (
    SESSION_DEFAULTS,
    guard_plan,
    init_session,
    persist,
    plan_to_text,
    reset_session,
    seed_page_widgets_from_plan,
    try_hydrate,
    widgets_into_plan_dict,
)
from ui.components import (
    footer,
    pill,
    section_header,
    step_nav,
    top_app_bar,
)
from ui.theme import inject_global_css
from views import result_view


CODE_DIR = Path(__file__).resolve().parents[1]
ROOT = CODE_DIR.parent
load_dotenv(dotenv_path=ROOT / ".env", override=False)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "").strip()
BUFFER_API_KEY = os.getenv("BUFFER_API_KEY", "").strip()
BUFFER_CHANNEL_ID = os.getenv("BUFFER_CHANNEL_ID", "").strip()
_SNS_READY = bool(
    SUPABASE_URL
    and SUPABASE_SERVICE_ROLE_KEY
    and SUPABASE_BUCKET
    and BUFFER_API_KEY
    and BUFFER_CHANNEL_ID
)


# ---------- Helpers ----------

def _resolve_design_templates(
    target_pages: int, multipage_variant: str
) -> tuple[str, str, bool]:
    """1장이면 포스터 템플릿만, 2장 이상이면 선택한 변형 (v1/v2) 의 표지·본문.

    Returns (cover_full, body_full, single_page).
    """
    if target_pages == 1:
        return load_one_page_template_text(), "", True
    return (
        load_cover_template_text(multipage_variant),
        load_body_template_text(multipage_variant),
        False,
    )


# ---------- Step bodies ----------

def _payload_from_fetch(
    items_with_atts: list[tuple[PressItem, list[Attachment]]],
) -> list[dict]:
    """dataclass 결과를 session_state·snapshot 직렬화 친화 dict 로."""
    return [
        {"item": asdict(it), "attachments": [asdict(a) for a in atts]}
        for it, atts in items_with_atts
    ]


def _fmt_relative(ts: float | None) -> str:
    if not ts:
        return ""
    diff = max(0, int(time.time() - ts))
    if diff < 60:
        return f"{diff}초 전 조회"
    if diff < 3600:
        return f"{diff // 60}분 전 조회"
    return f"{diff // 3600}시간 전 조회"


def _select_press_attachment(
    item_dict: dict, att_dict: dict
) -> None:
    """선택된 첨부 다운로드 + 텍스트 추출 + 세션 저장."""
    ext = att_dict.get("ext", "")
    url = att_dict.get("url", "")
    filename = att_dict.get("filename", "")
    try:
        with st.spinner("첨부 다운로드 → 본문 추출…"):
            raw = press_release.download_attachment(url)
            if ext == "pdf":
                text = extract_text_from_pdf_bytes(raw)
            elif ext == "hwpx":
                text = extract_text_from_hwpx_bytes(raw)
            else:
                st.error(f"지원하지 않는 첨부 형식: {ext}")
                return
            if not text or not text.strip():
                st.error("본문 추출 결과가 비었습니다. 스캔본일 수 있습니다.")
                return
            st.session_state.pdf_bytes = raw
            st.session_state.pdf_name = filename
            st.session_state.press_text = text
            st.session_state.selected_press_ntt_id = item_dict.get("ntt_id")
            st.session_state.selected_attachment_name = filename
            st.session_state.selected_attachment_ext = ext
            persist()
        st.success(
            f"본문 추출 완료 ({len(text):,}자) — `{filename}`"
        )
    except PressFetchError as exc:
        st.error(f"다운로드 실패: {exc}")
    except HwpxExtractError as exc:
        st.error(f"HWPX 추출 실패: {exc}")
    except Exception as exc:
        st.error(f"본문 추출 실패: {type(exc).__name__}: {exc}")


def _clear_press_selection() -> None:
    """모드 전환·신규 업로드 시 기존 추출 본문/선택을 비운다."""
    for k in (
        "pdf_bytes",
        "pdf_name",
        "press_text",
        "selected_press_ntt_id",
        "selected_attachment_name",
        "selected_attachment_ext",
    ):
        st.session_state[k] = SESSION_DEFAULTS[k]
    st.session_state["_manual_pdf_fp"] = None
    persist()


def _render_rss_panel() -> None:
    payload: list[dict] = list(st.session_state.get("press_items_payload") or [])

    if not payload:
        st.info("재정경제부 보도·참고자료 게시판에서 최신 20건을 가져옵니다.")
        c_fetch, _ = st.columns([1.3, 4])
        with c_fetch:
            if st.button(
                "📰 최신 보도자료 불러오기",
                type="primary",
                key="rss_fetch_btn",
                use_container_width=True,
            ):
                try:
                    with st.spinner("보도자료 메타 수집 중..."):
                        items_with_atts = press_release.fetch_items_with_attachments(20)
                    if not items_with_atts:
                        st.warning(
                            "보도자료 메타 수집 결과가 비었습니다. 잠시 후 다시 시도하세요."
                        )
                    else:
                        st.session_state.press_items_payload = _payload_from_fetch(
                            items_with_atts
                        )
                        st.session_state.press_items_fetched_at = time.time()
                        st.session_state.press_page = 0
                        persist()
                        st.rerun()
                except PressFetchError as exc:
                    st.error(f"보도자료 메타 수집 실패: {exc}")
                except Exception as exc:
                    st.error(f"보도자료 메타 수집 실패: {type(exc).__name__}: {exc}")
        return

    meta_l, meta_r = st.columns([5, 1])
    with meta_l:
        st.caption("PDF · HWPX 외 첨부 게시물은 카드뉴스 제작 불가")
    with meta_r:
        if st.button(
            "🔄 새로고침",
            key="rss_refresh_btn",
            use_container_width=True,
        ):
            press_release.fetch_recent_items.clear()
            press_release.fetch_attachments.clear()
            press_release.fetch_items_with_attachments.clear()
            st.session_state.press_items_payload = []
            st.session_state.press_items_fetched_at = None
            st.session_state.press_page = 0
            persist()
            st.rerun()

    per_page = 5
    total = len(payload)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(st.session_state.get("press_page", 0)), total_pages - 1))
    start = page * per_page
    end = start + per_page
    page_payload = payload[start:end]

    sel_ntt = st.session_state.get("selected_press_ntt_id")
    col_widths = [4.5, 1.2, 1, 1, 1.6]
    h = st.columns(col_widths)
    for col, label in zip(h, ["제목", "발행일", "작성자", "형식", "선택"]):
        col.markdown(
            f"<div style='font-size:12px;font-weight:700;"
            f"color:var(--rp-on-surface-variant);"
            f"padding:6px 0 4px;letter-spacing:0.02em'>{label}</div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div style='border-top:1px solid var(--rp-outline-variant);"
        "margin:0 0 4px'></div>",
        unsafe_allow_html=True,
    )

    for idx, entry in enumerate(page_payload):
        item = entry.get("item", {})
        atts = entry.get("attachments", [])
        ntt_id = item.get("ntt_id", "")
        title = item.get("title", "(제목 없음)")
        pub_date_full = item.get("pub_date", "")
        pub_date = pub_date_full.split(" ")[0] if pub_date_full else ""
        author = item.get("author", "")
        best = pick_best_attachment(
            [Attachment(**a) for a in atts]
        )
        available_exts = {a.get("ext", "") for a in atts}
        has_pdf = "pdf" in available_exts
        has_hwpx = "hwpx" in available_exts
        has_hwp_only = (
            "hwp" in available_exts
            and not has_pdf
            and not has_hwpx
        )
        is_selected = ntt_id == sel_ntt

        r = st.columns(col_widths)
        with r[0]:
            title_color = (
                "var(--rp-primary)" if is_selected else "var(--rp-on-surface)"
            )
            prefix = "✓ " if is_selected else ""
            st.markdown(
                f"<div style='font-size:14px;font-weight:600;"
                f"color:{title_color};padding:8px 0'>{prefix}{title}</div>",
                unsafe_allow_html=True,
            )
        with r[1]:
            st.markdown(
                f"<div style='font-size:13px;color:var(--rp-on-surface-variant);"
                f"padding:9px 0'>{pub_date}</div>",
                unsafe_allow_html=True,
            )
        with r[2]:
            st.markdown(
                f"<div style='font-size:13px;color:var(--rp-on-surface-variant);"
                f"padding:9px 0'>{author}</div>",
                unsafe_allow_html=True,
            )
        with r[3]:
            if has_pdf:
                chip = pill("PDF", "ok")
            elif has_hwpx:
                chip = pill("HWPX", "info")
            elif has_hwp_only:
                chip = pill("HWP", "warn")
            elif not atts:
                chip = pill("없음", "warn")
            else:
                chip = pill("기타", "warn")
            st.markdown(
                f"<div style='padding:7px 0'>{chip}</div>",
                unsafe_allow_html=True,
            )
        with r[4]:
            btn_disabled = best is None
            btn_label = (
                "선택됨" if is_selected
                else ("선택" if best else "불가")
            )
            btn_type = "primary" if (best and not is_selected) else "secondary"
            sel_key = f"sel_{ntt_id}" if ntt_id else f"sel_p{page}_i{idx}"
            if st.button(
                btn_label,
                key=sel_key,
                type=btn_type,
                disabled=btn_disabled or is_selected,
                use_container_width=True,
            ):
                _select_press_attachment(item, asdict(best))
                st.rerun()
        st.markdown(
            "<div style='border-top:1px solid var(--rp-outline-variant);"
            "opacity:0.5;margin:2px 0'></div>",
            unsafe_allow_html=True,
        )

    pc_prev, pc_mid, pc_next = st.columns([1, 3, 1])
    with pc_prev:
        if st.button(
            "← 이전",
            key="rss_page_prev",
            use_container_width=True,
            disabled=page <= 0,
        ):
            st.session_state.press_page = page - 1
            persist()
            st.rerun()
    with pc_mid:
        st.markdown(
            f"<div style='text-align:center;font-size:13px;"
            f"color:var(--rp-on-surface-variant);padding:8px 0'>"
            f"페이지 {page + 1} / {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with pc_next:
        if st.button(
            "다음 →",
            key="rss_page_next",
            use_container_width=True,
            disabled=page >= total_pages - 1,
        ):
            st.session_state.press_page = page + 1
            persist()
            st.rerun()

    if st.session_state.press_text:
        with st.expander("추출 본문 미리보기 (디버깅용)"):
            st.text(st.session_state.press_text[:8000])


def _render_manual_upload_panel() -> None:
    st.caption(
        "PDF 파일만 지원합니다. "
        "보도자료 본문이 텍스트 레이어로 포함된 PDF 를 권장합니다 (스캔본은 추출 불가)."
    )
    pdf_file = st.file_uploader(
        "보도자료 PDF",
        type=["pdf"],
        key="manual_pdf_upload",
        accept_multiple_files=False,
    )
    if pdf_file is None:
        if st.session_state.press_text:
            with st.expander("추출 본문 미리보기 (디버깅용)"):
                st.text(st.session_state.press_text[:8000])
        return

    fp = f"{pdf_file.name}:{pdf_file.size}"
    if st.session_state.get("_manual_pdf_fp") != fp:
        try:
            raw = pdf_file.getvalue()
            with st.spinner("PDF → 본문 추출…"):
                text = extract_text_from_pdf_bytes(raw)
            if not text or not text.strip():
                st.error("본문 추출 결과가 비었습니다. 스캔본일 수 있습니다.")
                return
            st.session_state.pdf_bytes = raw
            st.session_state.pdf_name = pdf_file.name
            st.session_state.press_text = text
            st.session_state.selected_press_ntt_id = None
            st.session_state.selected_attachment_name = pdf_file.name
            st.session_state.selected_attachment_ext = "pdf"
            st.session_state._manual_pdf_fp = fp
            persist()
        except Exception as exc:
            st.error(f"본문 추출 실패: {type(exc).__name__}: {exc}")
            return

    st.success(
        f"본문 추출 완료 ({len(st.session_state.press_text):,}자) — `{pdf_file.name}`"
    )
    with st.expander("추출 본문 미리보기 (디버깅용)"):
        st.text(st.session_state.press_text[:8000])


def step_upload(client: OpenAI) -> None:
    section_header(1, "보도자료 선택")

    prev_mode = st.session_state.get("_prev_upload_mode")
    cur_mode = st.session_state.get("upload_mode", "rss")
    if prev_mode is not None and prev_mode != cur_mode:
        _clear_press_selection()
    st.session_state["_prev_upload_mode"] = cur_mode

    mode_labels = {
        "rss": "📰 최신 보도자료 (RSS)",
        "manual": "📁 PDF 직접 업로드",
    }
    mode = st.radio(
        "보도자료 입력 방식",
        list(mode_labels.keys()),
        format_func=lambda k: mode_labels[k],
        horizontal=True,
        key="upload_mode",
        label_visibility="collapsed",
    )

    if mode == "rss":
        _render_rss_panel()
    else:
        _render_manual_upload_panel()

    st.divider()
    with st.container(border=True):
        st.markdown("##### 카드뉴스 페이지 수")
        target_pages = st.slider(
            "총 페이지 수 (슬라이드)",
            min_value=1,
            max_value=12,
            value=int(st.session_state.get("target_pages", 5)),
        )
        st.session_state.target_pages = int(target_pages)

        pg_group = page_group(target_pages)
        if st.session_state.get("target_pages_group") != pg_group:
            st.session_state.target_pages_group = pg_group
            st.session_state.concept_template_id = None
            st.session_state.concept_confirmed = False
            st.session_state.concept_thumb_paths = {}

        if target_pages == 1:
            st.caption(
                "1장 제작 — template2 표지·본문과 동일 디자인 시스템 "
                "(`themes/template1/템플릿-1.txt` + `template2/템플릿1`)"
            )
            st.session_state.multipage_variant = DEFAULT_MULTIPAGE_VARIANT
        else:
            v_labels = multipage_variant_labels()
            v_ids = list(v_labels.keys())
            cur = st.session_state.get("multipage_variant", DEFAULT_MULTIPAGE_VARIANT)
            chosen = st.radio(
                "디자인 템플릿 (2장 이상)",
                v_ids,
                index=v_ids.index(cur) if cur in v_ids else 0,
                format_func=lambda v: v_labels.get(v, v),
                horizontal=True,
                key="multipage_variant_radio",
            )
            st.session_state.multipage_variant = chosen
            st.caption(
                "선택한 템플릿이 기획안·카드 생성에 모두 적용됩니다. "
                "변경 시 기획·카드를 다시 생성하세요."
            )


def step_plan(client: OpenAI) -> None:
    section_header(2, "내용 분석 · 기획안")

    target_pages = st.session_state.get("target_pages", 5)
    concept_id = (
        st.session_state.concept_template_id
        if st.session_state.concept_confirmed
        else None
    )
    plan_block = concept_plan_block(concept_id)

    can_generate = bool(st.session_state.press_text and st.session_state.press_text.strip())
    if st.button(
        "AI 분석 시작",
        type="primary",
        disabled=not can_generate,
        key="plan_gen_btn",
    ):
        try:
            with st.spinner("LLM 기획 작성 → 안전 필터 검사…"):
                plan = generate_plan(
                    client,
                    st.session_state.press_text,
                    target_pages=target_pages,
                    concept_style_block=plan_block,
                    template_variant=st.session_state.get(
                        "multipage_variant", DEFAULT_MULTIPAGE_VARIANT
                    ),
                )
                st.session_state.plan_dict = plan.to_json_dict()
                st.session_state.plan_phase = "draft"
                st.session_state.plan_first_approved = False
                st.session_state.plan_revisions_remaining = 0
                st.session_state.plan_bump = st.session_state.get("plan_bump", 0) + 1
                st.session_state._seed_bump = -1
                plan_to_text()
            st.success("기획안 생성 완료 (안전 필터 통과)")
            persist()
            st.rerun()
        except ContentFilterError as exc:
            st.error(str(exc))
            st.stop()

    plan_locked = st.session_state.plan_phase == "locked"
    plan_readonly = plan_locked or (
        st.session_state.plan_phase == "post_first"
        and st.session_state.plan_revisions_remaining <= 0
    )

    if st.session_state.plan_dict:
        bump = st.session_state.get("plan_bump", 0)
        if st.session_state.get("_seed_bump", -1) != bump:
            seed_page_widgets_from_plan()
            st.session_state._seed_bump = bump

        st.markdown("##### 페이지별 내용")
        with st.container(border=True):
            st.caption("공통 문안")
            st.text_input("시리즈명", key="pg_series", disabled=plan_readonly)
            st.text_input("헤드카피", key="pg_head", disabled=plan_readonly)

        slides = st.session_state.plan_dict.get("slides") or []
        role_ko = {"cover": "표지", "body": "본문", "closing": "맺음"}
        for i, slide in enumerate(slides):
            r = slide.get("role", "body")
            with st.container(border=True):
                st.markdown(f"**페이지 {i + 1}** · {role_ko.get(str(r), r)}")
                st.text_input(
                    "슬라이드 제목", key=f"pg_{i}_title", disabled=plan_readonly
                )
                st.text_area(
                    "불릿 (줄마다 하나)",
                    key=f"pg_{i}_bullets",
                    height=120,
                    disabled=plan_readonly,
                )
                st.text_input(
                    "각주 (선택)", key=f"pg_{i}_footnote", disabled=plan_readonly
                )

        ga, gb, gc = st.columns(3)
        with ga:
            if st.button(
                "페이지 편집 반영",
                disabled=plan_readonly,
                use_container_width=True,
            ):
                widgets_into_plan_dict()
                if not guard_plan(st.session_state.plan_dict, context="기획 반영"):
                    st.stop()
                persist()
                st.success("기획안이 업데이트되었습니다.")
        with gb:
            if st.button(
                "1차 승인",
                disabled=(
                    st.session_state.plan_dict is None
                    or st.session_state.plan_phase != "draft"
                ),
                use_container_width=True,
            ):
                widgets_into_plan_dict()
                if not guard_plan(st.session_state.plan_dict, context="기획 1차 승인"):
                    st.stop()
                st.session_state.plan_phase = "post_first"
                st.session_state.plan_revisions_remaining = 2
                st.session_state.plan_first_approved = True
                persist()
                st.rerun()
        with gc:
            if st.button(
                "기획 확정 →",
                type="primary",
                disabled=(
                    st.session_state.plan_dict is None
                    or st.session_state.plan_phase == "draft"
                    or plan_locked
                ),
                use_container_width=True,
            ):
                widgets_into_plan_dict()
                if not guard_plan(st.session_state.plan_dict, context="기획 확정"):
                    st.stop()
                st.session_state.plan_phase = "locked"
                persist()
                st.rerun()

        if st.session_state.plan_phase == "post_first":
            if st.button(
                f"수정 반영 (남은 횟수 {st.session_state.plan_revisions_remaining})",
                disabled=st.session_state.plan_revisions_remaining <= 0 or plan_locked,
                key="plan_revise_btn",
            ):
                widgets_into_plan_dict()
                st.session_state.plan_revisions_remaining -= 1
                persist()
                st.rerun()

        with st.expander("고급: JSON 보기"):
            st.code(st.session_state.get("plan_text", "") or "", language="json")


def step_design(client: OpenAI) -> None:
    section_header(3, "디자인 설정")

    target_pages = st.session_state.get("target_pages", 5)
    concepts = concepts_for_pages(target_pages)

    # Auto-confirm a recommended concept silently — no UI shown
    recommended_id = recommend_concept_id(
        st.session_state.press_text or "", target_pages
    )
    valid_ids = {c.id for c in concepts}
    if st.session_state.get("concept_template_id") not in valid_ids:
        st.session_state.concept_template_id = recommended_id
    st.session_state.concept_confirmed = True

    def _group_head(icon: str, title: str) -> None:
        st.markdown(
            f'<div class="rp-group-head">'
            f'<span class="material-symbols-outlined">{icon}</span>'
            f'<span>{title}</span></div>',
            unsafe_allow_html=True,
        )

    # Row 1: 템플릿 & 컬러 톤 | 로고 & 캐릭터
    r1c1, r1c2 = st.columns(2, gap="medium")
    with r1c1:
        st.markdown('<div class="rp-design-row-marker"></div>', unsafe_allow_html=True)
        with st.container(border=True):
            _group_head("palette", "템플릿 & 컬러 톤")
            themes = list_theme_ids()
            st.selectbox(
                "템플릿 (YAML)", themes, index=0, key="dc_theme",
                label_visibility="visible",
            )
            tone_options = ["neutral", "green", "blue", "purple", "orange"]
            tone_labels = {
                "neutral": "기본", "green": "녹색", "blue": "파랑",
                "purple": "보라", "orange": "주황",
            }
            tone_swatches = {
                "neutral": "#94a3b8", "green": "#16a34a", "blue": "#2563eb",
                "purple": "#7c3aed", "orange": "#ea580c",
            }
            current_tone = st.session_state.get("dc_tone", "blue")
            st.markdown(
                '<div class="rp-tone-label">섹션 컬러 톤</div>',
                unsafe_allow_html=True,
            )
            swatch_cols = st.columns(len(tone_options))
            for i, tn in enumerate(tone_options):
                with swatch_cols[i]:
                    is_sel = tn == current_tone
                    clicked = st.button(
                        "✓" if is_sel else " ",
                        key=f"tone_btn_{tn}",
                        use_container_width=True,
                        type="primary" if is_sel else "secondary",
                    )
                    if clicked:
                        st.session_state.dc_tone = tn
                        st.rerun()
                    sel = " sel" if is_sel else ""
                    st.markdown(
                        f'<div class="rp-tone-name{sel}">{tone_labels[tn]}</div>',
                        unsafe_allow_html=True,
                    )

    with r1c2:
        with st.container(border=True):
            _group_head("location_on", "로고 & 캐릭터")
            st.radio(
                "로고 위치",
                ["top_right", "top_left", "bottom_center"],
                horizontal=True,
                key="dc_logo",
                label_visibility="collapsed",
            )
            char_file = st.file_uploader(
                "캐릭터 PNG (선택)", type=["png"], key="dc_char"
            )
            if char_file:
                st.session_state.character_bytes = char_file.getvalue()

    # Row 2: 텍스트 색상 | 본문 레이아웃
    r2c1, r2c2 = st.columns(2, gap="medium")
    with r2c1:
        st.markdown('<div class="rp-design-row-marker"></div>', unsafe_allow_html=True)
        with st.container(border=True):
            _group_head("format_color_text", "텍스트 색상")
            cc1, cc2 = st.columns(2)
            with cc1:
                st.color_picker("제목 색", value="#111111", key="dc_tcol")
                st.checkbox("제목 색 반영", value=False, key="dc_ut")
            with cc2:
                st.color_picker("본문 색", value="#333333", key="dc_bcol")
                st.checkbox("본문 색 반영", value=False, key="dc_ub")

    with r2c2:
        with st.container(border=True):
            _group_head("dashboard", "본문 레이아웃")
            st.radio(
                "레이아웃",
                ["여백형 (패턴 A)", "흰 카드형 (패턴 B)"],
                horizontal=True,
                key="dc_layout",
                label_visibility="collapsed",
            )
            st.markdown(
                '<div class="rp-tone-label" style="margin-top:0.75rem">폰트 크기 배율</div>',
                unsafe_allow_html=True,
            )
            st.slider(
                "배율", 0.75, 1.25, 1.0, 0.05,
                key="dc_fscale", label_visibility="collapsed",
            )

    # Row 3 — model
    with st.container(border=True):
        _group_head("auto_awesome", "이미지 생성 모델")
        st.selectbox(
            "모델",
            image_models_for_selectbox(),
            index=0,
            key="dc_imgmodel",
            label_visibility="collapsed",
        )


def _build_design_args() -> dict:
    body_layout_label = st.session_state.get("dc_layout", "여백형 (패턴 A)")
    return {
        "theme_id": st.session_state.get("dc_theme", "mofe_body"),
        "section_tone": st.session_state.get("dc_tone", "blue"),
        "body_layout": "simple" if "A" in body_layout_label else "white_card",
        "font_scale": st.session_state.get("dc_fscale", 1.0),
        "use_custom_title": st.session_state.get("dc_ut", False),
        "use_custom_body": st.session_state.get("dc_ub", False),
        "title_color": st.session_state.get("dc_tcol", "#111111"),
        "body_color": st.session_state.get("dc_bcol", "#333333"),
        "logo_pos": st.session_state.get("dc_logo", "top_right"),
    }


def _finalize_single_page_variant(
    selected_variant: str,
    variants_state: dict[str, str],
    out_root: Path,
) -> Path | None:
    """1페이지: 선택한 후보 이미지를 cards/ko/01_cover.jpg 로 복사."""
    src = Path(variants_state.get(selected_variant, ""))
    if not src.is_file():
        return None
    ko_dir = out_root / "cards" / "ko"
    ko_dir.mkdir(parents=True, exist_ok=True)
    dest = ko_dir / "01_cover.jpg"
    shutil.copy2(src, dest)
    return dest


def step_generate(client: OpenAI) -> None:
    section_header(4, "카드 이미지 생성 (JPEG)")

    img_model_label = st.session_state.get("dc_imgmodel") or image_models_for_selectbox()[0]
    img_model = resolve_model(img_model_label)

    da = _build_design_args()
    theme_desc = build_theme_description(**da)
    img_concept_block = concept_image_block(
        st.session_state.concept_template_id
        if st.session_state.concept_confirmed
        else None
    )

    target_pages = st.session_state.get("target_pages", 5)
    is_single_page = target_pages == 1
    if is_single_page:
        st.caption(
            "1장 제작 — 생성된 A/B/C가 곧 **최종 후보**입니다. "
            "마음에 드는 1장을 선택 후 확정하세요 (추가 API 생성 없음). "
            f"영문 세트는 결과 단계에서 공식 **영문 MI 로고**로 별도 생성합니다."
        )
    else:
        st.caption(
            "동일 템플릿·디자인으로 **한국어 세트** 생성 후 번역해 **영어 세트** 생성. "
            "한국어는 `국문-좌우조합.png`, 영어는 `영문-좌우조합.png` 공식 MI 로고가 합성됩니다."
        )
    with st.expander("정서·이미지 안전 필터 안내"):
        st.markdown(
            "- 기획·프롬프트·생성 단계에서 일본·식민지 잔재, 북한·분단, 정치 이념·갈등, 혐오 표현 차단\n"
            "- 통과하지 않은 기획은 API 호출 없이 중단"
        )

    out_root = (
        Path(tempfile.gettempdir())
        / "cardnews_exports"
        / st.session_state.session_id
    )
    out_root.mkdir(parents=True, exist_ok=True)
    st.session_state["_out_root"] = str(out_root)

    can_generate = (
        st.session_state.plan_phase == "locked"
        and st.session_state.plan_dict is not None
    )
    if not can_generate:
        st.warning("이전 단계에서 **기획 확정**을 먼저 완료하세요.")
        return

    mv = st.session_state.get("multipage_variant", DEFAULT_MULTIPAGE_VARIANT)
    title_color = da["title_color"] if da["use_custom_title"] else None
    body_color = da["body_color"] if da["use_custom_body"] else None

    if is_single_page:
        st.markdown(
            "**① 결과물 후보 3종 생성** — 레이아웃·폰트 정렬·3D 소스를 달리한 1장 카드 후보"
        )
        btn_variants_label = "결과물 후보 3종 생성"
        btn_variants_help = "1장 카드뉴스 후보 3종(A/B/C) 생성"
        success_variants_msg = "결과물 후보 3종 생성 완료 — 아래에서 최종 1장을 선택·확정하세요."
    else:
        st.markdown(
            "**① 표지 디자인 시안 3종 생성** — 레이아웃·폰트 정렬·3D 소스를 달리한 표지 시안"
        )
        btn_variants_label = "표지 시안 3종 생성"
        btn_variants_help = "기획안 1페이지(표지) 기준으로 디자인 시안 3종 생성"
        success_variants_msg = "표지 시안 3종 생성 완료 — 아래에서 선택하세요."

    if st.button(
        btn_variants_label,
        key="btn_variants",
        help=btn_variants_help,
    ):
        widgets_into_plan_dict()
        plan_d = st.session_state.plan_dict
        if not plan_d or not guard_plan(plan_d, context="표지 시안 생성"):
            st.stop()
        cover_full, body_full, single_page = _resolve_design_templates(
            target_pages, mv
        )
        if not cover_full.strip():
            st.error("디자인 템플릿 파일을 찾을 수 없습니다.")
            st.stop()
        var_dir = out_root / "cover_variants"
        var_dir.mkdir(parents=True, exist_ok=True)
        prog = st.progress(0)
        try:
            vids = design_variant_ids()
            done = [0]

            def _pv(_c: int, _t: int) -> None:
                done[0] += 1
                prog.progress(min(1.0, done[0] / max(1, len(vids))))

            spinner_label = (
                f"결과물 후보 {len(vids)}종 생성 중… ({img_model})"
                if is_single_page
                else f"표지 시안 {len(vids)}종 생성 중… ({img_model})"
            )
            with st.spinner(spinner_label):
                variants = generate_cover_variant_jpegs(
                    client, img_model, plan_d,
                    theme_desc=theme_desc,
                    out_dir=var_dir,
                    cover_template_full=cover_full,
                    variant_ids=vids,
                    copy_locale="ko",
                    section_tone=da["section_tone"],
                    theme_id=da["theme_id"],
                    logo_pos=da["logo_pos"],
                    title_color=title_color,
                    body_color=body_color,
                    single_page_template=single_page,
                    progress_callback=_pv,
                )
            st.session_state.cover_variants = {k: str(v) for k, v in variants.items()}
            st.session_state.card_paths = []
            st.session_state.card_paths_en = []
            st.session_state.card_review_approved = False
            st.session_state.card_revisions_remaining = 0
            persist()
            st.success(success_variants_msg)
        except ContentFilterError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"시안 생성 실패: {type(exc).__name__}: {exc}")
        finally:
            prog.empty()
        st.rerun()

    # ② 후보/시안 선택
    variants_state = st.session_state.get("cover_variants") or {}
    selected_variant: str | None = None
    if variants_state:
        if is_single_page:
            st.markdown("**② 최종 결과물 선택**")
            radio_label = "완성본으로 사용할 후보"
        else:
            st.markdown("**② 시안 선택**")
            radio_label = "완성본에 사용할 시안"
        vids_existing = [v for v in design_variant_ids() if v in variants_state]
        vcols = st.columns(len(vids_existing))
        for vi, vid in enumerate(vids_existing):
            with vcols[vi]:
                p = Path(variants_state[vid])
                if p.exists():
                    st.image(
                        str(p),
                        use_container_width=True,
                        caption=design_variant_label(vid),
                    )
        cur_sel = st.session_state.get("selected_variant")
        if cur_sel not in vids_existing and vids_existing:
            cur_sel = vids_existing[0]
        selected_variant = st.radio(
            radio_label,
            vids_existing,
            index=vids_existing.index(cur_sel) if cur_sel in vids_existing else 0,
            format_func=design_variant_label,
            horizontal=True,
            key="selected_variant_radio",
        )
        st.session_state.selected_variant = selected_variant
        st.caption(
            f"선택: **{design_variant_label(selected_variant)}** — "
            + (
                "선택한 후보가 한국어 완성본이 됩니다 (추가 생성 없음)."
                if is_single_page
                else "표지·본문 전체 제작에 적용"
            )
        )
    else:
        if is_single_page:
            st.caption(
                "먼저 **결과물 후보 3종**을 생성하고 하나를 선택·확정하면 다음 단계로 진행할 수 있습니다."
            )
        else:
            st.caption(
                "먼저 **표지 시안 3종**을 생성하고 하나를 선택하면 한국어 카드 생성이 활성화됩니다."
            )

    if is_single_page:
        st.markdown("**③ 선택한 후보로 한국어 완성본 확정**")
        if st.button(
            "이 결과물로 확정 (한국어 1장)",
            type="primary",
            disabled=not variants_state or not selected_variant,
            key="confirm_single_page_btn",
        ):
            dest = _finalize_single_page_variant(
                selected_variant, variants_state, out_root
            )
            if dest is None:
                st.error("선택한 후보 파일을 찾을 수 없습니다. 후보를 다시 생성하세요.")
                st.stop()
            st.session_state.card_paths = [str(dest)]
            st.session_state.card_paths_en = []
            st.session_state.card_review_approved = False
            st.session_state.card_revisions_remaining = 0
            persist()
            st.success(
                "한국어 1장 확정 완료 — **다음 →** 로 결과 확인."
            )
            st.rerun()
    else:
        st.markdown("**③ 선택한 시안으로 한국어 카드 생성**")
        if st.button(
            "한국어 카드 생성",
            type="primary",
            disabled=not variants_state or not selected_variant,
            key="gen_ko_btn",
        ):
            widgets_into_plan_dict()
            plan_d = st.session_state.plan_dict
            if not plan_d or not guard_plan(plan_d, context="카드 생성"):
                st.stop()
            cover_full, body_full, single_page = _resolve_design_templates(
                target_pages, mv
            )
            if not cover_full.strip() or (not single_page and not body_full.strip()):
                st.error("디자인 템플릿 파일을 찾을 수 없습니다.")
                st.stop()
            prog = st.progress(0)
            try:
                nslides = len(plan_d.get("slides") or [])
                total_steps = max(1, nslides)
                done = [0]

                def _pk(_c: int, _t: int) -> None:
                    done[0] += 1
                    prog.progress(min(1.0, done[0] / total_steps))

                ko_dir = out_root / "cards" / "ko"
                ko_dir.mkdir(parents=True, exist_ok=True)
                variant_block = design_variant_block(selected_variant)
                with st.spinner(f"한국어 카드 생성 중… ({img_model}, 페이지당 1~2분)"):
                    paths_ko = generate_plan_card_jpegs(
                        client, img_model, plan_d,
                        theme_desc=theme_desc,
                        out_dir=ko_dir,
                        cover_template_full=cover_full,
                        body_template_full=body_full,
                        progress_callback=_pk if nslides else None,
                        copy_locale="ko",
                        section_tone=da["section_tone"],
                        theme_id=da["theme_id"],
                        logo_pos=da["logo_pos"],
                        concept_style_block=variant_block,
                        title_color=title_color,
                        body_color=body_color,
                        single_page_template=single_page,
                    )
                st.session_state.card_paths = [str(p) for p in paths_ko]
                st.session_state.card_paths_en = []
                st.session_state.card_review_approved = False
                st.session_state.card_revisions_remaining = 0
                persist()
                st.success(
                    f"한국어 {len(paths_ko)}장 생성 완료 — **다음 →** 으로 결과 확인."
                )
            except ContentFilterError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"이미지 생성 실패: {type(exc).__name__}: {exc}")
            finally:
                prog.empty()
            st.rerun()

    if st.session_state.card_paths:
        st.success(
            f"한국어 {len(st.session_state.card_paths)}장 생성 완료. **다음 →** 으로 이동."
        )


# ---------- Step 6: SNS upload (Instagram via Buffer + Supabase) ----------

def _do_instagram_upload(paths_ko: list[Path]) -> None:
    caption = st.session_state.get("instagram_caption", "").strip()
    session_id = st.session_state.session_id
    assets: list[UploadedAsset] = []
    try:
        with st.spinner("이미지 업로드 중..."):
            assets = upload_card_images(paths_ko, session_id)
        with st.spinner("인스타그램 발행 요청 중..."):
            post_id = create_instagram_post(
                BUFFER_CHANNEL_ID,
                [a.url for a in assets],
                caption,
            )
        st.session_state.instagram_uploaded_post_id = post_id
        st.session_state.instagram_uploaded_keys = [a.key for a in assets]
        persist()
        st.success(f"발행 완료. post_id={post_id}")
        st.info(
            "정상 노출 확인 후 아래 '임시 파일 정리' 로 원격 파일을 삭제하세요."
        )
    except SupabaseStorageError as exc:
        st.error(f"Supabase 업로드 실패: {exc}")
        if assets:
            try:
                delete_objects([a.key for a in assets])
            except Exception:
                pass
    except BufferError as exc:
        st.error(f"Buffer 발행 실패: {exc}")
        if assets:
            try:
                delete_objects([a.key for a in assets])
            except Exception:
                pass
    except Exception as exc:
        st.error(f"업로드 실패: {exc}")


def step_sns(client: OpenAI) -> None:
    section_header(6, "SNS 업로드")

    if not _SNS_READY:
        st.warning(
            "SNS 업로드 환경변수 미설정. `.env` 에 `SUPABASE_URL`, "
            "`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_BUCKET`, "
            "`BUFFER_API_KEY`, `BUFFER_CHANNEL_ID` 를 설정하세요."
        )
        return

    paths_ko = [Path(p) for p in st.session_state.card_paths if Path(p).exists()]
    if not paths_ko:
        st.info("렌더된 한국어 카드가 없습니다. 이전 단계에서 카드 생성을 완료하세요.")
        return
    if len(paths_ko) > 10:
        st.error(
            f"카드 {len(paths_ko)}장 — Instagram 캐러셀 최대 10장. 페이지 수를 줄여 다시 렌더."
        )
        return

    already_posted = bool(st.session_state.get("instagram_uploaded_post_id"))
    if already_posted:
        st.success(
            f"이미 발행 완료. post_id={st.session_state.instagram_uploaded_post_id}"
        )

    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("캡션 초안 생성", disabled=already_posted, key="btn_caption_draft"):
            try:
                with st.spinner("캡션 생성 중..."):
                    caption = generate_instagram_caption(
                        client,
                        st.session_state.get("press_text", ""),
                        st.session_state.get("plan_dict"),
                    )
                st.session_state.instagram_caption = caption
                persist()
            except ContentFilterError as exc:
                st.error(f"캡션 안전 필터 차단:\n\n{exc}")
            except Exception as exc:
                st.error(f"캡션 생성 실패: {exc}")
    with col_b:
        st.caption(
            "PDF 본문 + 기획안 기반으로 OpenAI 가 한국어 캡션 초안 작성. "
            "프롬프트는 `code/caption_llm.py` 에서 조정."
        )

    st.text_area(
        "Instagram 캡션",
        key="instagram_caption",
        height=240,
        max_chars=MAX_CAPTION_LEN,
        disabled=already_posted,
    )
    caption_len = len(st.session_state.get("instagram_caption", ""))
    st.caption(f"{caption_len} / {MAX_CAPTION_LEN}자")

    upload_disabled = (
        already_posted or caption_len == 0 or caption_len > MAX_CAPTION_LEN
    )
    if st.button(
        "인스타그램 업로드",
        disabled=upload_disabled,
        key="btn_instagram_upload",
        type="primary",
    ):
        _do_instagram_upload(paths_ko)

    pending_keys = st.session_state.get("instagram_uploaded_keys") or []
    has_post = bool(st.session_state.get("instagram_uploaded_post_id"))
    if has_post and pending_keys and not st.session_state.get("instagram_cleanup_done"):
        st.divider()
        st.caption(f"Supabase 잔존 파일 {len(pending_keys)}개")
        if st.button("Supabase 임시 파일 정리", key="btn_supabase_cleanup"):
            try:
                delete_objects(pending_keys)
                st.session_state.instagram_cleanup_done = True
                st.session_state.instagram_uploaded_keys = []
                persist()
                st.success("Supabase 임시 파일 삭제 완료.")
            except Exception as ce:
                st.error(f"삭제 실패: {ce}")


# ---------- Step transition footer ----------

def step_footer() -> None:
    step = st.session_state.editor_step
    st.divider()
    c_prev, c_spacer, c_next = st.columns([1, 5, 1])
    with c_prev:
        if step > 1:
            if st.button("← 이전", use_container_width=True, key=f"prev_{step}"):
                st.session_state.editor_step = step - 1
                persist()
                st.rerun()
    with c_next:
        if step < 6:
            disabled = _next_disabled(step)
            if st.button(
                "다음 →",
                type="primary",
                disabled=disabled,
                use_container_width=True,
                key=f"next_{step}",
            ):
                st.session_state.editor_step = step + 1
                persist()
                st.rerun()


def _next_disabled(step: int) -> bool:
    if step == 1:
        return not bool(st.session_state.press_text and st.session_state.press_text.strip())
    if step == 2:
        return st.session_state.plan_phase != "locked"
    if step == 3:
        return not bool(st.session_state.concept_confirmed)
    if step == 4:
        return not bool(st.session_state.card_paths)
    if step == 5:
        return not bool(st.session_state.card_paths)
    return True


# ---------- Page entry ----------

def render() -> None:
    inject_global_css()
    init_session()
    try_hydrate()
    nav = st.query_params.get("nav")
    if nav == "home":
        st.query_params.clear()
        st.switch_page("views/landing.py")
    elif nav == "editor":
        st.query_params.clear()
    top_app_bar(active="editor")

    if not OPENAI_API_KEY:
        st.error("`.env` 에 `OPENAI_API_KEY` 를 설정하세요. (저장소 루트 `Releasepick/.env`)")
        st.stop()
    client = OpenAI(api_key=OPENAI_API_KEY)

    nav_col, work_col = st.columns([1.1, 4], gap="medium")
    with nav_col:
        completed = {n for n in range(1, st.session_state.editor_step)}
        step_nav(current=st.session_state.editor_step, completed=completed)
        if st.button(
            "세션 초기화",
            use_container_width=True,
            key="reset_session_btn",
        ):
            reset_session()
            st.rerun()

    with work_col:
        step = st.session_state.editor_step
        if step == 1:
            step_upload(client)
        elif step == 2:
            step_plan(client)
        elif step == 3:
            step_design(client)
        elif step == 4:
            step_generate(client)
        elif step == 5:
            result_view.render(client)
        elif step == 6:
            step_sns(client)
        step_footer()

    footer()


render()
