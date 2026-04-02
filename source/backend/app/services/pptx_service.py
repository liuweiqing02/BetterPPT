from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
    from pptx.util import Inches, Pt
except ImportError as exc:  # pragma: no cover - dependency guard
    raise RuntimeError(
        'python-pptx is required for pptx generation. Install the `python-pptx` package first.'
    ) from exc


class PPTXGenerationError(RuntimeError):
    pass


def _safe_hex_color(value: Any, default_rgb: tuple[int, int, int]) -> RGBColor:
    if isinstance(value, str):
        raw = value.strip().lstrip('#')
        if len(raw) == 6:
            try:
                return RGBColor.from_string(raw.upper())
            except Exception:
                pass
    return RGBColor(*default_rgb)


def _style_tokens(slide: dict[str, Any]) -> dict[str, Any]:
    style = slide.get('style_tokens_json')
    if isinstance(style, dict):
        return style
    return {}


def _coerce_text(value: Any, default: str = '') -> str:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        return text or default
    return str(value).strip() or default


def _coerce_items(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def _normalize_bullets(slide: dict[str, Any], slide_index: int) -> list[str]:
    raw_candidates = (
        slide.get('bullets'),
        slide.get('key_points'),
        slide.get('items'),
        slide.get('points'),
        slide.get('outline'),
    )

    bullets: list[str] = []
    for candidate in raw_candidates:
        for item in _coerce_items(candidate):
            text = _coerce_text(item)
            if text:
                bullets.append(text)

    if bullets:
        return bullets

    title = _coerce_text(slide.get('title') or slide.get('heading') or slide.get('page_title'), default=f'Slide {slide_index}')
    summary = _coerce_text(
        slide.get('summary')
        or slide.get('description')
        or slide.get('content')
        or slide.get('body')
        or slide.get('text'),
        default='',
    )
    return [
        f'Key idea: {title}',
        summary or 'Auto-generated placeholder bullet for missing slide_plan bullets.',
    ]


def _normalize_title(slide: dict[str, Any], slide_index: int, title_prefix: str) -> str:
    base_title = _coerce_text(
        slide.get('title')
        or slide.get('heading')
        or slide.get('page_title')
        or slide.get('section_title')
        or slide.get('name'),
        default=f'Slide {slide_index}',
    )
    if title_prefix:
        return f'{title_prefix}{base_title}'
    return base_title


def _normalize_tables(slide: dict[str, Any]) -> list[dict[str, Any]]:
    raw_candidates = (
        slide.get('tables'),
        slide.get('table'),
        slide.get('table_data'),
        slide.get('table_slots'),
    )

    tables: list[dict[str, Any]] = []
    for candidate in raw_candidates:
        for item in _coerce_items(candidate):
            if not isinstance(item, dict):
                continue

            title = _coerce_text(item.get('title') or item.get('name') or item.get('caption') or item.get('slot_key'))
            headers = [_coerce_text(value) for value in _coerce_items(item.get('headers') or item.get('columns')) if _coerce_text(value)]
            rows: list[list[str]] = []
            raw_rows = item.get('rows') or item.get('data') or item.get('items')

            for row in _coerce_items(raw_rows):
                if isinstance(row, dict):
                    row_values = [_coerce_text(value, default='-') for value in row.values()]
                    if row_values:
                        rows.append(row_values)
                    if not headers:
                        headers = [_coerce_text(key) for key in row.keys()]
                elif isinstance(row, (list, tuple)):
                    row_values = [_coerce_text(value, default='-') for value in row]
                    if row_values:
                        rows.append(row_values)
                else:
                    row_text = _coerce_text(row, default='-')
                    if row_text:
                        rows.append([row_text])

            if not rows:
                hint = item.get('hint') or item.get('summary') or item.get('content') or item.get('text')
                hint_text = _coerce_text(hint, default='')
                if hint_text:
                    rows = [[hint_text]]

            if not rows and not headers:
                continue

            if not headers:
                headers = ['Item', 'Value']

            tables.append(
                {
                    'title': title,
                    'headers': headers,
                    'rows': rows,
                    'source': _coerce_text(item.get('source') or item.get('content_source')),
                }
            )

    return tables


def _normalize_images(slide: dict[str, Any]) -> list[dict[str, Any]]:
    raw_candidates = (
        slide.get('images'),
        slide.get('image'),
        slide.get('image_slots'),
        slide.get('visuals'),
    )

    images: list[dict[str, Any]] = []
    for candidate in raw_candidates:
        for item in _coerce_items(candidate):
            if isinstance(item, dict):
                caption = _coerce_text(item.get('caption') or item.get('alt_text') or item.get('title') or item.get('slot_key'))
                image_path = _coerce_text(
                    item.get('image_path')
                    or item.get('path')
                    or item.get('file_path')
                    or item.get('local_path')
                )
                images.append(
                    {
                        'caption': caption,
                        'alt_text': _coerce_text(item.get('alt_text') or caption),
                        'source': _coerce_text(item.get('source') or item.get('content_source')),
                        'image_path': image_path,
                        'path': image_path,
                    }
                )
            else:
                caption = _coerce_text(item)
                if caption:
                    images.append(
                        {
                            'caption': caption,
                            'alt_text': caption,
                            'source': '',
                            'image_path': '',
                            'path': '',
                        }
                    )

    return images


def _add_title_box(slide, title: str, *, page_function: str, style: dict[str, Any]):
    title_y = Inches(0.35 if page_function != 'cover' else 1.1)
    title_h = Inches(0.8 if page_function != 'cover' else 1.2)
    title_box = slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12.0), Inches(0.8))
    title_box.left = Inches(0.6)
    title_box.top = title_y
    title_box.width = Inches(12.0)
    title_box.height = title_h
    text_frame = title_box.text_frame
    text_frame.clear()
    text_frame.word_wrap = True
    text_frame.margin_left = Pt(0)
    text_frame.margin_right = Pt(0)
    text_frame.margin_top = Pt(0)
    text_frame.margin_bottom = Pt(0)
    text_frame.vertical_anchor = MSO_ANCHOR.TOP

    paragraph = text_frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = title
    font = run.font
    font.name = str(style.get('font_family') or 'Arial')
    font.size = Pt(34 if page_function == 'cover' else 28)
    font.bold = True
    font.color.rgb = _safe_hex_color(style.get('text_color'), (30, 43, 57))
    paragraph.alignment = PP_ALIGN.CENTER if page_function in {'cover', 'ending'} else PP_ALIGN.LEFT
    return title_box


