# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the scraper

The project ships with `run.sh`, which activates `./venv/`, defaults `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to `/usr/bin/google-chrome`, and forwards args to `main.py`. Prefer it over invoking Python directly:

```bash
./run.sh --app telegram --version 12.4.0          # single version
./run.sh --app telegram --version 12.4.0 --all-sources   # one package per source (cannot combine with --source)
./run.sh --bulk versions.json --app telegram      # iterate versions from a JSON list
./run.sh --no-headless --debug                    # visible browser + debug logs
```

Equivalent direct invocation: `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome ./venv/bin/python main.py ...`.

Override the browser by exporting `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` before calling `run.sh`. `BaseScraper._get_browser` reads this env var and falls back to Playwright's bundled Chromium when unset.

Run local tests with `./venv/bin/python -m unittest discover -s tests -v`. No linter or build step is configured. Dependencies live in `requirements.txt` (playwright, pyyaml, beautifulsoup4, lxml).

## Architecture

The system is a three-layer pipeline driven by `main.py`:

1. **Config layer** (`src/config.py`) parses `config.yaml` into `Config → AppConfig → per-source dicts`. Each app declares one block per source under `sources:`, and the keys inside that block are source-specific (e.g. APKMirror needs `developer`/`app_name`, Uptodown needs `app_slug`, APKPure needs `package`).

2. **Orchestration layer** (`main.py`) owns source priority, fallback, dedup, and bulk loops. The priority order `["apkmirror", "uptodown", "apkpure"]` is defined in **two places** — `main.py:32` and `src/__init__.py:33`; keep them in sync when reordering. `fetch_apk` has two modes:
   - Default: walks `SOURCE_PRIORITY`, returns on first success.
   - `--all-sources`: walks every configured source, collecting one package per source. Dedup checks become per-source.

   Dedup is filename-based via `is_already_downloaded` — it looks for `{app}-{version}-{source}` with a known Android package extension (`.apk`, `.xapk`, `.apkm`, `.apks`, `.aab`) or `.download` in `downloads/{app}/`. There is no manifest or hash check; deleting a file is enough to retrigger a download.

3. **Scraper layer** (`src/base.py` + one module per source). Every scraper subclasses `BaseScraper`, sets `SOURCE_NAME` (used in the output filename), and implements `scrape(config, version) -> Optional[APKInfo]`. The base class owns Playwright lifecycle (`_get_browser` / `_close_browser`) and the output-path convention (`_save_downloaded_file` writes to `{output_dir}/{app_name}/{app_name}-{version}-{SOURCE_NAME}.{detected_extension}` after inspecting the downloaded package). ABI detection lives in the module-level `detect_architectures` in `src/base.py` (reuses `classify_package_zip`), is stored on `APKInfo.architectures`, and is logged per-source by `main.py`.

### Exception contract

Scrapers communicate outcomes through three exceptions defined in `src/base.py`:

- `VersionNotFoundError` — version doesn't exist on this source; orchestrator continues to next source.
- `DownloadError` — found but couldn't download (timeout, missing button); orchestrator continues to next source.
- `ScraperError` (base) / unexpected `Exception` — logged louder but still falls through to the next source.

`fetch_apk` catches all of these per-source so one broken scraper never aborts the run. When adding a new source, raise these exceptions rather than returning `None` so the fallback logic stays meaningful.

### Adding a new source

1. Create `src/<source>.py` with a `BaseScraper` subclass, setting `SOURCE_NAME` to the slug used in filenames.
2. Export it from `src/__init__.py` and add it to both `SCRAPERS` dicts (`main.py` and `src/__init__.py`) plus `SOURCE_PRIORITY`.
3. Document the config keys it expects in its `scrape` docstring; the orchestrator passes the raw dict from `config.yaml`.

### Per-source quirks worth knowing

- **APKMirror** constructs the version URL deterministically from `developer`/`app_name`/version slug, then walks variant → download page → final link. `variant_index` in config picks among multiple APK variants (0 = first, usually universal).
- **Uptodown** has no deterministic per-version URL — it scrapes the versions index page and matches the requested version via `data-version-id`, `data-url`, then a generic href fallback. UI changes there are the most common breakage.
- **APKPure** uses the mobile domain `m.apkpure.com` because the desktop site is heavily Cloudflare-gated. Downloads still frequently fail in headless mode; README explicitly calls this out as a known limitation.
