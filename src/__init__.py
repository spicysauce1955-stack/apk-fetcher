"""APK Scraper - Download specific APK versions from multiple sources."""

from .base import (
    BaseScraper,
    APKInfo,
    ScraperError,
    VersionNotFoundError,
    DownloadError,
    RateLimitError,
    PACKAGE_FILE_EXTENSIONS,
    setup_logging,
    detect_architectures,
    classify_package_zip,
)
from .apkmirror import APKMirrorScraper
from .uptodown import UptodownScraper
from .apkpure import APKPureScraper
from .config import load_config, AppConfig

__all__ = [
    "BaseScraper",
    "APKInfo",
    "ScraperError",
    "VersionNotFoundError", 
    "DownloadError",
    "RateLimitError",
    "PACKAGE_FILE_EXTENSIONS",
    "APKMirrorScraper",
    "UptodownScraper",
    "APKPureScraper",
    "load_config",
    "AppConfig",
    "setup_logging",
    "detect_architectures",
    "classify_package_zip",
]

# Source priority order for fallback
SOURCE_PRIORITY = ["apkmirror", "uptodown", "apkpure"]

SCRAPERS = {
    "apkmirror": APKMirrorScraper,
    "uptodown": UptodownScraper,
    "apkpure": APKPureScraper,
}
