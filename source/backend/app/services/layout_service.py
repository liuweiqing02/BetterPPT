from __future__ import annotations

from typing import Any


def _normalize_slide(slide: dict[str, Any], page_no: int) -> dict[str, Any]:
    title = str(
        slide.get('title')
        or slide.get('heading')
        or slide.get('page_title')
        or slide.get('name')
        or f'Page {page_no}'
    ).strip()
    bullets = slide.get('bullets')
    if not isinstance(bullets, list):
        bullets = []
    bullets = [str(item).strip() for item in bullets if str(item).strip()]
    if not bullets:
        bullets = ['Key facts', 'Insights', 'Recommendations']
    return {
        'page_no': page_no,
        'title': title,
        'bullets': bullets[:8],
    }


def map_slide_plan_to_template(
    slide_plan: list[dict[str, Any]],
    template_pages: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """
    Map a normalized slide plan onto template page schemas.

    Returns a mapped slide plan that carries page-level style/layout hints
    used by downstream generation.
    """

    normalized = [_normalize_slide(item if isinstance(item, dict) else {}, idx + 1) for idx, item in enumerate(slide_plan)]
    if not normalized:
        return {
            'mapping_mode': 'empty',
            'template_page_count': 0,
            'mapped_slide_plan': [],
        }

    template_pages = [item for item in (template_pages or []) if isinstance(item, dict)]
    if not template_pages:
        mapped = []
        for slide in normalized:
            mapped.append(
                {
                    **slide,
                    'page_function': 'content',
                    'cluster_label': 'cluster_default',
                    'template_page_no': slide['page_no'],
                    'layout_schema_json': {'slots': ['title', 'bullets']},
                    'style_tokens_json': {
                        'font_family': 'Arial',
                        'accent_color': '#1B6EF3',
                        'background_color': '#F7FAFF',
                        'text_color': '#1E2B39',
                    },
                }
            )
        return {
            'mapping_mode': 'default',
            'template_page_count': 0,
            'mapped_slide_plan': mapped,
        }

    mapped = []
    tpl_len = len(template_pages)
    for idx, slide in enumerate(normalized):
        tpl = template_pages[idx % tpl_len]
        mapped.append(
            {
                **slide,
                'page_function': str(tpl.get('page_function') or 'content'),
                'cluster_label': str(tpl.get('cluster_label') or f'cluster_{(idx % tpl_len) + 1}'),
                'template_page_no': int(tpl.get('page_no') or ((idx % tpl_len) + 1)),
                'layout_schema_json': tpl.get('layout_schema_json') or {'slots': ['title', 'bullets']},
                'style_tokens_json': tpl.get('style_tokens_json') or {},
            }
        )

    return {
        'mapping_mode': 'template',
        'template_page_count': tpl_len,
        'mapped_slide_plan': mapped,
    }


def _normalize_llm_suggestion(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    try:
        page_no = int(raw.get('page_no') or raw.get('page') or 0)
    except Exception:
        page_no = 0
    if page_no <= 0:
        return None

    page_function = str(raw.get('page_function') or '').strip().lower()
    if not page_function:
        page_function = ''

    layout_suggestions: dict[str, Any] = {}
    if isinstance(raw.get('layout_suggestions'), dict):
        layout_suggestions.update(raw['layout_suggestions'])
    if isinstance(raw.get('layout'), dict):
        layout_suggestions.update(raw['layout'])

    style_suggestions: dict[str, Any] = {}
    if isinstance(raw.get('style_suggestions'), dict):
        style_suggestions.update(raw['style_suggestions'])
    if isinstance(raw.get('style'), dict):
        style_suggestions.update(raw['style'])

    result: dict[str, Any] = {
        'page_no': page_no,
        'layout_suggestions': layout_suggestions,
        'style_suggestions': style_suggestions,
    }
    if page_function:
        result['page_function'] = page_function
    if raw.get('reason'):
        result['reason'] = str(raw.get('reason'))
    return result


def apply_template_llm_suggestions(
    mapped_slide_plan: list[dict[str, Any]],
    llm_page_suggestions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    normalized = [
        item
        for item in (_normalize_llm_suggestion(raw) for raw in (llm_page_suggestions or []))
        if item is not None
    ]
    if not normalized:
        return {
            'mapped_slide_plan': mapped_slide_plan,
            'suggestions_total': 0,
            'suggestions_applied': 0,
        }

    suggestion_by_page = {int(item['page_no']): item for item in normalized}
    applied = 0
    updated: list[dict[str, Any]] = []

    for raw_slide in mapped_slide_plan:
        slide = dict(raw_slide if isinstance(raw_slide, dict) else {})
        try:
            template_page_no = int(slide.get('template_page_no') or 0)
        except Exception:
            template_page_no = 0
        try:
            current_page_no = int(slide.get('page_no') or 0)
        except Exception:
            current_page_no = 0

        suggestion = suggestion_by_page.get(template_page_no) or suggestion_by_page.get(current_page_no)
        if suggestion:
            applied += 1

            suggested_function = str(suggestion.get('page_function') or '').strip()
            if suggested_function:
                slide['page_function'] = suggested_function
                slide['page_function_source'] = 'llm_template_suggestion'

            layout_schema = dict(slide.get('layout_schema_json') or {})
            layout_rules = dict(layout_schema.get('layout_rules') or {})
            for key in ('density_hint', 'title_style', 'columns', 'text_alignment', 'max_bullets'):
                if key in suggestion['layout_suggestions'] and suggestion['layout_suggestions'].get(key) is not None:
                    layout_rules[key] = suggestion['layout_suggestions'][key]
            layout_schema['layout_rules'] = layout_rules
            slide['layout_schema_json'] = layout_schema

            style_tokens = dict(slide.get('style_tokens_json') or {})
            for key in ('accent_strategy', 'primary_color', 'accent_color', 'background_color', 'text_color'):
                if key in suggestion['style_suggestions'] and suggestion['style_suggestions'].get(key) is not None:
                    style_tokens[key] = suggestion['style_suggestions'][key]
            slide['style_tokens_json'] = style_tokens
            slide['llm_enhancement_json'] = suggestion

        updated.append(slide)

    return {
        'mapped_slide_plan': updated,
        'suggestions_total': len(normalized),
        'suggestions_applied': applied,
    }


__all__ = ['map_slide_plan_to_template', 'apply_template_llm_suggestions']
