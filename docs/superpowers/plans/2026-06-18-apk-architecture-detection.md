# APK Architecture Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect which CPU architectures (ABIs) a downloaded APK/bundle supports and surface them per-source to the user.

**Architecture:** A module-level `detect_architectures(filepath)` in `src/base.py` opens the package once, classifies it (via an extracted `classify_package_zip`), and combines two signals — `lib/<abi>/` entries and `config.<abi>` split-APK names, with deep inspection of nested APKs. The result is stored on `APKInfo.architectures` and logged in `main.py` at both the success and the `--all-sources` cached-skip sites.

**Tech Stack:** Python 3, stdlib `zipfile`/`dataclasses`, `unittest`.

## Global Constraints

- No new third-party dependencies (stdlib only).
- Canonical ABI slugs: `armeabi-v7a`, `arm64-v8a`, `x86`, `x86_64`, plus legacy `armeabi`, `mips`, `mips64` only when present.
- `detect_architectures` returns a **sorted** `list[str]`; `["universal"]` when a valid package has no native libs; `[]` for non-zip/unknown files.
- Tests run via `./venv/bin/python -m unittest discover -s tests -v`.

---

### Task 1: Core detection in `src/base.py`

**Files:**
- Modify: `src/base.py` (extract `classify_package_zip`, add `detect_architectures`, add `APKInfo.architectures`)
- Test: `tests/test_architectures.py` (create)

**Interfaces:**
- Produces:
  - `classify_package_zip(filepath: str) -> str` — module-level; returns one of `"apk"`, `"xapk"`, `"aab"`, `"split-apk"`, `"unknown"`.
  - `detect_architectures(filepath: str) -> list[str]` — module-level; sorted ABI list, `["universal"]`, or `[]`.
  - `APKInfo.architectures: list[str]` — defaults to `[]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_architectures.py`:

```python
import io
import os
import tempfile
import unittest
import zipfile

from src.base import detect_architectures


def _zip(entries):
    """Write a zip with the given entry names to a temp file, return its path."""
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        for name in entries:
            zf.writestr(name, b"x")
    return path


def _nested_zip(outer_entries, inner_name, inner_entries):
    """Zip containing a nested apk (inner_name) that itself holds inner_entries."""
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner:
        for name in inner_entries:
            inner.writestr(name, b"x")
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        for name in outer_entries:
            zf.writestr(name, b"x")
        zf.writestr(inner_name, inner_buf.getvalue())
    return path


class TestDetectArchitectures(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            if os.path.exists(p):
                os.remove(p)

    def track(self, path):
        self._paths.append(path)
        return path

    def test_plain_apk_single_abi(self):
        path = self.track(_zip(["AndroidManifest.xml", "lib/arm64-v8a/libfoo.so"]))
        self.assertEqual(detect_architectures(path), ["arm64-v8a"])

    def test_plain_apk_multiple_abis_sorted(self):
        path = self.track(_zip([
            "AndroidManifest.xml",
            "lib/x86_64/libfoo.so",
            "lib/arm64-v8a/libfoo.so",
        ]))
        self.assertEqual(detect_architectures(path), ["arm64-v8a", "x86_64"])

    def test_xapk_config_split_name(self):
        path = self.track(_zip(["manifest.json", "base.apk", "config.x86_64.apk"]))
        self.assertEqual(detect_architectures(path), ["x86_64"])

    def test_split_bundle_nested_lib(self):
        path = self.track(_nested_zip(
            ["base.apk"],
            "split_extra.apk",
            ["AndroidManifest.xml", "lib/armeabi-v7a/libbar.so"],
        ))
        self.assertEqual(detect_architectures(path), ["armeabi-v7a"])

    def test_aab_base_lib(self):
        path = self.track(_zip(["BundleConfig.pb", "base/lib/x86/libfoo.so"]))
        self.assertEqual(detect_architectures(path), ["x86"])

    def test_pure_java_apk_universal(self):
        path = self.track(_zip(["AndroidManifest.xml", "classes.dex"]))
        self.assertEqual(detect_architectures(path), ["universal"])

    def test_non_zip_returns_empty(self):
        fd, path = tempfile.mkstemp(suffix=".download")
        os.write(fd, b"not a zip")
        os.close(fd)
        self.track(path)
        self.assertEqual(detect_architectures(path), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m unittest tests.test_architectures -v`
Expected: FAIL with `ImportError: cannot import name 'detect_architectures'`.

- [ ] **Step 3: Add the `architectures` field to `APKInfo`**

In `src/base.py`, change the import line:

```python
from dataclasses import dataclass, field
```

