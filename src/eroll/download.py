# SPDX-License-Identifier: GPL-3.0-or-later
"""Download every electoral-roll PDF of one Assembly Constituency from the ECI voter portal.

A real browser is driven automatically: pick the roll in the form, tick up to a page of
parts at a time, solve the CAPTCHA (retrying with a fresh one on rejection) and capture the
downloads. If the solver keeps failing, it falls back to asking a human to type the CAPTCHA
in the browser window. Progress is saved after every batch, so a run can be stopped and
resumed at any time.
"""

import base64
import json
import logging
import random
import time
import zipfile
from pathlib import Path

from playwright.sync_api import Download, Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from .captcha import Solver
from .layout import DataDir, UserError, part_from_filename

log = logging.getLogger(__name__)

PORTAL = "https://voters.eci.gov.in/download-eroll?stateCode={state}"
CDN_BASE = "https://voters.eci.gov.in/eroll"  # every accepted file is also served at CDN_BASE/<path>
PART_LIST_API = "get-publish-part-list"
GENERATE_API = "generate-published-pdfs"

CAPTCHA_IMG = 'img[alt="Captcha"]'
CAPTCHA_REFRESH = "refrsh captcha"  # sic: the portal's accessible name
PART_CELLS = "tbody tr td:nth-child(2)"

MAX_AUTO_TRIES = 15  # solver attempts per batch before asking the human
MAX_MISSES = 3  # batches a part may come back missing from before it is given up on
DOWNLOAD_TIMEOUT = 180  # seconds to wait for a batch's files once the server accepts
MANUAL_TIMEOUT = 15 * 60
PAUSE_BETWEEN_BATCHES = (3, 8)  # seconds, randomised, to go easy on the portal


# ------------------------------------------------------------------ helpers


def parse_parts_spec(spec: str | None, all_parts: list[int]) -> list[int]:
    """Filter `all_parts` by a spec like "1-20,45"; None or "" keeps everything."""
    if not spec:
        return all_parts
    wanted = set()
    for chunk in spec.split(","):
        lo, _, hi = chunk.partition("-")
        wanted.update(range(int(lo), int(hi or lo) + 1))
    return [p for p in all_parts if p in wanted]


def unpack(path: Path) -> list[Path]:
    """The portal sends multi-part batches as a zip: extract its PDFs next to it."""
    if path.suffix.lower() != ".zip":
        return [path]
    with zipfile.ZipFile(path) as z:
        z.extractall(path.parent)
        pdfs = [path.parent / n for n in z.namelist() if n.lower().endswith(".pdf")]
    path.unlink()
    return pdfs


class Inbox:
    """Collects the PDFs the browser downloads into one directory."""

    def __init__(self, dest: Path):
        self.dest = dest
        self.files: list[Path] = []

    def on_download(self, download: Download) -> None:
        path = self.dest / download.suggested_filename
        download.save_as(path)
        self.files.extend(unpack(path))

    def add(self, name: str, body: bytes) -> None:
        (self.dest / name).write_bytes(body)
        self.files.append(self.dest / name)

    def wait(self, page: Page, expected: int, timeout: float) -> None:
        """Return once `expected` files arrived, or none has for 20s, or `timeout` passed."""
        start = last_change = time.time()
        last = 0
        while time.time() - start < timeout:
            page.wait_for_timeout(1000)
            if len(self.files) != last:
                last, last_change = len(self.files), time.time()
            if self.files and (len(self.files) >= expected or time.time() - last_change > 20):
                return


# --------------------------------------------------------- portal interaction


def select_when_ready(page: Page, label: str, option: str) -> None:
    """Select `option` in the dropdown labelled `label` once the portal has populated it."""
    page.wait_for_function(
        """([l, o]) => { const s = document.querySelector(`select[aria-label="${l}"]`);
             return s && [...s.options].some(x => x.text.trim() === o); }""",
        arg=[label, option],
        timeout=60_000,
    )
    page.get_by_label(label).select_option(label=option)


def visible_part_numbers(page: Page) -> list[int]:
    cells = page.locator(PART_CELLS).all_inner_texts()
    return [int(c.split("-", 1)[0].strip()) for c in cells if c.strip()[:1].isdigit()]


