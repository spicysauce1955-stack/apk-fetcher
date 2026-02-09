#!/usr/bin/env python3
"""
Base scraper abstraction with common functionality.
"""

import os
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

from playwright.sync_api import sync_playwright, Browser, Page


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
    """Information about a downloaded APK."""
    filepath: str
    version: str
    source: str
    size_bytes: int
    app_name: str


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
        browser = playwright.chromium.launch(headless=self.headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        return playwright, browser, page
    
    def _close_browser(self, playwright, browser):
        """Close browser resources."""
        try:
            browser.close()
            playwright.stop()
        except Exception:
            pass
    
    def _save_downloaded_file(self, download, app_name: str, version: str) -> str:
        """Save a Playwright download to disk with source-specific filename."""
        filename = f"{app_name}-{version}-{self.SOURCE_NAME}.apk"
        filepath = os.path.join(self.output_dir, filename)
        download.save_as(filepath)
        return filepath
    
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
