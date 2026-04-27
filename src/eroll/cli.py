# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line entry point: ``eroll <command>``. See README.md for a walkthrough."""

import argparse
import json
import logging
import os
from pathlib import Path

from .layout import DataDir, UserError


def cmd_download(args: argparse.Namespace, data: DataDir) -> None:
    from .download import download

    failed = download(data, args.state, args.district, args.ac, args.language, args.parts, args.headless)
    if failed:
        raise SystemExit(1)


def cmd_ocr(args: argparse.Namespace, data: DataDir) -> None:
    from .ocr import ocr_pdfs
    from .parse import parse_part
    from .report import index_markdown, part_filename, part_markdown, write_csv

    pdfs = sorted(data.pdfs.glob("*.pdf"))[: args.limit]
    if not pdfs:
        raise UserError(f"no PDFs in {data.pdfs}; run `eroll download` first")
    pages = ocr_pdfs(pdfs, data.ocr_cache, args.workers)

    roll = json.loads(data.roll.read_text()) if data.roll.exists() else {}
    constituency = roll.get("constituency", "unknown")
    part_names = {p["partNumber"]: p["partName"] for p in roll.get("parts", [])}

    data.markdown.mkdir(parents=True, exist_ok=True)
    rows = []
    for pdf in pdfs:
        part = parse_part(pdf.name, pages[pdf])
        name = part_names.get(part.number, "") if part.number else ""
        (data.markdown / part_filename(part, pdf.stem)).write_text(part_markdown(part, pdf.name, name, constituency))
        rows.append((part, pdf))
    (data.markdown / "index.md").write_text(index_markdown(rows, part_names, constituency))
    voters = [v for part, _ in rows for v in part.voters]
    write_csv(data.voters_csv, voters)

    log = logging.getLogger("eroll")
    log.info("Wrote %d part files, %d voters → %s/ and %s", len(rows), len(voters), data.markdown, data.voters_csv)
    bad = [part for part, _ in sorted(rows, key=lambda r: r[0].number or 0) if part.check != "ok"]
    log.info("Summary check: %d/%d parts ok", len(rows) - len(bad), len(rows))
    for part in bad:
        log.info("  part %s: %s", part.number, part.check)


def cmd_search(args: argparse.Namespace, data: DataDir) -> None:
    from .search import search

    seen_file: Path | None = args.new_only
    seen = set(json.loads(seen_file.read_text())) if seen_file and seen_file.exists() else set()
    hits = search(data.ocr_cache, args.queries, args.threshold)
    for h in hits:
        if h.key not in seen:
            print(h, flush=True)
    if seen_file:
        seen_file.write_text(json.dumps(sorted(seen | {h.key for h in hits})))
    else:
        n_parts = len({p.parent for p in data.ocr_cache.glob("*/p*.json")})
        print(f"{len(hits)} hit(s) across {n_parts} OCR'd parts")


def cmd_captcha_collect(args: argparse.Namespace, data: DataDir) -> None:
    from .download import collect_captchas

    collect_captchas(args.out, args.n, args.state, args.headless)


def cmd_captcha_train(args: argparse.Namespace, data: DataDir) -> None:
    from .captcha import SEED_MODEL, train

    labels = json.loads(args.labels.read_text())
    train(labels, args.images or args.labels.parent, args.out or SEED_MODEL)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="eroll", description="Download, OCR and search ECI electoral rolls.")
    ap.add_argument("--data-dir", type=Path, default=Path("data"), help="working directory (default: ./data)")
    ap.add_argument("-q", "--quiet", action="store_true", help="only log warnings")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="download a constituency's roll PDFs from the ECI portal")
    p.add_argument("--state", required=True, help="portal state code, e.g. U05 for Delhi")
    p.add_argument("--district", required=True, help='as shown in the portal, e.g. "New Delhi"')
    p.add_argument("--ac", required=True, help='assembly constituency as shown, e.g. "40 - NEW DELHI"')
    p.add_argument("--language", default="ENGLISH", help="roll language (default: ENGLISH)")
    p.add_argument("--parts", help='subset of part numbers, e.g. "1-20,45"')
    p.add_argument("--headless", action="store_true", help="hide the browser (often blocked by the portal)")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("ocr", help="OCR downloaded PDFs into markdown and CSV")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--limit", type=int, help="only process the first N PDFs")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("search", help="fuzzy-search voter names in the OCR cache")
    p.add_argument("queries", nargs="+", metavar="NAME")
    p.add_argument("--threshold", type=float, default=0.8, help="minimum similarity, 0-1 (default: 0.8)")
    p.add_argument("--new-only", type=Path, metavar="FILE", help="JSON file remembering hits already shown")
    p.set_defaults(func=cmd_search)

    captcha = sub.add_parser("captcha", help="build the CAPTCHA solver's model").add_subparsers(required=True)
    p = captcha.add_parser("collect", help="save sample CAPTCHA images from the portal for labelling")
    p.add_argument("out", type=Path, help="directory to save cNNN.jpg images to")
    p.add_argument("-n", type=int, default=30, help="how many to save (default: 30)")
    p.add_argument("--state", default="U05", help="portal state code (default: U05)")
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_captcha_collect)

    p = captcha.add_parser("train", help="rebuild the bundled glyph model from labelled CAPTCHAs")
    p.add_argument("labels", type=Path, help='JSON {"<image stem>": "<answer>", ...}')
    p.add_argument("--images", type=Path, help="directory of <stem>.jpg images (default: next to labels)")
    p.add_argument("--out", type=Path, help="model file to write (default: the bundled seed model)")
    p.set_defaults(func=cmd_captcha_train)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        args.func(args, DataDir(args.data_dir))
    except UserError as e:
        raise SystemExit(f"eroll: {e}") from None