Add the field to `APKInfo` (after `package_type`):

```python
    package_type: str = "unknown"
    architectures: list = field(default_factory=list)
```

- [ ] **Step 4: Extract `classify_package_zip` to module level**

Add this module-level function near the top of `src/base.py` (after the constants, before `class ScraperError`):

```python
def classify_package_zip(filepath: str) -> str:
    """Classify common Android package containers by ZIP contents."""
    if not zipfile.is_zipfile(filepath):
        return "unknown"

    try:
        with zipfile.ZipFile(filepath) as archive:
            names = [name.strip("/") for name in archive.namelist()]
    except zipfile.BadZipFile:
        return "unknown"

    lower_names = {name.lower() for name in names}
    root_names = {name.lower() for name in names if "/" not in name}
    apk_entries = [name for name in lower_names if name.endswith(".apk")]

    if "manifest.json" in root_names and apk_entries:
        return "xapk"
    if "androidmanifest.xml" in root_names:
        return "apk"
    if "bundleconfig.pb" in root_names:
        return "aab"
    if apk_entries:
        return "split-apk"
    return "unknown"
```

Replace the body of the existing `BaseScraper._classify_package_zip` method so it delegates:

```python
    def _classify_package_zip(self, filepath: str) -> str:
        """Classify common Android package containers by ZIP contents."""
        return classify_package_zip(filepath)
```

- [ ] **Step 5: Add `detect_architectures`**

Add this module-level function in `src/base.py` after `classify_package_zip`:

```python
KNOWN_ABIS = {
    "armeabi-v7a",
    "arm64-v8a",
    "x86",
    "x86_64",
    "armeabi",
    "mips",
    "mips64",
}


def _abis_from_names(names) -> set:
    """Collect ABIs from lib/<abi>/ dirs and config.<abi> split names."""
    found = set()
    for raw in names:
        name = raw.strip("/")
        lower = name.lower()
        parts = lower.split("/")
        # lib/<abi>/... or base/lib/<abi>/... (aab)
        if "lib" in parts:
            idx = parts.index("lib")
            if idx + 1 < len(parts) and parts[idx + 1] in KNOWN_ABIS:
                found.add(parts[idx + 1])
        # config.<abi>.apk / split_config.<abi>.apk
        if lower.endswith(".apk") and "config." in lower:
            stem = os.path.basename(lower)[: -len(".apk")]
            slug = stem.split("config.", 1)[1]
            abi = slug.replace("_", "-")
            if abi in KNOWN_ABIS:
                found.add(abi)
    return found


def detect_architectures(filepath: str) -> list:
    """Detect supported CPU ABIs of a downloaded package.

    Returns a sorted ABI list (e.g. ["arm64-v8a", "x86_64"]), ["universal"]
    when the package has no native libraries, or [] when the file is not a
    recognised package.
    """
    package_type = classify_package_zip(filepath)
    if package_type == "unknown":
        return []

    found = set()
    try:
        with zipfile.ZipFile(filepath) as archive:
            names = archive.namelist()
            found |= _abis_from_names(names)
            # Deep-inspect nested APKs in split bundles.
            for name in names:
                if name.lower().endswith(".apk"):
                    try:
                        with archive.open(name) as inner_fp:
                            inner_bytes = io.BytesIO(inner_fp.read())
                        with zipfile.ZipFile(inner_bytes) as inner_zip:
                            found |= _abis_from_names(inner_zip.namelist())
                    except (zipfile.BadZipFile, KeyError):
                        continue
    except zipfile.BadZipFile:
        return []

    if not found:
        return ["universal"]
    return sorted(found)
```

