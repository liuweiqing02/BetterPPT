from __future__ import annotations

import importlib
import hashlib
import json
import logging
import math
import os
import re
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
import xml.etree.ElementTree as ET

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import DETAIL_LEVEL_PAGE_RANGE
from app.models.file import File
from app.models.template_page_schema import TemplatePageSchema
from app.models.template_profile import TemplateProfile

logger = logging.getLogger(__name__)

_DEFAULT_EMBEDDING_MODEL = 'vit-base'
_DEFAULT_LLM_MODEL = 'gpt-4.1-mini'
_DEFAULT_PROFILE_VERSION = 'v1'

_THEME_FALLBACKS = [
    {'theme_name': 'exec_blue', 'primary_color': '#1B6EF3', 'accent_color': '#4F8CFF', 'background_color': '#F7FAFF', 'text_color': '#1E2B39'},
    {'theme_name': 'graphite', 'primary_color': '#223449', 'accent_color': '#5B6C7F', 'background_color': '#F6F8FB', 'text_color': '#1D2630'},
    {'theme_name': 'teal_grid', 'primary_color': '#158B7A', 'accent_color': '#5AC8B3', 'background_color': '#F4FBF9', 'text_color': '#19322D'},
    {'theme_name': 'sunrise', 'primary_color': '#D97706', 'accent_color': '#F59E0B', 'background_color': '#FFF9F2', 'text_color': '#312014'},
]

_BASE_LAYOUT_PRESETS: dict[str, list[dict[str, Any]]] = {
    'cover': [
        {'name': 'title', 'kind': 'text', 'position': 'center_top', 'weight': 0.44},
        {'name': 'subtitle', 'kind': 'text', 'position': 'center_middle', 'weight': 0.22},
        {'name': 'hero_visual', 'kind': 'visual', 'position': 'right_panel', 'weight': 0.34},
    ],
    'toc': [
        {'name': 'title', 'kind': 'text', 'position': 'top', 'weight': 0.24},
        {'name': 'agenda_list', 'kind': 'list', 'position': 'main', 'weight': 0.56},
        {'name': 'accent_band', 'kind': 'shape', 'position': 'bottom', 'weight': 0.20},
    ],
    'section': [
        {'name': 'section_kicker', 'kind': 'text', 'position': 'top_left', 'weight': 0.20},
        {'name': 'section_title', 'kind': 'text', 'position': 'center_left', 'weight': 0.48},
        {'name': 'divider', 'kind': 'shape', 'position': 'bottom_right', 'weight': 0.32},
    ],
    'comparison': [
        {'name': 'title', 'kind': 'text', 'position': 'top', 'weight': 0.18},
        {'name': 'left_panel', 'kind': 'card', 'position': 'left', 'weight': 0.36},
        {'name': 'right_panel', 'kind': 'card', 'position': 'right', 'weight': 0.36},
        {'name': 'summary_footer', 'kind': 'text', 'position': 'bottom', 'weight': 0.10},
    ],
    'summary': [
        {'name': 'title', 'kind': 'text', 'position': 'top', 'weight': 0.20},
        {'name': 'key_takeaways', 'kind': 'list', 'position': 'main', 'weight': 0.50},
        {'name': 'action_items', 'kind': 'callout', 'position': 'bottom', 'weight': 0.30},
    ],
    'ending': [
        {'name': 'closing_message', 'kind': 'text', 'position': 'center', 'weight': 0.34},
        {'name': 'contact_info', 'kind': 'text', 'position': 'bottom_left', 'weight': 0.24},
        {'name': 'thanks_stamp', 'kind': 'shape', 'position': 'right', 'weight': 0.42},
    ],
    'content': [
        {'name': 'title', 'kind': 'text', 'position': 'top', 'weight': 0.18},
        {'name': 'body', 'kind': 'text', 'position': 'main_left', 'weight': 0.46},
        {'name': 'visual', 'kind': 'visual', 'position': 'main_right', 'weight': 0.24},
        {'name': 'footer_note', 'kind': 'text', 'position': 'bottom', 'weight': 0.12},
    ],
}

_PAGE_FUNCTION_PRIORITY = ['cover', 'toc', 'section', 'comparison', 'ending', 'summary', 'content']
_LLM_PAGE_BATCH_SIZE = 12
_DEFAULT_VISION_MODEL_NAME = 'google/vit-base-patch16-224-in21k'
_DEFAULT_VISION_LOCAL_DIR = (
    Path(__file__).resolve().parents[2] / 'models' / 'vision' / 'vit-base-patch16-224-in21k'
).resolve()
_EMBEDDING_MODEL_DB_MAX_LEN = 64

_COVER_KEYWORDS = {'cover', 'title', 'intro', 'welcome', '封面', '首页', '论文答辩', '模板', 'thesis', 'defense'}
_TOC_KEYWORDS = {'agenda', 'outline', 'contents', '目录', '议程', '结构', 'overview', 'chapter'}
_SECTION_KEYWORDS = {'section', 'part', 'chapter', '章节', '部分', '模块', '阶段', 'part one', 'part two'}
_COMPARISON_KEYWORDS = {'comparison', 'compare', 'vs', 'versus', '对比', '比较', '差异', 'contrast'}
_SUMMARY_KEYWORDS = {'summary', 'conclusion', 'conclusions', 'takeaway', 'takeaways', '总结', '结论', '参考文献', 'references', 'bibliography'}
_ENDING_KEYWORDS = {'thanks', 'thank you', 'thank', 'q&a', 'qa', 'questions', 'contact', '再见', '谢谢', '感谢', '指导', 'guidance', '结束'}

