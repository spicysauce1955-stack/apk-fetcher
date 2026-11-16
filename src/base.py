#!/usr/bin/env python3
"""
Base scraper abstraction with common functionality.
"""

import io
import os
import re
import shutil
import tempfile
import time
import logging
import zipfile
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.request import Request, urlopen

from patchright.sync_api import Error as PlaywrightError
from patchright.sync_api import TimeoutError as PlaywrightTimeout
from patchright.sync_api import sync_playwright


PACKAGE_EXTENSIONS = {
    ".apk": "apk",
    ".xapk": "xapk",
    ".apkm": "apkm",
    ".apks": "apks",
    ".aab": "aab",
}

PACKAGE_FILE_EXTENSIONS = tuple(PACKAGE_EXTENSIONS.keys())

# Titles of the interstitial pages that replace a page behind a bot check.
# Page HTML is no use here: ordinary pages also embed Cloudflare's scripts.
CHALLENGE_TITLES = ("just a moment", "attention required")

# Settings a snap-packaged GUI app (e.g. VS Code) leaks into its terminals.
SNAP_GUI_VARIABLES = (
    "GSETTINGS_SCHEMA_DIR",
    "GTK_PATH",
    "GTK_EXE_PREFIX",
    "GTK_IM_MODULE_FILE",
    "GIO_MODULE_DIR",
)

# Chrome profile kept between runs so a completed bot check carries over.
BROWSER_PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "browser-profile",
)