def goto_page_containing(page: Page, part_no: int) -> None:
    """Page the parts table forwards or backwards until `part_no` is on screen."""
    for _ in range(60):
        nums = visible_part_numbers(page)
        if nums and nums[0] <= part_no <= nums[-1]:
            return
        direction = ">" if not nums or part_no > nums[-1] else "<"
        prev_first = page.locator(PART_CELLS).first.inner_text()
        page.get_by_role("button", name=direction, exact=True).click()
        page.wait_for_function(
            "([sel, prev]) => { const c = document.querySelector(sel); return c && c.innerText !== prev; }",
            arg=[PART_CELLS, prev_first],
            timeout=15_000,
        )
    raise RuntimeError(f"could not page to part {part_no}")


def set_checkboxes(page: Page, wanted: set[int]) -> None:
    for row in page.locator("tbody tr").all():
        num = int(row.locator("td:nth-child(2)").inner_text().split("-", 1)[0].strip())
        box = row.locator("input[type=checkbox]")
        if num in wanted:
            box.check()
        elif box.is_checked():
            box.uncheck()


def captcha_src(page: Page) -> str:
    return page.locator(CAPTCHA_IMG).get_attribute("src") or ""


def fresh_captcha(page: Page, used: str | None) -> str:
    """Return the src of a CAPTCHA image we have not already submitted."""
    src = captcha_src(page)
    if src and src != used:
        return src
    page.get_by_role("img", name=CAPTCHA_REFRESH).click()
    page.wait_for_function(
        "([sel, u]) => { const s = document.querySelector(sel)?.src; return s && s !== u; }",
        arg=[CAPTCHA_IMG, used or ""],
        timeout=15_000,
    )
    return captcha_src(page)


def captcha_jpeg(src: str) -> bytes:
    return base64.b64decode(src.split(",", 1)[1])  # the src is an inline data: URL


def fetch_stragglers(page: Page, paths: list[str], inbox: Inbox) -> None:
    """The portal's own per-file fetch from its CDN sometimes fails silently, so fetch any
    accepted file that has not arrived directly."""
    have = {f.name for f in inbox.files}
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        if name in have:
            continue
        for _ in range(3):
            try:
                resp = page.request.get(f"{CDN_BASE}/{path}", timeout=120_000)
                if resp.ok and resp.body()[:4] == b"%PDF":
                    inbox.add(name, resp.body())
                    log.info("    fetched %s directly", name)
                    break
            except PWTimeout:
                pass
            page.wait_for_timeout(5000)


def prepare_batch(page: Page, parts: list[int]) -> None:
    goto_page_containing(page, parts[0])
    set_checkboxes(page, set(parts))  # the portal may clear ticks after a failed submit


def auto_batch(page: Page, solver: Solver, parts: list[int], inbox: Inbox) -> bool:
    """Download `parts` (all on the current table page), solving the CAPTCHA ourselves."""
    used = None
    for attempt in range(1, MAX_AUTO_TRIES + 1):
        prepare_batch(page, parts)
        used = fresh_captcha(page, used)
        jpg = captcha_jpeg(used)
        answer = solver.solve(jpg)
        page.get_by_placeholder("Enter Captcha").fill(answer)
        try:
            with page.expect_response(lambda r: GENERATE_API in r.url, timeout=20_000) as info:
                page.get_by_role("button", name="Download Selected PDFs").click()
            body = info.value.json()
        except (PWTimeout, ValueError):
            body = {}
        files = body.get("payload") if body.get("status") == "Success" else None
        if not files:
            log.info("    try %d: '%s' rejected (%s)", attempt, answer, body.get("message") or "no response")
            page.wait_for_timeout(random.randint(1500, 3000))
            continue
        log.info("    try %d: '%s' accepted, %d file(s) coming", attempt, answer, len(files))
        solver.learn(jpg, answer)
        inbox.wait(page, len(files), DOWNLOAD_TIMEOUT)
        fetch_stragglers(page, files, inbox)
        return True
    return False


def manual_batch(page: Page, parts: list[int], inbox: Inbox) -> None:
    prepare_batch(page, parts)
    page.get_by_placeholder("Enter Captcha").fill("")
    page.bring_to_front()
    log.warning("    solver gave up: type the CAPTCHA in the browser and click 'Download Selected PDFs'")
    inbox.wait(page, len(parts), MANUAL_TIMEOUT)


