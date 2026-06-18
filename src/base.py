#!/usr/bin/env python3
"""
Base scraper abstraction with common functionality.
"""

import io
import os
import shutil
import tempfile
import time
import logging
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Tuple
from urllib.request import Request, urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


PACKAGE_EXTENSIONS = {
    ".apk": "apk",
    ".xapk": "xapk",
    ".apkm": "apkm",
    ".apks": "apks",
    ".aab": "aab",
}

PACKAGE_FILE_EXTENSIONS = tuple(PACKAGE_EXTENSIONS.keys())

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


class ScraperError(Exception):
    """Base exception for scraper errors."""
    pass


class VersionNotFoundError(ScraperError):
    """Raised when the requested version is not found."""
    pass


class DownloadError(ScraperError):
    """Raised when download fails."""
    pass


class RateLimitError(ScraperError):
    """Raised when rate limited by the source."""
    pass


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


class BaseScraper(ABC):
    """Abstract base class for APK scrapers."""
    
    SOURCE_NAME: str = "unknown"
    
    def __init__(self, output_dir: str = "./downloads", headless: bool = True):
        self.output_dir = output_dir
        self.headless = headless
        self.logger = logging.getLogger(self.__class__.__name__)
        os.makedirs(output_dir, exist_ok=True)
    
    @abstractmethod
    def scrape(self, config: dict, version: str) -> Optional[APKInfo]:
        """
        Scrape and download an APK.
        
        Args:
            config: Source-specific configuration from config.yaml
            version: Version string to download
            
        Returns:
            APKInfo on success, None on failure
        """
        pass
    
    def _get_browser(self) -> tuple:
        """Create a Playwright browser instance."""
        playwright = sync_playwright().start()
        try:
            browser = self._launch_browser(playwright)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            return playwright, browser, page
        except Exception:
            playwright.stop()
            raise

    def _launch_browser(self, playwright):
        """Launch Chromium, falling back to a system Chrome when appropriate."""
        errors = []

        for label, executable_path in self._browser_candidates():
            launch_kwargs = {"headless": self.headless}
            if executable_path:
                launch_kwargs["executable_path"] = executable_path

            try:
                self.logger.debug(f"Launching browser: {label}")
                return playwright.chromium.launch(**launch_kwargs)
            except PlaywrightError as e:
                message = str(e).splitlines()[0]
                errors.append(f"{label}: {message}")
                self.logger.debug(f"Browser launch failed for {label}: {e}")

        tried = "; ".join(errors) if errors else "no browser candidates found"
        raise DownloadError(
            "Could not launch Chromium. Tried: "
            f"{tried}. Run `playwright install chromium` or set "
            "`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to a working "
            "Chrome/Chromium executable."
        )

    def _browser_candidates(self):
        """Return browser launch candidates in priority order."""
        env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if env_path:
            return [(f"configured browser ({env_path})", env_path)]

        candidates = [("Playwright bundled Chromium", None)]
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
    
    def _save_downloaded_file(self, download, app_name: str,
                              version: str) -> Tuple[str, str]:
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
            download.save_as(temp_path)
            return self._finalize_downloaded_file(
                temp_path,
                app_name,
                version,
                suggested_filename,
            )
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _save_url_file(self, url: str, app_name: str, version: str,
                       referer: str = "", timeout: int = 180) -> Tuple[str, str]:
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
                with os.fdopen(fd, "wb") as file_obj:
                    fd = None
                    shutil.copyfileobj(response, file_obj)

            return self._finalize_downloaded_file(
                temp_path,
                app_name,
                version,
                suggested_filename,
            )
        except Exception:
            if fd is not None:
                os.close(fd)
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def _finalize_downloaded_file(self, temp_path: str, app_name: str,
                                  version: str,
                                  suggested_filename: str = "") -> Tuple[str, str]:
        """Rename a temporary package file using the detected extension."""
        package_type, extension = self._identify_package_file(
            temp_path,
            suggested_filename,
        )
        filename = f"{app_name}-{version}-{self.SOURCE_NAME}{extension}"
        filepath = os.path.join(self.output_dir, app_name, filename)

        if os.path.exists(filepath):
            os.remove(filepath)
        os.replace(temp_path, filepath)
        return filepath, package_type

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

        if suggested_type:
            return suggested_type, suggested_ext

        return "unknown", ".download"

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
