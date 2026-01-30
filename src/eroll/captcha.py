# SPDX-License-Identifier: GPL-3.0-or-later
"""Solver for the ECI portal CAPTCHA: 6 italic glyphs (a-z0-9) over pastel rules and one
diagonal strike-through line.

Pipeline: dark-pixel mask -> erase the diagonal line (Hough) -> split into 6 glyphs by
connected components -> nearest-neighbour match against labelled example glyphs.

The bundled seed model (captcha_glyphs.npz) is built by ``eroll captcha train``. Answers the
server accepts at download time are appended to a separate learned file in the data
directory, so accuracy keeps improving without modifying the bundled model.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

N_CHARS = 6
GLYPH = 24  # glyphs are normalised to GLYPH x GLYPH
SEED_MODEL = Path(__file__).with_name("captcha_glyphs.npz")


def mask_of(img: np.ndarray) -> np.ndarray:
    """Binary ink mask with the diagonal strike-through line erased."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    mask = (gray < 150).astype(np.uint8)
    lines = cv2.HoughLinesP(mask * 255, 1, np.pi / 180, threshold=60, minLineLength=80, maxLineGap=8)
    if lines is not None:
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
            if abs(x2 - x1) > 40 and 0.15 < abs((y2 - y1) / (x2 - x1 + 1e-6)) < 1.5:
                cv2.line(mask, (int(x1), int(y1)), (int(x2), int(y2)), 0, 2)
    return mask


@dataclass
class _Blob:
    """A glyph candidate: bounding box, ink area and the connected components it is made of."""

    x0: int
    x1: int
    y0: int
    y1: int
    area: int
    ids: list[int]

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def absorb(self, other: "_Blob") -> None:
        self.x0, self.x1 = min(self.x0, other.x0), max(self.x1, other.x1)
        self.y0, self.y1 = min(self.y0, other.y0), max(self.y1, other.y1)
        self.area += other.area
        self.ids += other.ids


def segment(mask: np.ndarray) -> list[np.ndarray]:
    """Return exactly N_CHARS glyph crops (left to right), merging/splitting as needed."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    blobs: list[_Blob] = []
    for i in sorted(range(1, n), key=lambda i: stats[i, 0]):
        x, y, w, h, area = stats[i]
        if area < 6:
            continue
        blob = _Blob(x, x + w, y, y + h, area, [i])
        for b in blobs:  # attach dots / fragments that overlap an existing glyph horizontally
            overlap = min(b.x1, blob.x1) - max(b.x0, blob.x0)
            if overlap > 0.5 * min(w, b.width):
                b.absorb(blob)
                break
        else:
            blobs.append(blob)

    while len(blobs) > N_CHARS:  # fold the smallest fragment into its nearest neighbour
        smallest = blobs.pop(min(range(len(blobs)), key=lambda j: blobs[j].area))
        min(blobs, key=lambda b: abs(b.cx - smallest.cx)).absorb(smallest)
    blobs.sort(key=lambda b: b.x0)

    crops = [np.isin(labels[b.y0 : b.y1, b.x0 : b.x1], b.ids).astype(np.uint8) for b in blobs]
    while 0 < len(crops) < N_CHARS:  # touching glyphs: split the widest at its thinnest column
        k = max(range(len(crops)), key=lambda j: crops[j].shape[1])
        c = crops[k]
        w = c.shape[1]
        cols = c.sum(0)[w // 4 : 3 * w // 4]
        cut = w // 4 + int(np.argmin(cols)) if len(cols) else w // 2
        crops[k : k + 1] = [c[:, :cut], c[:, cut:]]
    return crops


def normalise(crop: np.ndarray) -> np.ndarray:
    """Feature vector for one glyph: centred square bitmap + aspect ratio, unit length."""
    ys, xs = np.nonzero(crop)
    if len(xs):
        crop = crop[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    h, w = crop.shape
    side = max(h, w)
    sq = np.zeros((side, side), np.float32)
    sq[(side - h) // 2 : (side - h) // 2 + h, (side - w) // 2 : (side - w) // 2 + w] = crop
    v = cv2.resize(sq, (GLYPH, GLYPH), interpolation=cv2.INTER_AREA).ravel()
    aspect = np.float32(w / max(h, 1))  # 'l' vs 'm' style shape cue the square pad would lose
    v = np.append(v - v.mean(), aspect * 2)
    return v / (np.linalg.norm(v) + 1e-6)


def features(img: np.ndarray) -> list[np.ndarray]:
    return [normalise(c) for c in segment(mask_of(img))]


def decode(img: np.ndarray | bytes) -> np.ndarray:
    if isinstance(img, (bytes, bytearray)):
        return cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR)
    return img


class Solver:
    def __init__(self, learned: Path | None = None, seed: Path = SEED_MODEL):
        d = np.load(seed)
        self.X, self.y = d["X"], d["y"]
        self._n_seed = len(self.y)
        self.learned = learned
        if learned and learned.exists():
            extra = np.load(learned)
            self.X, self.y = np.vstack([self.X, extra["X"]]), np.concatenate([self.y, extra["y"]])

    def solve(self, img: np.ndarray | bytes) -> str:
        return "".join(str(self.y[int(np.argmax(self.X @ f))]) for f in features(decode(img)))

    def learn(self, img: np.ndarray | bytes, text: str) -> None:
        """Add a server-verified answer to the example set, so accuracy improves as we go."""
        fs = features(decode(img))
        if len(fs) != len(text):
            return
        self.X = np.vstack([self.X, np.array(fs)])
        self.y = np.concatenate([self.y, np.array(list(text))])
        if self.learned:
            np.savez(self.learned, X=self.X[self._n_seed :], y=self.y[self._n_seed :])


def train(labels: dict[str, str], img_dir: Path, out: Path = SEED_MODEL) -> None:
    """Build a model from hand-labelled CAPTCHAs: {"<image stem>": "<answer>"} + <stem>.jpg files."""
    X, y = [], []
    for stem, text in labels.items():
        fs = features(cv2.imread(str(img_dir / f"{stem}.jpg")))
        if len(fs) != len(text):
            log.warning("skip %s: segmented %d glyphs", stem, len(fs))
            continue
        X += fs
        y += list(text)
    np.savez(out, X=np.array(X), y=np.array(y))
    log.info("saved %d glyphs (%d classes) -> %s", len(y), len(set(y)), out)
