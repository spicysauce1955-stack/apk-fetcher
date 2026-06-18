# APK Architecture Detection — Design

**Date:** 2026-06-18
**Status:** Approved

## Goal

After an APK (or split bundle) is downloaded, detect which CPU architectures
(ABIs) it supports — e.g. `arm64-v8a`, `x86_64` — and surface that information to
the user, including per-source in `--all-sources` mode.

## Canonical ABI set

Detection maps to the standard Android ABI slugs:

- `armeabi-v7a`
- `arm64-v8a`
- `x86`
- `x86_64`
- `armeabi`, `mips`, `mips64` (legacy — included only if actually present)

Special results:

- `["universal"]` — the package contains no native libraries (pure-Dalvik app,
  runs on any ABI).
- `[]` — the file is not a recognised zip / package (`unknown`, `.download`),
  so architecture could not be determined.

The returned list is **sorted** for stable output and testing.

## Detection logic

A single module-level function in `src/base.py`:

```python
def detect_architectures(filepath: str) -> list[str]:
    ...
```

It opens the package once and combines two signals:

1. **Native lib directories** — any zip entry matching `lib/<abi>/...`
   contributes `<abi>`.
2. **Split config APKs** — entry names like `config.arm64_v8a.apk` or
   `split_config.x86.apk` map the underscore slug (`arm64_v8a`) back to the ABI
   (`arm64-v8a`).

### Per-format handling (deep inspection)

The function first classifies the package (see "Shared classification" below),
then:

- **apk** — scan its own `lib/<abi>/` entries.
- **xapk / apks / apkm / split-apk** — list bundled `.apk` entries, map any
  `config.<abi>` / `split_config.<abi>` names to ABIs, **and** open each nested
  APK to read its `lib/<abi>/` directories.
- **aab** — read `base/lib/<abi>/` entries.
- After collecting, if the set is empty → return `["universal"]`.
- Non-zip / `unknown` → return `[]`.

Nested-zip reads are wrapped defensively: a `BadZipFile` on any inner entry is
skipped rather than aborting detection.

### Shared classification

The existing `BaseScraper._classify_package_zip` body is extracted to a
module-level `classify_package_zip(filepath) -> str`. The existing method
delegates to it (no behaviour change), and `detect_architectures` reuses it so
package-type logic lives in one place.

## Data model

`APKInfo` gains a field:

```python
architectures: list[str] = field(default_factory=list)
```

(`dataclasses.field` import added.)

## Threading the result

Each of the three scrapers already does:

```python
filepath, package_type = self._save_downloaded_file(...)   # or _save_url_file
...
return APKInfo(..., package_type=package_type)
```

They add one argument:

```python
return APKInfo(..., package_type=package_type,
               architectures=detect_architectures(filepath))
```

`detect_architectures` and `classify_package_zip` are exported from
`src/__init__.py`.

## Surfacing to the user (`main.py`)

Two log sites, both formatting the ABI list as a comma-joined string (or
`unknown` when the list is empty):

1. **Success line** (`main.py:118`) — append `| Arch: <abis>` to the existing
   `Source | Type | Size` line. Because this fires inside the per-source loop,
   `--all-sources` already prints one line per source.

2. **`--all-sources` skip line** (`main.py:104`, "already exists") — call
   `detect_architectures(existing)` on the cached file and append the ABI list,
   so cached packages also show arch per source.

A small helper formats the list: `", ".join(abis) if abis else "unknown"`.

## Testing

Unit tests (`tests/`) build tiny in-memory zips and assert
`detect_architectures` output:

- Plain APK with `androidmanifest.xml` + `lib/arm64-v8a/libfoo.so` →
  `["arm64-v8a"]`.
- Plain APK with libs for two ABIs → both, sorted.
- xapk (`manifest.json` + `config.x86_64.apk`) → `["x86_64"]`.
- Split bundle whose nested APK contains `lib/armeabi-v7a/...` → that ABI
  (verifies deep inspection).
- aab with `base/lib/x86/...` → `["x86"]`.
- Pure-Java apk (manifest, no `lib/`) → `["universal"]`.
- Non-zip / junk file → `[]`.

## Out of scope

- Persisted metadata sidecar files.
- Re-inspecting already-downloaded files in default (non-`--all-sources`) fast-skip
  mode (that path skips before any source loop and is unchanged).
- Reporting per-architecture sizes or minSdkVersion.