def open_roll(page: Page, state: str, district: str, constituency: str, language: str) -> list[dict]:
    """Fill in the roll form and return the portal's part list for it."""
    part_list: list[dict] = []

    def capture_part_list(resp) -> None:
        if not part_list and PART_LIST_API in resp.url and resp.ok:
            part_list.extend(resp.json()["payload"])

    page.on("response", capture_part_list)
    page.goto(PORTAL.format(state=state))
    select_when_ready(page, "District", district)
    select_when_ready(page, "Assembly Constituency", constituency)
    select_when_ready(page, "Select Language", language)
    page.wait_for_selector("tbody tr input[type=checkbox]")
    page.wait_for_timeout(1500)
    page.remove_listener("response", capture_part_list)
    if not part_list:
        raise RuntimeError("portal did not return a part list")
    return part_list


# ---------------------------------------------------------------------- entry


def _load_json(path: Path, default: dict) -> dict:
    return json.loads(path.read_text()) if path.exists() else default


def _on_dialog(dialog) -> None:
    log.info("    [portal] %s", dialog.message)
    dialog.accept()


def download(
    data: DataDir,
    state: str,
    district: str,
    constituency: str,
    language: str = "ENGLISH",
    parts: str | None = None,
    headless: bool = False,
) -> list[int]:
    """Download the roll into `data`, resuming where a previous run stopped.
    Returns the part numbers that could not be downloaded."""
    roll = {"state": state, "district": district, "constituency": constituency, "language": language}
    existing = _load_json(data.roll, {})
    if existing and any(existing.get(k) != v for k, v in roll.items()):
        raise UserError(
            f"{data.root} already holds the roll for {existing.get('constituency')} ({existing.get('language')}); "
            "use a different --data-dir"
        )
    data.pdfs.mkdir(parents=True, exist_ok=True)
    progress = _load_json(data.state, {"done": {}})
    done: dict[str, str] = progress["done"]
    solver = Solver(learned=data.learned_glyphs)
    inbox = Inbox(data.pdfs)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 1000})
        page = ctx.new_page()
        page.on("download", inbox.on_download)
        page.on("dialog", _on_dialog)

        part_list = open_roll(page, state, district, constituency, language)
        data.roll.write_text(json.dumps({**roll, "parts": part_list}, indent=2))
        all_parts = sorted(x["partNumber"] for x in part_list)
        # Files already on disk count as done too (e.g. from an interrupted run).
        for f in data.pdfs.glob("*.pdf"):
            if (n := part_from_filename(f.name)) is not None:
                done.setdefault(str(n), f.name)
        todo = [n for n in parse_parts_spec(parts, all_parts) if str(n) not in done]
        log.info("%d parts in constituency, %d still to download", len(all_parts), len(todo))

        misses: dict[int, int] = {}
        failed: list[int] = []
        while todo:
            goto_page_containing(page, todo[0])
            on_page = set(visible_part_numbers(page))
            batch = [n for n in todo if n in on_page]
            log.info("Parts %d-%d (%d parts, %d left)", batch[0], batch[-1], len(batch), len(todo))

            inbox.files.clear()
            if not auto_batch(page, solver, batch, inbox):
                manual_batch(page, batch, inbox)

            got = {n: f.name for f in inbox.files if (n := part_from_filename(f.name)) is not None}
            done.update({str(n): name for n, name in got.items()})
            data.state.write_text(json.dumps(progress, indent=2))

            missing = [n for n in batch if n not in got]
            for n in missing:
                misses[n] = misses.get(n, 0) + 1
            given_up = [n for n in missing if misses[n] >= MAX_MISSES]
            log.info(
                "  ✓ %d downloaded%s%s",
                len(got),
                f", missing {missing}" if missing else "",
                f", giving up on {given_up}" if given_up else "",
            )
            todo = [n for n in todo if n not in got and n not in given_up]
            failed += given_up
            page.wait_for_timeout(random.randint(*PAUSE_BETWEEN_BATCHES) * 1000)

        browser.close()
    log.info("Done. PDFs in %s%s", data.pdfs, f"; FAILED parts: {failed}" if failed else "")
    return failed


def collect_captchas(out_dir: Path, n: int, state: str, headless: bool = False) -> None:
    """Save `n` CAPTCHA images from the portal as training material for `captcha.train`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(PORTAL.format(state=state))
        page.locator(CAPTCHA_IMG).wait_for()
        used = None
        for i in range(n):
            used = fresh_captcha(page, used)
            (out_dir / f"c{i:03d}.jpg").write_bytes(captcha_jpeg(used))
            page.wait_for_timeout(700)
        browser.close()
    log.info('saved %d CAPTCHAs to %s; label them in a JSON file {"c000": "ab12cd", ...}', n, out_dir)
