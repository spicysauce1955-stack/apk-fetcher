#!/usr/bin/env python3
"""Uptodown scraper using Playwright browser automation."""

import os
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import (
    BaseScraper,
    APKInfo,
    VersionNotFoundError,
    DownloadError,
    detect_architectures,
)


class UptodownScraper(BaseScraper):
    """Scraper for Uptodown.com using Playwright."""
    
    SOURCE_NAME = "uptodown"
    DOWNLOAD_BUTTON_SELECTOR = (
        "button#detail-download-button, "
        "a.button.download.green, "
        "a[href*='/android/download/']"
    )
    
    def _get_versions_url(self, app_slug: str) -> str:
        return f"https://{app_slug}.en.uptodown.com/android/versions"

    def _find_download_button(self, page, timeout: int = 20000):
        """Find the current Uptodown download trigger."""
        return page.wait_for_selector(self.DOWNLOAD_BUTTON_SELECTOR,
                                      timeout=timeout)

    def _start_download(self, page):
        """Click Uptodown's download trigger with a re-querying retry."""
        for attempt in range(2):
            download_btn = self._find_download_button(page)
            if not download_btn:
                raise DownloadError(
                    "Could not find the main Download button on the "
                    "version page"
                )

            timeout = 25000 if attempt == 0 else 60000
            try:
                with page.expect_download(timeout=timeout) as download_info:
                    download_btn.click(force=True, delay=500)
                return download_info.value
            except PlaywrightTimeout:
                self.logger.warning(
                    "Download was not emitted after click; re-querying the "
                    "button and trying again"
                )
                self.logger.debug(
                    f"After missed click: url={page.url!r}, title={page.title()!r}"
                )
                if attempt == 1:
                    raise
                self._wait(2)

        raise DownloadError("Uptodown download did not start")
    
    def scrape(self, config: dict, version: str) -> Optional[APKInfo]:
        """
        Download APK from Uptodown.
        
        Config requires:
            - app_slug: App slug for URL (e.g., "telegram")
        """
        app_slug = config.get("app_slug")
        
        if not app_slug:
            raise ValueError("Uptodown config requires 'app_slug'")
        
        versions_url = self._get_versions_url(app_slug)
        self.logger.info(f"Fetching {app_slug} v{version} from Uptodown")
        
        playwright, browser, page = self._get_browser()
        
        try:
            # Step 1: Load versions page
            self.logger.info(f"Loading versions page: {versions_url}")
            page.goto(versions_url, wait_until="domcontentloaded", timeout=30000)
            self._wait(5) # Give more time for Cloudflare/scripts
            
            self.logger.debug(f"Page title: {page.title()}")
            
            # Step 2: Find the target version
            version_link = None
            
            # Look for version in the list
            self.logger.info("Searching for version in the list...")
            # Uptodown uses data-url on div or a tags in the version list
            version_items = page.query_selector_all("div[data-url], a[data-url]")
            self.logger.debug(f"Found {len(version_items)} version items")
            
            if not version_items:
                # Fallback to broad selectors if specific ones fail
                version_items = page.query_selector_all(".version, .item, a")
                self.logger.debug(f"Fallback: found {len(version_items)} items")

            for item in version_items:
                text = item.inner_text().lower()
                data_url = item.get_attribute("data-url") or ""
                href = item.get_attribute("href") or ""
                version_id = item.get_attribute("data-version-id")
                
                # Check for version in text or attributes
                if version in text or (version_id and version in text):
                    if version_id:
                        # Construct the specific download page URL
                        version_link = f"https://{app_slug}.en.uptodown.com/android/download/{version_id}"
                        self.logger.info(f"Matched version {version} via ID: {version_id}")
                        break
                    
                    # Fallback to data-url or href if ID not found
                    if version in text or version in data_url or version in href:
                        potential_link = data_url or href
                        if potential_link and "/android/versions" not in potential_link and potential_link != f"https://{app_slug}.en.uptodown.com/android":
                            version_link = potential_link
                            self.logger.info(f"Matched version {version} in text/link")
                            break
            
            if not version_link:
                # Final fallback: search specifically for the version
                self.logger.info("Trying fallback specific version link search...")
                all_links = page.query_selector_all("a")
                for link in all_links:
                    href = link.get_attribute("href") or ""
                    text = link.inner_text().lower()
                    if version in text or f"download-{version.replace('.', '-')}" in href:
                        version_link = href
                        self.logger.info("Matched via fallback specific search")
                        break
            
            if not version_link:
                raise VersionNotFoundError(f"Could not find version {version} for {app_slug} on Uptodown")

            # Step 4: Navigate to version page
            self.logger.info(f"Navigating to version link: {version_link}")
            page.goto(version_link, wait_until="domcontentloaded", timeout=45000)
            self._wait(5)
            
            # Step 5: Find and click the large "Download" button on the version page
            self.logger.info("Looking for the main download button...")
            try:
                self._find_download_button(page)
            except Exception as e:
                self.logger.error(f"Download button not found on Uptodown. Page title: {page.title()}")
                # Log snippet
                content = page.content()
                self.logger.debug(f"First 500 chars of page: {content[:500]}")
                raise DownloadError(f"Could not find the main Download button: {e}")

            self.logger.info("Clicking the Download button...")
            download = self._start_download(page)
            
            filepath, package_type = self._save_downloaded_file(
                download,
                app_slug,
                version,
            )
            file_size = os.path.getsize(filepath)
            self.logger.info(
                f"Downloaded: {filepath} ({package_type}, {file_size:,} bytes)"
            )
            
            return APKInfo(
                filepath=filepath,
                version=version,
                source=self.SOURCE_NAME,
                size_bytes=file_size,
                app_name=app_slug,
                package_type=package_type,
                architectures=detect_architectures(filepath),
            )
            
        except PlaywrightTimeout as e:
            self.logger.error(f"Timeout: {e}")
            raise DownloadError(f"Timeout while downloading: {e}")
        finally:
            self._close_browser(playwright, browser)