Add `import io` to the imports at the top of `src/base.py` (alongside `import os`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `./venv/bin/python -m unittest tests.test_architectures -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Run the full suite to confirm no regressions**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/base.py tests/test_architectures.py
git commit -m "feat: detect supported ABIs of downloaded packages"
```

---

### Task 2: Export and thread `architectures` into scrapers

**Files:**
- Modify: `src/__init__.py` (export `detect_architectures`)
- Modify: `src/apkmirror.py`, `src/uptodown.py`, `src/apkpure.py` (populate `APKInfo.architectures`)

**Interfaces:**
- Consumes: `detect_architectures(filepath)` from Task 1.
- Produces: each scraper returns `APKInfo` with `architectures` populated.

- [ ] **Step 1: Export `detect_architectures` from `src/__init__.py`**

Add to the `from .base import (...)` block:

```python
    detect_architectures,
    classify_package_zip,
```

Add to `__all__`:

```python
    "detect_architectures",
    "classify_package_zip",
```

- [ ] **Step 2: Populate `architectures` in `src/apkmirror.py`**

Change the import line `from .base import BaseScraper, APKInfo, VersionNotFoundError, DownloadError` to include `detect_architectures`:

```python
from .base import (
    BaseScraper,
    APKInfo,
    VersionNotFoundError,
    DownloadError,
    detect_architectures,
)
```

In the `return APKInfo(...)` block, add the argument after `package_type=package_type,`:

```python
                package_type=package_type,
                architectures=detect_architectures(filepath),
```

- [ ] **Step 3: Apply the same change to `src/uptodown.py` and `src/apkpure.py`**

Both files use the identical `from .base import BaseScraper, APKInfo, VersionNotFoundError, DownloadError` import and the identical `package_type=package_type,` line in their `APKInfo(...)` construction. Apply the exact same two edits from Step 2 to each file:
1. Expand the import to include `detect_architectures` (multi-line form shown above).
2. Add `architectures=detect_architectures(filepath),` after `package_type=package_type,`.

- [ ] **Step 4: Verify imports load**

Run: `./venv/bin/python -c "from src import detect_architectures, APKMirrorScraper, UptodownScraper, APKPureScraper; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Run the full suite**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/__init__.py src/apkmirror.py src/uptodown.py src/apkpure.py
git commit -m "feat: populate APKInfo.architectures in all scrapers"
```

---

### Task 3: Surface architectures in `main.py`

**Files:**
- Modify: `main.py` (import, success log, `--all-sources` skip log)

**Interfaces:**
- Consumes: `detect_architectures` from `src`; `result.architectures` on `APKInfo`.

- [ ] **Step 1: Import `detect_architectures` and add a formatter**

In `main.py`, add `detect_architectures` to the `from src import (...)` block (after `PACKAGE_FILE_EXTENSIONS,`).

Add a helper near the top (after `SAVED_FILE_EXTENSIONS = ...`):

```python
def _format_abis(abis) -> str:
    """Render an ABI list for logging."""
    return ", ".join(abis) if abis else "unknown"
```

- [ ] **Step 2: Append arch to the success log**

In `fetch_apk`, change the success log block (currently `main.py:118-121`):

```python
                logger.info(
                    f"   Source: {result.source} | Type: {result.package_type} | "
                    f"Size: {result.size_bytes:,} bytes | "
                    f"Arch: {_format_abis(result.architectures)}"
                )
```

- [ ] **Step 3: Append arch to the `--all-sources` cached-skip log**

In `fetch_apk`, change the per-source skip block (currently `main.py:104-108`):

```python
        if all_sources and not force:
            existing = is_already_downloaded(output_dir, app_config.name, version, source_name)
            if existing:
                logger.info(
                    f"⏭️  {app_config.name} v{version} from {source_name} "
                    f"already exists: {existing} | "
                    f"Arch: {_format_abis(detect_architectures(existing))}"
                )
                any_success = True
                continue
```

- [ ] **Step 4: Verify `main.py` still parses and runs**

Run: `./venv/bin/python main.py --help`
Expected: usage text prints without error.

- [ ] **Step 5: Run the full suite**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat: log detected ABIs per source"
```

---

### Task 4: Document the feature

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: Update README**

Find the section describing output/download behavior (where package types are explained) and add a sentence noting that each successful download logs the detected CPU architectures (ABIs), e.g. `Arch: arm64-v8a, x86_64`, and that pure-Dalvik apps report `universal`. Keep wording consistent with the surrounding README style.

- [ ] **Step 2: Update CLAUDE.md architecture notes**

In `CLAUDE.md`, under the scraper-layer description that mentions `_save_downloaded_file` inspecting the package, add a sentence: detection of supported ABIs lives in the module-level `detect_architectures` in `src/base.py` (reuses `classify_package_zip`), is stored on `APKInfo.architectures`, and is logged per-source by `main.py`.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: describe ABI detection feature"
```

---

## Self-Review Notes

- **Spec coverage:** canonical ABI set (Task 1 `KNOWN_ABIS`), `universal`/`[]` fallbacks (Task 1 tests), deep inspection of nested APKs + config-split names (Task 1 `detect_architectures`/`_abis_from_names`), `APKInfo` field (Task 1), exports (Task 2), scraper threading (Task 2), both log sites incl. `--all-sources` skip (Task 3), tests (Task 1), docs (Task 4). All spec sections covered.
- **Placeholder scan:** none — all code shown inline.
- **Type consistency:** `detect_architectures(filepath) -> list`, `classify_package_zip(filepath) -> str`, `_format_abis(abis) -> str`, `APKInfo.architectures` used consistently across tasks.
