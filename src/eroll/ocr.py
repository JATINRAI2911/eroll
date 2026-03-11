# SPDX-License-Identifier: GPL-3.0-or-later
"""OCR roll PDFs locally with Tesseract.

Each page is rendered, its ruled voter boxes are located and each box is OCR'd on its own.
The raw text is cached per page, so this slow stage only ever runs once per page and
re-runs of the (fast) parsing stage are free.
"""

import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytesseract

log = logging.getLogger(__name__)

# Parallelism comes from worker processes; stop each Tesseract from also spawning threads.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

RENDER_SCALE = 1.0  # ECI pages are single ~240dpi JPEGs sized 1:1 with the PDF page in points
EPIC_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/"

Box = tuple[int, int, int, int]


def page_count(pdf_path: Path) -> int:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_gray(pdf_path: Path, index: int) -> np.ndarray:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        img = pdf[index].render(scale=RENDER_SCALE, grayscale=True).to_numpy()
    finally:
        pdf.close()
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def find_voter_boxes(gray: np.ndarray) -> list[Box]:
    """Locate the ruled voter boxes (normally a 3-column grid) via line morphology,
    returned in reading order."""
    H, W = gray.shape
    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (W // 25, 1)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, H // 50)))
    grid = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))
    # Voter boxes are separated by white gutters, so each one is its own outermost contour;
    # the serial-number and photo boxes nested inside are ignored.
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 0.22 * W < w < 0.42 * W and 0.04 * H < h < 0.16 * H:
            boxes.append((x, y, w, h))
    row_h = np.median([b[3] for b in boxes]) if boxes else 1
    return sorted(boxes, key=lambda b: (round(b[1] / (row_h / 2)), b[0]))


def _ocr_line(img: np.ndarray, whitelist: str) -> str:
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    img = cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    cfg = f"--psm 7 -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(img, lang="eng", config=cfg).strip()


def ocr_box(box: np.ndarray) -> str:
    """OCR one voter box. Tesseract's page segmenter drops the right-aligned EPIC in the top
    row, so it is read on its own. (The serial is not OCR'd: it is sequential within a part,
    so it is derived from reading order instead.)"""
    h, w = box.shape
    epic = _ocr_line(box[int(0.03 * h) : int(0.20 * h), int(0.55 * w) : int(0.99 * w)], EPIC_CHARS)
    body = pytesseract.image_to_string(box[int(0.20 * h) :, : int(0.74 * w)], lang="eng", config="--psm 6")
    return f"{epic}\n{body}"


def ocr_page(pdf_path: Path, index: int, cache_dir: Path) -> dict:
    """OCR one page (cached). Voter pages yield per-box text; anything else, full-page text."""
    out = cache_dir / pdf_path.stem / f"p{index + 1:03d}.json"
    if out.exists():
        return json.loads(out.read_text())
    gray = render_gray(pdf_path, index)
    boxes = find_voter_boxes(gray)
    texts = [ocr_box(gray[y + 4 : y + h - 4, x + 4 : x + w - 4]) for x, y, w, h in boxes]
    # Cover/summary pages also have ruled tables; only call it a voter page if the boxes
    # actually look like voter entries.
    if texts and sum("name" in t.lower() for t in texts) > len(texts) / 2:
        top = min(b[1] for b in boxes)
        header = pytesseract.image_to_string(gray[: max(top - 4, 1)], lang="eng", config="--psm 6")
        result = {"page": index + 1, "kind": "voters", "header": header, "boxes": texts}
    else:
        text = pytesseract.image_to_string(gray, lang="eng", config="--psm 4")
        result = {"page": index + 1, "kind": "text", "text": text}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False))
    return result


def ocr_pdfs(pdfs: list[Path], cache_dir: Path, workers: int) -> dict[Path, list[dict]]:
    """OCR every page of every PDF in parallel; returns each PDF's pages in page order."""
    jobs = [(pdf, i) for pdf in pdfs for i in range(page_count(pdf))]
    log.info("%d PDFs, %d pages, %d workers", len(pdfs), len(jobs), workers)
    results: dict[Path, list[dict]] = {pdf: [] for pdf in pdfs}
    with ProcessPoolExecutor(workers) as ex:
        futures = {ex.submit(ocr_page, pdf, i, cache_dir): pdf for pdf, i in jobs}
        for n, fut in enumerate(as_completed(futures), 1):
            results[futures[fut]].append(fut.result())
            if n % 25 == 0 or n == len(jobs):
                log.info("  OCR %d/%d pages", n, len(jobs))
    return {pdf: sorted(pages, key=lambda p: p["page"]) for pdf, pages in results.items()}
