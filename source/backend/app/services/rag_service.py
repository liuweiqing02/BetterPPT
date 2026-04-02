from __future__ import annotations

import re
from collections import Counter
from typing import Any

_STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'into', 'onto', 'about', 'through',
    '根据', '以及', '以及', '一个', '一些', '我们', '你们', '他们', '她们', '它们', '可以', '需要', '进行',
    '以及', '因此', '同时', '如果', '由于', '但是', '或者', '并且', '并', '或', '与', '及', '和', '在', '对',
    '的', '了', '是', '为', '于', '中', '上', '下', '而', '被', '将', '把', '及其', '其', '各', '该', '这', '那',
}

_WORD_RE = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str | None) -> str:
    if not text:
        return ''
    return _WHITESPACE_RE.sub(' ', text).strip()


def _tokenize(text: str | None) -> list[str]:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return []
    tokens = [token for token in _WORD_RE.findall(normalized) if token not in _STOPWORDS]
    return tokens


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def chunk_document_text(text: str, chunk_size: int = 400, overlap: int = 60) -> list[dict[str, Any]]:
    """Split a document into stable overlapping character chunks."""
    normalized = _normalize_text(text)
    if not normalized:
        return []

    chunk_size = max(1, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size - 1))
    step = max(1, chunk_size - overlap)

    chunks: list[dict[str, Any]] = []
    start = 0
    chunk_index = 1
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunk_text = normalized[start:end].strip()
        if chunk_text:
            chunks.append(
                {
                    'chunk_id': f'chunk_{chunk_index:04d}',
                    'chunk_index': chunk_index,
                    'start_char': start,
                    'end_char': end,
                    'char_count': len(chunk_text),
                    'text': chunk_text,
                    'keywords': _dedupe_preserve_order(_tokenize(chunk_text))[:12],
                }
            )
            chunk_index += 1
        if end >= len(normalized):
            break
        start += step

    return chunks


def _extract_fallback_keywords_from_text(text: str, limit: int = 8) -> list[str]:
    tokens = _tokenize(text)
    if not tokens:
        return []

    counts = Counter(tokens)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], tokens.index(item[0]), item[0]))
    return [token for token, _ in ordered[:limit]]


def _extract_fallback_keywords_from_chunks(chunks: list[dict[str, Any]], limit: int = 8) -> list[str]:
    all_text = ' '.join(str(chunk.get('text', '')) for chunk in chunks)
    return _extract_fallback_keywords_from_text(all_text, limit=limit)


def build_query(user_prompt: str | None, fallback_keywords: list[str] | None) -> str:
    """Build a deterministic retrieval query."""
    prompt = _normalize_text(user_prompt)
    if prompt:
        return prompt

    keywords = [keyword.strip() for keyword in (fallback_keywords or []) if keyword and keyword.strip()]
    if keywords:
        return ' '.join(_dedupe_preserve_order(keywords))

    return 'document key facts summary'


def _score_chunk(chunk_text: str, query_tokens: list[str]) -> tuple[float, list[str], list[str]]:
    chunk_tokens = _tokenize(chunk_text)
    chunk_counts = Counter(chunk_tokens)
    chunk_token_set = set(chunk_tokens)
    matched = [token for token in _dedupe_preserve_order(query_tokens) if token in chunk_token_set]
    if not query_tokens:
        return 0.0, [], []

    score = 0.0
    for token in matched:
        score += 2.0 + min(3.0, chunk_counts.get(token, 0) * 0.5)

    if matched:
        score += min(2.0, len(matched) / max(1, len(query_tokens)) * 2.0)

    return round(score, 4), matched, chunk_tokens


def retrieve_chunks(chunks: list[dict[str, Any]], query: str, top_k: int = 5) -> dict[str, Any]:
    """Rank chunks with a deterministic lexical scorer."""
    normalized_chunks = chunks or []
    top_k = max(1, int(top_k))

    query_text = _normalize_text(query)
    query_tokens = _tokenize(query_text)
    if not query_tokens:
        query_tokens = _extract_fallback_keywords_from_chunks(normalized_chunks)
        query_text = build_query('', query_tokens)

    scored_chunks: list[dict[str, Any]] = []
    topic_counter: Counter[str] = Counter()

    for chunk in normalized_chunks:
        chunk_text = _normalize_text(str(chunk.get('text') or chunk.get('content') or chunk.get('chunk_text') or ''))
        if not chunk_text:
            continue

        score, matched_tokens, chunk_tokens = _score_chunk(chunk_text, query_tokens)
        if score <= 0 and query_tokens:
            continue

        topic_counter.update(matched_tokens)
        chunk_id = str(chunk.get('chunk_id') or chunk.get('id') or f'chunk_{chunk.get("chunk_index", 0)}')
        scored_chunks.append(
            {
                'chunk_id': chunk_id,
                'chunk_index': chunk.get('chunk_index'),
                'start_char': chunk.get('start_char'),
                'end_char': chunk.get('end_char'),
                'score': score,
                'matched_keywords': matched_tokens,
                'text': chunk_text,
                'excerpt': chunk_text[:240],
                'keywords': chunk.get('keywords') or _dedupe_preserve_order(chunk_tokens)[:12],
            }
        )

    scored_chunks.sort(key=lambda item: (-item['score'], item.get('start_char') or 0, item['chunk_id']))
    selected_chunks = scored_chunks[:top_k]

    citations = [
        {
            'chunk_id': item['chunk_id'],
            'start_char': item.get('start_char'),
            'end_char': item.get('end_char'),
            'excerpt': item['excerpt'],
        }
        for item in selected_chunks
    ]

    total_topic_hits = sum(topic_counter.values()) or 1
    topic_weights = {
        keyword: round(count / total_topic_hits, 4)
        for keyword, count in sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))
    }

    return {
        'query': query_text,
        'query_tokens': query_tokens,
        'retrieved_chunks': selected_chunks,
        'citations': citations,
        'topic_weights': topic_weights,
        'fallback_used': not bool(_normalize_text(query)),
        'retrieved_count': len(selected_chunks),
    }


__all__ = [
    'chunk_document_text',
    'build_query',
    'retrieve_chunks',
]
