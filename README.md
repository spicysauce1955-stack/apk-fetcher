# APK Fetcher

APK Fetcher is a Python CLI for downloading specific Android APK versions from
multiple third-party APK sources. It is config-driven, uses Patchright browser
automation, and can fall back across APKMirror, Uptodown, and APKPure when a
version is missing or a source fails.

Use this responsibly and only for apps and artifacts you are allowed to
download. Third-party APK sites change frequently, so scraping selectors may
need maintenance over time.

## Features

- Fetch one app/version, all configured versions, or a bulk JSON version list.
- Try sources in priority order: APKMirror, then Uptodown, then APKPure.
- Download every variant a source lists for a version (e.g. both the `.apk`
  and the `.apkm` on APKMirror).
- Optionally download from every configured source with `--all-sources`.
- Skip already-downloaded variants unless `--force` is provided.
- Store downloads under
  `downloads/<app>/<app>-<version>-<source>-<variant-id>-<arch>.<type>`.
- Detect common Android package containers: `.apk`, `.xapk`, `.apkm`,
  `.apks`, and `.aab`.
- Show the browser so a person can complete bot checks, with headless mode
  available through `config.yaml`.
- Support Patchright's bundled Chromium or a system Chrome/Chromium binary.

## Requirements

- Python 3.10 or newer.
- A Chromium-compatible browser:
  - Patchright-managed Chromium via `patchright install chromium`, or
  - a system Chrome/Chromium path set with `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`.
- Network access to the configured APK sources.

## Installation

```bash
git clone <repo-url>
cd apk-fetcher

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
patchright install chromium
```

If Patchright's browser download is unavailable in your environment, use an
installed Chrome/Chromium binary instead:

```bash
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/google-chrome
```

The repository also includes `run.sh`, which uses `./venv/bin/python`, defaults
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to `/usr/bin/google-chrome` when unset,
and forwards all arguments to `main.py`.

## Quick Start

Fetch one version. A Chrome window opens, and if a bot check appears, complete
it there:

```bash
./run.sh --version 13.2.1
```

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

Download every variant from every source, instead of stopping at the first
source that works:

```bash
./run.sh --version 13.2.0 --all-sources
```

Keep only packages that support particular platforms:

```bash
./run.sh --version 13.2.0 --platform x86
./run.sh --version 13.2.0 --platform arm64,x86
```

Find out whether anything newer than a version is listed, downloading nothing:

```bash
./run.sh --check 13.2.0
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
headless: false

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

Source-specific keys:

- `apkmirror.developer`: APKMirror developer slug.
- `apkmirror.app_name`: APKMirror app slug.
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
  --check VERSION           Download nothing; report anything newer than VERSION.
  --platform, -p LIST       Keep only packages supporting every listed platform.

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

`--platform` takes a comma-separated list of `arm` (32-bit), `arm64`,
`x86` (32-bit) and `x86_64`. The Android names `armeabi-v7a` and `arm64-v8a` also
work. A package must support every platform listed.

`--check` exits `3` when a newer version is listed, `0` when every source was
read and none is newer, and `1` when it could not read every source and found
nothing newer. It cannot be combined with `--version`, `--bulk`, `--force` or
`--platform`.

## Output Layout

Downloaded files are named deterministically:

```text
downloads/
`-- telegram/
    |-- telegram-13.0.3-apkmirror-1-universal.apk
    |-- telegram-13.0.3-apkmirror-2-universal.apkm
    |-- telegram-13.0.0-uptodown-1224906954-universal.apk
    `-- telegram-12.10.0-apkpure-70242-arm64-v8a.xapk
```

The variant ID is the source's own ID for that variant (APKMirror's variant
number, Uptodown's version ID, APKPure's `versionCode`). `<arch>` comes from
scanning the downloaded file: `universal` when it has no native code or ships
all of `arm64-v8a`, `armeabi-v7a`, `x86` and `x86_64`, otherwise the ABIs it
contains joined with `+`. The extension is based on the downloaded file's
content and browser-supplied filename.

The skip check is per variant across known package extensions (`.apk`, `.xapk`,
`.apkm`, `.apks`, `.aab`), and empty or non-package files do not satisfy it.
Each run still loads the version page to
see which variants exist. Removing a file, changing `--output`, or passing
`--force` will cause the fetcher to attempt that variant again. Files saved
before variant support (`<app>-<version>-<source>.<type>`) are not recognised as
any variant.

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
- All three sources use Cloudflare bot checks, so headless runs are often
  blocked. The shipped `config.yaml` therefore shows the browser window, where
  a person can complete a check when one appears. The browser keeps its profile
  in `browser-profile/`, so a completed check carries over to later runs for as
  long as the site allows. Delete that folder to start fresh.
- Existing package files are not hashed; before skipping, the project only
  checks for a matching filename that still reads as a package.

## Testing

The repository uses Python's built-in `unittest` framework for local tests:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

## License

MIT
