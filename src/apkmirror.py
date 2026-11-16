#!/usr/bin/env python3
"""APKMirror scraper using Playwright browser automation."""

from typing import List

from bs4 import BeautifulSoup

from .base import (
    BaseScraper,
    APKInfo,
    VariantRef,
    VersionNotFoundError,
    DownloadError,
    last_word,
)


BASE_URL = "https://www.apkmirror.com"
VARIANT_SUFFIX = "-android-apk-download"


def _apkmirror_variant_id(href: str, app_name: str, version: str) -> str:
    """Derive a stable ID from a variant URL.

    APKMirror numbers extra variants in the slug: "telegram-13-0-3-android-
    apk-download" is the first one ("1"), "telegram-13-0-3-2-android-apk-
    download" the second ("2"). Unexpected slugs fall back to the slug itself.
    """
    slug = href.rstrip("/").rsplit("/", 1)[-1]
    if slug.endswith(VARIANT_SUFFIX):
        slug = slug[: -len(VARIANT_SUFFIX)]

    version_prefix = f"{app_name}-{version.replace('.', '-')}"
    if slug == version_prefix:
        return "1"
    if slug.startswith(version_prefix + "-"):
        number = slug[len(version_prefix) + 1:]
        if number.isdigit():
            return number
    return slug


def parse_apkmirror_variants(html: str, app_name: str,
                             version: str) -> List[VariantRef]:
    """Parse the variants table of an APKMirror release page."""
    soup = BeautifulSoup(html, "lxml")
    variants = []
    seen_hrefs = set()

    for row in soup.select("div.variants-table div.table-row"):
        link = row.select_one("a.accent_color")
        href = link.get("href", "") if link else ""
        if VARIANT_SUFFIX not in href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        badge = row.select_one(".apkm-badge")
        details = [cell.get_text(" ", strip=True)
                   for cell in row.select("div.table-cell")[1:]]
        label_parts = [badge.get_text(strip=True)] if badge else []
        label_parts.extend(part for part in details if part)

        variants.append(VariantRef(
            variant_id=_apkmirror_variant_id(href, app_name, version),
            url=BASE_URL + href if href.startswith("/") else href,
            label=", ".join(label_parts),
        ))

    return variants


def parse_apkmirror_versions(html: str, developer: str,
                             app_name: str) -> List[str]:
    """Read release titles from an app's APKMirror page.

    The page also lists other apps, which link elsewhere. A release title ends
    in its version, so the last word of a beta's title is "beta".
    """
    prefix = f"/apk/{developer}/{app_name}/{app_name}-"
    titles = [link.get_text(" ", strip=True)
              for link in BeautifulSoup(html, "lxml").select("a[href]")
              if link["href"].startswith(prefix)
              and link["href"].rstrip("/").endswith("-release")]
    return [last_word(title) for title in titles if title]


class APKMirrorScraper(BaseScraper):
    """Scraper for APKMirror.com using Playwright."""

    SOURCE_NAME = "apkmirror"
    BASE_URL = BASE_URL
    _warned_variant_index = False  # warn once per run, not once per version

    def _app_name(self, config: dict) -> str:
        developer = config.get("developer")
        app_name = config.get("app_name")

        if not developer or not app_name:
            raise ValueError("APKMirror config requires 'developer' and 'app_name'")
        if "variant_index" in config and not APKMirrorScraper._warned_variant_index:
            APKMirrorScraper._warned_variant_index = True
            self.logger.warning(
                "'variant_index' is no longer used; all variants are downloaded"
            )
        return app_name

    def _list_versions(self, page, config: dict) -> List[str]:
        developer = config["developer"]
        app_name = config["app_name"]
        self._open(page, f"{self.BASE_URL}/apk/{developer}/{app_name}/")
        self._wait(2)
        return parse_apkmirror_versions(page.content(), developer, app_name)

    def _list_variants(self, page, config: dict,
                       version: str) -> List[VariantRef]:
        """
        List every variant on the APKMirror release page.

        Config requires:
            - developer: Developer slug (e.g., "telegram-fz-llc")
            - app_name: App name in URL (e.g., "telegram")
        """
        developer = config["developer"]
        app_name = config["app_name"]
        version_slug = version.replace(".", "-")
        version_url = f"{self.BASE_URL}/apk/{developer}/{app_name}/{app_name}-{version_slug}-release/"

        self.logger.info(f"Fetching {app_name} v{version} from APKMirror")
        self.logger.debug(f"Version URL: {version_url}")

        self._open(page, version_url)
        self._wait(2)

        # Check for 404
        if "not found" in page.title().lower():
            raise VersionNotFoundError(f"Version {version} not found on APKMirror")

        return parse_apkmirror_variants(page.content(), app_name, version)

    def _download_variant(self, page, app_name: str, version: str,
                          variant: VariantRef) -> APKInfo:
        # Step 1: Open the variant page
        self._open(page, variant.url)
        self._wait(2)

        # Step 2: Find download button
        download_btn = page.query_selector("a.downloadButton")
        if not download_btn:
            raise DownloadError("Download button not found on variant page")

        download_page_href = download_btn.get_attribute("href")
        self._open(page, self.BASE_URL + download_page_href)
        self._wait(3)

        # Step 3: Find final download link
        download_link = page.query_selector("a#download-link") or page.query_selector("a[rel='nofollow']")
        if not download_link:
            raise DownloadError("Final download link not found")

        # Step 4: Download the file
        with page.expect_download(timeout=180000) as download_info:
            download_link.click()

        return self._save_downloaded_file(
            download_info.value,
            app_name,
            version,
            variant.variant_id,
        )
