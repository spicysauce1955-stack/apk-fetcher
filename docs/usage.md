# APK Fetcher Usage Guide

This guide documents the runtime behavior, configuration format, source
requirements, and maintenance model for APK Fetcher.

## How a Fetch Works

`main.py` loads `config.yaml`, chooses the target apps and versions, then calls
the configured scrapers in source-priority order.

Default source order:

1. `apkmirror`
2. `uptodown`
3. `apkpure`

Each source downloads every variant it lists for the version (for example
APKMirror's `.apk` and `.apkm` builds). In normal mode, the first source that
succeeds ends the attempt for that app/version. In `--all-sources` mode, every
configured source is attempted and each one writes its own variant files.

Failures are handled per source:

- `VersionNotFoundError`: the version was not found on that source.
- `DownloadError`: the version may exist, but the download flow failed.
- `ScraperError`: source-specific scraper failure.
- unexpected exceptions: logged and treated as source failures.

One failed source does not stop fallback to the next source.

## Configuration File

The default config path is `config.yaml`. Use `--config` to load another file.

```yaml
output_dir: ./downloads
headless: true

apps:
  telegram:
    versions:
      - "12.4.0"
    sources:
      apkmirror:
        developer: telegram-fz-llc
        app_name: telegram
      uptodown:
        app_slug: telegram
      apkpure:
        package: org.telegram.messenger
        app_slug: telegram
```

### Global Keys

`output_dir`

Directory where packages are saved. Defaults to `./downloads` if omitted. The
CLI `--output` flag overrides it.

`headless`

Whether Playwright runs without a visible browser. Defaults to `true` if
omitted. The CLI `--no-headless` flag overrides it for a run.

### App Keys

Each key under `apps` is the local app name used by CLI selection, skip checks,
and output folders.

`versions`

List of versions to fetch when `--version` and `--bulk` are not provided.
Quote version strings in YAML so values such as `12.4.0` are not parsed as
numbers.

`sources`

Map of source names to source-specific configuration. Only configured sources
are attempted for that app.

## Source Configuration

### APKMirror

Required:

- `developer`: developer slug from APKMirror URLs.
- `app_name`: app slug from APKMirror URLs.

The scraper builds a deterministic version page URL:

```text
https://www.apkmirror.com/apk/<developer>/<app_name>/<app_name>-<version-slug>-release/
```

For version `12.4.0`, the version slug is `12-4-0`.

Every row of the page's variants table is downloaded. The variant ID in the
filename is APKMirror's own numbering: the first variant's URL ends in
`<app_name>-<version-slug>-android-apk-download/` (ID `1`), later ones in
`<app_name>-<version-slug>-2-android-apk-download/` (ID `2`) and so on. A
leftover `variant_index` key is ignored with a warning.

### Uptodown

Required:

- `app_slug`: the Uptodown subdomain slug.

The scraper loads:

```text
https://<app_slug>.en.uptodown.com/android/versions
```

Uptodown lists a version once per variant, so every row whose version number
matches the requested version exactly is downloaded. Each row's
`data-version-id` is the variant ID in the filename and addresses its download
page (`https://<app_slug>.en.uptodown.com/android/download/<id>`). Only the
first page of the version list is searched. Uptodown markup changes can affect
this flow.

### APKPure

Required:

- `package`: Android package name.

Optional:

- `app_slug`: APKPure app slug. Defaults to the package name.

The scraper uses the mobile site:

```text
https://m.apkpure.com/<app_slug>/<package>/versions
```

It follows the requested version's link and downloads every entry in that
page's "All Variants" list (for example an APK and an XAPK, or two XAPKs built
for the same CPU). The variant ID in the filename is the entry's `versionCode`.
Download links for other packages on the page are ignored.

APKPure is heavily protected and is expected to be less reliable in headless
automation than APKMirror or Uptodown.

## Bulk Version Files

Bulk mode expects a JSON array. Each item must contain a `version` field.
Additional fields, such as `date`, are ignored by the downloader.

```json
[
  {
    "version": "12.4.1",
    "date": "2026-02-09"
  },
  {
    "version": "12.4.0",
    "date": "2026-02-06"
  }
]
```

Run bulk mode for a specific app:

```bash
./run.sh --app telegram --bulk versions.json
```

Without `--app`, the same bulk version list is attempted for every configured
app.

## Download and Skip Behavior

Package files are saved as:

```text
<output_dir>/<app>/<app>-<version>-<source>-<variant-id>-<arch>.<type>
```

Every variant a source lists for the version is downloaded. Examples:

```text
downloads/telegram/telegram-13.0.3-apkmirror-1-universal.apk
downloads/telegram/telegram-13.0.3-apkmirror-2-universal.apkm
downloads/telegram/telegram-13.0.0-uptodown-1224906954-universal.apk
downloads/telegram/telegram-12.10.0-apkpure-70242-arm64-v8a.xapk
```

