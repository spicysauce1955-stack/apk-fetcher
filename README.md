# APK Fetcher

APK Fetcher is a Python CLI for downloading specific Android APK versions from
multiple third-party APK sources. It is config-driven, uses Playwright browser
automation, and can fall back across APKMirror, Uptodown, and APKPure when a
version is missing or a source fails.

Use this responsibly and only for apps and artifacts you are allowed to
download. Third-party APK sites change frequently, so scraping selectors may
need maintenance over time.

## Features

- Fetch one app/version, all configured versions, or a bulk JSON version list.
- Try sources in priority order: APKMirror, then Uptodown, then APKPure.
- Optionally download one package from every configured source with
  `--all-sources`.
- Skip already-downloaded files unless `--force` is provided.
- Store downloads under `downloads/<app>/<app>-<version>-<source>.<type>`.
- Detect common Android package containers: `.apk`, `.xapk`, `.apkm`,
  `.apks`, and `.aab`.
- Run headless by default, with visible-browser debug mode available.
- Support Playwright's bundled Chromium or a system Chrome/Chromium binary.

## Requirements

- Python 3.10 or newer.
- A Chromium-compatible browser:
  - Playwright-managed Chromium via `playwright install chromium`, or
  - a system Chrome/Chromium path set with `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`.
- Network access to the configured APK sources.

## Installation

```bash
git clone <repo-url>
cd apk-fetcher

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

If Playwright's browser download is unavailable in your environment, use an
installed Chrome/Chromium binary instead:

```bash
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome
```

The repository also includes `run.sh`, which uses `./venv/bin/python`, defaults
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to `/usr/bin/google-chrome` when unset,
and forwards all arguments to `main.py`.

## Quick Start

Fetch every app/version declared in `config.yaml`:

```bash
./run.sh
```

Fetch one configured app:

```bash
./run.sh --app telegram
```

Fetch one app version:

```bash
./run.sh --app telegram --version 12.4.0
```

Use one source only:

```bash
./run.sh --app telegram --version 12.4.0 --source apkmirror
```

Download from every configured source instead of stopping after the first
success:

```bash
./run.sh --app telegram --version 12.4.0 --all-sources
```

Bulk-fetch versions from `versions.json`:

```bash
./run.sh --app telegram --bulk versions.json
```

Run with a visible browser and debug logs:

```bash
./run.sh --app telegram --version 12.4.0 --no-headless --debug
```

Direct Python invocation works too:

```bash
./venv/bin/python main.py --app telegram --version 12.4.0
```

## Configuration

`config.yaml` defines global runtime settings and one block per app.

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
        variant_index: 0
      uptodown:
        app_slug: telegram
      apkpure:
        package: org.telegram.messenger
        app_slug: telegram
```

Source-specific keys:

- `apkmirror.developer`: APKMirror developer slug.
- `apkmirror.app_name`: APKMirror app slug.
- `apkmirror.variant_index`: optional zero-based APK variant selector.
- `uptodown.app_slug`: Uptodown app subdomain slug.
- `apkpure.package`: Android package name.
- `apkpure.app_slug`: optional APKPure app slug; defaults to `package`.

See [docs/usage.md](docs/usage.md) for full configuration notes, bulk file
format, source behavior, troubleshooting, and development guidance.

## CLI Reference

```text
Selection:
  --app, -a APP             App key from config.yaml. Defaults to all apps.
  --version, -v VERSION     Version to fetch. Defaults to app versions in config.
  --bulk, -b FILE           JSON file containing objects with a version field.

Execution:
  --config, -c FILE         Config file path. Default: config.yaml.
  --source, -s SOURCE       Use only apkmirror, uptodown, or apkpure.
  --all-sources             Try every configured source and keep each result.
  --output, -o DIR          Output directory. Overrides config output_dir.
  --force, -f               Re-download even if a matching package exists.
  --no-headless             Show the browser window.
  --debug, -d               Enable debug logging.
```

`--all-sources` cannot be combined with `--source`.

## Output Layout

Downloaded files are named deterministically:

```text
downloads/
`-- telegram/
    |-- telegram-12.4.0-apkmirror.apkm
    |-- telegram-12.4.0-uptodown.apk
    `-- telegram-12.4.0-apkpure.xapk
```

The extension is based on the downloaded file's content and browser-supplied
filename. The skip check is filename-based across known package extensions, so
`.apk`, `.xapk`, `.apkm`, `.apks`, `.aab`, and unknown `.download` files can
all satisfy an existing download check. Removing a file, changing `--output`,
or passing `--force` will cause the fetcher to attempt the download again.

Each successful download also logs the detected CPU architectures (ABIs) of the
package, for example `Arch: arm64-v8a, x86_64`. Pure-Dalvik apps that contain
no native libraries report `Arch: universal`.

## Project Layout

```text
apk-fetcher/
|-- main.py              CLI, fallback orchestration, bulk mode, dedup checks
|-- run.sh               Convenience launcher for the local virtualenv
|-- config.yaml          Example app/source configuration
|-- versions.json        Example bulk version list
|-- requirements.txt     Python dependencies
|-- docs/
|   `-- usage.md         Detailed user and maintainer documentation
`-- src/
    |-- base.py          Shared scraper base class and exceptions
    |-- config.py        YAML configuration loader
    |-- apkmirror.py     APKMirror scraper
    |-- uptodown.py      Uptodown scraper
    `-- apkpure.py       APKPure scraper
```

## Known Limitations

- APK source websites may change markup, flows, ads, or bot protections without
  notice. A scraper can break even when the CLI is unchanged.
- APKPure is Cloudflare-protected and is the least reliable source in headless
  automation, even though the scraper uses the mobile site.
- Existing package files are not hashed or verified; the project only checks
  for a matching filename before skipping.

## Testing

The repository uses Python's built-in `unittest` framework for local tests:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

## License

MIT
