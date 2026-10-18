# Download All Variants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every source downloads every variant it lists for a version (e.g. APKMirror's `.apk` and `.apkm` for Telegram 13.0.3), each saved under a distinct, self-describing filename.

**Architecture:** `BaseScraper.scrape` becomes a concrete template method: it opens the browser, asks the subclass for the version's variants (`_list_variants`), skips variants already on disk, downloads the rest one by one (`_download_variant`), and returns a `ScrapeResult`. Each subclass parses its variant list from `page.content()` with a pure, unit-testable BeautifulSoup function. Dedup moves from `main.py` into the base loop and works per variant; the version-level "anything exists → skip" checks in `main.py` are removed.

**Tech Stack:** Python 3, Playwright (sync), BeautifulSoup + lxml (already in `requirements.txt`), `unittest`.

**Spec:** Agreed in chat on 2026-10-18; every decision is copied into Global Constraints below.

## Global Constraints

- Applies to all three sources, on every run. No flag. `variant_index` is removed (APKMirror logs a warning if it is still present in config).
- Fallback is unchanged: default mode stops at the first source that succeeds, and `--all-sources` tries every source. A source succeeds if at least one variant was downloaded or was already on disk.
- One variant failing must not stop the others. Failed variants are logged with their ID and the reason. If every variant fails, the scraper raises `DownloadError`.
- Filename: `{app}-{version}-{source}-{variant_id}-{arch}.{ext}`, e.g. `telegram-13.0.3-apkmirror-1-universal.apk`, `telegram-13.0.3-apkmirror-2-universal.apkm`, `telegram-12.10.0-apkpure-70242-arm64-v8a.xapk`.
- `variant_id` is the site's own ID. For APKMirror it is the number in the variant URL: no suffix → `1`, `-2-android-apk-download` → `2`. For Uptodown it is `data-version-id`. For APKPure it is the `versionCode`. IDs never contain `-` (base replaces `-` with `_`). Duplicate IDs in one listing get `_2`, `_3`, … appended in listing order.
- `{arch}` comes from `detect_architectures` on the downloaded file: `universal` when it returns `["universal"]` or a superset of `{arm64-v8a, armeabi-v7a, x86, x86_64}`; `unknown` when it returns `[]`; otherwise the sorted ABIs joined with `+` (e.g. `arm64-v8a+armeabi-v7a`).
- Reruns load the version page and skip each variant whose `{app}-{version}-{source}-{variant_id}-` file exists (any package extension or `.download`). `--force` re-downloads and replaces that variant's file even if its arch label changed.
- Old-style files (`telegram-13.0.3-apkmirror.apkm`) are left alone and are not treated as existing variants.
- Out of scope: APKPure's Cloudflare block in headless mode, and Uptodown versions-list pagination.
- No new dependencies. Tests run with `./venv/bin/python -m unittest discover -s tests -v`.
- Do not commit unless the user asks. `run.sh`, `docs/usage.md` and `tests/test_package_handling.py` are untracked user files. Edit them in place.

## Review Focus

- Variant ID `1` must not match files for IDs `12` or `1_2` → `find_existing_variant` test (Task 1).
- A legacy `{app}-{version}-{source}.apkm` file must not block the new variant downloads → `find_existing_variant` test (Task 1).
- Uptodown version `13.0.3` must not match a `13.0.31` row → parser test (Task 4).
- APKPure pages carry ad download links for other packages, and those must never be downloaded → parser test (Task 5).
- `--force` must not leave a stale file behind when a variant's arch label changes → finalize test (Task 1).

---

### Task 1: Variant naming and lookup in `src/base.py`

**Files:**
- Modify: `src/base.py`
- Modify: `tests/test_package_handling.py` (delete `ExistingDownloadTests` and the `from main import is_already_downloaded` import; those tests move to `test_variants.py` as variant lookups)
- Test: `tests/test_variants.py` (create)

