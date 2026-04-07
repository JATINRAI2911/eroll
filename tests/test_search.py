import json

from samples import BOX

from eroll.search import score, search


def test_score_tolerates_ocr_slips_and_middle_names():
    assert score("ASHA DEVI", "ASHA DEVI") == 1.0
    assert score("ASHA DEVI", "ASHA DEVl") >= 0.8  # 'l' misread for 'I'
    assert score("ASHA DEVI", "ASHA KUMARI DEVI") == 1.0  # every query word is present
    assert score("ASHA DEVI", "MOHAN LAL") < 0.8
    assert score("", "ASHA") == 0.0


def test_search_reads_the_ocr_cache(tmp_path):
    page_dir = tmp_path / "2026-EROLLGEN-U05-40-SIR-DraftRoll-Revision1-ENG-5-WI"
    page_dir.mkdir()
    (page_dir / "p003.json").write_text(json.dumps({"page": 3, "kind": "voters", "header": "", "boxes": ["", BOX]}))
    (page_dir / "p001.json").write_text(json.dumps({"page": 1, "kind": "text", "text": "Name : ASHA DEVI"}))

    [hit] = search(tmp_path, ["asha devi", "nobody"])
    assert (hit.part, hit.page, hit.box, hit.voter.epic) == (5, 3, 2, "ABC1234567")
    assert hit.key == "asha devi|ABC1234567|5|3|2"
