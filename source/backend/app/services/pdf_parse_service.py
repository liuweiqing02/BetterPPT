from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", (line or "")).strip()


def _split_table_cells(line: str) -> list[str]:
    raw = (line or "").strip()
    if not raw:
        return []
    if "|" in raw:
        cells = [cell.strip() for cell in raw.split("|")]
    elif "\t" in raw:
        cells = [cell.strip() for cell in raw.split("\t")]
    else:
        cells = [cell.strip() for cell in re.split(r"\s{2,}", raw)]
    return [cell for cell in cells if cell]


def _is_table_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    normalized = [re.sub(r"\s+", "", cell) for cell in cells]
    return all(bool(re.fullmatch(r"[-:=]+", cell)) for cell in normalized if cell)


def _normalize_table_block(block: list[list[str]]) -> list[list[str]]:
    if len(block) < 2:
        return []

    filtered = [row for row in block if len(row) >= 2 and not _is_table_separator_row(row)]
    if len(filtered) < 2:
        return []

    width_counts = Counter(len(row) for row in filtered)
    target_width = width_counts.most_common(1)[0][0]

    normalized: list[list[str]] = []
    for row in filtered:
        if len(row) > target_width:
            normalized.append(row[:target_width])
        else:
            normalized.append(row + [''] * (target_width - len(row)))
    return normalized


def _extract_table_blocks(lines: list[str]) -> list[list[list[str]]]:
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in lines:
        cells = _split_table_cells(line)
        if len(cells) >= 2:
            current.append(cells)
            continue
        if len(current) >= 2:
            normalized = _normalize_table_block(current)
            if normalized:
                blocks.append(normalized)
        current = []
    if len(current) >= 2:
        normalized = _normalize_table_block(current)
        if normalized:
            blocks.append(normalized)
    return blocks


def _extract_key_facts(lines: list[str], limit: int = 16) -> list[str]:
    facts: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"(\d[\d,\.]*\s*(%|万元|亿元|万|亿|k|m|b)?)", re.IGNORECASE)
    for line in lines:
        normalized = _normalize_line(line)
        if not normalized or normalized in seen:
            continue
        if pattern.search(normalized):
            seen.add(normalized)
            facts.append(normalized[:120])
            if len(facts) >= limit:
                break
    return facts


def _extract_sections(lines: list[str], page_no: int, limit: int = 10) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for line in lines:
        normalized = _normalize_line(line)
        if len(normalized) < 4 or len(normalized) > 48:
            continue
        if normalized.endswith((".", "。", "!", "?", "！", "？", ":", "：")):
            continue
        if re.search(r"[A-Za-z\u4e00-\u9fff]", normalized) is None:
            continue
        sections.append({"title": normalized, "page": page_no})
        if len(sections) >= limit:
            break
    return sections


def _extract_evidence_spans(lines: list[str], page_no: int, limit: int = 3) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for line in lines:
        normalized = _normalize_line(line)
        if len(normalized) < 20:
            continue
        spans.append({"page": page_no, "text": normalized[:220]})
        if len(spans) >= limit:
            break
    return spans


def _infer_image_ext(name: str, data: bytes) -> str:
    raw_name = (name or "").lower()
    for ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
        if raw_name.endswith(ext):
            return ext
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xFF\xD8"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    return ".png"


def parse_pdf_document(
    pdf_path: Path,
    *,
    image_output_dir: Path | None = None,
    max_pages: int = 80,
    max_images: int = 40,
    max_tables: int = 40,
) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required for PDF parsing") from exc

    reader = PdfReader(str(pdf_path))
    sections: list[dict[str, Any]] = []
    key_facts: list[str] = []
    evidence_spans: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []

    if image_output_dir is not None:
        image_output_dir.mkdir(parents=True, exist_ok=True)

    for page_idx, page in enumerate(reader.pages[:max_pages], start=1):
        text = ""
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""

        lines = [line for line in (_normalize_line(raw) for raw in text.splitlines()) if line]
        if lines:
            sections.extend(_extract_sections(lines, page_idx, limit=4))
            key_facts.extend(_extract_key_facts(lines, limit=4))
            evidence_spans.extend(_extract_evidence_spans(lines, page_idx, limit=2))

            if len(tables) < max_tables:
                for block_idx, block in enumerate(_extract_table_blocks(lines), start=1):
                    headers = block[0]
                    rows = block[1:7]
                    if not rows:
                        continue
                    tables.append(
                        {
                            "page_no": page_idx,
                            "title": f"Table P{page_idx}-{block_idx}",
                            "headers": headers,
                            "rows": rows,
                            "source": "pdf_native_table",
                        }
                    )
                    if len(tables) >= max_tables:
                        break

        if len(images) >= max_images:
            continue
        page_images = getattr(page, "images", None) or []
        if not page_images:
            continue
        for image_idx, image in enumerate(page_images, start=1):
            data = getattr(image, "data", None)
            if not data:
                continue
            if image_output_dir is None:
                image_path = ""
            else:
                ext = _infer_image_ext(getattr(image, "name", ""), data)
                image_path_obj = image_output_dir / f"page_{page_idx:03d}_img_{image_idx:02d}{ext}"
                image_path_obj.write_bytes(data)
                image_path = str(image_path_obj)

            caption = f"Source image P{page_idx}-{image_idx}"
            images.append(
                {
                    "page_no": page_idx,
                    "caption": caption,
                    "alt_text": caption,
                    "image_path": image_path,
                    "path": image_path,
                    "source": "pdf_native_image",
                }
            )
            if len(images) >= max_images:
                break

    dedup_facts: list[str] = []
    seen_facts: set[str] = set()
    for fact in key_facts:
        if fact in seen_facts:
            continue
        seen_facts.add(fact)
        dedup_facts.append(fact)

    if not sections:
        sections = [{"title": "Document Overview", "page": 1}]

    if not evidence_spans and dedup_facts:
        evidence_spans = [{"page": 1, "text": dedup_facts[0]}]

    return {
        "sections": sections[:24],
        "key_facts": dedup_facts[:24],
        "evidence_spans": evidence_spans[:40],
        "images": images[:max_images],
        "tables": tables[:max_tables],
        "analysis_source": "pdf_parse_service",
        "page_count": min(len(reader.pages), max_pages),
    }