def _add_bullets_box(
    slide,
    bullets: list[str],
    *,
    page_function: str,
    style: dict[str, Any],
    asset_mode: bool = False,
):
    box = slide.shapes.add_textbox(Inches(0.9), Inches(1.35), Inches(11.4), Inches(5.9))
    if page_function == 'cover':
        box.top = Inches(2.5)
        box.height = Inches(3.8)
    elif page_function == 'toc':
        box.top = Inches(1.55)
        box.height = Inches(3.6 if asset_mode else 5.5)
    elif page_function == 'comparison':
        box.width = Inches(5.4 if not asset_mode else 5.0)
        box.height = Inches(5.6 if not asset_mode else 3.1)
    elif asset_mode:
        box.height = Inches(2.95)
    text_frame = box.text_frame
    text_frame.word_wrap = True
    text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    text_frame.margin_left = Pt(2)
    text_frame.margin_right = Pt(2)
    text_frame.margin_top = Pt(2)
    text_frame.margin_bottom = Pt(2)

    for idx, bullet in enumerate(bullets):
        if idx == 0:
            paragraph = text_frame.paragraphs[0]
        else:
            paragraph = text_frame.add_paragraph()
        paragraph.level = 0
        run = paragraph.add_run()
        prefix = f'{idx + 1}. ' if page_function == 'toc' else ''
        run.text = f'{prefix}{bullet}'
        run.font.name = str(style.get('font_family') or 'Arial')
        run.font.size = Pt(16 if page_function == 'toc' else 18)
        run.font.color.rgb = _safe_hex_color(style.get('text_color'), (35, 49, 66))
        paragraph.alignment = PP_ALIGN.LEFT
        paragraph.space_after = Pt(4)

    if page_function == 'comparison' and not asset_mode:
        right_box = slide.shapes.add_textbox(Inches(6.95), Inches(1.35), Inches(5.4), Inches(5.6))
        right_tf = right_box.text_frame
        right_tf.word_wrap = True
        right_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        split = max(1, len(bullets) // 2)
        right_items = bullets[split:]
        if not right_items:
            right_items = ['Comparison note', 'Trade-offs', 'Recommendation']
        for idx, bullet in enumerate(right_items):
            paragraph = right_tf.paragraphs[0] if idx == 0 else right_tf.add_paragraph()
            paragraph.level = 0
            run = paragraph.add_run()
            run.text = bullet
            run.font.name = str(style.get('font_family') or 'Arial')
            run.font.size = Pt(18)
            run.font.color.rgb = _safe_hex_color(style.get('text_color'), (35, 49, 66))
            paragraph.alignment = PP_ALIGN.LEFT
            paragraph.space_after = Pt(4)
    return box


def _apply_background(slide, style: dict[str, Any]):
    bg = _safe_hex_color(style.get('background_color'), (247, 250, 255))
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = bg


def _add_accent_bar(slide, style: dict[str, Any]):
    accent = _safe_hex_color(style.get('accent_color'), (27, 110, 243))
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(1.15), Inches(2.8), Inches(0.08))
    bar.fill.solid()
    bar.fill.fore_color.rgb = accent
    bar.line.fill.background()