SYSTEM_CHROME_CANDIDATES = (
    "google-chrome",
    "chromium",
    "chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


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


KNOWN_ABIS = {
    "armeabi-v7a",
    "arm64-v8a",
    "x86",
    "x86_64",
    "armeabi",
    "mips",
    "mips64",
}

# Names accepted by --platform. As in Android's own ABIs, "arm" and "x86" are
# the 32-bit platforms.
PLATFORM_ABIS = {
    "arm": "armeabi-v7a",
    "armeabi-v7a": "armeabi-v7a",
    "arm64": "arm64-v8a",
    "arm64-v8a": "arm64-v8a",
    "x86": "x86",
    "x86_64": "x86_64",
}

# Whole words only, so "x86" never matches inside "x86_64".
ABI_IN_LABEL = re.compile(r"(?<![\w-])(" + "|".join(KNOWN_ABIS) + r")(?![\w-])")


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
        if lower.endswith(".apk"):
            stem = os.path.basename(lower)[: -len(".apk")]
            # Guard against names like "config.apk" where "config." only appears
            # because the stem abuts the extension; the slug lives in the stem.
            if "config." in stem:
                slug = stem.split("config.", 1)[1]
                # Some ABIs use underscores (x86_64); others use dashes
                # (armeabi-v7a). Check the raw slug first, then the dash form.
                abi = slug if slug in KNOWN_ABIS else slug.replace("_", "-")
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


MAIN_ABIS = frozenset({"arm64-v8a", "armeabi-v7a", "x86", "x86_64"})


def arch_label(abis: list) -> str:
    """Render detected ABIs as a filename-safe label.

    "universal" covers both no native code and packages shipping every main
    ABI; "unknown" means the file was not a recognised package.
    """
    if not abis:
        return "unknown"
    if abis == ["universal"] or MAIN_ABIS <= set(abis):
        return "universal"
    return "+".join(sorted(abis))


def supports_platforms(abis: list, platforms) -> bool:
    """True when a package with these ABIs runs on every requested platform."""
    return abis == ["universal"] or set(platforms) <= set(abis)


def label_excludes_platforms(label: str, platforms) -> bool:
    """True when a listing label names ABIs that miss a requested platform.

    A label naming no ABI, such as "universal" or "apk", proves nothing, so
    the downloaded file has to be checked instead.
    """
    named = set(ABI_IN_LABEL.findall(label.lower()))
    return bool(named) and not set(platforms) <= named


def is_version_number(text: str) -> bool:
    """True for plain dotted numbers; betas and other suffixes do not count."""
    return re.fullmatch(r"\d+(\.\d+)*", text) is not None


def version_key(version: str) -> Tuple[int, ...]:
    """Sort key for versions, treating 13.2 and 13.2.0 as the same version."""
    parts = [int(part) for part in re.findall(r"\d+", version)]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def last_word(text: str) -> str:
    """Return the final word of text, or "" when there is none."""
    words = text.split()
    return words[-1] if words else ""


def variant_file_prefix(app_name: str, version: str, source: str,
                        variant_id: str) -> str:
    """Filename prefix shared by every saved file of one variant."""
    return f"{app_name}-{version}-{source}-{variant_id}-"


def _matching_files(output_dir: str, app_name: str, prefixes: tuple) -> list:
    """Paths in the app folder whose filename starts with one of prefixes."""
    app_dir = os.path.join(output_dir, app_name)
    if not os.path.isdir(app_dir):
        return []

    return [
        os.path.join(app_dir, filename)
        for filename in os.listdir(app_dir)
        if filename.startswith(prefixes)
    ]


def _saved_files(output_dir: str, app_name: str, prefixes: tuple) -> list:
    """Paths of usable saved packages whose filename starts with prefixes.

    Empty files and anything that is not a package do not count as saved.
    """
    return [
        path for path in _matching_files(output_dir, app_name, prefixes)
        if path.endswith(PACKAGE_FILE_EXTENSIONS)
        and classify_package_zip(path) != "unknown"
    ]


def _source_version_files(output_dir: str, app_name: str, version: str,
                          source: str) -> list:
    """Paths of saved packages for any variant of version from source.

    Includes files saved before variant support ("{app}-{version}-{source}.apk").
    """
    stem = f"{app_name}-{version}-{source}"
    return sorted(_saved_files(output_dir, app_name, (stem + "-", stem + ".")))


def find_existing_variant(output_dir: str, app_name: str, version: str,
                          source: str, variant_id: str) -> Optional[str]:
    """Return the saved file for this variant, if one exists."""
    prefix = variant_file_prefix(app_name, version, source, variant_id)
    matches = _saved_files(output_dir, app_name, (prefix,))
    return matches[0] if matches else None


class ScraperError(Exception):
    """Base exception for scraper errors."""
    pass


class VersionNotFoundError(ScraperError):
    """Raised when the requested version is not found."""
    pass


class DownloadError(ScraperError):
    """Raised when download fails."""
    pass


class SourceBlockedError(ScraperError):
    """Raised when the source refuses the request or demands a bot check."""
    pass


class RateLimitError(SourceBlockedError):
    """Raised when rate limited by the source."""
    pass


class PlatformMismatchError(ScraperError):
    """Raised when a package does not support the requested platforms."""
    pass


def is_challenge_title(title: str) -> bool:
    """True when a page title belongs to a bot-check interstitial."""
    lowered = title.lower()
    return any(marker in lowered for marker in CHALLENGE_TITLES)


def package_problem(filepath: str) -> Optional[str]:
    """Explain why filepath is not a complete Android package, or None if it is."""
    if classify_package_zip(filepath) == "unknown":
        return "not a recognised Android package"

    try:
        with zipfile.ZipFile(filepath) as archive:
            # A package's first entry sits at byte 0. Anything later means
            # zipfile found the footer of an APK nested inside a bundle that
            # was cut off right after it.
            if min(info.header_offset for info in archive.infolist()) != 0:
                return "only part of the archive arrived"
            corrupt = archive.testzip()
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError,
            NotImplementedError, zlib.error) as e:
        return f"unreadable archive ({e})"

    return f"corrupt entry {corrupt}" if corrupt else None


def browser_environment(environ) -> dict:
    """Copy environ without GUI settings leaked by a snap-packaged parent.

    Terminals opened from a snap app such as VS Code inherit these, and they
    crash a visible Chrome at startup.
    """
    cleaned = {
        name: value for name, value in environ.items()
        if not (name in SNAP_GUI_VARIABLES and "/snap/" in value)
    }
    if "XDG_DATA_DIRS" in cleaned:
        cleaned["XDG_DATA_DIRS"] = ":".join(
            folder for folder in cleaned["XDG_DATA_DIRS"].split(":")
            if "/snap/" not in folder
        )
    return cleaned


