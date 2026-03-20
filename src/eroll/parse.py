# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn cached OCR text (see ocr.py) into voter records. Pure text processing, no OCR deps."""

import re
from dataclasses import dataclass, field

from .layout import part_from_filename, year_from_filename

FIELD_SEP = r"\s*[:;=+\-~]*\s*"  # Tesseract reads the ':' after a label as any of these
RE_EPIC = re.compile(r"\b([A-Z]{3}[0-9]{7}|[A-Z]{2}/\d{2}/\d{3}/\d{6})\b")
RE_NAME = re.compile(rf"^\W*Name{FIELD_SEP}(.+)$", re.M | re.I)
RE_REL = re.compile(rf"(Father|Husband|Mother|Wife|Other)[’'`s]*\s*Name{FIELD_SEP}(.+)$", re.M | re.I)
RE_HOUSE = re.compile(rf"House\s*(?:Number|No\.?){FIELD_SEP}(.*?)\s*(?:Photo\b.*)?$", re.M | re.I)
RE_AGE = re.compile(rf"Age{FIELD_SEP}(\d{{1,3}})", re.I)
RE_GENDER = re.compile(rf"Gender{FIELD_SEP}(Male|Female|Third\s*Gender|Transgender)", re.I)
RE_SECTION = re.compile(rf"Section\s*No\.?\s*and\s*Name{FIELD_SEP}(.+)$", re.M | re.I)
RE_COVER_PART = re.compile(r"Part\s*No\.?\s*[:\-]?\s*(\d{1,4})", re.I)


@dataclass
class Voter:
    """One voter entry. Field order is the CSV column order."""

    part: int | None = None
    section: str = ""
    serial: int = 0
    epic: str = ""
    name: str = ""
    relation: str = ""
    relative: str = ""
    house: str = ""
    age: str = ""
    gender: str = ""
    deleted: bool = False
    page: int = 0


@dataclass
class Part:
    number: int | None
    voters: list[Voter]
    official: dict[str, int] | None  # counts printed on the roll's own summary page
    text_pages: list[dict] = field(default_factory=list)  # cover/summary pages, kept verbatim

    @property
    def check(self) -> str:
        return check_counts(self.voters, self.official)


def clean(s: str) -> str:
    s = re.sub(r"\s*(Photo|Available)\b.*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" :;.-|")


def clean_person(s: str) -> str:
    """Names are letters only: '!' is a misread 'I', and stray leading symbols are noise."""
    s = clean(s).replace("!", "I")
    return re.sub(r"^[^A-Za-z]+", "", s)


def _first(rx: re.Pattern, text: str) -> str:
    return clean(m.group(1)) if (m := rx.search(text)) else ""


def section_of(header: str) -> str:
    return re.sub(r"\s+", " ", m.group(1)).strip() if (m := RE_SECTION.search(header)) else ""


def parse_box(text: str) -> Voter | None:
    """Parse the OCR text of one voter box; None if it holds neither a name nor an EPIC."""
    if not text.strip():
        return None
    name = RE_NAME.search(text)
    rel = RE_REL.search(text)
    voter = Voter(
        epic=_first(RE_EPIC, text),
        name=clean_person(name.group(1)) if name else "",
        relation=rel.group(1).title() if rel else "",
        relative=clean_person(rel.group(2)) if rel else "",
        house=_first(RE_HOUSE, text),
        age=_first(RE_AGE, text),
        gender=_first(RE_GENDER, text).title(),
        deleted="DELETED" in text.upper(),
    )
    return voter if voter.name or voter.epic else None


def official_counts(summary_text: str, ignore: set[int] = frozenset()) -> dict[str, int] | None:
    """Male/Female/(Third)/Total from the summary page: the first row of integers whose
    last value equals the sum of the others. `ignore` drops tokens such as the roll year
    that appear on the same line as the counts."""
    for line in summary_text.splitlines():
        nums = [int(n) for n in re.findall(r"\b\d{1,5}\b", line) if int(n) not in ignore]
        for k in (4, 3):  # with and without a Third-gender column
            if len(nums) >= k and nums[-1] == sum(nums[-k:-1]) and nums[-1] > 0:
                male, female, *rest = nums[-k:-1]
                return {"male": male, "female": female, "third": rest[0] if rest else 0, "total": nums[-1]}
    return None


def check_counts(voters: list[Voter], official: dict[str, int] | None) -> str:
    if not official:
        return "no summary"
    got = {
        "male": sum(v.gender == "Male" for v in voters),
        "female": sum(v.gender == "Female" for v in voters),
        "total": len(voters),
    }
    bad = [f"{k} {got[k]}≠{official[k]}" for k in got if got[k] != official[k]]
    return "ok" if not bad else "MISMATCH " + ", ".join(bad)


def parse_part(pdf_name: str, pages: list[dict]) -> Part:
    """Parse all cached pages of one roll PDF (in page order) into a Part."""
    text_pages = [p for p in pages if p["kind"] == "text"]
    number = part_from_filename(pdf_name)
    if number is None and text_pages and (m := RE_COVER_PART.search(text_pages[0]["text"])):
        number = int(m.group(1))

    voters = []
    serial = 0
    for p in pages:
        if p["kind"] != "voters":
            continue
        section = section_of(p.get("header", ""))
        for box in p["boxes"]:
            # Serials are not OCR'd: they run 1..N in reading order across the whole part.
            serial += 1
            if voter := parse_box(box):
                voter.part, voter.section, voter.serial, voter.page = number, section, serial, p["page"]
                voters.append(voter)

    year = year_from_filename(pdf_name)
    official = official_counts(text_pages[-1]["text"], {year} if year else set()) if text_pages else None
    return Part(number, voters, official, text_pages)