def _add_table_placeholder(
    slide,
    table_data: dict[str, Any],
    *,
    left,
    top,
    width,
    style: dict[str, Any],
):
    headers = [_coerce_text(value, default='') for value in _coerce_items(table_data.get('headers'))]
    rows_raw = _coerce_items(table_data.get('rows'))
    rows: list[list[str]] = []
    for row in rows_raw:
        if isinstance(row, dict):
            rows.append([_coerce_text(value, default='-') for value in row.values()])
        elif isinstance(row, (list, tuple)):
            rows.append([_coerce_text(value, default='-') for value in row])
        else:
            row_text = _coerce_text(row, default='-')
            if row_text:
                rows.append([row_text])

    if not rows:
        rows = [['No data']]

    column_count = max(1, len(headers), max(len(row) for row in rows))
    normalized_headers = headers[:column_count] + [''] * max(0, column_count - len(headers))
    normalized_rows: list[list[str]] = []
    for row in rows[:6]:
        normalized_rows.append(row[:column_count] + [''] * max(0, column_count - len(row)))

    row_count = len(normalized_rows) + 1
    table_height = Inches(min(2.5, max(1.15, 0.28 * row_count + 0.38)))
    table_shape = slide.shapes.add_table(row_count, column_count, left, top, width, table_height)
    table = table_shape.table
    column_width = int(width / column_count)
    accent = _safe_hex_color(style.get('accent_color'), (27, 110, 243))
    text_color = _safe_hex_color(style.get('text_color'), (35, 49, 66))

    for col_idx in range(column_count):
        table.columns[col_idx].width = column_width

    for col_idx, header in enumerate(normalized_headers):
        cell = table.cell(0, col_idx)
        cell.text = header or f'Column {col_idx + 1}'
        cell.fill.solid()
        cell.fill.fore_color.rgb = accent
        for paragraph in cell.text_frame.paragraphs:
            paragraph.alignment = PP_ALIGN.CENTER
            for run in paragraph.runs:
                run.font.name = str(style.get('font_family') or 'Arial')
                run.font.size = Pt(12)
                run.font.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)

    for row_idx, row in enumerate(normalized_rows, start=1):
        for col_idx in range(column_count):
            cell = table.cell(row_idx, col_idx)
            cell.text = row[col_idx] if col_idx < len(row) else ''
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(255, 255, 255)
            for paragraph in cell.text_frame.paragraphs:
                paragraph.alignment = PP_ALIGN.LEFT
                for run in paragraph.runs:
                    run.font.name = str(style.get('font_family') or 'Arial')
                    run.font.size = Pt(11)
                    run.font.color.rgb = text_color

    return table_shape