def browser_launch_hint(environ) -> str:
    """Suggest the most likely fix for a failed browser launch."""
    if environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"):
        return (
            "Check that `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` points to a "
            "working Chrome/Chromium executable."
        )
    return (
        "Run `patchright install chromium` or set "
        "`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to a working Chrome/Chromium "
        "executable."
    )


@dataclass
class APKInfo:
    """Information about a downloaded Android package."""
    filepath: str
    version: str
    source: str
    size_bytes: int
    app_name: str
    package_type: str = "unknown"
    architectures: list = field(default_factory=list)
    variant_id: str = ""


@dataclass
class VariantRef:
    """One downloadable variant of a version, as listed by a source."""
    variant_id: str
    url: str
    label: str = ""


@dataclass
class ScrapeResult:
    """Outcome of fetching every variant of one version from one source."""
    downloaded: List[APKInfo] = field(default_factory=list)
    existing: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return bool(self.downloaded or self.existing)


def normalize_variant_ids(variants: List[VariantRef]) -> List[VariantRef]:
    """Make variant IDs filename-safe and unique within one listing.

    IDs end up between dashes in filenames, so dashes become underscores;
    repeated IDs get _2, _3, ... in listing order.
    """
    logger = logging.getLogger("variants")
    seen = {}
    normalized = []
    for variant in variants:
        base_id = variant.variant_id.replace("-", "_")
        count = seen.get(base_id, 0) + 1
        seen[base_id] = count
        variant_id = base_id if count == 1 else f"{base_id}_{count}"
        if count > 1:
            logger.warning(
                f"Duplicate variant ID {base_id!r}; using {variant_id!r}"
            )
        normalized.append(
            VariantRef(variant_id=variant_id, url=variant.url,
                       label=variant.label)
        )
    return normalized


