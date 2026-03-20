from samples import BOX

from eroll.parse import Voter, check_counts, official_counts, parse_box, parse_part, section_of


def test_parse_box_extracts_all_fields():
    assert parse_box(BOX) == Voter(
        epic="ABC1234567",
        name="ASHA DEVI",
        relation="Husband",
        relative="RAVI KUMAR",
        house="12-B",
        age="42",
        gender="Female",
    )


def test_parse_box_tolerates_ocr_noise():
    v = parse_box("XYZ7654321\nName ;- !SHAAN\nFather’s Name = | MOHAN LAL\nAge ~ 30 Gender: male\n")
    assert (v.name, v.relation, v.relative, v.age, v.gender) == ("ISHAAN", "Father", "MOHAN LAL", "30", "Male")


def test_parse_box_flags_deleted_and_skips_empty():
    assert parse_box(BOX + "DELETED\n").deleted
    assert parse_box("   \n") is None
    assert parse_box("no recognisable fields") is None


def test_section_of():
    header = "Assembly Constituency No and Name : 1-SOMEWHERE Part No.:3\nSection No and Name 2-MAIN  ROAD\n"
    assert section_of(header) == "2-MAIN ROAD"
    assert section_of("") == ""


def test_official_counts_with_and_without_third_gender():
    assert official_counts("Integrated Electoral Roll, 232 205 437") == {
        "male": 232, "female": 205, "third": 0, "total": 437
    }  # fmt: skip
    assert official_counts("Total 100 90 1 191")["third"] == 1
    assert official_counts("nothing adds up 1 2 4") is None


def test_official_counts_ignores_roll_year():
    assert official_counts("Revision 2026: 10 5 15 2026", ignore={2026})["total"] == 15
    assert official_counts("Revision 2026: 10 5 15 2026") is None


def test_check_counts():
    voters = [Voter(gender="Male"), Voter(gender="Female"), Voter(gender="Female")]
    assert check_counts(voters, {"male": 1, "female": 2, "third": 0, "total": 3}) == "ok"
    assert check_counts(voters, {"male": 2, "female": 2, "third": 0, "total": 4}) == "MISMATCH male 1≠2, total 3≠4"
    assert check_counts(voters, None) == "no summary"


def test_parse_part_numbers_serials_across_pages():
    pages = [
        {"page": 1, "kind": "text", "text": "cover"},
        {"page": 2, "kind": "voters", "header": "Section No and Name 1-NORTH", "boxes": [BOX, "", BOX]},
        {"page": 3, "kind": "voters", "header": "Section No and Name 2-SOUTH", "boxes": [BOX]},
        {"page": 4, "kind": "text", "text": "Roll 2026 summary 0 3 3"},
    ]
    part = parse_part("2026-EROLLGEN-U05-40-SIR-DraftRoll-Revision1-ENG-7-WI.pdf", pages)
    assert part.number == 7
    serials = [(v.serial, v.page, v.section) for v in part.voters]
    assert serials == [(1, 2, "1-NORTH"), (3, 2, "1-NORTH"), (4, 3, "2-SOUTH")]  # the blank box still takes serial 2
    assert part.official == {"male": 0, "female": 3, "third": 0, "total": 3}
    assert part.check == "ok"


def test_parse_part_falls_back_to_cover_page_number():
    part = parse_part("renamed.pdf", [{"page": 1, "kind": "text", "text": "Part No. : 42"}])
    assert part.number == 42
