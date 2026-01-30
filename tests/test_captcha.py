import json
from pathlib import Path

import cv2
import pytest

from eroll.captcha import SEED_MODEL, Solver

FIXTURES = Path(__file__).parent / "fixtures" / "captcha"
LABELS = json.loads((FIXTURES / "labels.json").read_text())


@pytest.mark.parametrize(("stem", "answer"), LABELS.items())
def test_solves_portal_captchas(stem, answer):
    jpg = (FIXTURES / f"{stem}.jpg").read_bytes()
    assert Solver().solve(jpg) == answer
    assert Solver().solve(cv2.imread(str(FIXTURES / f"{stem}.jpg"))) == answer


def test_learning_goes_to_the_learned_file_not_the_seed(tmp_path):
    seed_before = SEED_MODEL.read_bytes()
    learned = tmp_path / "learned.npz"
    stem, answer = next(iter(LABELS.items()))
    jpg = (FIXTURES / f"{stem}.jpg").read_bytes()

    solver = Solver(learned=learned)
    n = len(solver.y)
    solver.learn(jpg, answer)
    solver.learn(jpg, "short")  # wrong glyph count: ignored

    assert SEED_MODEL.read_bytes() == seed_before
    assert len(Solver(learned=learned).y) == n + len(answer)
