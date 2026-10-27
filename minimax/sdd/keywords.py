"""Tokenisation used by the material index.

English identifiers and words are kept as-is (identifiers are split on
underscores); Chinese runs are split into character bigrams, which works well
for the short clause/task sentences used by this demo and needs no external
tokenizer.
"""

from __future__ import annotations

import re
from collections import Counter

ASCII_WORD = re.compile(r"[a-z_][a-z0-9_]{1,}")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
CLAUSE_ID = re.compile(r"\bS-\d{2}\b")

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have",
    "not", "but", "you", "your", "can", "may", "must", "should", "will", "would", "into",
    "when", "then", "than", "over", "under", "each", "per", "all", "any", "its", "their",
    "they", "them", "there", "here", "which", "what", "who", "how", "why", "use", "used",
    "using", "return", "returns", "returned", "also", "only", "same", "such", "other",
    "more", "most", "some", "both", "one", "two", "covers", "test", "tests", "def", "class",
    "import", "from", "self", "none", "true", "false",
    "并且", "或者", "因此", "如果", "那么", "以及", "对于", "通过", "进行", "可以",
}

MAX_KEYWORDS_PER_DOCUMENT = 60


def tokens(text: str) -> list[str]:
    """Return the searchable tokens of ``text`` (ASCII words + CJK bigrams)."""
    found: list[str] = []
    for match in ASCII_WORD.findall(text.lower()):
        for part in match.split("_"):
            if len(part) >= 2 and part not in STOPWORDS:
                found.append(part)
    for run in CJK_RUN.findall(text):
        if len(run) == 1:
            if run not in STOPWORDS:
                found.append(run)
            continue
        found.extend(run[index : index + 2] for index in range(len(run) - 1))
    return [token for token in found if token not in STOPWORDS]


def keyword_weights(text: str, *, title: str = "") -> dict[str, int]:
    """Return ``{keyword: weight}`` for one document.

    Title tokens are weighted three times as high as body tokens because clause
    titles are the most reliable description of what a clause is about.
    """
    counter: Counter[str] = Counter(tokens(text))
    for token, count in Counter(tokens(title)).items():
        counter[token] += count * 3
    ranked = counter.most_common(MAX_KEYWORDS_PER_DOCUMENT)
    return {token: min(count, 5) for token, count in ranked}


def clause_ids(text: str) -> list[str]:
    """Return the distinct clause identifiers mentioned in ``text``."""
    seen: list[str] = []
    for match in CLAUSE_ID.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def query_tokens(task: str) -> list[str]:
    """Return the deduplicated tokens of a task description."""
    seen: list[str] = []
    for token in tokens(task):
        if token not in seen:
            seen.append(token)
    return seen