**Interfaces:**
- Produces (module-level in `src/base.py`):
  - `MAIN_ABIS = frozenset({"arm64-v8a", "armeabi-v7a", "x86", "x86_64"})`
  - `arch_label(abis: list) -> str`
  - `variant_file_prefix(app_name: str, version: str, source: str, variant_id: str) -> str` returns `f"{app_name}-{version}-{source}-{variant_id}-"`
  - `find_existing_variant(output_dir: str, app_name: str, version: str, source: str, variant_id: str) -> Optional[str]`
  - `APKInfo.variant_id: str = ""` (new field, last)
  - `BaseScraper._finalize_downloaded_file(temp_path, app_name, version, variant_id, suggested_filename="") -> APKInfo`
  - `BaseScraper._save_downloaded_file(download, app_name, version, variant_id) -> APKInfo`
  - `BaseScraper._save_url_file(url, app_name, version, variant_id, referer="", timeout=180) -> APKInfo`

- [ ] **Step 1: Write failing tests** in `tests/test_variants.py`:
  - `ArchLabelTests`: `arch_label(["universal"]) == "universal"`; `arch_label(["arm64-v8a","armeabi-v7a","x86","x86_64"]) == "universal"`; `arch_label(["arm64-v8a","armeabi","armeabi-v7a","x86","x86_64"]) == "universal"`; `arch_label(["arm64-v8a"]) == "arm64-v8a"`; `arch_label(["armeabi-v7a","arm64-v8a"]) == "arm64-v8a+armeabi-v7a"`; `arch_label([]) == "unknown"`.
  - `FindExistingVariantTests` (temp dir with a `telegram/` folder):
    - `test_matches_variant_with_any_package_extension`: `telegram-13.0.3-apkmirror-2-universal.apkm` is found for id `"2"`.
    - `test_matches_download_extension`: `telegram-13.0.3-apkpure-70242-unknown.download` is found for id `"70242"`.
    - `test_id_is_not_a_prefix_match`: only `telegram-13.0.3-apkmirror-12-universal.apk` and `telegram-13.0.3-apkmirror-1_2-universal.apk` exist, so lookup for `"1"` returns `None`.
    - `test_ignores_legacy_unversioned_file`: only `telegram-13.0.3-apkmirror.apkm` exists, so lookup for `"1"` returns `None`.
    - `test_ignores_other_source_and_temp_files`: `telegram-13.0.3-uptodown-1-universal.apk` and `.telegram-13.0.3-apkmirror-1-x.apk` exist, so the apkmirror lookup for `"1"` returns `None`.
    - `test_missing_app_dir_returns_none`.
  - `FinalizeVariantTests` (use a `ProbeScraper` with `SOURCE_NAME = "probe"` and the `_write_zip` style helper from `test_package_handling.py`):
    - `test_names_file_with_variant_and_arch`: zip with `AndroidManifest.xml` + `lib/arm64-v8a/a.so` → returned `APKInfo.filepath` basename is `telegram-13.0.3-probe-7-arm64-v8a.apk`, and `variant_id == "7"`, `package_type == "apk"`, `architectures == ["arm64-v8a"]`, `size_bytes` equals the file size, and the temp file is gone.
    - `test_split_bundle_keeps_suggested_extension`: zip with `base.apk` + `split_config.arm64_v8a.apk` and suggested filename `x.apkm` → basename `telegram-13.0.3-probe-2-arm64-v8a.apkm`.
    - `test_force_replaces_same_variant_with_new_arch_label`: pre-create `telegram-13.0.3-probe-7-unknown.download`, finalize a universal APK as variant `7`. Only `telegram-13.0.3-probe-7-universal.apk` remains for that prefix, and the `probe-70-…` neighbor file is untouched.

- [ ] **Step 2: Run** `./venv/bin/python -m unittest tests.test_variants -v`. Expected: ImportError / FAIL.

- [ ] **Step 3: Implement** the Interfaces above. `find_existing_variant` accepts the same endings `main.SAVED_FILE_EXTENSIONS` uses today (`PACKAGE_FILE_EXTENSIONS + (".download",)`). Before `os.replace`, `_finalize_downloaded_file` removes every other file that `find_existing_variant`-style matching finds for that prefix, then calls `detect_architectures` once on the temp file. Use that result for both `arch_label` and `APKInfo.architectures`. `app_name` passed to these is the scraper's own folder/file name. Remove `ExistingDownloadTests` and its import from `tests/test_package_handling.py`.

- [ ] **Step 4: Run** the full suite. Expected: all `test_variants` tests pass. `main.py` still imports, because nothing there calls the changed methods. Scrapers will break at runtime until Tasks 3–5, but no test exercises them.

