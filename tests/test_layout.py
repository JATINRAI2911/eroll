import pytest

from eroll.layout import part_from_filename, year_from_filename

NAME = "2026-EROLLGEN-U05-40-SIR-DraftRoll-Revision1-ENG-12-WI.pdf"


@pytest.mark.parametrize(("name", "part"), [(NAME, 12), (NAME.removesuffix(".pdf"), 12), ("other.pdf", None)])
def test_part_from_filename(name, part):
    assert part_from_filename(name) == part


def test_year_from_filename():
    assert year_from_filename(NAME) == 2026
    assert year_from_filename("other.pdf") is None
