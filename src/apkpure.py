#!/usr/bin/env python3
"""APKPure scraper using Playwright browser automation."""

import os
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import BaseScraper, APKInfo, VersionNotFoundError, DownloadError


class APKPureScraper(BaseScraper):
    """Scraper for APKPure.net using Playwright."""
    
    SOURCE_NAME = "apkpure"
    BASE_URL = "https://apkpure.net"
    
    def scrape(self, config: dict, version: str) -> Optional[APKInfo]:
        """
        Download APK from APKPure.
        
        Config requires:
            - package: Package name (e.g., "org.telegram.messenger")
            - app_slug: Optional app slug for URL (e.g., "telegram")
        """
        package = config.get("package")
        app_slug = config.get("app_slug", package)
        
        if not package:
            raise ValueError("APKPure config requires 'package'")
        
        # APKPure URL format: /app-name/package-name/versions
        versions_url = f"{self.BASE_URL}/{app_slug}/{package}/versions"
        
        self.logger.info(f"Fetching {package} v{version} from APKPure")
        
        playwright, browser, page = self._get_browser()
        
        try:
            # Step 1: Load versions page
            self.logger.info(f"Loading versions page: {versions_url}")
            page.goto(versions_url, wait_until="domcontentloaded", timeout=30000)
            self._wait(5)
            
            self.logger.debug(f"Page title: {page.title()}")
            content = page.content()
            
            # Step 2: Find the target version
            version_link = None
            
            # Look for version in the list
            self.logger.info("Searching for version in the list...")
            version_items = page.query_selector_all("a.ver-item, a.version-item, li.ver a")
            self.logger.debug(f"Found {len(version_items)} version items")
            
            if not version_items:
                if "cloudflare" in content.lower() or "verify you are human" in content.lower():
                    self.logger.error("Blocked by Cloudflare on APKPure")
                else:
                    self.logger.debug(f"First 500 chars of page: {content[:500]}")
            for item in version_items:
                text = item.inner_text()
                if version in text:
                    version_link = item.get_attribute("href")
                    break
            
            # Fallback: search all links for version
            if not version_link:
                all_links = page.query_selector_all("a[href*='download']")
                for link in all_links:
                    text = link.inner_text()
                    href = link.get_attribute("href") or ""
                    if version in text or version.replace(".", "-") in href:
                        version_link = href
                        break
            
            if not version_link:
                raise VersionNotFoundError(f"Version {version} not found on APKPure")
            
            self.logger.info(f"Found version link")
            
            # Step 3: Navigate to download page
            if not version_link.startswith("http"):
                version_link = self.BASE_URL + version_link
            
            page.goto(version_link, wait_until="domcontentloaded", timeout=30000)
            self._wait(2)
            
            # Step 4: Find and click download button
            download_btn = page.query_selector("a.download-btn, a[href*='APK/download']")
            
            if not download_btn:
                # Try more generic selectors
                download_btn = page.query_selector("a.download-btn, a[href*='download'], button.download")
            
            if not download_btn:
                raise DownloadError("Download button not found on APKPure version page")
            
            # Step 5: Download
            self.logger.info("Starting download...")
            
            with page.expect_download(timeout=180000) as download_info:
                download_btn.click()
            
            download = download_info.value
            app_name = app_slug.split("/")[-1] if "/" in app_slug else app_slug
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
