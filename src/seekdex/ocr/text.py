"""FTS postings for Chinese substrings and Latin words, without an NLP model."""
from __future__ import annotations

import re
import unicodedata

HAN = r"\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f"
PARTS = re.compile(f"[{HAN}]+|[^\\W_{HAN}]+", re.UNICODE)
CHINESE = re.compile(f"^[{HAN}]+$")


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(f"(?<=[{HAN}]) +(?=[{HAN}])", "", value)


def query_terms(text: str) -> list[str]:
    if len(text) > 512:
        raise ValueError("图片文字关键词请控制在 512 个字符以内")
    return PARTS.findall(normalize_text(text))


def tokens(text: str, *, query: bool = False) -> list[str]:
    postings = []
    for part in PARTS.findall(normalize_text(text)):
        if CHINESE.fullmatch(part):
            if not query or len(part) == 1:
                postings.extend(part)
            postings.extend(part[i:i+2] for i in range(len(part)-1))
        else:
            postings.append(part)
    return postings


def search_tokens(text: str) -> str:
    return " ".join(tokens(text))


def fts_query(text: str) -> str:
    return " AND ".join('"' + token.replace('"', '""') + '"'
                        for token in dict.fromkeys(tokens(text, query=True)))
