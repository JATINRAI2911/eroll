import csv
from pathlib import Path

from eroll.parse import Part, Voter
from eroll.report import index_markdown, part_markdown, write_csv


def test_part_markdown_strikes_deleted_voters_and_escapes_pipes():
    voters = [Voter(part=3, serial=1, name="A|B", house="1"), Voter(part=3, serial=2, name="C", deleted=True)]
    part = Part(3, voters, None)
    md = part_markdown(part, "x-3-WI.pdf", "SOME COLONY", "40 - SOMEWHERE")
    assert md.startswith("---\npart: 3\n")
    assert "# Part 3: SOME COLONY" in md
    assert "| 1 |  | A\\|B |" in md
    assert "~~C~~ (deleted)" in md
    assert 'check: "no summary"' in md


def test_index_links_each_part():
    md = index_markdown([(Part(3, [Voter()], None), Path("x-3-WI.pdf"))], {3: "COLONY"}, "AC")
    assert "| [3](part-003.md) | COLONY | 1 | no summary |" in md


def test_csv_columns(tmp_path):
    path = tmp_path / "voters.csv"
    write_csv(path, [Voter(part=1, name="X")])
    [row] = csv.DictReader(path.open())
    assert list(row) == [
        "part", "section", "serial", "epic", "name", "relation", "relative", "house", "age", "gender", "deleted", "page"
    ]  # fmt: skip
    assert row["name"] == "X"
