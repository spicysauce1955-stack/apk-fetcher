"""APK Scraper - Download specific APK versions from multiple sources."""

from .base import (
    BaseScraper,
    APKInfo,
    ScraperError,
    VersionNotFoundError,
    DownloadError,
    RateLimitError,
    setup_logging,
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
    "APKMirrorScraper",
    "UptodownScraper",
    "APKPureScraper",
    "load_config",
    "AppConfig",
    "setup_logging",
]

# Source priority order for fallback
SOURCE_PRIORITY = ["apkmirror", "uptodown", "apkpure"]

SCRAPERS = {
    "apkmirror": APKMirrorScraper,
    "uptodown": UptodownScraper,
    "apkpure": APKPureScraper,
}
