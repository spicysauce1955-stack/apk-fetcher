import tempfile
import unittest

from src.apkmirror import parse_apkmirror_variants
from src.apkpure import APKPureScraper, parse_apkpure_variants
from src.uptodown import parse_uptodown_variants


APKMIRROR_RELEASE = "/apk/telegram-fz-llc/telegram/telegram-13-0-3-release/"


def _apkmirror_row(href, badge, arch, min_android, dpi):
    return f"""
    <div class="table-row headerFont">
      <div class="table-cell">
        <a class="accent_color" href="{href}">13.0.3</a>
        <span class="apkm-badge success">{badge}</span>
        <span class="colorLightBlack">71831</span>
      </div>
      <div class="table-cell">{arch}</div>
      <div class="table-cell">{min_android}</div>
      <div class="table-cell">{dpi}</div>
    </div>
    """


APKMIRROR_PAGE = f"""
<html><body>
<div class="variants-table">
  <div class="table-row headerFont">
    <div class="table-cell">Variant</div>
    <div class="table-cell">Arch</div>
    <div class="table-cell">Version</div>
    <div class="table-cell">DPI</div>
  </div>
  {_apkmirror_row(APKMIRROR_RELEASE + "telegram-13-0-3-2-android-apk-download/",
                  "BUNDLE", "universal", "Android 6.0+", "120-640dpi")}
  {_apkmirror_row(APKMIRROR_RELEASE + "telegram-13-0-3-android-apk-download/",
                  "APK", "universal", "Android 6.0+", "nodpi")}
</div>
<a class="accent_color"
   href="/apk/telegram-fz-llc/telegram/telegram-13-0-2-release/telegram-13-0-2-android-apk-download/">13.0.2</a>
</body></html>
"""


class APKMirrorParserTests(unittest.TestCase):
    def _parse(self, html=APKMIRROR_PAGE):
        return parse_apkmirror_variants(html, "telegram", "13.0.3")

    def test_parses_both_variants_with_ids(self):
        variants = self._parse()

        self.assertEqual([v.variant_id for v in variants], ["2", "1"])
        for variant in variants:
            self.assertTrue(variant.url.startswith(
                "https://www.apkmirror.com" + APKMIRROR_RELEASE
            ))

    def test_label_mentions_badge_and_dpi(self):
        label = self._parse()[0].label

        self.assertIn("BUNDLE", label)
        self.assertIn("120-640dpi", label)

    def test_ignores_links_outside_variants_table(self):
        self.assertFalse(any("13-0-2" in v.url for v in self._parse()))

    def test_unexpected_slug_falls_back_to_slug_id(self):
        html = f"""
        <div class="variants-table">
          {_apkmirror_row(APKMIRROR_RELEASE + "foo-bar-android-apk-download/",
                          "APK", "arm64-v8a", "Android 6.0+", "nodpi")}
        </div>
        """

        self.assertEqual([v.variant_id for v in self._parse(html)], ["foo-bar"])

    def test_empty_page_returns_empty_list(self):
        self.assertEqual(self._parse("<html><body></body></html>"), [])


def _uptodown_row(version_id, version):
    return f"""
    <div data-url="https://telegram.en.uptodown.com/android"
         data-version-id="{version_id}" data-extra-url="download">
      <span class="type apk" title="apk">apk</span>
      <span class="version">{version}</span>
      <span class="sdkVersion">Android + 6.0</span>
      <span class="date">Oct 8, 2026</span>
    </div>
    """


UPTODOWN_PAGE = f"""
<html><body><div id="versions-items-list">
  {_uptodown_row("1224906954", "13.0.0")}
  {_uptodown_row("1224938414", "13.0.0")}
  {_uptodown_row("1226283995", "13.0.2")}
  {_uptodown_row("999", "13.0.31")}
</div></body></html>
"""


class UptodownParserTests(unittest.TestCase):
    def _parse(self, version):
        return parse_uptodown_variants(UPTODOWN_PAGE, "telegram", version)

    def test_repeated_rows_are_variants(self):
        variants = self._parse("13.0.0")

        self.assertEqual(
            [v.variant_id for v in variants],
            ["1224906954", "1224938414"],
        )
        self.assertEqual(
            variants[0].url,
            "https://telegram.en.uptodown.com/android/download/1224906954",
        )

    def test_exact_version_match(self):
        self.assertEqual(self._parse("13.0.3"), [])

    def test_label_is_package_type(self):
        self.assertEqual(self._parse("13.0.2")[0].label, "apk")


