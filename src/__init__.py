"""APK Scraper - Download specific APK versions from multiple sources."""

from .base import (
    BaseScraper,
    APKInfo,
    ScraperError,
    VersionNotFoundError,
    DownloadError,
    SourceBlockedError,
    RateLimitError,
    PlatformMismatchError,
    PACKAGE_FILE_EXTENSIONS,
    PLATFORM_ABIS,
    is_version_number,
    version_key,
    setup_logging,
    detect_architectures,
    classify_package_zip,
    arch_label,
    find_existing_variant,
    VariantRef,
    ScrapeResult,
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
    "SourceBlockedError",
    "RateLimitError",
    "PlatformMismatchError",
    "PACKAGE_FILE_EXTENSIONS",
    "PLATFORM_ABIS",
    "is_version_number",
    "version_key",
    "APKMirrorScraper",
    "UptodownScraper",
    "APKPureScraper",
    "load_config",
    "AppConfig",
    "setup_logging",
    "detect_architectures",
    "classify_package_zip",
    "arch_label",
    "find_existing_variant",
    "VariantRef",
    "ScrapeResult",
]

# Source priority order for fallback
SOURCE_PRIORITY = ["apkmirror", "uptodown", "apkpure"]

SCRAPERS = {
    "apkmirror": APKMirrorScraper,
    "uptodown": UptodownScraper,
    "apkpure": APKPureScraper,
}