_NS = {
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'pr': 'http://schemas.openxmlformats.org/package/2006/relationships',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


def _normalize_detail_level(detail_level: str | None) -> str:
    normalized = (detail_level or 'balanced').strip().lower()
    return normalized if normalized in DETAIL_LEVEL_PAGE_RANGE else 'balanced'


def _resolve_vision_model_ref() -> str:
    settings = get_settings()
    local_model_env = _coerce_text(os.getenv('BETTERPPT_TEMPLATE_VISION_MODEL_PATH') or settings.template_vision_model_path)
    if local_model_env:
        local_path = Path(local_model_env).expanduser()
        if local_path.exists():
            return str(local_path.resolve())

    model_env = _coerce_text(os.getenv('BETTERPPT_TEMPLATE_VISION_MODEL') or settings.template_vision_model or _DEFAULT_VISION_MODEL_NAME)
    if model_env:
        candidate = Path(model_env).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        return model_env

    if _DEFAULT_VISION_LOCAL_DIR.exists():
        return str(_DEFAULT_VISION_LOCAL_DIR)
    return _DEFAULT_VISION_MODEL_NAME


def _resolve_vision_cache_dir() -> str | None:
    settings = get_settings()
    cache_env = _coerce_text(os.getenv('BETTERPPT_TEMPLATE_VISION_CACHE_DIR') or settings.template_vision_cache_dir)
    if not cache_env:
        return None
    return str(Path(cache_env).expanduser().resolve())


def _embedding_model_db_value(model_ref: str, embedding_mode: str) -> str:
    ref = _coerce_text(model_ref)
    if not ref:
        return _DEFAULT_EMBEDDING_MODEL
    if embedding_mode != 'vision_model':
        return _DEFAULT_EMBEDDING_MODEL

    if ('\\' in ref or '/' in ref) and Path(ref).exists():
        token = Path(ref).name
        token = _coerce_text(token) or 'vision-local'
        value = f'local:{token}'
    else:
        value = ref

    if len(value) <= _EMBEDDING_MODEL_DB_MAX_LEN:
        return value
    return value[: _EMBEDDING_MODEL_DB_MAX_LEN]


def _stable_hash(*parts: object) -> int:
    payload = '|'.join('' if part is None else str(part) for part in parts)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return int(digest[:16], 16)


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _coerce_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _extract_json_object(content: str) -> dict[str, Any] | None:
    raw_text = _coerce_text(content)
    if not raw_text:
        return None

    candidates = [raw_text]
    fenced = re.search(r'```(?:json)?\s*(.*?)\s*```', content, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue

        if candidate[0] != '{':
            start = candidate.find('{')
            end = candidate.rfind('}')
            if start != -1 and end != -1 and end > start:
                candidate = candidate[start:end + 1]

        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _compact_page_metrics(page: dict[str, Any], total_pages: int, detail_level: str) -> dict[str, Any]:
    metrics = page.get('metrics') or {}
    return {
        'page_no': int(page.get('page_no') or 0),
        'total_pages': total_pages,
        'detail_level': detail_level,
        'page_function': page.get('page_function'),
        'title_text': _coerce_text(metrics.get('title_text') or ''),
        'text_box_count': int(metrics.get('text_box_count') or 0),
        'text_char_count': int(metrics.get('text_char_count') or 0),
        'image_count': int(metrics.get('image_count') or 0),
        'shape_count': int(metrics.get('shape_count') or 0),
        'title_box_count': int(metrics.get('title_box_count') or 0),
        'subtitle_box_count': int(metrics.get('subtitle_box_count') or 0),
        'body_box_count': int(metrics.get('body_box_count') or 0),
        'footer_box_count': int(metrics.get('footer_box_count') or 0),
        'placeholder_type_counts': metrics.get('placeholder_type_counts') or {},
        'region_counts': metrics.get('region_counts') or {},
        'dominant_font_family': _coerce_text(metrics.get('dominant_font_family') or ''),
        'dominant_font_size': float(metrics.get('dominant_font_size') or 0) or None,
    }


def _normalize_llm_page_suggestion(raw_page: Any, fallback_page_no: int) -> dict[str, Any] | None:
    if not isinstance(raw_page, dict):
        return None

    try:
        page_no = int(raw_page.get('page_no') or raw_page.get('page') or fallback_page_no or 0)
    except Exception:
        page_no = fallback_page_no
    if page_no <= 0:
        page_no = fallback_page_no

    page_function = _coerce_text(raw_page.get('page_function') or '').lower()
    if page_function not in _PAGE_FUNCTION_PRIORITY:
        page_function = ''

    layout_suggestions: dict[str, Any] = {}
    if isinstance(raw_page.get('layout_suggestions'), dict):
        layout_suggestions.update(raw_page['layout_suggestions'])
    if isinstance(raw_page.get('layout'), dict):
        layout_suggestions.update(raw_page['layout'])
    for key in ('density_hint', 'title_style', 'columns', 'text_alignment', 'max_bullets'):
        if key in raw_page and raw_page.get(key) is not None:
            layout_suggestions.setdefault(key, raw_page.get(key))

    style_suggestions: dict[str, Any] = {}
    if isinstance(raw_page.get('style_suggestions'), dict):
        style_suggestions.update(raw_page['style_suggestions'])
    if isinstance(raw_page.get('style'), dict):
        style_suggestions.update(raw_page['style'])
    for key in ('accent_strategy', 'primary_color', 'accent_color', 'background_color', 'text_color'):
        if key in raw_page and raw_page.get(key) is not None:
            style_suggestions.setdefault(key, raw_page.get(key))

    result: dict[str, Any] = {
        'page_no': page_no,
        'layout_suggestions': layout_suggestions,
        'style_suggestions': style_suggestions,
    }
    if page_function:
        result['page_function'] = page_function

    reason = _coerce_text(raw_page.get('reason') or raw_page.get('page_function_reason') or raw_page.get('analysis') or '')
    if reason:
        result['reason'] = reason

    confidence = raw_page.get('confidence')
    if isinstance(confidence, (int, float)):
        result['confidence'] = float(confidence)

    return result


def _build_llm_page_input(page: dict[str, Any], total_pages: int, detail_level: str) -> dict[str, Any]:
    compact_metrics = _compact_page_metrics(page, total_pages, detail_level)
    return {
        'page_no': compact_metrics['page_no'],
        'page_function': compact_metrics['page_function'],
        'title_text': compact_metrics['title_text'],
        'text_box_count': compact_metrics['text_box_count'],
        'text_char_count': compact_metrics['text_char_count'],
        'image_count': compact_metrics['image_count'],
        'shape_count': compact_metrics['shape_count'],
        'title_box_count': compact_metrics['title_box_count'],
        'subtitle_box_count': compact_metrics['subtitle_box_count'],
        'body_box_count': compact_metrics['body_box_count'],
        'footer_box_count': compact_metrics['footer_box_count'],
        'placeholder_type_counts': compact_metrics['placeholder_type_counts'],
        'region_counts': compact_metrics['region_counts'],
        'dominant_font_family': compact_metrics['dominant_font_family'],
        'dominant_font_size': compact_metrics['dominant_font_size'],
    }


def _apply_llm_page_suggestion(
    page: dict[str, Any],
    suggestion: dict[str, Any],
    detail_level: str,
    total_pages: int,
    theme_info: dict[str, Any],
    reference_file: File,
) -> None:
    page_no = int(page.get('page_no') or suggestion.get('page_no') or 0)
    if page_no <= 0:
        return

    original_page_function = str(page.get('page_function') or 'content')
    suggested_page_function = _coerce_text(suggestion.get('page_function') or '')
    if suggested_page_function in _PAGE_FUNCTION_PRIORITY and suggested_page_function != original_page_function:
        page['page_function'] = suggested_page_function
        original_reason = _coerce_text(page.get('page_function_reason') or '')
        llm_reason = _coerce_text(suggestion.get('reason') or '')
        page['page_function_reason'] = '; '.join(
            [text for text in [original_reason, f'llm suggestion: {llm_reason}' if llm_reason else 'llm suggestion applied'] if text]
        )

    page['layout_schema_json'] = _build_layout_schema(
        str(page.get('page_function') or 'content'),
        detail_level,
        page_no,
        total_pages,
        page.get('metrics') or {},
        _coerce_text((page.get('metrics') or {}).get('title_text') or ''),
        _coerce_text(page.get('page_function_reason') or ''),
    )
    page['style_tokens_json'] = _build_style_tokens(
        theme_info,
        page.get('metrics') or {},
        detail_level,
        str(page.get('page_function') or 'content'),
        reference_file,
    )

    layout_overrides = suggestion.get('layout_suggestions') or {}
    if layout_overrides:
        layout_rules = page['layout_schema_json'].setdefault('layout_rules', {})
        for key in ('density_hint', 'title_style', 'columns', 'text_alignment', 'max_bullets'):
            if key in layout_overrides and layout_overrides.get(key) is not None:
                layout_rules[key] = layout_overrides.get(key)

    style_overrides = suggestion.get('style_suggestions') or {}
    if style_overrides:
        for key in ('accent_strategy', 'primary_color', 'accent_color', 'background_color', 'text_color'):
            if key in style_overrides and style_overrides.get(key) is not None:
                page['style_tokens_json'][key] = style_overrides.get(key)

    page['llm_enhancement_json'] = suggestion


def _enhance_pptx_pages_with_llm(
    pages: list[dict[str, Any]],
    detail_level: str,
    theme_info: dict[str, Any],
    reference_file: File,
    slide_size: dict[str, Any],
    task_no: str,
) -> dict[str, Any]:
    settings = get_settings()
    llm_model = settings.llm_model or _DEFAULT_LLM_MODEL
    llm_meta: dict[str, Any] = {
        'llm_enhanced': False,
        'llm_model': llm_model,
        'llm_error': None,
        'llm_usage': None,
        'llm_page_suggestions': [],
        'llm_batches_total': 0,
        'llm_batches_succeeded': 0,
    }

    api_key = (settings.llm_api_key or '').strip()
    if not api_key:
        return llm_meta

    try:
        from app.services.llm_service import call_chat_completions

        compact_pages = [_build_llm_page_input(page, len(pages), detail_level) for page in pages]
        theme_payload = {
            'theme_name': theme_info.get('theme_name'),
            'primary_color': (theme_info.get('colors') or {}).get('accent1'),
            'accent_color': (theme_info.get('colors') or {}).get('accent2'),
            'background_color': (theme_info.get('colors') or {}).get('bg1') or (theme_info.get('colors') or {}).get('lt1'),
            'text_color': (theme_info.get('colors') or {}).get('dk1') or (theme_info.get('colors') or {}).get('dk2'),
        }
        system_prompt = (
            'You analyze slide pages from compact metrics. '
            'Return strict JSON only, no markdown, no explanation text. '
            'Top-level object schema: {"pages":[...]}.\n'
            'Each page item schema: {"page_no":int, "page_function":str(optional), '
            '"layout_suggestions":object(optional), "style_suggestions":object(optional), "reason":str(optional)}.\n'
            'If correction is uncertain, keep the original page_function. '
            'Only suggest lightweight tweaks such as density_hint/title_style/accent_strategy.'
        )

        suggestion_by_page_no: dict[int, dict[str, Any]] = {}
        usage_agg: dict[str, Any] = {}
        batch_errors: list[str] = []

        for batch_index, batch_start in enumerate(range(0, len(compact_pages), _LLM_PAGE_BATCH_SIZE), start=1):
            batch_pages = compact_pages[batch_start : batch_start + _LLM_PAGE_BATCH_SIZE]
            if not batch_pages:
                continue
            llm_meta['llm_batches_total'] += 1

            prompt_payload = {
                'task_no': task_no,
                'detail_level': detail_level,
                'batch_index': batch_index,
                'batch_total': (len(compact_pages) + _LLM_PAGE_BATCH_SIZE - 1) // _LLM_PAGE_BATCH_SIZE,
                'slide_size': slide_size,
                'theme': theme_payload,
                'allowed_page_functions': _PAGE_FUNCTION_PRIORITY,
                'pages': batch_pages,
            }

            try:
                result = call_chat_completions(
                    model=llm_model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': _canonical_json(prompt_payload)},
                    ],
                    temperature=0.0,
                    max_tokens=1100,
                )
                parsed = _extract_json_object(result.content)
                if not parsed:
                    raise ValueError('llm returned non-json content')

                raw_pages = parsed.get('pages') if isinstance(parsed.get('pages'), list) else parsed.get('page_suggestions')
                if not isinstance(raw_pages, list):
                    raise ValueError('llm returned no page suggestions')

                for index, raw_page in enumerate(raw_pages, start=1):
                    fallback_page_no = int(batch_pages[min(index - 1, len(batch_pages) - 1)].get('page_no') or index)
                    suggestion = _normalize_llm_page_suggestion(raw_page, fallback_page_no)
                    if not suggestion:
                        continue
                    page_no = int(suggestion.get('page_no') or 0)
                    if page_no <= 0 or page_no > len(pages):
                        continue
                    suggestion_by_page_no[page_no] = suggestion

                usage = result.usage if isinstance(result.usage, dict) else {}
                for key, value in usage.items():
                    if isinstance(value, (int, float)):
                        usage_agg[key] = usage_agg.get(key, 0) + value
                    elif key not in usage_agg:
                        usage_agg[key] = value

                llm_meta['llm_batches_succeeded'] += 1
            except Exception as exc:
                batch_errors.append(f'batch_{batch_index}:{exc}')

        if not suggestion_by_page_no:
            if batch_errors:
                llm_meta['llm_error'] = '; '.join(batch_errors[:3])
            else:
                llm_meta['llm_error'] = 'llm returned empty page suggestions'
            return llm_meta

        for page in pages:
            page_no = int(page.get('page_no') or 0)
            suggestion = suggestion_by_page_no.get(page_no)
            if suggestion:
                _apply_llm_page_suggestion(page, suggestion, detail_level, len(pages), theme_info, reference_file)

        llm_meta.update(
            {
                'llm_enhanced': True,
                'llm_usage': usage_agg or None,
                'llm_page_suggestions': [suggestion_by_page_no[key] for key in sorted(suggestion_by_page_no.keys())],
            }
        )
        if batch_errors:
            llm_meta['llm_error'] = '; '.join(batch_errors[:3])
        return llm_meta
    except Exception as exc:
        logger.warning('template llm enhancement failed for %s: %s', reference_file.filename, exc)
        llm_meta['llm_error'] = str(exc)
        return llm_meta


def _resolve_reference_path(reference_file: File) -> Path:
    settings = get_settings()
    storage_path = Path(reference_file.storage_path)
    if storage_path.is_absolute():
        return storage_path
    return (settings.storage_root_path / storage_path).resolve()


def _xml_root(zf: zipfile.ZipFile, member_name: str) -> ET.Element | None:
    try:
        return ET.fromstring(zf.read(member_name))
    except Exception:
        return None


def _resolve_color_value(color_node: ET.Element | None) -> str | None:
    if color_node is None:
        return None

    srgb = color_node.find('a:srgbClr', _NS)
    if srgb is not None and srgb.get('val'):
        return f"#{srgb.get('val').upper()}"

    sys_clr = color_node.find('a:sysClr', _NS)
    if sys_clr is not None:
        if sys_clr.get('lastClr'):
            return f"#{sys_clr.get('lastClr').upper()}"
        if sys_clr.get('val'):
            return f"#{sys_clr.get('val').upper()}"

    scheme = color_node.find('a:schemeClr', _NS)
    if scheme is not None and scheme.get('val'):
        return scheme.get('val')

    return None


def _extract_theme_info(zf: zipfile.ZipFile) -> dict[str, Any]:
    theme_member = next((name for name in sorted(zf.namelist()) if name.startswith('ppt/theme/') and name.endswith('.xml')), None)
    if not theme_member:
        return {
            'theme_name': 'default',
            'colors': {},
            'major_font': {'latin': '', 'ea': '', 'cs': ''},
            'minor_font': {'latin': '', 'ea': '', 'cs': ''},
            'source': 'fallback-default',
        }

    root = _xml_root(zf, theme_member)
    if root is None:
        return {
            'theme_name': Path(theme_member).stem,
            'colors': {},
            'major_font': {'latin': '', 'ea': '', 'cs': ''},
            'minor_font': {'latin': '', 'ea': '', 'cs': ''},
            'source': 'fallback-theme-parse',
        }

    colors: dict[str, str] = {}
    color_scheme = root.find('.//a:clrScheme', _NS)
    if color_scheme is not None:
        for child in list(color_scheme):
            resolved = _resolve_color_value(child)
            if resolved:
                colors[child.tag.split('}')[-1]] = resolved

    def _extract_font_group(path: str) -> dict[str, str]:
        node = root.find(path, _NS)
        if node is None:
            return {'latin': '', 'ea': '', 'cs': ''}
        return {
            'latin': node.find('a:latin', _NS).get('typeface') if node.find('a:latin', _NS) is not None else '',
            'ea': node.find('a:ea', _NS).get('typeface') if node.find('a:ea', _NS) is not None else '',
            'cs': node.find('a:cs', _NS).get('typeface') if node.find('a:cs', _NS) is not None else '',
        }

    return {
        'theme_name': root.get('name') or Path(theme_member).stem,
        'colors': colors,
        'major_font': _extract_font_group('.//a:majorFont'),
        'minor_font': _extract_font_group('.//a:minorFont'),
        'source': theme_member,
    }


def _resolve_theme_font(typeface: str | None, theme_info: dict[str, Any]) -> str:
    raw = _coerce_text(typeface)
    if not raw:
        return ''
    if raw.startswith('+mj-') or raw.startswith('+mn-'):
        group = 'major_font' if raw.startswith('+mj-') else 'minor_font'
        suffix = raw.rsplit('-', 1)[-1]
        key = {'lt': 'latin', 'latin': 'latin', 'ea': 'ea', 'cs': 'cs'}.get(suffix, 'latin')
        group_map = theme_info.get(group) or {}
        resolved = _coerce_text(group_map.get(key))
        if resolved:
            return resolved
        for fallback_key in ('latin', 'ea', 'cs'):
            resolved = _coerce_text(group_map.get(fallback_key))
            if resolved:
                return resolved
    return raw


def _shape_placeholder_type(shape: ET.Element) -> str | None:
    ph = shape.find('.//p:ph', _NS)
    return (ph.get('type') if ph is not None else None) or None


def _shape_kind(shape: ET.Element) -> str:
    tag = shape.tag.split('}')[-1]
    if tag == 'pic':
        return 'image'
    if tag == 'graphicFrame':
        if shape.find('.//c:chart', _NS) is not None:
            return 'chart'
        if shape.find('.//a:tbl', _NS) is not None:
            return 'table'
        return 'graphic'
    if tag == 'cxnSp':
        return 'connector'
    if tag == 'grpSp':
        return 'group'
    if tag == 'sp':
        return 'text' if shape.find('.//a:t', _NS) is not None else 'shape'
    return tag


def _shape_bounds(shape: ET.Element) -> dict[str, int] | None:
    for xfrm in list(shape.findall('.//a:xfrm', _NS)) + list(shape.findall('.//p:xfrm', _NS)):
        off = xfrm.find('a:off', _NS) or xfrm.find('p:off', _NS)
        ext = xfrm.find('a:ext', _NS) or xfrm.find('p:ext', _NS)
        if off is None or ext is None:
            continue
        try:
            return {
                'left': int(off.get('x') or 0),
                'top': int(off.get('y') or 0),
                'width': int(ext.get('cx') or 0),
                'height': int(ext.get('cy') or 0),
            }
        except Exception:
            continue
    return None


def _iter_slide_shapes(container: ET.Element):
    for child in list(container):
        tag = child.tag.split('}')[-1]
        if tag in {'nvGrpSpPr', 'grpSpPr'}:
            continue
        if tag == 'grpSp':
            yield from _iter_slide_shapes(child)
            continue
        yield child


def _collect_shape_text(shape: ET.Element) -> str:
    texts = [node.text for node in shape.findall('.//a:t', _NS) if node.text and node.text.strip()]
    return _coerce_text(' '.join(texts))


def _extract_font_candidates(shape: ET.Element, theme_info: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for node in list(shape.findall('.//a:rPr', _NS)) + list(shape.findall('.//a:defRPr', _NS)) + list(shape.findall('.//a:endParaRPr', _NS)):
        for child_name in ('latin', 'ea', 'cs'):
            child = node.find(f'a:{child_name}', _NS)
            if child is None:
                continue
            resolved = _resolve_theme_font(child.get('typeface'), theme_info)
            if resolved:
                candidates.append(resolved)
    return candidates


def _extract_font_size_candidates(shape: ET.Element) -> list[float]:
    sizes: list[float] = []
    for node in list(shape.findall('.//a:rPr', _NS)) + list(shape.findall('.//a:defRPr', _NS)) + list(shape.findall('.//a:endParaRPr', _NS)):
        size = node.get('sz')
        if not size:
            continue
        try:
            sizes.append(int(size) / 100.0)
        except Exception:
            continue
    return sizes


def _extract_slide_background(slide_root: ET.Element) -> str | None:
    for path in (
        './/p:bg//a:solidFill',
        './/p:bg/p:bgPr//a:solidFill',
        './/p:cSld/p:bg//a:solidFill',
    ):
        fill = slide_root.find(path, _NS)
        if fill is not None:
            resolved = _resolve_color_value(fill)
            if resolved:
                return resolved
    return None


def _extract_slide_size(presentation_root: ET.Element | None) -> dict[str, int]:
    if presentation_root is None:
        return {'width': 12192000, 'height': 6858000}
    sld_sz = presentation_root.find('p:sldSz', _NS)
    if sld_sz is None:
        return {'width': 12192000, 'height': 6858000}
    try:
        return {'width': int(sld_sz.get('cx') or 12192000), 'height': int(sld_sz.get('cy') or 6858000)}
    except Exception:
        return {'width': 12192000, 'height': 6858000}


def _iter_slide_paths(zf: zipfile.ZipFile) -> list[str]:
    presentation_root = _xml_root(zf, 'ppt/presentation.xml')
    rels_root = _xml_root(zf, 'ppt/_rels/presentation.xml.rels')
    if presentation_root is None or rels_root is None:
        return []

    rid_to_target = {
        rel.get('Id'): rel.get('Target')
        for rel in rels_root.findall('pr:Relationship', _NS)
        if rel.get('Id') and rel.get('Target')
    }

    slide_ids: list[str] = []
    sld_id_lst = presentation_root.find('p:sldIdLst', _NS)
    if sld_id_lst is not None:
        for sld_id in sld_id_lst.findall('p:sldId', _NS):
            rid = sld_id.get(f'{{{_NS["r"]}}}id')
            if rid:
                slide_ids.append(rid)

    slide_paths: list[str] = []
    for rid in slide_ids:
        target = rid_to_target.get(rid)
        if target:
            slide_paths.append(str(PurePosixPath('ppt') / target.lstrip('/')))

    if slide_paths:
        return slide_paths

    fallback_paths = [name for name in zf.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml')]
    return sorted(fallback_paths, key=lambda value: (int(re.search(r'(\d+)(?=\.xml$)', value).group(1)) if re.search(r'(\d+)(?=\.xml$)', value) else 0, value))


def _looks_like_agenda(text: str) -> bool:
    compact = _coerce_text(text)
    if not compact:
        return False
    if len(re.findall(r'\b(?:0?\d{1,2}|[一二三四五六七八九十])\b', compact)) >= 3:
        return True
    return any(marker in compact for marker in ('1.', '2.', '3.', '01', '02', '03', '目录', '议程', 'agenda', 'outline'))


def _resolve_page_region(item: dict[str, Any]) -> str:
    placeholder = _coerce_text(item.get('placeholder_type')).lower()
    kind = _coerce_text(item.get('kind')).lower()
    text = _coerce_text(item.get('text')).lower()
    text_len = len(re.sub(r'\s+', '', text))
    font_size = float(item.get('max_font_size') or 0)

    if placeholder in {'title', 'ctrtitle'} or ('cover' in text and text_len <= 64):
        return 'title'
    if placeholder in {'subtitle', 'sub-title'}:
        return 'subtitle'
    if placeholder in {'ftr', 'dt', 'sldnum'} or text_len <= 24:
        return 'footer'
    if kind == 'image':
        return 'visual'
    if kind in {'chart', 'table', 'graphic'}:
        return 'visual'
    if font_size >= 24 and text_len <= 80:
        return 'title'
    return 'body'


def _extract_slide_metrics(slide_root: ET.Element, slide_size: dict[str, int], theme_info: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    placeholder_counts: Counter[str] = Counter()
    region_counts: Counter[str] = Counter()
    text_box_count = 0
    image_count = 0
    shape_count = 0
    text_char_count = 0
    font_candidates: list[str] = []
    font_sizes: list[float] = []
    background_color = _extract_slide_background(slide_root)

    sp_tree = slide_root.find('.//p:spTree', _NS) or slide_root
    for shape in _iter_slide_shapes(sp_tree):
        kind = _shape_kind(shape)
        placeholder_type = _shape_placeholder_type(shape)
        text = _collect_shape_text(shape)
        bounds = _shape_bounds(shape)
        candidates = _extract_font_candidates(shape, theme_info)
        sizes = _extract_font_size_candidates(shape)
        font_candidates.extend(candidates)
        font_sizes.extend(sizes)

        item = {
            'kind': kind,
            'placeholder_type': placeholder_type,
            'text': text,
            'text_len': len(re.sub(r'\s+', '', text)),
            'max_font_size': max(sizes) if sizes else 0.0,
            'font_candidates': candidates,
            'bounds': bounds,
        }
        items.append(item)

        if placeholder_type:
            placeholder_counts[placeholder_type] += 1
        region_counts[_resolve_page_region(item)] += 1

        if kind == 'image':
            image_count += 1
            shape_count += 1
            continue

        if text:
            text_box_count += 1
            text_char_count += len(re.sub(r'\s+', '', text))
            continue

        if kind in {'shape', 'graphic', 'chart', 'table', 'connector'}:
            shape_count += 1

    title_candidates = [item for item in items if item['kind'] == 'text' and item['text']]
    title_candidates.sort(
        key=lambda item: (
            -int(_coerce_text(item.get('placeholder_type')).lower() in {'title', 'ctrtitle'}),
            -float(item.get('max_font_size') or 0),
            len(re.sub(r'\s+', '', _coerce_text(item.get('text')))),
        )
    )
    title_text = title_candidates[0]['text'] if title_candidates else ''
    title_placeholder_count = sum(placeholder_counts.get(key, 0) for key in ('title', 'ctrTitle', 'ctrtitle'))
    subtitle_placeholder_count = sum(placeholder_counts.get(key, 0) for key in ('subtitle', 'subTitle', 'subtitle'))
    body_placeholder_count = sum(placeholder_counts.get(key, 0) for key in ('body', 'obj', 'content'))
    footer_placeholder_count = sum(placeholder_counts.get(key, 0) for key in ('ftr', 'dt', 'sldNum'))
    dominant_font = Counter(font_candidates).most_common(1)[0][0] if font_candidates else ''
    dominant_font_size = max(font_sizes) if font_sizes else 0.0

    return {
        'text_box_count': text_box_count,
        'image_count': image_count,
        'shape_count': shape_count,
        'text_char_count': text_char_count,
        'title_box_count': title_placeholder_count,
        'subtitle_box_count': subtitle_placeholder_count,
        'body_box_count': body_placeholder_count,
        'footer_box_count': footer_placeholder_count,
        'placeholder_type_counts': dict(placeholder_counts),
        'region_counts': dict(region_counts),
        'title_text': title_text,
        'dominant_font_family': dominant_font,
        'dominant_font_size': dominant_font_size,
        'background_color': background_color,
        'items': items,
        'slide_width': slide_size['width'],
        'slide_height': slide_size['height'],
    }


def _detect_page_function(page_no: int, total_pages: int, metrics: dict[str, Any], text_blob: str, title_text: str) -> tuple[str, str]:
    text = _coerce_text(text_blob).lower()
    title = _coerce_text(title_text).lower()
    scores: Counter[str] = Counter()
    reasons: dict[str, list[str]] = {name: [] for name in _PAGE_FUNCTION_PRIORITY}

    if page_no == 1:
        scores['cover'] += 4
        reasons['cover'].append('first slide')
        if metrics['title_box_count'] or metrics['subtitle_box_count']:
            scores['cover'] += 2
            reasons['cover'].append('title/subtitle placeholders present')
        if metrics['text_char_count'] <= 240:
            scores['cover'] += 1
        if any(keyword in text for keyword in _COVER_KEYWORDS):
            scores['cover'] += 3
            reasons['cover'].append('cover keywords found')

    if page_no == total_pages:
        scores['summary'] += 1
        scores['ending'] += 1
        reasons['summary'].append('last slide')
        reasons['ending'].append('last slide')

    if any(keyword in text for keyword in _TOC_KEYWORDS):
        scores['toc'] += 5
        reasons['toc'].append('agenda keywords found')
    if page_no in {2, 3} and metrics['text_box_count'] >= 4 and metrics['text_char_count'] <= 600 and _looks_like_agenda(text):
        scores['toc'] += 2
        reasons['toc'].append('agenda-like numbered structure')

    if any(keyword in text or keyword in title for keyword in _SECTION_KEYWORDS):
        scores['section'] += 4
        reasons['section'].append('section keywords found')
    if metrics['text_box_count'] <= 4 and metrics['text_char_count'] <= 180 and (metrics['title_box_count'] or title):
        scores['section'] += 2
        reasons['section'].append('short title slide')

    if any(keyword in text for keyword in _COMPARISON_KEYWORDS):
        scores['comparison'] += 5
        reasons['comparison'].append('comparison keywords found')
    if metrics['text_box_count'] >= 4 and metrics['image_count'] <= 2 and 120 <= metrics['text_char_count'] <= 900 and metrics['body_box_count'] >= 2:
        scores['comparison'] += 1
        reasons['comparison'].append('balanced body layout')

    if any(keyword in text for keyword in _SUMMARY_KEYWORDS):
        scores['summary'] += 4
        reasons['summary'].append('summary keywords found')
    if page_no >= max(2, total_pages - 2):
        scores['summary'] += 1
        reasons['summary'].append('late-stage slide')

    if any(keyword in text for keyword in _ENDING_KEYWORDS):
        scores['ending'] += 5
        reasons['ending'].append('ending keywords found')
    if page_no == total_pages and metrics['text_char_count'] <= 220:
        scores['ending'] += 2
        reasons['ending'].append('short closing slide')

    if not scores:
        return 'content', 'default content slide'

    best = sorted(scores.items(), key=lambda item: (-item[1], _PAGE_FUNCTION_PRIORITY.index(item[0]) if item[0] in _PAGE_FUNCTION_PRIORITY else 99))[0][0]
    return best, '; '.join(reasons.get(best, [])) or 'rule-based classification'


def _default_page_metrics(page_no: int, total_pages: int, page_function: str, detail_level: str, reference_file: File) -> dict[str, Any]:
    base = {
        'cover': {'text_box_count': 5, 'image_count': 0, 'shape_count': 3, 'text_char_count': 120},
        'toc': {'text_box_count': 10, 'image_count': 0, 'shape_count': 4, 'text_char_count': 260},
        'section': {'text_box_count': 3, 'image_count': 1, 'shape_count': 2, 'text_char_count': 90},
        'comparison': {'text_box_count': 6, 'image_count': 1, 'shape_count': 4, 'text_char_count': 420},
        'summary': {'text_box_count': 6, 'image_count': 0, 'shape_count': 3, 'text_char_count': 280},
        'ending': {'text_box_count': 4, 'image_count': 0, 'shape_count': 2, 'text_char_count': 130},
        'content': {'text_box_count': 6, 'image_count': 1, 'shape_count': 4, 'text_char_count': 340},
    }.get(page_function, {'text_box_count': 5, 'image_count': 0, 'shape_count': 3, 'text_char_count': 220})
    detail_boost = {'concise': 0.85, 'balanced': 1.0, 'detailed': 1.18}.get(detail_level, 1.0)
    metrics = {
        'text_box_count': max(1, int(round(base['text_box_count'] * detail_boost))),
        'image_count': max(0, int(round(base['image_count'] * detail_boost))),
        'shape_count': max(0, int(round(base['shape_count'] * detail_boost))),
        'text_char_count': max(32, int(round(base['text_char_count'] * detail_boost))),
        'title_box_count': 1 if page_function in {'cover', 'toc', 'section', 'summary', 'ending'} else 0,
        'subtitle_box_count': 1 if page_function == 'cover' else 0,
        'body_box_count': 3 if page_function in {'toc', 'content', 'summary', 'comparison'} else 1,
        'footer_box_count': 1 if page_function in {'summary', 'ending'} else 0,
        'placeholder_type_counts': {'title': 1, 'body': 1},
        'region_counts': {'body': 1},
        'title_text': f'{reference_file.filename} - {page_function.title()}',
        'dominant_font_family': 'Aptos',
        'dominant_font_size': 28.0 if page_function in {'cover', 'section'} else 18.0,
        'background_color': None,
        'items': [],
        'slide_width': 12192000,
        'slide_height': 6858000,
    }
    if page_function == 'cover':
        metrics['placeholder_type_counts'] = {'title': 1, 'subtitle': 1}
    elif page_function == 'toc':
        metrics['placeholder_type_counts'] = {'title': 1, 'body': 1}
    elif page_function == 'ending':
        metrics['placeholder_type_counts'] = {'title': 1, 'body': 1}
    return metrics


def _fallback_page_function_for(page_no: int, total_pages: int) -> str:
    if page_no == 1:
        return 'cover'
    if page_no == 2 and total_pages > 4:
        return 'toc'
    if page_no == total_pages:
        return 'ending' if total_pages <= 6 else 'summary'
    return ['section', 'content', 'content', 'comparison', 'summary'][(page_no - 3) % 5]


def _fallback_total_pages(reference_file: File, detail_level: str) -> int:
    size_score = max(0, int(reference_file.file_size or 0) // 300_000)
    detail_span = {'concise': 1, 'balanced': 3, 'detailed': 5}.get(detail_level, 3)
    return _clamp(6 + size_score + detail_span // 2, 6, 18)


def _build_layout_schema(page_function: str, detail_level: str, page_no: int, total_pages: int, metrics: dict[str, Any], title_text: str, reason: str) -> dict[str, Any]:
    slots_template = _BASE_LAYOUT_PRESETS.get(page_function, _BASE_LAYOUT_PRESETS['content'])
    text_boxes = int(metrics.get('text_box_count') or 0)
    images = int(metrics.get('image_count') or 0)
    shapes = int(metrics.get('shape_count') or 0)
    body_boxes = int(metrics.get('body_box_count') or 0)
    title_boxes = int(metrics.get('title_box_count') or 0)
    subtitle_boxes = int(metrics.get('subtitle_box_count') or 0)
    footer_boxes = int(metrics.get('footer_box_count') or 0)
    total_elements = max(1, text_boxes + images + shapes)
    columns = 2 if page_function == 'comparison' or (page_function == 'content' and body_boxes >= 4 and text_boxes >= 6 and images <= 1) else 1
    density_hint = {'concise': 'compact', 'balanced': 'balanced', 'detailed': 'dense'}.get(detail_level, 'balanced')
    max_bullets = {'concise': 3, 'balanced': 5, 'detailed': 7}.get(detail_level, 5)

    def _slot_source_count(slot: dict[str, Any]) -> int:
        name = slot['name']
        if name in {'title', 'section_title', 'closing_message', 'summary_title', 'toc_title', 'agenda_title'}:
            return max(1 if title_text else 0, title_boxes or min(text_boxes, 1))
        if name == 'subtitle':
            return subtitle_boxes or (1 if text_boxes > 1 else 0)
        if name in {'hero_visual', 'visual', 'left_panel', 'right_panel', 'divider', 'accent_band', 'thanks_stamp'}:
            return max(images, max(1, shapes - text_boxes))
        if name in {'agenda_list', 'key_takeaways', 'action_items', 'body', 'summary_footer', 'contact_info', 'footer_note'}:
            return max(body_boxes, max(1, text_boxes - max(1, title_boxes)))
        return max(1, text_boxes)

    slots: list[dict[str, Any]] = []
    for slot in slots_template:
        source_count = _slot_source_count(slot)
        slots.append(
            {
                'name': slot['name'],
                'kind': slot['kind'],
                'position': slot['position'],
                'weight': round(float(slot['weight']) * (1.0 + min(source_count, 4) * 0.05), 3),
                'source_count': int(source_count),
                'source_share': round(source_count / total_elements, 3),
            }
        )

    return {
        'page_type': page_function,
        'slots': slots,
        'layout_rules': {
            'columns': columns,
            'allow_visual': bool(images or page_function in {'cover', 'comparison', 'summary'}),
            'text_alignment': 'center' if page_function in {'cover', 'ending'} else 'left',
            'density_hint': density_hint,
            'max_bullets': max_bullets,
            'content_flow': 'two_column' if columns == 2 else 'single_column',
            'title_style': 'hero' if page_function == 'cover' else 'section' if page_function == 'section' else 'standard',
            'page_span': {'page_no': page_no, 'total_pages': total_pages},
            'placeholder_mix': metrics.get('placeholder_type_counts') or {},
            'region_counts': metrics.get('region_counts') or {},
            'source_metrics': {
                'text_box_count': text_boxes,
                'image_count': images,
                'shape_count': shapes,
                'text_char_count': int(metrics.get('text_char_count') or 0),
                'title_box_count': title_boxes,
                'subtitle_box_count': subtitle_boxes,
                'body_box_count': body_boxes,
                'footer_box_count': footer_boxes,
            },
            'page_function_reason': reason,
            'title_text': title_text,
        },
    }


def _build_style_tokens(theme_info: dict[str, Any], metrics: dict[str, Any], detail_level: str, page_function: str, reference_file: File) -> dict[str, Any]:
    colors = theme_info.get('colors') or {}
    major_font = theme_info.get('major_font') or {}
    minor_font = theme_info.get('minor_font') or {}
    background_color = metrics.get('background_color') or colors.get('bg1') or colors.get('lt1') or '#FFFFFF'
    primary_color = colors.get('accent1') or colors.get('accent2') or '#1B6EF3'
    accent_color = colors.get('accent2') or colors.get('accent3') or primary_color
    text_color = colors.get('dk1') or colors.get('dk2') or '#1E2B39'
    heading_font = _coerce_text(major_font.get('ea') or major_font.get('latin') or major_font.get('cs') or '') or 'Aptos'
    body_font = _coerce_text(minor_font.get('ea') or minor_font.get('latin') or minor_font.get('cs') or '') or heading_font
    dominant_font = _coerce_text(metrics.get('dominant_font_family') or body_font or heading_font) or 'Aptos'
    font_size_hint = float(metrics.get('dominant_font_size') or 0) or (28.0 if page_function in {'cover', 'section'} else 20.0)

    return {
        'theme_name': theme_info.get('theme_name') or 'default',
        'primary_color': primary_color,
        'accent_color': accent_color,
        'background_color': background_color,
        'text_color': text_color,
        'secondary_colors': [colors.get('accent3') or accent_color, colors.get('accent4') or primary_color, colors.get('accent5') or accent_color],
        'heading_font_family': heading_font,
        'body_font_family': body_font,
        'font_family': dominant_font,
        'font_size_hint': font_size_hint,
        'corner_radius': 10 + (_stable_hash(reference_file.id, page_function, detail_level) % 5),
        'shadow_level': 'soft' if metrics.get('image_count') else 'light',
        'density': detail_level,
        'page_function': page_function,
        'reference_extension': reference_file.ext,
        'palette_source': theme_info.get('source') or 'fallback',
    }


def _cluster_signature(page: dict[str, Any]) -> str:
    metrics = page.get('metrics') or {}
    return _canonical_json(
        {
            'page_function': page.get('page_function'),
            'title_box_count': metrics.get('title_box_count', 0) > 0,
            'subtitle_box_count': metrics.get('subtitle_box_count', 0) > 0,
            'body_box_bucket': min(4, int(metrics.get('body_box_count') or 0)),
            'text_bucket': min(5, int((metrics.get('text_char_count') or 0) // 180)),
            'image_bucket': min(3, int(metrics.get('image_count') or 0)),
            'shape_bucket': min(4, int(metrics.get('shape_count') or 0) // 4),
            'placeholder_keys': sorted((metrics.get('placeholder_type_counts') or {}).keys()),
        }
    )


def _assign_cluster_labels(pages: list[dict[str, Any]]) -> int:
    signatures = sorted({_cluster_signature(page) for page in pages})
    signature_to_label = {signature: f'cluster_{index:02d}' for index, signature in enumerate(signatures, start=1)}
    for page in pages:
        signature = _cluster_signature(page)
        page['cluster_signature'] = json.loads(signature)
        page['cluster_label'] = signature_to_label[signature]
    return len(signature_to_label)


def _normalize_feature_vector(values: list[float]) -> list[float]:
    cleaned = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not cleaned:
        return [0.0]
    norm = math.sqrt(sum(value * value for value in cleaned))
    if norm <= 0:
        return cleaned
    return [round(value / norm, 8) for value in cleaned]


def _page_rule_feature_vector(page: dict[str, Any], total_pages: int, detail_level: str) -> list[float]:
    metrics = page.get('metrics') or {}
    page_no = float(page.get('page_no') or 0)
    page_function = str(page.get('page_function') or 'content')
    detail_scale = {'concise': 0.82, 'balanced': 1.0, 'detailed': 1.18}.get(detail_level, 1.0)
    total_pages_safe = float(max(total_pages, 1))
    function_flags = [
        1.0 if page_function == 'cover' else 0.0,
        1.0 if page_function == 'toc' else 0.0,
        1.0 if page_function == 'section' else 0.0,
        1.0 if page_function == 'comparison' else 0.0,
        1.0 if page_function == 'ending' else 0.0,
        1.0 if page_function == 'summary' else 0.0,
    ]
    text_chars = float(metrics.get('text_char_count') or 0)
    text_boxes = float(metrics.get('text_box_count') or 0)
    image_count = float(metrics.get('image_count') or 0)
    shape_count = float(metrics.get('shape_count') or 0)
    title_boxes = float(metrics.get('title_box_count') or 0)
    subtitle_boxes = float(metrics.get('subtitle_box_count') or 0)
    body_boxes = float(metrics.get('body_box_count') or 0)
    footer_boxes = float(metrics.get('footer_box_count') or 0)
    placeholder_count = float(sum((metrics.get('placeholder_type_counts') or {}).values()))
    region_count = float(sum((metrics.get('region_counts') or {}).values()))
    title_len = float(len(_coerce_text(metrics.get('title_text') or '')))
    dominant_font_size = float(metrics.get('dominant_font_size') or 0)
    return _normalize_feature_vector(
        [
            page_no / total_pages_safe,
            (text_chars / 600.0) * detail_scale,
            text_boxes / 12.0,
            image_count / 8.0,
            shape_count / 18.0,
            title_boxes / 4.0,
            subtitle_boxes / 4.0,
            body_boxes / 6.0,
            footer_boxes / 3.0,
            placeholder_count / 8.0,
            region_count / 8.0,
            title_len / 80.0,
            dominant_font_size / 40.0,
            *function_flags,
        ]
    )


def _build_slide_snapshot_image(page: dict[str, Any], detail_level: str, reference_file: File):
    try:
        pil_image = importlib.import_module('PIL.Image')
        pil_draw = importlib.import_module('PIL.ImageDraw')
    except Exception:
        return None

    metrics = page.get('metrics') or {}
    width = 256
    height = 256
    background = _coerce_text((page.get('style_tokens_json') or {}).get('background_color') or '#F7FAFF')
    text_color = _coerce_text((page.get('style_tokens_json') or {}).get('text_color') or '#1E2B39')
    accent_color = _coerce_text((page.get('style_tokens_json') or {}).get('primary_color') or '#1B6EF3')
    image = pil_image.new('RGB', (width, height), background)
    draw = pil_draw.Draw(image)

    title_bar_h = 28 if page.get('page_function') in {'cover', 'section'} else 20
    draw.rectangle([14, 16, width - 14, 16 + title_bar_h], fill=accent_color)
    body_top = 16 + title_bar_h + 12

    text_boxes = max(1, int(metrics.get('text_box_count') or 0))
    image_boxes = max(0, int(metrics.get('image_count') or 0))
    shape_boxes = max(0, int(metrics.get('shape_count') or 0))
    rows = max(2, min(6, text_boxes + 1))
    col_split = 144 if image_boxes or page.get('page_function') in {'comparison', 'content'} else 212
    row_height = max(12, (height - body_top - 22) // rows)

    for idx in range(rows):
        top = body_top + idx * row_height
        left = 18
        right = col_split if idx % 2 == 0 else width - 18
        outline = accent_color if idx % 2 == 0 else text_color
        draw.rectangle([left, top, right, min(height - 18, top + row_height - 4)], outline=outline, width=2)

    if image_boxes:
        draw.rectangle([col_split + 10, body_top, width - 18, height - 56], outline=accent_color, width=2)
        draw.rectangle([col_split + 20, body_top + 10, width - 28, body_top + 54], fill=accent_color)
    else:
        draw.line([col_split, body_top, col_split, height - 40], fill=accent_color, width=2)

    footer_y = height - 34
    draw.rectangle([18, footer_y, width - 18, height - 18], fill=text_color)
    if shape_boxes > 2:
        draw.ellipse([width - 52, 22, width - 24, 50], outline=accent_color, width=2)

    return image


@lru_cache(maxsize=1)
def _load_vision_modules() -> tuple[Any | None, Any | None, str | None]:
    try:
        torch = importlib.import_module('torch')
        transformers = importlib.import_module('transformers')
    except Exception as exc:
        return None, None, f'vision deps unavailable: {exc}'
    return torch, transformers, None


@lru_cache(maxsize=4)
def _load_vision_processor_and_model(model_ref: str, cache_dir: str | None) -> tuple[Any | None, Any | None, str | None]:
    torch, transformers, load_error = _load_vision_modules()
    if load_error:
        return None, None, load_error

    try:
        processor_cls = getattr(transformers, 'AutoImageProcessor', None) or getattr(transformers, 'AutoProcessor', None)
        model_cls = getattr(transformers, 'AutoModel', None)
        if processor_cls is None or model_cls is None:
            raise RuntimeError('transformers vision classes unavailable')
        kwargs: dict[str, Any] = {'local_files_only': True}
        if cache_dir:
            kwargs['cache_dir'] = cache_dir
        processor = processor_cls.from_pretrained(model_ref, **kwargs)
        model = model_cls.from_pretrained(model_ref, **kwargs)
        model.eval()
        return torch, (processor, model), None
    except Exception as exc:
        return None, None, f'vision model unavailable: {exc}'


def _build_vision_embedding_vector(
    page: dict[str, Any],
    detail_level: str,
    reference_file: File,
    *,
    model_ref: str,
    cache_dir: str | None,
) -> tuple[list[float] | None, str | None, str | None]:
    image = _build_slide_snapshot_image(page, detail_level, reference_file)
    if image is None:
        return None, None, 'PIL image snapshot unavailable'

    try:
        torch, model_bundle, load_error = _load_vision_processor_and_model(model_ref, cache_dir)
        if load_error:
            raise RuntimeError(load_error)
        assert model_bundle is not None
        processor, model = model_bundle
        with torch.no_grad():
            inputs = processor(images=image, return_tensors='pt')
            outputs = model(**inputs)
            tensor = getattr(outputs, 'last_hidden_state', None)
            if tensor is None:
                tensor = getattr(outputs, 'pooler_output', None)
            if tensor is None:
                raise RuntimeError('vision model returned no tensor output')
            if hasattr(tensor, 'mean') and len(getattr(tensor, 'shape', [])) >= 2:
                tensor = tensor.mean(dim=1)
            if hasattr(tensor, 'detach'):
                tensor = tensor.detach().cpu()
            flattened = tensor.flatten().tolist()
        if not flattened:
            raise RuntimeError('vision embedding empty')
        return _normalize_feature_vector([float(value) for value in flattened[:384]]), 'vision_model', None
    except Exception as exc:
        return None, None, f'vision embedding failed: {exc}'


def _build_page_embeddings(
    pages: list[dict[str, Any]],
    detail_level: str,
    reference_file: File,
) -> tuple[list[list[float]], dict[str, Any]]:
    vectors: list[list[float]] = []
    embedding_mode = 'rule_features'
    fallback_reason = None
    vision_success = False
    vision_reasons: list[str] = []
    vision_model_ref = _resolve_vision_model_ref()
    vision_cache_dir = _resolve_vision_cache_dir()

    for page in pages:
        vision_vector, vision_mode, vision_reason = _build_vision_embedding_vector(
            page,
            detail_level,
            reference_file,
            model_ref=vision_model_ref,
            cache_dir=vision_cache_dir,
        )
        if vision_vector is not None:
            vision_success = True
            embedding_mode = vision_mode or 'vision_model'
            vectors.append(_normalize_feature_vector(vision_vector + _page_rule_feature_vector(page, len(pages), detail_level)))
        else:
            if vision_reason:
                vision_reasons.append(vision_reason)
            vectors.append(_page_rule_feature_vector(page, len(pages), detail_level))

    if not vision_success and vision_reasons:
        fallback_reason = vision_reasons[0]
    elif vision_success and vision_reasons:
        fallback_reason = vision_reasons[0]

    return vectors, {
        'embedding_mode': embedding_mode if vision_success else 'rule_features',
        'embedding_source': 'vision+rule' if vision_success else 'rule',
        'fallback_reason': fallback_reason,
        'embedding_model_ref': vision_model_ref if vision_success else _DEFAULT_EMBEDDING_MODEL,
        'embedding_model': _embedding_model_db_value(vision_model_ref, embedding_mode if vision_success else 'rule_features'),
    }


def _cluster_pages_hierarchically(vectors: list[list[float]], target_cluster_count: int) -> tuple[list[int], str | None, str | None]:
    if len(vectors) <= 1:
        return [1 for _ in vectors], 'hierarchical', None

    try:
        import numpy as np
        scipy_hierarchy = importlib.import_module('scipy.cluster.hierarchy')

        matrix = np.asarray(vectors, dtype=float)
        if matrix.ndim != 2 or matrix.shape[0] < 2:
            raise RuntimeError('invalid embedding matrix')
        linkage = scipy_hierarchy.linkage(matrix, method='ward', metric='euclidean')
        cluster_count = max(2, min(int(target_cluster_count), matrix.shape[0]))
        labels = scipy_hierarchy.fcluster(linkage, t=cluster_count, criterion='maxclust')
        return [int(label) for label in labels.tolist()], 'hierarchical', None
    except Exception as exc:
        try:
            import numpy as np
            from sklearn.cluster import AgglomerativeClustering

            matrix = np.asarray(vectors, dtype=float)
            cluster_count = max(2, min(int(target_cluster_count), matrix.shape[0]))
            model = AgglomerativeClustering(n_clusters=cluster_count)
            labels = model.fit_predict(matrix)
            return [int(label) + 1 for label in labels.tolist()], 'hierarchical', None
        except Exception as fallback_exc:
            return [], None, f'hierarchical clustering unavailable: {exc}; {fallback_exc}'


def _apply_embedding_clusters(
    pages: list[dict[str, Any]],
    vectors: list[list[float]],
    cluster_labels: list[int],
) -> int:
    if not cluster_labels:
        return _assign_cluster_labels(pages)

    unique_labels = sorted({int(label) for label in cluster_labels})
    label_to_cluster = {label: f'cluster_{index:02d}' for index, label in enumerate(unique_labels, start=1)}
    for page, label, vector in zip(pages, cluster_labels, vectors):
        page['cluster_signature'] = {
            'embedding_head': vector[:8],
            'source': 'embedding',
        }
        page['cluster_label'] = label_to_cluster[int(label)]
        page['embedding_vector_head'] = vector[:16]
    return len(unique_labels)


def _analyze_pptx_template(reference_file: File, detail_level: str, task_no: str) -> dict[str, Any]:
    file_path = _resolve_reference_path(reference_file)
    if not file_path.exists():
        raise FileNotFoundError(f'reference file missing: {file_path}')
    if file_path.suffix.lower() != '.pptx':
        raise ValueError(f'unsupported reference file type: {file_path.suffix}')
    if not zipfile.is_zipfile(file_path):
        raise ValueError('reference file is not a valid pptx archive')

    warnings: list[str] = []
    with zipfile.ZipFile(file_path) as zf:
        presentation_root = _xml_root(zf, 'ppt/presentation.xml')
        slide_size = _extract_slide_size(presentation_root)
        slide_paths = _iter_slide_paths(zf)
        if not slide_paths:
            raise ValueError('presentation contains no slides')

        theme_info = _extract_theme_info(zf)
        if theme_info.get('source') in {'fallback-default', 'fallback-theme-parse'}:
            warnings.append('theme file missing or unreadable; default colors used')

        pages: list[dict[str, Any]] = []
        for page_no, slide_member in enumerate(slide_paths, start=1):
            slide_root = _xml_root(zf, slide_member)
            if slide_root is None:
                warnings.append(f'failed to parse slide xml: {slide_member}')
                page_function = _fallback_page_function_for(page_no, len(slide_paths))
                metrics = _default_page_metrics(page_no, len(slide_paths), page_function, detail_level, reference_file)
                reason = 'slide xml parse failure; fallback metrics used'
            else:
                metrics = _extract_slide_metrics(slide_root, slide_size, theme_info)
                text_blob = ' '.join(item.get('text', '') for item in metrics.get('items', []))
                page_function, reason = _detect_page_function(page_no, len(slide_paths), metrics, text_blob, metrics.get('title_text') or '')

            pages.append(
                {
                    'page_no': page_no,
                    'metrics': metrics,
                    'page_function': page_function,
                    'page_function_reason': reason,
                    'layout_schema_json': _build_layout_schema(page_function, detail_level, page_no, len(slide_paths), metrics, metrics.get('title_text') or '', reason),
                    'style_tokens_json': _build_style_tokens(theme_info, metrics, detail_level, page_function, reference_file),
                }
            )

    llm_meta = _enhance_pptx_pages_with_llm(pages, detail_level, theme_info, reference_file, slide_size, task_no)
    embedding_vectors, embedding_meta = _build_page_embeddings(pages, detail_level, reference_file)
    cluster_labels, clustering_mode, clustering_fallback_reason = _cluster_pages_hierarchically(
        embedding_vectors,
        target_cluster_count=max(2, min(4, len(pages) // 2 or 2)),
    )
    if cluster_labels:
        cluster_count = _apply_embedding_clusters(pages, embedding_vectors, cluster_labels)
    else:
        cluster_count = _assign_cluster_labels(pages)
        clustering_mode = clustering_mode or 'signature'
    page_function_counts = Counter(page['page_function'] for page in pages)
    parse_status = 'ok' if not warnings else 'partial_fallback'
    fallback_notes = [note for note in [embedding_meta.get('fallback_reason'), clustering_fallback_reason] if note]
    fallback_reason = '; '.join(fallback_notes) if fallback_notes else None
    analysis_fingerprint = hashlib.sha256(
        _canonical_json(
            {
                'file_id': reference_file.id,
                'file_size': reference_file.file_size,
                'storage_path': reference_file.storage_path,
                'detail_level': detail_level,
                'slide_size': slide_size,
                'cluster_signatures': [page['cluster_signature'] for page in pages],
                'page_functions': [page['page_function'] for page in pages],
            }
        ).encode('utf-8')
    ).hexdigest()

    summary_json = {
        'analysis_mode': 'pptx_xml',
        'parse_status': parse_status,
        'detail_level': detail_level,
        'task_no': task_no,
        'source_file_id': reference_file.id,
        'source_filename': reference_file.filename,
        'source_storage_path': reference_file.storage_path,
        'source_path': str(file_path),
        'slide_size': slide_size,
        'total_pages': len(pages),
        'cluster_count': cluster_count,
        'embedding_mode': embedding_meta['embedding_mode'],
        'embedding_source': embedding_meta['embedding_source'],
        'clustering_mode': clustering_mode or 'signature',
        'fallback_reason': fallback_reason,
        'page_function_counts': dict(page_function_counts),
        'page_metrics': [
            {
                'page_no': page['page_no'],
                'page_function': page['page_function'],
                'cluster_label': page['cluster_label'],
                'text_box_count': page['metrics'].get('text_box_count', 0),
                'image_count': page['metrics'].get('image_count', 0),
                'shape_count': page['metrics'].get('shape_count', 0),
                'text_char_count': page['metrics'].get('text_char_count', 0),
            }
            for page in pages
        ],
        'llm_enhanced': llm_meta['llm_enhanced'],
        'llm_model': llm_meta['llm_model'],
        'llm_error': llm_meta['llm_error'],
        'llm_usage': llm_meta['llm_usage'],
        'llm_page_suggestions': llm_meta['llm_page_suggestions'],
        'llm_batches_total': llm_meta['llm_batches_total'],
        'llm_batches_succeeded': llm_meta['llm_batches_succeeded'],
        'theme': {
            'theme_name': theme_info.get('theme_name'),
            'primary_color': (theme_info.get('colors') or {}).get('accent1'),
            'accent_color': (theme_info.get('colors') or {}).get('accent2'),
            'background_color': pages[0]['style_tokens_json'].get('background_color') if pages else '#FFFFFF',
            'text_color': pages[0]['style_tokens_json'].get('text_color') if pages else '#1E2B39',
        },
        'analysis_fingerprint': analysis_fingerprint,
        'warnings': warnings,
    }

    return {
        'profile_version': _DEFAULT_PROFILE_VERSION,
        'detail_level': detail_level,
        'task_no': task_no,
        'reference_file_id': reference_file.id,
        'reference_filename': reference_file.filename,
        'reference_storage_path': reference_file.storage_path,
        'source_path': str(file_path),
        'analysis_source': 'pptx_xml',
        'parse_status': parse_status,
        'analysis_warnings': warnings,
        'total_pages': len(pages),
        'cluster_count': cluster_count,
        'embedding_model': embedding_meta.get('embedding_model') or _DEFAULT_EMBEDDING_MODEL,
        'embedding_mode': embedding_meta['embedding_mode'],
        'embedding_source': embedding_meta['embedding_source'],
        'clustering_mode': clustering_mode or 'signature',
        'fallback_reason': fallback_reason,
        'llm_model': llm_meta['llm_model'],
        'llm_enhanced': llm_meta['llm_enhanced'],
        'llm_error': llm_meta['llm_error'],
        'llm_usage': llm_meta['llm_usage'],
        'llm_page_suggestions': llm_meta['llm_page_suggestions'],
        'llm_batches_total': llm_meta['llm_batches_total'],
        'llm_batches_succeeded': llm_meta['llm_batches_succeeded'],
        'analysis_fingerprint': analysis_fingerprint,
        'slide_size': slide_size,
        'pages': pages,
        'summary_json': summary_json,
        'page_schemas_count': len(pages),
    }


def _build_fallback_analysis(reference_file: File, detail_level: str, task_no: str, reason: str) -> dict[str, Any]:
    settings = get_settings()
    llm_model = settings.llm_model or _DEFAULT_LLM_MODEL
    total_pages = _fallback_total_pages(reference_file, detail_level)
    pages: list[dict[str, Any]] = []
    theme_info = {
        'theme_name': _THEME_FALLBACKS[_stable_hash(reference_file.id, reference_file.filename, detail_level) % len(_THEME_FALLBACKS)]['theme_name'],
        'colors': {},
        'major_font': {'latin': 'Aptos', 'ea': 'Aptos', 'cs': 'Aptos'},
        'minor_font': {'latin': 'Aptos', 'ea': 'Aptos', 'cs': 'Aptos'},
        'source': 'fallback',
    }

    for page_no in range(1, total_pages + 1):
        page_function = _fallback_page_function_for(page_no, total_pages)
        metrics = _default_page_metrics(page_no, total_pages, page_function, detail_level, reference_file)
        pages.append(
            {
                'page_no': page_no,
                'metrics': metrics,
                'page_function': page_function,
                'page_function_reason': f'fallback analysis: {reason}',
                'layout_schema_json': _build_layout_schema(page_function, detail_level, page_no, total_pages, metrics, metrics.get('title_text') or '', reason),
                'style_tokens_json': _build_style_tokens(theme_info, metrics, detail_level, page_function, reference_file),
            }
        )

    cluster_count = _assign_cluster_labels(pages)
    page_function_counts = Counter(page['page_function'] for page in pages)
    analysis_fingerprint = hashlib.sha256(
        _canonical_json(
            {
                'file_id': reference_file.id,
                'file_size': reference_file.file_size,
                'storage_path': reference_file.storage_path,
                'detail_level': detail_level,
                'fallback_reason': reason,
                'cluster_signatures': [page['cluster_signature'] for page in pages],
                'page_functions': [page['page_function'] for page in pages],
            }
        ).encode('utf-8')
    ).hexdigest()

    summary_json = {
        'analysis_mode': 'metadata_fallback',
        'parse_status': 'fallback',
        'detail_level': detail_level,
        'task_no': task_no,
        'source_file_id': reference_file.id,
        'source_filename': reference_file.filename,
        'source_storage_path': reference_file.storage_path,
        'fallback_reason': reason,
        'total_pages': len(pages),
        'cluster_count': cluster_count,
        'embedding_mode': 'rule_features',
        'embedding_source': 'rule',
        'clustering_mode': 'signature',
        'fallback_reason': reason,
        'page_function_counts': dict(page_function_counts),
        'page_metrics': [
            {
                'page_no': page['page_no'],
                'page_function': page['page_function'],
                'cluster_label': page['cluster_label'],
                'text_box_count': page['metrics'].get('text_box_count', 0),
                'image_count': page['metrics'].get('image_count', 0),
                'shape_count': page['metrics'].get('shape_count', 0),
                'text_char_count': page['metrics'].get('text_char_count', 0),
            }
            for page in pages
        ],
        'llm_enhanced': False,
        'llm_model': llm_model,
        'llm_error': None,
        'llm_usage': None,
        'llm_page_suggestions': [],
        'llm_batches_total': 0,
        'llm_batches_succeeded': 0,
        'theme': pages[0]['style_tokens_json'] if pages else {},
        'analysis_fingerprint': analysis_fingerprint,
        'warnings': [reason],
    }

    return {
        'profile_version': _DEFAULT_PROFILE_VERSION,
        'detail_level': detail_level,
        'task_no': task_no,
        'reference_file_id': reference_file.id,
        'reference_filename': reference_file.filename,
        'reference_storage_path': reference_file.storage_path,
        'source_path': str(_resolve_reference_path(reference_file)),
        'analysis_source': 'metadata_fallback',
        'parse_status': 'fallback',
        'analysis_warnings': [reason],
        'total_pages': len(pages),
        'cluster_count': cluster_count,
        'embedding_model': _DEFAULT_EMBEDDING_MODEL,
        'embedding_mode': 'rule_features',
        'embedding_source': 'rule',
        'clustering_mode': 'signature',
        'fallback_reason': reason,
        'llm_model': llm_model,
        'llm_enhanced': False,
        'llm_error': None,
        'llm_usage': None,
        'llm_page_suggestions': [],
        'llm_batches_total': 0,
        'llm_batches_succeeded': 0,
        'analysis_fingerprint': analysis_fingerprint,
        'slide_size': {'width': 12192000, 'height': 6858000},
        'pages': pages,
        'summary_json': summary_json,
        'page_schemas_count': len(pages),
    }


def analyze_reference_template(reference_file: File, detail_level: str, task_no: str) -> dict[str, Any]:
    normalized_detail_level = _normalize_detail_level(detail_level)
    try:
        return _analyze_pptx_template(reference_file, normalized_detail_level, task_no)
    except Exception as exc:
        logger.warning('template analysis fallback used for %s: %s', reference_file.filename, exc)
        return _build_fallback_analysis(reference_file, normalized_detail_level, task_no, str(exc))


def upsert_template_profile(db: Session, file_id: int, analysis_result: dict[str, Any]) -> int:
    profile_version = analysis_result.get('profile_version', _DEFAULT_PROFILE_VERSION)
    profile = db.scalar(
        select(TemplateProfile).where(
            TemplateProfile.file_id == file_id,
            TemplateProfile.profile_version == profile_version,
        )
    )

    if profile is None:
        profile = TemplateProfile(
            file_id=file_id,
            profile_version=profile_version,
            total_pages=int(analysis_result.get('total_pages') or len(analysis_result.get('pages', [])) or 0),
            cluster_count=int(analysis_result.get('cluster_count') or 0),
            embedding_model=analysis_result.get('embedding_model', _DEFAULT_EMBEDDING_MODEL),
            llm_model=analysis_result.get('llm_model', _DEFAULT_LLM_MODEL),
            summary_json=analysis_result.get('summary_json') or {},
        )
        db.add(profile)
        db.flush()

    profile.total_pages = int(analysis_result.get('total_pages') or len(analysis_result.get('pages', [])) or 0)
    profile.cluster_count = int(analysis_result.get('cluster_count') or 0)
    profile.embedding_model = analysis_result.get('embedding_model', _DEFAULT_EMBEDDING_MODEL)
    profile.llm_model = analysis_result.get('llm_model', _DEFAULT_LLM_MODEL)
    profile.summary_json = analysis_result.get('summary_json') or {
        'analysis_source': analysis_result.get('analysis_source'),
        'parse_status': analysis_result.get('parse_status'),
        'reference_filename': analysis_result.get('reference_filename'),
        'task_no': analysis_result.get('task_no'),
        'total_pages': analysis_result.get('total_pages'),
        'cluster_count': analysis_result.get('cluster_count'),
    }
    db.flush()

    db.execute(delete(TemplatePageSchema).where(TemplatePageSchema.template_profile_id == profile.id))
    db.flush()

    for page in sorted(analysis_result.get('pages', []), key=lambda item: int(item.get('page_no') or 0)):
        db.add(
            TemplatePageSchema(
                template_profile_id=profile.id,
                page_no=int(page.get('page_no') or 0),
                cluster_label=str(page.get('cluster_label') or 'cluster_01'),
                page_function=str(page.get('page_function') or 'content'),
                layout_schema_json=page.get('layout_schema_json') or {'slots': [], 'layout_rules': {}},
                style_tokens_json=page.get('style_tokens_json') or {},
            )
        )

    db.flush()
    return profile.id


def analyze_and_persist_template(db: Session, reference_file: File, detail_level: str, task_no: str) -> dict[str, Any]:
    analysis_result = analyze_reference_template(reference_file, detail_level, task_no)
    profile_id = upsert_template_profile(db, reference_file.id, analysis_result)
    db.commit()

    persisted = dict(analysis_result)
    persisted['profile_id'] = profile_id
    persisted['__persisted__'] = True
    persisted['page_schemas_count'] = len(analysis_result.get('pages', []))
    return persisted


analyze_template = analyze_reference_template


__all__ = [
    'analyze_reference_template',
    'analyze_and_persist_template',
    'analyze_template',
    'upsert_template_profile',
]