APKPURE_DL = "https://d.apkpure.com/b/{kind}/org.telegram.messenger?versionCode={code}"
APKPURE_AD = "https://d.apkpure.com/b/APK/com.binance.dev?version=latest"


def _apkpure_row(code, kind, name="12.10.0"):
    return f"""
    <div class="apk" data-dt-app="org.telegram.messenger">
      <div class="info">
        <div class="info-top">
          <span class="name one-line">{name}</span>
          <span class="code one-line">({code})</span>
          <span class="tag one-line" data-tag="{kind}">{kind}</span>
        </div>
      </div>
      <a class="download-btn" href="{APKPURE_DL.format(kind=kind, code=code)}">Download</a>
    </div>
    """


APKPURE_PAGE = f"""
<html><body>
<a href="{APKPURE_AD}">Binance | Download</a>
<div class="module apk-variants"><div class="show-more"><div class="show-more-content">
  <div class="group-title">arm64-v8a, armeabi-v7a, x86, x86_64</div>
  {_apkpure_row("70312", "APK")}
  <div class="group-title">arm64-v8a</div>
  {_apkpure_row("70242", "XAPK")}
</div></div></div>
</body></html>
"""


class APKPureParserTests(unittest.TestCase):
    def _parse(self, html, version="12.10.0"):
        return parse_apkpure_variants(html, "org.telegram.messenger", version)

    def test_parses_all_variants(self):
        variants = self._parse(APKPURE_PAGE)

        self.assertEqual([v.variant_id for v in variants], ["70312", "70242"])
        self.assertIn("APK", variants[0].label)
        self.assertIn("XAPK", variants[1].label)
        self.assertEqual(variants[1].url, APKPURE_DL.format(kind="XAPK", code="70242"))

    def test_ignores_other_packages(self):
        variants = self._parse(APKPURE_PAGE)

        self.assertFalse(any("com.binance.dev" in v.url for v in variants))

    def test_fallback_without_variants_module(self):
        link = APKPURE_DL.format(kind="XAPK", code="71671")
        html = f"""
        <a href="{link}">Download XAPK</a>
        <a href="{link}">Download</a>
        <a href="{APKPURE_AD}">Download</a>
        """

        variants = self._parse(html, "13.0.1")

        self.assertEqual([v.variant_id for v in variants], ["71671"])
        self.assertEqual(variants[0].url, link)

    def test_ignores_packages_that_only_start_with_configured_package(self):
        link = APKPURE_DL.format(kind="XAPK", code="71671")
        lookalike = "https://d.apkpure.com/b/XAPK/org.telegram.messenger.web?versionCode=999"
        html = f'<a href="{lookalike}">Download</a><a href="{link}">Download</a>'

        self.assertEqual([v.url for v in self._parse(html, "13.0.1")], [link])

    def test_skips_variant_rows_for_other_versions(self):
        html = f"""
        <div class="apk-variants"><div class="show-more-content">
          {_apkpure_row("70312", "APK", name="12.10.0")}
          {_apkpure_row("70399", "APK", name="12.10.01")}
        </div></div>
        """

        self.assertEqual([v.variant_id for v in self._parse(html)], ["70312"])


class _FakeLink:
    def __init__(self, text, href):
        self._text = text
        self._href = href

    def inner_text(self):
        return self._text

    def get_attribute(self, name):
        return self._href if name == "href" else None


class _FakePage:
    def __init__(self, links):
        self._links = links

    def query_selector_all(self, selector):
        return self._links


APKPURE_DOWNLOAD_PAGE = "https://apkpure.com/telegram/org.telegram.messenger/download/"


class APKPureVersionLinkTests(unittest.TestCase):
    def setUp(self):
        self.scraper = APKPureScraper(output_dir=tempfile.mkdtemp())

    def _find(self, versions, wanted):
        page = _FakePage([
            _FakeLink(f"Telegram\n{v}", APKPURE_DOWNLOAD_PAGE + v) for v in versions
        ])
        return self.scraper._find_version_link(page, wanted)

    def test_prefers_exact_version_over_longer_one(self):
        self.assertEqual(
            self._find(["12.2.11", "12.2.10", "12.2.1"], "12.2.1"),
            APKPURE_DOWNLOAD_PAGE + "12.2.1",
        )

    def test_longer_version_alone_is_not_a_match(self):
        self.assertIsNone(self._find(["12.2.11"], "12.2.1"))


if __name__ == "__main__":
    unittest.main()
