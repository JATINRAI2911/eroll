# SPDX-License-Identifier: GPL-3.0-or-later
"""Fuzzy name search straight over the OCR cache, so it works while OCR is still running.

A voter matches when the whole name is similar (ratio >= threshold) or when every query
word fuzzily matches some word of the name, so a query for "ASHA DEVI" also finds
"ASHA KUMARI DEVI" and OCR slips like "ASHA DEVl".
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .layout import part_from_filename
from .parse import Voter, parse_box

THRESHOLD = 0.8


@dataclass
class Hit:
    query: str
    score: float
    part: int | str  # part number, or the PDF stem if it has none
    page: int
    box: int  # 1-based position of the voter box on the page
    voter: Voter

    @property
    def key(self) -> str:
        """Stable identity of a hit, for remembering which ones were already reported."""
        return f"{self.query}|{self.voter.epic}|{self.part}|{self.page}|{self.box}"

    def __str__(self) -> str:
        v = self.voter
        return (
            f"[{self.query}] {self.score:.2f}  part {self.part} p{self.page} #{self.box}  {v.epic}  "
            f"{v.name} ({v.relation}: {v.relative}), house {v.house}, {v.age} {v.gender}"
        )


def normalise_name(s: str) -> str:
    return " ".join(re.sub(r"[^A-Z ]", "", s.upper()).split())


def score(query: str, name: str) -> float:
    q, n = normalise_name(query), normalise_name(name)
    if not q or not n:
        return 0.0
    whole = SequenceMatcher(None, q, n).ratio()
    words = [max(SequenceMatcher(None, qw, nw).ratio() for nw in n.split()) for qw in q.split()]
    return max(whole, min(words))


def cached_voter_pages(cache_dir: Path) -> Iterator[tuple[Path, dict]]:
    for page_file in sorted(cache_dir.glob("*/p*.json")):
        page = json.loads(page_file.read_text())
        if page.get("kind") == "voters":
            yield page_file, page


def search(cache_dir: Path, queries: list[str], threshold: float = THRESHOLD) -> list[Hit]:
    """All voters whose name matches any query, grouped by query, best match first."""
    hits = []
    for page_file, page in cached_voter_pages(cache_dir):
        stem = page_file.parent.name
        part = part_from_filename(stem) or stem
        for i, box in enumerate(page["boxes"], 1):
            if not (voter := parse_box(box)):
                continue
            for q in queries:
                if (s := score(q, voter.name)) >= threshold:
                    hits.append(Hit(q, round(s, 2), part, page["page"], i, voter))
    return sorted(hits, key=lambda h: (h.query, -h.score))