See [Telling variants apart](#telling-variants-apart) for how the variant ID
and `<arch>` are chosen.

The fetcher saves each browser download to a temporary file, inspects the ZIP
contents when possible, combines that with Playwright's suggested filename, and
then renames the file to one of the known Android package extensions:

- `.apk`: single Android package.
- `.xapk`: APK plus expansion data or metadata, commonly from APKPure-style
  packages.
- `.apkm`: APKMirror split-package container when the browser/source reports
  that extension.
- `.apks`: generic split-APK archive.
- `.aab`: Android App Bundle.

Unknown files are saved with `.download` rather than being mislabeled as APKs.
Those files also satisfy future skip checks for the same variant.

Skip checks are per variant: each run still opens the source's version page to
see which variants exist, then downloads only those without a
`<app>-<version>-<source>-<variant-id>-*` file (any package extension or
`.download`). A source counts as successful when at least one of its variants
was downloaded or already present, so default fallback mode stops there, while
`--all-sources` continues to every configured source. If some variants fail,
the rest are still downloaded and the failures are logged by variant ID.

Files saved before variant support (`<app>-<version>-<source>.<type>`) are left
alone and do not count as any variant, so those variants are downloaded again
under their new names.

If a source's version page cannot be read (for example a Cloudflare challenge)
but files for that version from that source are already on disk, including
pre-variant names, the run keeps them, logs a warning, and treats the source as
successful instead of falling back to the next source. `--force` turns this
off.

Use `--force` to re-download variants even when matching files already exist;
the variant's previous file is replaced.

## Browser Selection

By default, Playwright launches its managed Chromium. If
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` is set, `BaseScraper` passes it to
Playwright as `executable_path`.

Examples:

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome ./run.sh --app telegram
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium ./run.sh --app telegram
```

`run.sh` sets the path to `/usr/bin/google-chrome` only when the environment
variable is not already set.

## Troubleshooting

### `ModuleNotFoundError: No module named 'playwright'`

Install dependencies in the virtual environment and use that interpreter:

```bash
source venv/bin/activate
pip install -r requirements.txt
./venv/bin/python main.py --help
```

### Playwright cannot find a browser

Install Playwright Chromium:

```bash
playwright install chromium
```

Or set a system browser path:

```bash
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome
```

### A source times out or cannot find a button

Run with a visible browser and debug logging:

```bash
./run.sh --app telegram --version 12.4.0 --no-headless --debug
```

If the source website changed its markup or inserted an interstitial page, the
corresponding scraper module may need selector updates.

### Telling variants apart

Every variant a source lists is saved as
`<app>-<version>-<source>-<variant-id>-<arch>.<ext>`, for example
`telegram-13.0.3-apkmirror-1-universal.apk` and
`telegram-13.0.3-apkmirror-2-universal.apkm`. The variant ID is the source's own
ID (see each source above). `<arch>` comes from scanning the downloaded file:
`universal` when it has no native code or ships all of `arm64-v8a`,
`armeabi-v7a`, `x86` and `x86_64`; otherwise the ABIs it contains joined with
`+` (e.g. `arm64-v8a+armeabi-v7a`); `unknown` when the file is not a recognised
package.

### Bulk mode skips variants

A variant is skipped when a file with its
`<app>-<version>-<source>-<variant-id>-` prefix already exists. Pass `--force`,
delete that file, or use a different `--output` directory to retry. Bulk reruns
still load every version page, because a source may have added variants since
the last run.

## Adding a New App

1. Add a new key under `apps` in `config.yaml`.
2. Add one or more version strings under `versions`.
3. Add source-specific blocks for the sources you want to use.
4. Run a single visible-browser test before bulk fetching many versions.

Example:

```yaml
apps:
  whatsapp:
    versions:
      - "2.24.1.0"
    sources:
      apkmirror:
        developer: whatsapp-inc
        app_name: whatsapp
      uptodown:
        app_slug: whatsapp
      apkpure:
        package: com.whatsapp
        app_slug: whatsapp
```

## Adding a New Source

1. Create `src/<source>.py`.
2. Subclass `BaseScraper`.
3. Set `SOURCE_NAME` to the source slug used in filenames.
4. Implement `_app_name(config)`, `_list_variants(page, config, version)`
   (returning `VariantRef`s with the site's own stable variant ID) and
   `_download_variant(page, app_name, version, variant)`. `BaseScraper.scrape`
   handles the browser, skip checks and per-variant failures.
5. Raise `VersionNotFoundError`, `DownloadError`, or `ScraperError` for
   expected failures.
6. Export the scraper in `src/__init__.py`.
7. Add it to the `SCRAPERS` registry and `SOURCE_PRIORITY` in `main.py`.
8. Document its config keys in the README and this guide.

Keep `SOURCE_PRIORITY` in `main.py` and `src/__init__.py` synchronized if both
registries continue to be used.

## Repository Notes

- Run local tests with `./venv/bin/python -m unittest discover -s tests -v`.
- `requirements.txt` contains runtime dependencies only.
- `CLAUDE.md` contains agent-oriented implementation notes for future code
  maintenance.
