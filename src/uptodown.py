#!/usr/bin/env python3
"""Uptodown scraper using Playwright browser automation."""

from typing import List

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import (
    BaseScraper,
    APKInfo,
    VariantRef,
    VersionNotFoundError,
    DownloadError,
)


def parse_uptodown_variants(html: str, app_slug: str,
                            version: str) -> List[VariantRef]:
    """Parse the versions list; each row matching version is one variant.

    Uptodown lists a version once per variant, and the rows differ only by
    data-version-id, which also addresses the variant's download page.
    """
    soup = BeautifulSoup(html, "lxml")
    variants = []

    for row in soup.select("[data-version-id]"):
        version_el = row.select_one(".version")
        if not version_el or version_el.get_text(strip=True) != version:
            continue

        version_id = row["data-version-id"].strip()
        type_el = row.select_one(".type")
        variants.append(VariantRef(
            variant_id=version_id,
            url=f"https://{app_slug}.en.uptodown.com/android/download/{version_id}",
            label=type_el.get_text(strip=True) if type_el else "",
        ))

    return variants


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
    
    def _app_name(self, config: dict) -> str:
        app_slug = config.get("app_slug")
        if not app_slug:
            raise ValueError("Uptodown config requires 'app_slug'")
        return app_slug

    def _list_variants(self, page, config: dict,
                       version: str) -> List[VariantRef]:
        """
        List every Uptodown row for the requested version.
        
        Config requires:
            - app_slug: App slug for URL (e.g., "telegram")
        """
        app_slug = config["app_slug"]
        versions_url = self._get_versions_url(app_slug)
        self.logger.info(f"Fetching {app_slug} v{version} from Uptodown")
        
        self.logger.info(f"Loading versions page: {versions_url}")
        page.goto(versions_url, wait_until="domcontentloaded", timeout=30000)
        self._wait(5) # Give more time for Cloudflare/scripts
        
        self.logger.debug(f"Page title: {page.title()}")
        
        variants = parse_uptodown_variants(page.content(), app_slug, version)
        if not variants:
            raise VersionNotFoundError(f"Could not find version {version} for {app_slug} on Uptodown")
        return variants

    def _download_variant(self, page, app_name: str, version: str,
                          variant: VariantRef) -> APKInfo:
        self.logger.info(f"Navigating to variant page: {variant.url}")
        page.goto(variant.url, wait_until="domcontentloaded", timeout=45000)
        self._wait(5)
        
        # Find and click the large "Download" button on the version page
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
        
        return self._save_downloaded_file(
            download,
            app_name,
            version,
            variant.variant_id,
        )
