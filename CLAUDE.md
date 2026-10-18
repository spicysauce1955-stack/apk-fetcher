# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the scraper

The project ships with `run.sh`, which activates `./venv/`, defaults `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to `/usr/bin/google-chrome`, and forwards args to `main.py`. Prefer it over invoking Python directly:

```bash
./run.sh --app telegram --version 12.4.0          # single version
./run.sh --app telegram --version 12.4.0 --all-sources   # every variant from every source (cannot combine with --source)
./run.sh --bulk versions.json --app telegram      # iterate versions from a JSON list
./run.sh --no-headless --debug                    # visible browser + debug logs
```

Equivalent direct invocation: `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome ./venv/bin/python main.py ...`.

Override the browser by exporting `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` before calling `run.sh`. `BaseScraper._get_browser` reads this env var and falls back to Playwright's bundled Chromium when unset.

Run local tests with `./venv/bin/python -m unittest discover -s tests -v`. No linter or build step is configured. Dependencies live in `requirements.txt` (playwright, pyyaml, beautifulsoup4, lxml).

## Architecture

The system is a three-layer pipeline driven by `main.py`:

1. **Config layer** (`src/config.py`) parses `config.yaml` into `Config → AppConfig → per-source dicts`. Each app declares one block per source under `sources:`, and the keys inside that block are source-specific (e.g. APKMirror needs `developer`/`app_name`, Uptodown needs `app_slug`, APKPure needs `package`).

2. **Orchestration layer** (`main.py`) owns source priority, fallback, and bulk loops. The priority order `["apkmirror", "uptodown", "apkpure"]` is defined in **two places** — `main.py:32` and `src/__init__.py:33`; keep them in sync when reordering. `fetch_apk` has two modes:
   - Default: walks `SOURCE_PRIORITY`, returns on the first source whose `ScrapeResult.succeeded` (at least one variant downloaded or already on disk).
   - `--all-sources`: walks every configured source.

   Every source downloads every variant it lists. Dedup happens per variant inside `BaseScraper.scrape` via `find_existing_variant` — it looks for a `{app}-{version}-{source}-{variant_id}-` prefix with a known Android package extension (`.apk`, `.xapk`, `.apkm`, `.apks`, `.aab`) or `.download` in `downloads/{app}/`. There is no version-level skip: every run loads each version page, since new variants may appear. Legacy `{app}-{version}-{source}.{ext}` files are not recognised. There is no manifest or hash check; deleting a file is enough to retrigger a download.

3. **Scraper layer** (`src/base.py` + one module per source). Every scraper subclasses `BaseScraper`, sets `SOURCE_NAME` (used in the output filename), and implements three hooks: `_app_name(config)` (validate config, return the folder/file name), `_list_variants(page, config, version) -> List[VariantRef]` and `_download_variant(page, app_name, version, variant) -> APKInfo`. `BaseScraper.scrape(config, version, force=False) -> ScrapeResult` is the shared template: it owns the Playwright lifecycle (`_get_browser` / `_close_browser`), normalises variant IDs (`normalize_variant_ids`: `-` → `_`, duplicates get `_2`, `_3`…), skips variants already on disk, downloads the rest one by one, and records per-variant failures without stopping. The base class also owns the output-path convention: `_save_downloaded_file` / `_save_url_file` write `{output_dir}/{app_name}/{app_name}-{version}-{SOURCE_NAME}-{variant_id}-{arch}.{detected_extension}`, where `{arch}` is `arch_label(detect_architectures(file))` (`universal`, `unknown`, or ABIs joined with `+`). ABI detection lives in the module-level `detect_architectures` in `src/base.py` (reuses `classify_package_zip`), is stored on `APKInfo.architectures`, and is logged per-variant by `main.py`.

### Exception contract

Scrapers communicate outcomes through three exceptions defined in `src/base.py`:

- `VersionNotFoundError` — version doesn't exist on this source; orchestrator continues to next source.
- `DownloadError` — found but couldn't download (timeout, missing button); orchestrator continues to next source.
- `ScraperError` (base) / unexpected `Exception` — logged louder but still falls through to the next source.

`fetch_apk` catches all of these per-source so one broken scraper never aborts the run. Inside a source, `BaseScraper.scrape` catches each variant's exception, records it in `ScrapeResult.failed`, and moves on; it raises `VersionNotFoundError` when `_list_variants` returns nothing and `DownloadError` only when every variant failed. If listing fails (blocked page, markup change, empty list) but files for that version and source are already on disk (including pre-variant `{app}-{version}-{source}.{ext}` names), it logs a warning and returns them as `existing` instead of raising, so fallback does not re-download the version from the next source; `--force` disables this. A source counts as successful when at least one variant was downloaded or already on disk. When adding a new source, raise these exceptions rather than returning `None` or an empty result so the fallback logic stays meaningful.

### Adding a new source

1. Create `src/<source>.py` with a `BaseScraper` subclass, setting `SOURCE_NAME` to the slug used in filenames.
2. Export it from `src/__init__.py` and add it to both `SCRAPERS` dicts (`main.py` and `src/__init__.py`) plus `SOURCE_PRIORITY`.
3. Implement `_app_name`, `_list_variants` and `_download_variant`. Keep page parsing in a module-level `parse_<source>_variants(html, ...)` function over `page.content()` so it can be unit-tested offline with BeautifulSoup (see `tests/test_source_parsers.py`). Use the site's own stable ID for `VariantRef.variant_id` — it is part of the filename and the skip check.
4. Document the config keys it expects in its `_list_variants` docstring; the orchestrator passes the raw dict from `config.yaml`.

### Per-source quirks worth knowing

- **APKMirror** constructs the version URL deterministically from `developer`/`app_name`/version slug, then parses every row of the variants table (`parse_apkmirror_variants`) and walks each variant → download page → final link. Variant IDs come from the URL slug: no number → `1`, `-2-android-apk-download` → `2`. `variant_index` is obsolete and only triggers a warning.
- **Uptodown** has no deterministic per-version URL — it scrapes the versions index page (`parse_uptodown_variants`). A version is listed once per variant; every `[data-version-id]` row whose `.version` text equals the requested version exactly is a variant, and its `data-version-id` is both the variant ID and the `/android/download/<id>` page. Only the first page of the list is searched. UI changes there are the most common breakage.
- **APKPure** uses the mobile domain `m.apkpure.com` because the desktop site is heavily Cloudflare-gated. The versions page links to desktop `apkpure.com/.../download/<version>` pages, whose "All Variants" list (`parse_apkpure_variants`) yields one `d.apkpure.com/b/{APK|XAPK}/<package>?versionCode=N` link per variant; `versionCode` is the variant ID, and links for other packages (ads) are filtered out. Downloads still frequently fail in headless mode; README explicitly calls this out as a known limitation.