class BaseScraper(ABC):
    """Abstract base class for APK scrapers."""

    SOURCE_NAME: str = "unknown"
    CHALLENGE_WAIT_SECONDS: int = 180
    DOWNLOAD_SAVE_TIMEOUT_SECONDS: int = 1200

    def __init__(self, output_dir: str = "./downloads", headless: bool = True,
                 platforms=()):
        self.output_dir = output_dir
        self.headless = headless
        self.platforms = platforms
        self.logger = logging.getLogger(self.__class__.__name__)
        os.makedirs(output_dir, exist_ok=True)

    def scrape(self, config: dict, version: str,
               force: bool = False) -> ScrapeResult:
        """
        Download every variant of a version that is not already saved.

        Args:
            config: Source-specific configuration from config.yaml
            version: Version string to download
            force: Re-download variants that already exist on disk

        Raises:
            VersionNotFoundError: the source lists no variants for version
            PlatformMismatchError: no variant supports the requested platforms
            DownloadError: every variant tried failed to download
        """
        app_name = self._app_name(config)
        playwright, browser, page = self._get_browser()

        try:
            try:
                variants = normalize_variant_ids(
                    self._list_variants(page, config, version)
                )
                if not variants:
                    raise VersionNotFoundError(
                        f"No variants found for {version} on {self.SOURCE_NAME}"
                    )
            except Exception as e:
                # A blocked or changed page must not make a version that is
                # already on disk fall through to (and re-download from) the
                # next source.
                saved = [] if force else [
                    path for path in _source_version_files(
                        self.output_dir, app_name, version, self.SOURCE_NAME,
                    )
                    if self._suits_platforms(path)
                ]
                if not saved:
                    raise
                self.logger.warning(
                    f"Could not list variants ({e}); keeping {len(saved)} "
                    f"file(s) already saved for {version}"
                )
                return ScrapeResult(existing=saved)
            self.logger.info(f"Found {len(variants)} variant(s)")

            result = ScrapeResult()
            for variant in variants:
                description = f"variant {variant.variant_id}"
                if variant.label:
                    description += f" ({variant.label})"

                if label_excludes_platforms(variant.label, self.platforms):
                    self.logger.info(
                        f"Skipping {description}: not for "
                        f"{', '.join(self.platforms)}"
                    )
                    continue

                existing = None if force else find_existing_variant(
                    self.output_dir, app_name, version, self.SOURCE_NAME,
                    variant.variant_id,
                )
                if existing and not self._suits_platforms(existing):
                    self.logger.info(
                        f"Skipping {description}: saved file is not for "
                        f"{', '.join(self.platforms)}"
                    )
                    continue
                if existing:
                    self.logger.info(f"Skipping {description}: {existing}")
                    result.existing.append(existing)
                    continue

                self.logger.info(f"Downloading {description}...")
                try:
                    info = self._download_variant(page, app_name, version,
                                                  variant)
                except PlatformMismatchError as e:
                    self.logger.info(f"Discarded {description}: {e}")
                    continue
                except Exception as e:
                    self.logger.warning(f"Failed {description}: {e}")
                    result.failed.append((variant.variant_id, str(e)))
                    continue

                self.logger.info(
                    f"Downloaded {description}: {info.filepath} "
                    f"({info.package_type}, {info.size_bytes:,} bytes)"
                )
                result.downloaded.append(info)

            if result.failed and not result.succeeded:
                reasons = "; ".join(f"{vid}: {reason}"
                                    for vid, reason in result.failed)
                raise DownloadError(
                    f"All {len(result.failed)} variant(s) failed ({reasons})"
                )
            if not result.succeeded:
                raise PlatformMismatchError(
                    f"No variant of {version} on {self.SOURCE_NAME} supports "
                    f"{', '.join(self.platforms)}"
                )
            return result

        except PlaywrightTimeout as e:
            self.logger.error(f"Timeout: {e}")
            raise DownloadError(f"Timeout while listing variants: {e}")
        finally:
            self._close_browser(playwright, browser)

    def newest_version(self, config: dict) -> str:
        """Return the highest release number the source lists for the app.

        Betas and anything else that is not a plain number are ignored.
        """
        self._app_name(config)
        playwright, browser, page = self._get_browser()
        try:
            releases = [version for version in self._list_versions(page, config)
                        if is_version_number(version)]
        finally:
            self._close_browser(playwright, browser)

        if not releases:
            raise ScraperError(f"{self.SOURCE_NAME} listed no version numbers")
        return max(releases, key=version_key)

    def _suits_platforms(self, path: str) -> bool:
        """True when a saved package supports the requested platforms."""
        return not self.platforms or supports_platforms(
            detect_architectures(path), self.platforms
        )

    @abstractmethod
    def _app_name(self, config: dict) -> str:
        """Validate config and return the name used for folders and files."""

    @abstractmethod
    def _list_versions(self, page, config: dict) -> List[str]:
        """Return the version numbers the source lists for the app, any order."""

    @abstractmethod
    def _list_variants(self, page, config: dict,
                       version: str) -> List[VariantRef]:
        """Return the variants the source lists for version.

        Raise VersionNotFoundError when the version does not exist.
        """

    @abstractmethod
    def _download_variant(self, page, app_name: str, version: str,
                          variant: VariantRef) -> APKInfo:
        """Download one variant and save it via _save_*_file.

        Raise DownloadError when the download cannot be completed.
        """

    def _get_browser(self) -> tuple:
        """Start the browser and return (playwright, context, page)."""
        playwright = sync_playwright().start()
        try:
            context = self._launch_browser(playwright)
            return playwright, context, context.pages[0]
        except Exception:
            playwright.stop()
            raise

    def _launch_browser(self, playwright):
        """Launch Chromium, falling back to a system Chrome when appropriate."""
        errors = []

        for label, executable_path in self._browser_candidates():
            launch_kwargs = {
                "headless": self.headless,
                "env": browser_environment(os.environ),
                "no_viewport": not self.headless,
            }
            if executable_path:
                launch_kwargs["executable_path"] = executable_path

            try:
                self.logger.debug(f"Launching browser: {label}")
                return playwright.chromium.launch_persistent_context(
                    BROWSER_PROFILE_DIR, **launch_kwargs
                )
            except PlaywrightError as e:
                message = str(e).splitlines()[0]
                errors.append(f"{label}: {message}")
                self.logger.debug(f"Browser launch failed for {label}: {e}")

        tried = "; ".join(errors) if errors else "no browser candidates found"
        raise DownloadError(
            f"Could not launch Chromium. Tried: {tried}. "
            f"{browser_launch_hint(os.environ)}"
        )

    def _browser_candidates(self):
        """Return browser launch candidates in priority order."""
        env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if env_path:
            return [(f"configured browser ({env_path})", env_path)]

        candidates = [("Patchright bundled Chromium", None)]
        seen = set()

        for candidate in SYSTEM_CHROME_CANDIDATES:
            if os.path.isabs(candidate):
                path = candidate if os.path.exists(candidate) else None
            else:
                path = shutil.which(candidate)

            if path and path not in seen:
                seen.add(path)
                candidates.append((f"system browser ({path})", path))

        return candidates
    
    def _close_browser(self, playwright, browser):
        """Close browser resources."""
        try:
            browser.close()
            playwright.stop()
        except Exception:
            pass

    def _open(self, page, url: str, timeout: int = 30000):
        """Navigate to url, raising SourceBlockedError if the source refuses."""
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        if is_challenge_title(page.title()):
            self._clear_challenge(page, url)
            return

        status = response.status if response else None
        if status == 429:
            raise RateLimitError(
                f"{self.SOURCE_NAME} rate limited the request for {url} "
                "(HTTP 429)"
            )
        if status in (403, 503):
            raise SourceBlockedError(
                f"{self.SOURCE_NAME} refused the request for {url} "
                f"(HTTP {status})"
            )

    def _clear_challenge(self, page, url: str):
        """Give a person time to pass a bot check in a visible browser."""
        if self.headless:
            raise SourceBlockedError(
                f"{self.SOURCE_NAME} answered {url} with a bot check that the "
                "headless browser cannot complete; try --no-headless"
            )

        self.logger.warning(
            f"Bot check on {self.SOURCE_NAME}. Complete it in the browser "
            f"window; waiting up to {self.CHALLENGE_WAIT_SECONDS}s"
        )
        deadline = time.monotonic() + self.CHALLENGE_WAIT_SECONDS
        while time.monotonic() < deadline:
            page.wait_for_timeout(1000)
            try:
                if not is_challenge_title(page.title()):
                    page.wait_for_load_state("domcontentloaded")
                    return
            except PlaywrightError:
                continue  # the page is navigating

        raise SourceBlockedError(
            f"The bot check on {self.SOURCE_NAME} was not completed within "
            f"{self.CHALLENGE_WAIT_SECONDS}s"
        )

    def _save_with_deadline(self, download, path: str):
        """Save a browser download, cancelling it if it outlasts the deadline.

        save_as has no timeout and blocks until the transfer ends. Navigation
        events from a timer tab still reach their handler during that block,
        which gives it the chance to cancel a stalled transfer. Patchright
        disables console events, so they cannot do this job.
        """
        deadline = time.monotonic() + self.DOWNLOAD_SAVE_TIMEOUT_SECONDS
        timed_out = []

        def on_tick(_frame):
            if not timed_out and time.monotonic() >= deadline:
                timed_out.append(True)
                download.cancel()

        timer = download.page.context.new_page()
        try:
            timer.on("framenavigated", on_tick)
            timer.evaluate(
                "setInterval(() => { location.hash = Date.now(); }, 1000)"
            )
            download.save_as(path)
        except PlaywrightError as e:
            if timed_out:
                raise DownloadError(
                    "Download did not finish within "
                    f"{self.DOWNLOAD_SAVE_TIMEOUT_SECONDS}s and was cancelled"
                ) from e
            raise
        finally:
            timer.close()

    def _save_downloaded_file(self, download, app_name: str, version: str,
                              variant_id: str) -> APKInfo:
        """Save a Playwright download and keep the detected package extension."""
        app_dir = os.path.join(self.output_dir, app_name)
        os.makedirs(app_dir, exist_ok=True)

        suggested_filename = getattr(download, "suggested_filename", "") or ""
        suggested_ext = self._extension_from_filename(suggested_filename)
        temp_suffix = suggested_ext if suggested_ext else ".download"
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{app_name}-{version}-{self.SOURCE_NAME}-",
            suffix=temp_suffix,
            dir=app_dir,
        )
        os.close(fd)

        try:
            self._save_with_deadline(download, temp_path)
            return self._finalize_downloaded_file(
                temp_path,
                app_name,
                version,
                variant_id,
                suggested_filename,
            )
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _save_url_file(self, url: str, app_name: str, version: str,
                       variant_id: str, referer: str = "",
                       timeout: int = 180) -> APKInfo:
        """Download a direct package URL and keep the detected extension."""
        app_dir = os.path.join(self.output_dir, app_name)
        os.makedirs(app_dir, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(
            prefix=f".{app_name}-{version}-{self.SOURCE_NAME}-",
            suffix=".download",
            dir=app_dir,
        )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        if referer:
            headers["Referer"] = referer

        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                suggested_filename = response.headers.get_filename() or ""
                declared_size = response.headers.get("Content-Length", "")
                with os.fdopen(fd, "wb") as file_obj:
                    fd = None
                    shutil.copyfileobj(response, file_obj)

            # urllib does not raise when a connection closes early.
            received = os.path.getsize(temp_path)
            if declared_size.isdigit() and received != int(declared_size):
                raise DownloadError(
                    f"Incomplete download: received {received:,} of "
                    f"{int(declared_size):,} bytes"
                )

            return self._finalize_downloaded_file(
                temp_path,
                app_name,
                version,
                variant_id,
                suggested_filename,
            )
        except Exception:
            if fd is not None:
                os.close(fd)
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _finalize_downloaded_file(self, temp_path: str, app_name: str,
                                  version: str, variant_id: str,
                                  suggested_filename: str = "") -> APKInfo:
        """Rename a temporary package file to its variant filename.

        Anything but a complete package is refused before an earlier file for
        the variant is touched. The name carries the detected extension and
        an ABI label, so an earlier file (e.g. with a different label) is
        removed once the new one is in place.
        """
        problem = package_problem(temp_path)
        if problem:
            raise DownloadError(f"Download is not usable: {problem}")

        package_type, extension = self._identify_package_file(
            temp_path,
            suggested_filename,
        )
        architectures = detect_architectures(temp_path)
        if not supports_platforms(architectures, self.platforms):
            raise PlatformMismatchError(
                f"the file supports {', '.join(architectures)}, not "
                f"{', '.join(self.platforms)}"
            )
        prefix = variant_file_prefix(app_name, version, self.SOURCE_NAME,
                                     variant_id)
        filename = f"{prefix}{arch_label(architectures)}{extension}"
        filepath = os.path.join(self.output_dir, app_name, filename)

        os.replace(temp_path, filepath)
        for other in _matching_files(self.output_dir, app_name, (prefix,)):
            if other != filepath:
                os.remove(other)

        return APKInfo(
            filepath=filepath,
            version=version,
            source=self.SOURCE_NAME,
            size_bytes=os.path.getsize(filepath),
            app_name=app_name,
            package_type=package_type,
            architectures=architectures,
            variant_id=variant_id,
        )

    def _extension_from_filename(self, filename: str) -> str:
        """Return a known package extension from a filename, if present."""
        safe_name = filename.split("?", 1)[0].strip().lower()
        _, ext = os.path.splitext(safe_name)
        if ext in PACKAGE_EXTENSIONS:
            return ext
        return ""

    def _identify_package_file(self, filepath: str,
                               suggested_filename: str = "") -> Tuple[str, str]:
        """Identify package type and extension from content plus browser hint."""
        suggested_ext = self._extension_from_filename(suggested_filename)
        suggested_type = PACKAGE_EXTENSIONS.get(suggested_ext)
        content_type = self._classify_package_zip(filepath)

        if content_type == "apk":
            return "apk", ".apk"
        if content_type == "xapk":
            return "xapk", ".xapk"
        if content_type == "aab":
            return "aab", ".aab"

        if content_type == "split-apk":
            if suggested_type in {"apkm", "apks", "xapk"}:
                return suggested_type, suggested_ext
            return "apks", ".apks"

        raise DownloadError(
            f"Unrecognised package content in {os.path.basename(filepath)}"
        )

    def _classify_package_zip(self, filepath: str) -> str:
        """Classify common Android package containers by ZIP contents."""
        return classify_package_zip(filepath)
    
    def _wait(self, seconds: float = 1.0):
        """Wait for the specified time (helps avoid rate limiting)."""
        time.sleep(seconds)


def setup_logging(level: int = logging.INFO):
    """Configure logging for the scraper."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)-15s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S"
    )
