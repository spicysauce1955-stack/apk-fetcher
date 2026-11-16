#!/usr/bin/env python3
"""APKPure scraper using Playwright browser automation."""

import re
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .base import (
    BaseScraper,
    APKInfo,
    VariantRef,
    VersionNotFoundError,
    DownloadError,
    PlatformMismatchError,
    last_word,
)


def _mentions_version(haystack: str, version: str, sep: str = ".") -> bool:
    """True when haystack contains version as a whole version number.

    "12.2.1" matches "Telegram 12.2.1" and ".../download/12.2.1" but not
    "12.2.11" or "112.2.1"; sep="-" does the same for "12-2-1" slugs.
    """
    s = re.escape(sep)
    pattern = rf"(?<!\d)(?<!\d{s}){re.escape(version)}(?!\d)(?!{s}\d)"
    return re.search(pattern, haystack) is not None


def _apkpure_version_code(href: str, package: str) -> Optional[str]:
    """Return the versionCode of a direct download link for package."""
    parsed = urlparse(href)
    if parsed.netloc.lower() != "d.apkpure.com" or not parsed.path.startswith("/b/"):
        return None
    if parsed.path.rstrip("/").rsplit("/", 1)[-1].lower() != package.lower():
        return None
    codes = parse_qs(parsed.query).get("versionCode")
    return codes[0] if codes else None


def parse_apkpure_variants(html: str, package: str,
                           version: str) -> List[VariantRef]:
    """Parse the "All Variants" list of an APKPure version page.

    Rows naming a different version are skipped. Falls back to any direct
    download link for package when the list is missing. Links for other
    packages (ads) are ignored.
    """
    soup = BeautifulSoup(html, "lxml")
    variants = []
    seen_hrefs = set()

    def add(href: str, label: str):
        code = _apkpure_version_code(href, package)
        if code and href not in seen_hrefs:
            seen_hrefs.add(href)
            variants.append(VariantRef(variant_id=code, url=href, label=label))

    for row in soup.select("div.apk-variants div.apk"):
        link = row.select_one("a.download-btn")
        name = row.select_one(".name")
        if not link or (name and name.get_text(strip=True) != version):
            continue
        tag = row.select_one(".tag")
        group = row.find_previous("div", class_="group-title")
        label_parts = [el.get_text(" ", strip=True) for el in (tag, group) if el]
        add(link.get("href", ""), ", ".join(label_parts))

    if not variants:
        for link in soup.find_all("a", href=True):
            add(link["href"], link.get_text(" ", strip=True))

    return variants


def parse_apkpure_versions(html: str, package: str) -> List[str]:
    """Read version names from an APKPure versions page, skipping other apps."""
    soup = BeautifulSoup(html, "lxml")
    return [last_word(link.get_text(" ", strip=True))
            for link in soup.select("a.ver-item-n[href]")
            if f"/{package}/" in link["href"]]


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
                _mentions_version(text_lower, version)
                or _mentions_version(href_lower, version)
                or _mentions_version(text_lower, version_slug, "-")
                or _mentions_version(href_lower, version_slug, "-")
            )
            if not has_version:
                continue

            score = 0
            if "download" in href_lower:
                score += 4
            if "/versions" in href_lower:
                score += 2
            if _mentions_version(text_lower, version):
                score += 2
            if _mentions_version(href_lower, version_slug, "-"):
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

    def _app_name(self, config: dict) -> str:
        package = config.get("package")
        if not package:
            raise ValueError("APKPure config requires 'package'")
        app_slug = config.get("app_slug", package)
        return app_slug.split("/")[-1] if "/" in app_slug else app_slug

    def _versions_url(self, config: dict) -> str:
        package = config["package"]
        app_slug = config.get("app_slug", package)
        return f"{self.BASE_URL}/{app_slug}/{package}/versions"

    def _list_versions(self, page, config: dict) -> List[str]:
        self._open(page, self._versions_url(config))
        self._wait(5)
        return parse_apkpure_versions(page.content(), config["package"])

    def _list_variants(self, page, config: dict,
                       version: str) -> List[VariantRef]:
        """
        List every variant on the APKPure version page.
        
        Config requires:
            - package: Package name (e.g., "org.telegram.messenger")
            - app_slug: Optional app slug for URL (e.g., "telegram")
        """
        package = config["package"]
        versions_url = self._versions_url(config)

        self.logger.info(f"Fetching {package} v{version} from APKPure")
        
        # Step 1: Load versions page
        self.logger.info(f"Loading versions page: {versions_url}")
        self._open(page, versions_url)
        self._wait(5)

        self.logger.debug(f"Page title: {page.title()}")

        # Step 2: Find the target version
        self.logger.info("Searching for version links...")
        version_link = self._find_version_link(page, version)

        if not version_link:
            raise VersionNotFoundError(f"Version {version} not found on APKPure")

        self.logger.info("Found version link")
        
        # Step 3: Navigate to the version page and read its variants
        if not version_link.startswith("http"):
            version_link = self.BASE_URL + version_link
        
        self._open(page, version_link)
        self._wait(2)
        
        variants = parse_apkpure_variants(page.content(), package, version)
        if not variants:
            raise DownloadError(
                "Direct package download link not found on APKPure "
                "version page"
            )
        return variants

    def _download_variant(self, page, app_name: str, version: str,
                          variant: VariantRef) -> APKInfo:
        try:
            return self._save_url_file(
                variant.url,
                app_name,
                version,
                variant.variant_id,
                referer=page.url,
            )
        except PlatformMismatchError:
            raise
        except Exception as e:
            raise DownloadError(f"Direct package download failed: {e}") from e
