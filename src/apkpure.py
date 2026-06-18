#!/usr/bin/env python3
"""APKPure scraper using Playwright browser automation."""

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


class APKPureScraper(BaseScraper):
    """Scraper for APKPure.net using Playwright."""
    
    SOURCE_NAME = "apkpure"
    BASE_URL = "https://m.apkpure.com"

    def _find_version_link(self, page, version: str) -> Optional[str]:
        """Find a version-specific APKPure link from all anchors."""
        version_slug = version.replace(".", "-")
        candidates = []

        for link in page.query_selector_all("a"):
            text = link.inner_text().strip()
            href = link.get_attribute("href") or ""
            if not href or href == "#" or href.lower().startswith("javascript:"):
                continue

            text_lower = text.lower()
            href_lower = href.lower()
            has_version = (
                version in text_lower
                or version in href_lower
                or version_slug in text_lower
                or version_slug in href_lower
            )
            if not has_version:
                continue

            score = 0
            if "download" in href_lower:
                score += 4
            if "/versions" in href_lower:
                score += 2
            if version in text_lower:
                score += 2
            if version_slug in href_lower:
                score += 1
            if "variant" in href_lower:
                score -= 1

            candidates.append((score, href, text))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, href, text = candidates[0]
        self.logger.debug(
            f"Matched APKPure version link score={score}, href={href!r}, "
            f"text={text[:80]!r}"
        )
        return href

    def _find_package_download_link(self, page, package: str) -> Optional[str]:
        """Find the direct APK/XAPK download URL for the requested package."""
        package_lower = package.lower()
        candidates = []

        for link in page.query_selector_all("a"):
            href = link.get_attribute("href") or ""
            href_lower = href.lower()
            if package_lower not in href_lower:
                continue
            if "d.apkpure.com/b/" not in href_lower:
                continue

            text = link.inner_text().strip()
            text_lower = text.lower()
            score = 0
            if "/b/apk/" in href_lower:
                score += 4
            if "/b/xapk/" in href_lower:
                score += 3
            if "download apk" in text_lower:
                score += 3
            if "download" in text_lower:
                score += 1

            candidates.append((score, link, href, text))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, _link, href, text = candidates[0]
        self.logger.debug(
            f"Matched APKPure package download score={score}, href={href!r}, "
            f"text={text[:80]!r}"
        )
        return href
    
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
            self.logger.info("Searching for version links...")
            version_link = self._find_version_link(page, version)
            
            if not version_link:
                if "cloudflare" in content.lower() or "verify you are human" in content.lower():
                    self.logger.error("Blocked by Cloudflare on APKPure")
                raise VersionNotFoundError(f"Version {version} not found on APKPure")

            self.logger.info("Found version link")
            
            # Step 3: Navigate to download page
            if not version_link.startswith("http"):
                version_link = self.BASE_URL + version_link
            
            page.goto(version_link, wait_until="domcontentloaded", timeout=30000)
            self._wait(2)
            
            # Step 4: Find the direct package download URL
            download_url = self._find_package_download_link(page, package)
            
            if not download_url:
                raise DownloadError(
                    "Direct package download link not found on APKPure "
                    "version page"
                )
            
            # Step 5: Download
            self.logger.info("Starting download...")
            app_name = app_slug.split("/")[-1] if "/" in app_slug else app_slug
            try:
                filepath, package_type = self._save_url_file(
                    download_url,
                    app_name,
                    version,
                    referer=page.url,
                )
            except Exception as e:
                raise DownloadError(f"Direct package download failed: {e}") from e
            file_size = os.path.getsize(filepath)
            
            self.logger.info(
                f"Downloaded: {filepath} ({package_type}, {file_size:,} bytes)"
            )
            
            return APKInfo(
                filepath=filepath,
                version=version,
                source=self.SOURCE_NAME,
                size_bytes=file_size,
                app_name=app_name,
                package_type=package_type,
                architectures=detect_architectures(filepath),
            )
            
        except PlaywrightTimeout as e:
            self.logger.error(f"Timeout: {e}")
            raise DownloadError(f"Timeout while downloading: {e}")
        finally:
            self._close_browser(playwright, browser)