### Task 2: Template `scrape` loop in `BaseScraper`

**Files:**
- Modify: `src/base.py`, `src/__init__.py` (export `VariantRef`, `ScrapeResult`, `arch_label`, `find_existing_variant`)
- Modify: `CLAUDE.md` (Scraper layer, Exception contract and "Adding a new source": subclasses implement `_app_name`, `_list_variants`, `_download_variant`; config keys are documented in `_list_variants`'s docstring; dedup is per variant in `find_existing_variant`)
- Test: `tests/test_variants.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `@dataclass VariantRef(variant_id: str, url: str, label: str = "")`
  - `@dataclass ScrapeResult(downloaded: List[APKInfo], existing: List[str], failed: List[Tuple[str, str]])` (all default to empty lists; `failed` holds `(variant_id, reason)`), plus property `succeeded -> bool` (`bool(downloaded or existing)`)
  - `normalize_variant_ids(variants: List[VariantRef]) -> List[VariantRef]`, module-level: `-` → `_`, duplicate IDs suffixed `_2`, `_3`… in order, with a warning logged
  - `BaseScraper.scrape(self, config: dict, version: str, force: bool = False) -> ScrapeResult`, now concrete
  - Abstract: `_app_name(self, config: dict) -> str` (validates config and raises `ValueError`), `_list_variants(self, page, config: dict, version: str) -> List[VariantRef]` (raises `VersionNotFoundError`), `_download_variant(self, page, app_name: str, version: str, variant: VariantRef) -> APKInfo` (raises `DownloadError`)

- [ ] **Step 1: Write failing tests** `ScrapeLoopTests` using a `FakeScraper(BaseScraper)` whose `_get_browser` returns `(None, None, None)`, `_close_browser` is a no-op, `_list_variants` returns a canned list, and `_download_variant` writes a minimal APK zip to a temp path in the app dir and returns `self._finalize_downloaded_file(...)`, unless the variant ID is in a `fail_ids` set, in which case it raises `DownloadError("boom")`. The fake records the IDs it was asked to download.
  - `test_downloads_every_variant`: variants `1`, `2` → `len(result.downloaded) == 2`, and both files exist.
  - `test_skips_existing_variant`: pre-create `{app}-13.0.3-fake-1-universal.apk`. Only `2` is downloaded, and `result.existing` holds that path.
  - `test_force_redownloads_existing`: same setup with `force=True`, so both are downloaded and `result.existing == []`.
  - `test_one_failure_does_not_stop_others`: `fail_ids={"1"}` → `downloaded` has variant `2`, and `failed == [("1", "boom")]`.
  - `test_all_failures_raise_download_error`: `fail_ids={"1","2"}` → `assertRaises(DownloadError)`.
  - `test_no_variants_raises_version_not_found`.
  - `test_normalize_variant_ids`: `["1","1","a-b"]` becomes `["1","1_2","a_b"]`.

- [ ] **Step 2: Run** `./venv/bin/python -m unittest tests.test_variants -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** The loop catches `Exception` per variant, records it in `failed` and logs a warning with the variant's ID and label. A `PlaywrightTimeout` that escapes `_list_variants` becomes `DownloadError`. `_close_browser` runs in `finally`. Log `Found N variant(s)` and one line per variant: skipped existing, downloaded, or failed.

- [ ] **Step 4: Run** the full suite. Expected: PASS.

### Task 3: APKMirror variants

**Files:**
- Modify: `src/apkmirror.py`, `config.yaml` (drop the `variant_index` line), `README.md` (config example + key list), `docs/usage.md` (`variant_index` mentions; replace the "wrong APK variant" troubleshooting section with one that explains variant filenames)
- Test: `tests/test_source_parsers.py` (create)

**Interfaces:**
- Consumes: `VariantRef` from Task 2.
- Produces: `parse_apkmirror_variants(html: str, app_name: str, version: str) -> List[VariantRef]`, module-level in `src/apkmirror.py`. `url` is absolute (`https://www.apkmirror.com` + href).

- [ ] **Step 1: Write failing tests.** The fixture is inline HTML: a `div.variants-table` with a header `div.table-row` and two data rows. Each data row's first `div.table-cell` holds `<a class="accent_color" href="…">13.0.3</a><span class="apkm-badge">BUNDLE|APK</span>`, followed by cells `universal`, `Android 6.0+`, `120-640dpi|nodpi`. The hrefs are `/apk/telegram-fz-llc/telegram/telegram-13-0-3-release/telegram-13-0-3-2-android-apk-download/` (bundle) and `…/telegram-13-0-3-android-apk-download/` (apk). Outside the table there's one more `a.accent_color` pointing at `telegram-13-0-2-release/telegram-13-0-2-android-apk-download/`.
  - `test_parses_both_variants_with_ids`: IDs are `["2", "1"]` in page order, and URLs start with `https://www.apkmirror.com/apk/telegram-fz-llc/telegram/telegram-13-0-3-release/`.
  - `test_label_mentions_badge_and_dpi`: the first label contains `BUNDLE` and `120-640dpi`.
  - `test_ignores_links_outside_variants_table`: no URL contains `13-0-2`.
  - `test_unexpected_slug_falls_back_to_slug_id`: href segment `foo-bar-android-apk-download` gives ID `foo-bar` (Task 2's normalization turns it into `foo_bar` later).
  - `test_empty_page_returns_empty_list`.

- [ ] **Step 2: Run** `./venv/bin/python -m unittest tests.test_source_parsers -v`. Expected: FAIL.

- [ ] **Step 3: Implement** the parser (select `div.variants-table a.accent_color` with `-android-apk-download` in its href, and dedupe by href). Then convert `APKMirrorScraper` to the Task 2 contract. `_app_name` validates `developer`/`app_name` and returns `app_name`, and warns once if `variant_index` is in config. `_list_variants` reuses the current version-URL and "not found" title logic, then parses `page.content()`. `_download_variant` is today's steps 3–6 starting from `variant.url` and ends with `self._save_downloaded_file(download, app_name, version, variant.variant_id)`. Delete the old `scrape`.

- [ ] **Step 4: Run** the full suite. Expected: PASS.

### Task 4: Uptodown variants

**Files:**
- Modify: `src/uptodown.py`, `docs/usage.md` (Uptodown section: each repeated version row is a variant)
- Test: `tests/test_source_parsers.py`

**Interfaces:**
- Produces: `parse_uptodown_variants(html: str, app_slug: str, version: str) -> List[VariantRef]`, module-level. The URL is `https://{app_slug}.en.uptodown.com/android/download/{data-version-id}`.

- [ ] **Step 1: Write failing tests.** The fixture has rows shaped like `<div data-url="https://telegram.en.uptodown.com/android" data-version-id="ID" data-extra-url="download"><span class="type apk">apk</span><span class="version">V</span>…</div>`, with rows `(1224906954, 13.0.0)`, `(1224938414, 13.0.0)`, `(1226283995, 13.0.2)`, `(999, 13.0.31)`.
  - `test_repeated_rows_are_variants`: version `13.0.0` gives IDs `["1224906954", "1224938414"]` and the URL `https://telegram.en.uptodown.com/android/download/1224906954`.
  - `test_exact_version_match`: version `13.0.3` gives `[]` (the `13.0.31` row doesn't match).
  - `test_label_is_package_type`: label `"apk"`.

- [ ] **Step 2: Run** the parser tests. Expected: FAIL.

- [ ] **Step 3: Implement.** Match on the stripped `span.version` text equal to `version`. Drop the old text/href fallbacks, because the page uses `data-version-id` rows today. `_app_name` returns `app_slug`. `_list_variants` loads the versions page as today, parses it, and raises `VersionNotFoundError` when empty. `_download_variant` navigates to `variant.url`, waits, calls `_find_download_button` (raising `DownloadError` on failure, as today), then `_start_download`, then `_save_downloaded_file(..., variant.variant_id)`. Keep `_start_download` / `_find_download_button` as they are.

- [ ] **Step 4: Run** the full suite. Expected: PASS.

### Task 5: APKPure variants

**Files:**
- Modify: `src/apkpure.py`, `docs/usage.md` (APKPure section: the "All Variants" list)
- Test: `tests/test_source_parsers.py`

**Interfaces:**
- Produces: `parse_apkpure_variants(html: str, package: str) -> List[VariantRef]`, module-level.

- [ ] **Step 1: Write failing tests.** Fixture: `<div class="apk-variants"><div class="show-more-content">` with a `div.group-title` followed by two `div.apk` rows. Each row has `.info` containing `span.code` `(70312)` / `(70242)`, `span.tag` `APK` / `XAPK`, and `a.download-btn` with href `https://d.apkpure.com/b/APK/org.telegram.messenger?versionCode=70312` / `…/b/XAPK/…?versionCode=70242`. Outside the module there's an ad link `https://d.apkpure.com/b/APK/com.binance.dev?version=latest`.
  - `test_parses_all_variants`: IDs are `["70312", "70242"]` and labels contain `APK` / `XAPK`.
  - `test_ignores_other_packages`: no URL contains `com.binance.dev`.
  - `test_fallback_without_variants_module`: the page has only two `a` tags, `…/b/XAPK/org.telegram.messenger?versionCode=71671` twice (duplicate) and the Binance ad. The result is one variant with ID `"71671"`.

- [ ] **Step 2: Run** the parser tests. Expected: FAIL.

- [ ] **Step 3: Implement.** The primary path reads rows from `div.apk-variants div.apk`, and the fallback reads every `a[href]`. Both require `d.apkpure.com/b/`, the package (case-insensitive) and a `versionCode` query param, and both dedupe by href. Take the ID from the `versionCode` param. `_app_name` validates `package` and returns `app_slug.split("/")[-1]`. `_list_variants` keeps `_find_version_link` and the Cloudflare log line, then parses the version page. `_download_variant` calls `_save_url_file(variant.url, app_name, version, variant.variant_id, referer=page.url)` and wraps errors in `DownloadError`. Delete `_find_package_download_link`.

- [ ] **Step 4: Run** the full suite. Expected: PASS.

### Task 6: Orchestrator uses `ScrapeResult`

**Files:**
- Modify: `main.py`, `docs/usage.md` (the skip-check paragraph and the "Bulk mode skips versions" troubleshooting entry), `README.md` (if it describes skipping or filenames)
- Test: `tests/test_fetch.py` (create)

**Interfaces:**
- Consumes: `ScrapeResult`, and `BaseScraper.scrape(config, version, force=False)` from Task 2.
- Produces: `fetch_apk(...)` keeps its signature and `bool` return. `is_already_downloaded` and `SAVED_FILE_EXTENSIONS` are removed.

- [ ] **Step 1: Write failing tests.** Patch `main.SCRAPERS` with fake scraper classes. Each records its `(version, force)` calls and returns a canned `ScrapeResult` or raises. `AppConfig(name="telegram", versions=[], sources={"apkmirror": {"k": 1}, "uptodown": {"k": 1}})` (source configs must be non-empty, because `fetch_apk` skips falsy ones).
  - `test_fallback_stops_at_first_success`: apkmirror returns one downloaded `APKInfo`, so uptodown is never called and the result is `True`.
  - `test_existing_only_counts_as_success`: apkmirror returns `ScrapeResult(existing=["p"])`, so the result is `True` and uptodown is not called.
  - `test_falls_through_on_error`: apkmirror raises `DownloadError`, so uptodown is called.
  - `test_all_sources_tries_every_source`: `all_sources=True`, so both are called.
  - `test_force_is_passed_through`: `force=True` reaches `scrape`.

- [ ] **Step 2: Run** `./venv/bin/python -m unittest tests.test_fetch -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Remove the three version-level skip blocks (`main()`, `process_bulk`, `fetch_apk`) and the per-source pre-check. Call `scraper.scrape(source_config, version, force=force)`. Log each downloaded file (path, type, size, arch), each existing file (path + `detect_architectures`), and each failed variant as a warning. Drop `skip_count` from both summaries.

- [ ] **Step 4: Run** the full suite. Expected: PASS.

- [ ] **Step 5: Live check.** Run `./run.sh --app telegram --version 13.0.3 --source apkmirror`. Expected: `Found 2 variant(s)`, and `downloads/telegram/` gains `telegram-13.0.3-apkmirror-1-universal.apk` and `telegram-13.0.3-apkmirror-2-universal.apkm`. Run it again and expect both to be skipped as existing with no downloads. Then run `./run.sh --app telegram --version 13.0.0 --source uptodown` and expect two `uptodown-12249…` files.
