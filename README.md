# eroll

Download a full Assembly Constituency's electoral roll from the
[ECI voter portal](https://voters.eci.gov.in/download-eroll), OCR it locally, and search it.

The portal serves rolls as image-only PDFs, a few parts at a time, behind a CAPTCHA. `eroll`
automates that end to end:

1. **`eroll download`** drives a real browser through the portal form, solves the CAPTCHA with a
   small built-in solver (falling back to you if it keeps missing), and saves every part's PDF.
   It is resumable: stop it at any time and run it again.
2. **`eroll ocr`** finds the voter boxes on each page and reads them with Tesseract, then writes
   one Markdown file per part, an index, and a single `voters.csv`. Each part is checked against
   the male/female/total counts printed on its own summary page.
3. **`eroll search`** fuzzy-matches names across the roll, tolerating OCR slips. It reads the OCR
   cache directly, so it works while step 2 is still running.

> [!IMPORTANT]
> Electoral rolls contain personal data. Use this for legitimate purposes such as checking your
> own or your family's registration, and respect the portal's terms of use. Do not republish
> the downloaded PDFs or their extracted contents. The downloader deliberately works through
> one batch at a time with pauses in between. Please keep it that way.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- [Tesseract](https://github.com/tesseract-ocr/tesseract) with English data
  (`brew install tesseract` / `apt install tesseract-ocr`)
- Chromium for Playwright

```sh
uv sync
uv run playwright install chromium
```

## Usage

```sh
# 1. Download all parts of a constituency (the values are exactly as shown in the portal's dropdowns)
uv run eroll download --state U05 --district "New Delhi" --ac "40 - NEW DELHI"
uv run eroll download --state U05 --district "New Delhi" --ac "40 - NEW DELHI" --parts 1-20,45

# 2. OCR everything downloaded so far (slow the first time, cached afterwards)
uv run eroll ocr
uv run eroll ocr --limit 1        # just the first PDF, handy when tuning

# 3. Search
uv run eroll search "ASHA DEVI" "RAVI KUMAR"
uv run eroll search "ASHA DEVI" --new-only seen.json   # only hits not shown before
```

Leave the browser visible: the portal's firewall often rejects headless Chromium, and the
visible window is where you type the CAPTCHA if the solver gives up.

State codes are the `stateCode` in the portal URL after you pick a state (`U05` is Delhi).
Each working directory holds one roll. Use `--data-dir` to keep several apart:

```sh
uv run eroll --data-dir rolls/ac40 download ...
uv run eroll --data-dir rolls/ac40 ocr
```

### Output

Everything lives under `--data-dir` (default `./data`, git-ignored):

| Path | Contents |
|---|---|
| `pdfs/` | the roll PDFs as served by the portal |
| `roll.json` | which roll this is, and the portal's list of parts |
| `state.json` | download progress, used to resume |
| `ocr_cache/<pdf>/pNNN.json` | raw Tesseract output per page |
| `markdown/index.md`, `markdown/part-NNN.md` | the roll as Markdown tables, one section per locality |
| `voters.csv` | every voter: part, section, serial, EPIC, name, relation, relative, house, age, gender, deleted, page |
| `captcha_learned.npz` | CAPTCHA glyphs learned during downloads |

## Rebuilding the CAPTCHA model

The solver ships with a model trained on labelled portal CAPTCHAs. To rebuild it:

```sh
uv run eroll captcha collect samples/ -n 60   # then write samples/labels.json: {"c000": "ab12cd", ...}
uv run eroll captcha train samples/labels.json
```

## How it works

See [DESIGN.md](DESIGN.md) for the pipeline, the CAPTCHA solver, box detection and parsing,
and the reasoning behind them.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Only English rolls have been tested. The box detection and field regexes assume the English layout.

## License

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version. See [LICENSE](LICENSE).
