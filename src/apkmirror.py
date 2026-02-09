#!/usr/bin/env python3
"""APKMirror scraper using Playwright browser automation."""

import os
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import BaseScraper, APKInfo, VersionNotFoundError, DownloadError


class APKMirrorScraper(BaseScraper):
    """Scraper for APKMirror.com using Playwright."""
    
    SOURCE_NAME = "apkmirror"
    BASE_URL = "https://www.apkmirror.com"
    
    def scrape(self, config: dict, version: str) -> Optional[APKInfo]:
        """
        Download APK from APKMirror.
        
        Config requires:
            - developer: Developer slug (e.g., "telegram-fz-llc")
            - app_name: App name in URL (e.g., "telegram")
        """
        developer = config.get("developer")
        app_name = config.get("app_name")
        variant_index = config.get("variant_index", 0)
        
        if not developer or not app_name:
            raise ValueError("APKMirror config requires 'developer' and 'app_name'")
        
        version_slug = version.replace(".", "-")
        version_url = f"{self.BASE_URL}/apk/{developer}/{app_name}/{app_name}-{version_slug}-release/"
        
        self.logger.info(f"Fetching {app_name} v{version} from APKMirror")
        self.logger.debug(f"Version URL: {version_url}")
        
        playwright, browser, page = self._get_browser()
        
        try:
            # Step 1: Load version page
            page.goto(version_url, wait_until="domcontentloaded", timeout=30000)
            self._wait(2)
            
            # Check for 404
            if "not found" in page.title().lower():
                raise VersionNotFoundError(f"Version {version} not found on APKMirror")
            
            # Step 2: Find variant links
            variants = page.query_selector_all("a.accent_color")
            variant_links = []
            
            for v in variants:
                href = v.get_attribute("href")
                text = v.inner_text().strip()
                if href and "-android-apk-download" in href:
                    variant_links.append({"href": href, "text": text})
            
            if not variant_links:
                raise VersionNotFoundError(f"No APK variants found for {version}")
            
            self.logger.info(f"Found {len(variant_links)} variant(s)")
            
            # Step 3: Select and navigate to variant
            if variant_index >= len(variant_links):
                variant_index = 0
            selected = variant_links[variant_index]
            
            self.logger.info(f"Selecting variant: {selected['text'][:50]}")
            page.goto(self.BASE_URL + selected["href"], wait_until="domcontentloaded", timeout=30000)
            self._wait(2)
            
            # Step 4: Find download button
            download_btn = page.query_selector("a.downloadButton")
            if not download_btn:
                raise DownloadError("Download button not found on variant page")
            
            download_page_href = download_btn.get_attribute("href")
            page.goto(self.BASE_URL + download_page_href, wait_until="domcontentloaded", timeout=30000)
            self._wait(3)
            
            # Step 5: Find final download link
            download_link = page.query_selector("a#download-link") or page.query_selector("a[rel='nofollow']")
            if not download_link:
                raise DownloadError("Final download link not found")
            
            # Step 6: Download the file
            self.logger.info("Starting download...")
            
            with page.expect_download(timeout=180000) as download_info:
                download_link.click()
            
            download = download_info.value
            filepath = self._save_downloaded_file(download, app_name, version)
            file_size = os.path.getsize(filepath)
            
            self.logger.info(f"Downloaded: {filepath} ({file_size:,} bytes)")
            
            return APKInfo(
                filepath=filepath,
                version=version,
                source=self.SOURCE_NAME,
                size_bytes=file_size,
                app_name=app_name
            )
            
        except PlaywrightTimeout as e:
            self.logger.error(f"Timeout: {e}")
            raise DownloadError(f"Timeout while downloading: {e}")
        finally:
            self._close_browser(playwright, browser)
