# SPDX-License-Identifier: GPL-3.0-or-later
"""Write parsed parts out as searchable markdown (one file per part, plus an index) and CSV."""

import csv
from dataclasses import asdict, fields
from pathlib import Path

from .parse import Part, Voter

VOTER_TABLE_HEADER = [
    "| Sl | EPIC | Name | Relation | Relative's name | House no. | Age | Gender | Page |",
    "|---:|---|---|---|---|---|---:|---|---:|",
]


def md_cell(s: str) -> str:
    return s.replace("|", "\\|")


def part_filename(part: Part, pdf_stem: str) -> str:
    return f"part-{part.number:03d}.md" if part.number else f"{pdf_stem}.md"


def part_markdown(part: Part, pdf_name: str, part_name: str, constituency: str) -> str:
    official_total = part.official["total"] if part.official else "unknown"
    title = f"Part {part.number}: {part_name}" if part.number else Path(pdf_name).stem
    lines = [
        "---",
        f"part: {part.number}",
        f'part_name: "{part_name}"',
        f"constituency: {constituency}",
        f"source_pdf: {pdf_name}",
        f"voters: {len(part.voters)}",
        f"official_total: {official_total}",
        f'check: "{part.check}"',
        "---",
        f"# {title}",
        "",
    ]
    section = None
    for v in part.voters:
        if v.section != section:  # new sub-heading whenever the locality changes
            section = v.section
            lines += ["", f"## Section: {section or 'unknown'}", "", *VOTER_TABLE_HEADER]
        name = f"~~{md_cell(v.name)}~~ (deleted)" if v.deleted else md_cell(v.name)
        lines.append(
            f"| {v.serial} | {v.epic} | {name} | {v.relation} | {md_cell(v.relative)} | "
            f"{md_cell(v.house)} | {v.age} | {v.gender} | {v.page} |"
        )
    for p in part.text_pages:
        lines += ["", f"## Page {p['page']} (full-page OCR)", "", "```text", p["text"].strip(), "```"]
    return "\n".join(lines) + "\n"


def index_markdown(rows: list[tuple[Part, Path]], part_names: dict[int, str], constituency: str) -> str:
    total = sum(len(part.voters) for part, _ in rows)
    lines = [
        f"# Electoral roll index: {constituency}",
        "",
        f"{total} voter entries across {len(rows)} parts.",
        "",
        "Check compares OCR'd voter and gender counts with each part's official summary page.",
        "",
        "| Part | Name | Voters | Check |",
        "|---:|---|---:|---|",
    ]
    for part, pdf in sorted(rows, key=lambda r: r[0].number or 0):
        link = part_filename(part, pdf.stem)
        name = md_cell(part_names.get(part.number, pdf.stem))
        lines.append(f"| [{part.number or '?'}]({link}) | {name} | {len(part.voters)} | {part.check} |")
    return "\n".join(lines) + "\n"


def write_csv(path: Path, voters: list[Voter]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[f.name for f in fields(Voter)])
        w.writeheader()
        w.writerows(asdict(v) for v in voters)