def _resolve_local_image_path(image_data: dict[str, Any]) -> Path | None:
    candidate = _coerce_text(
        image_data.get('image_path')
        or image_data.get('path')
        or image_data.get('file_path')
        or image_data.get('local_path')
    )
    if not candidate:
        return None

    try:
        image_path = Path(candidate).expanduser()
    except Exception:
        return None

    allowed_suffixes = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
    if image_path.suffix.lower() not in allowed_suffixes:
        return None
    if not image_path.is_file():
        return None
    return image_path


def _try_add_picture(
    slide,
    image_path: Path | None,
    *,
    left,
    top,
    width,
    max_height,
):
    if image_path is None:
        return None

    picture_width = width
    picture_height = None
    try:
        from PIL import Image as PILImage

        with PILImage.open(image_path) as image:
            pixel_width, pixel_height = image.size
        if pixel_width > 0 and pixel_height > 0:
            max_width_emu = int(width)
            max_height_emu = int(max_height)
            aspect_ratio = pixel_width / pixel_height
            box_ratio = max_width_emu / max_height_emu if max_height_emu else aspect_ratio
            if aspect_ratio >= box_ratio:
                picture_width = max_width_emu
                picture_height = int(max_width_emu / aspect_ratio)
            else:
                picture_height = max_height_emu
                picture_width = int(max_height_emu * aspect_ratio)
    except Exception:
        picture_width = width
        picture_height = None

    try:
        if picture_height is None:
            return slide.shapes.add_picture(str(image_path), left, top, width=picture_width)
        return slide.shapes.add_picture(str(image_path), left, top, width=picture_width, height=picture_height)
    except Exception:
        return None


def _add_image_asset(
    slide,
    image_data: dict[str, Any],
    *,
    left,
    top,
    width,
    max_height,
    style: dict[str, Any],
):
    accent = _safe_hex_color(style.get('accent_color'), (27, 110, 243))
    caption_text = _coerce_text(image_data.get('caption') or image_data.get('alt_text') or 'Image')
    image_path = _resolve_local_image_path(image_data)
    picture = _try_add_picture(slide, image_path, left=left, top=top, width=width, max_height=max_height)

    if picture is not None:
        caption_top = top + picture.height + Inches(0.08)
        caption_box = slide.shapes.add_textbox(left, caption_top, width, Inches(0.36))
        caption_tf = caption_box.text_frame
        caption_tf.word_wrap = True
        caption_tf.clear()
        paragraph = caption_tf.paragraphs[0]
        run = paragraph.add_run()
        run.text = caption_text
        run.font.name = str(style.get('font_family') or 'Arial')
        run.font.size = Pt(11)
        run.font.color.rgb = _safe_hex_color(style.get('text_color'), (35, 49, 66))
        paragraph.alignment = PP_ALIGN.CENTER
        return caption_box

    frame_height = Inches(1.35)
    frame = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, frame_height)
    frame.fill.solid()
    frame.fill.fore_color.rgb = RGBColor(245, 247, 250)
    frame.line.color.rgb = accent
    frame.text_frame.clear()
    frame.text_frame.word_wrap = True
    frame.text_frame.margin_left = Pt(4)
    frame.text_frame.margin_right = Pt(4)
    frame.text_frame.margin_top = Pt(4)
    frame.text_frame.margin_bottom = Pt(4)
    paragraph = frame.text_frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = 'IMAGE PLACEHOLDER'
    run.font.name = str(style.get('font_family') or 'Arial')
    run.font.size = Pt(13)
    run.font.bold = True
    run.font.color.rgb = accent
    paragraph.alignment = PP_ALIGN.CENTER

    caption_box = slide.shapes.add_textbox(left, top + frame_height + Inches(0.03), width, Inches(0.36))
    caption_tf = caption_box.text_frame
    caption_tf.word_wrap = True
    caption_tf.clear()
    paragraph = caption_tf.paragraphs[0]
    run = paragraph.add_run()
    run.text = caption_text
    run.font.name = str(style.get('font_family') or 'Arial')
    run.font.size = Pt(11)
    run.font.color.rgb = _safe_hex_color(style.get('text_color'), (35, 49, 66))
    paragraph.alignment = PP_ALIGN.CENTER

    return caption_box


