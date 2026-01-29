# SPDX-License-Identifier: GPL-3.0-or-later
"""Where things live inside a working directory (``./data`` by default).

Everything under it is generated, and most of it is personal data, so it is git-ignored.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# ECI roll files are named like 2026-EROLLGEN-U05-40-SIR-DraftRoll-Revision1-ENG-12-WI.pdf
_PART_RE = re.compile(r"-(\d{1,4})-WI(?:\.pdf)?$", re.I)
_YEAR_RE = re.compile(r"^((?:19|20)\d{2})-")


def part_from_filename(name: str) -> int | None:
    """Part number from an ECI roll filename or stem: '...-ENG-12-WI.pdf' -> 12."""
    m = _PART_RE.search(name)
    return int(m.group(1)) if m else None


def year_from_filename(name: str) -> int | None:
    """Roll year from an ECI roll filename: '2026-EROLLGEN-...' -> 2026."""
    m = _YEAR_RE.match(name)
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class DataDir:
    root: Path

    @property
    def pdfs(self) -> Path:
        return self.root / "pdfs"

    @property
    def ocr_cache(self) -> Path:
        """Raw Tesseract output, one JSON file per page: ocr_cache/<pdf stem>/pNNN.json."""
        return self.root / "ocr_cache"

    @property
    def markdown(self) -> Path:
        return self.root / "markdown"

    @property
    def voters_csv(self) -> Path:
        return self.root / "voters.csv"

    @property
    def state(self) -> Path:
        """Download progress: {"done": {"<part>": "<filename>"}}."""
        return self.root / "state.json"

    @property
    def roll(self) -> Path:
        """Which roll this directory holds, plus the portal's part list."""
        return self.root / "roll.json"

    @property
    def learned_glyphs(self) -> Path:
        """CAPTCHA glyphs confirmed by the server, layered over the bundled seed model."""
        return self.root / "captcha_learned.npz"


class UserError(Exception):
    """A problem the user can fix (bad arguments, missing inputs); reported without a traceback."""