def _render_assets(
    slide,
    *,
    tables: list[dict[str, Any]],
    images: list[dict[str, Any]],
    page_function: str,
    style: dict[str, Any],
):
    has_tables = bool(tables)
    has_images = bool(images)
    if not (has_tables or has_images):
        return

    zone_top = Inches(4.15 if page_function != 'cover' else 4.65)
    gap = Inches(0.18)
    full_left = Inches(0.75)
    full_width = Inches(11.85)
    half_width = Inches(5.72)
    left_column = full_left
    right_column = Inches(6.82)

    if has_tables and has_images:
        table_left = left_column
        table_width = half_width
        image_left = right_column
        image_width = half_width
    else:
        table_left = full_left
        table_width = full_width
        image_left = full_left
        image_width = full_width

    current_top = zone_top
    if has_tables:
        for table_data in tables[:3]:
            rendered = _add_table_placeholder(
                slide,
                table_data,
                left=table_left,
                top=current_top,
                width=table_width,
                style=style,
            )
            current_top = rendered.top + rendered.height + gap

    current_top = zone_top
    if has_images:
        for image_data in images[:3]:
            rendered = _add_image_asset(
                slide,
                image_data,
                left=image_left,
                top=current_top,
                width=image_width,
                max_height=Inches(1.95 if has_tables else 2.25),
                style=style,
            )
            current_top = rendered.top + rendered.height + gap


def generate_pptx_from_plan(slide_plan: list[dict], output_path: Path, title_prefix: str = '') -> dict:
    """
    Render a slide plan into an editable PPTX deck.

    Each slide gets a title and a bullet list, with deterministic fallback
    content when the input omits bullets.
    """

    try:
        if not isinstance(output_path, Path):
            output_path = Path(output_path)

        if not slide_plan:
            raise PPTXGenerationError('slide_plan is empty')

        output_path.parent.mkdir(parents=True, exist_ok=True)

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        blank_layout = prs.slide_layouts[6]

        rendered_titles: list[str] = []
        for index, slide_data in enumerate(slide_plan, start=1):
            if not isinstance(slide_data, dict):
                raise PPTXGenerationError(f'slide_plan[{index - 1}] must be a dict')

            slide = prs.slides.add_slide(blank_layout)
            page_function = _coerce_text(slide_data.get('page_function'), default='content').lower()
            style = _style_tokens(slide_data)
            title = _normalize_title(slide_data, index, title_prefix)
            bullets = _normalize_bullets(slide_data, index)
            tables = _normalize_tables(slide_data)
            images = _normalize_images(slide_data)
            asset_mode = bool(tables or images)

            _apply_background(slide, style)
            _add_title_box(slide, title, page_function=page_function, style=style)
            _add_accent_bar(slide, style)
            _add_bullets_box(slide, bullets, page_function=page_function, style=style, asset_mode=asset_mode)
            _render_assets(
                slide,
                tables=tables,
                images=images,
                page_function=page_function,
                style=style,
            )
            rendered_titles.append(title)

        prs.save(output_path)
        file_size = output_path.stat().st_size

        return {
            'output_path': str(output_path),
            'page_count': len(slide_plan),
            'file_size': file_size,
            'titles': rendered_titles,
        }
    except PPTXGenerationError:
        raise
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise PPTXGenerationError(f'failed to generate pptx: {exc}') from exc

