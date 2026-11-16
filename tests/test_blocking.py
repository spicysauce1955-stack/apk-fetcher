"""How scrapers report bot checks, refusals and rate limits."""

import tempfile
import unittest
from collections import namedtuple

from patchright.sync_api import Error as PlaywrightError
from patchright.sync_api import TimeoutError as PlaywrightTimeout

from src.apkmirror import APKMirrorScraper
from src.apkpure import APKPureScraper
from src.base import (
    BaseScraper,
    RateLimitError,
    SourceBlockedError,
    VariantRef,
    is_challenge_title,
)
from src.uptodown import UptodownScraper


CHALLENGE = "Just a moment..."

Visit = namedtuple("Visit", "status titles html links", defaults=("", ()))


class FakeLink:
    def __init__(self, text, href):
        self.text = text
        self.href = href

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.href if name == "href" else None


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakePage:
    """Scripted page: each goto consumes one Visit, the last one repeats."""

    def __init__(self, visits):
        self.visits = list(visits)
        self.current = None
        self.titles = []
        self.url = ""
        self.waits = 0

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        self.current = self.visits.pop(0) if len(self.visits) > 1 else self.visits[0]
        self.titles = list(self.current.titles)
        return FakeResponse(self.current.status)

    def title(self):
        title = self.titles.pop(0) if len(self.titles) > 1 else self.titles[0]
        if isinstance(title, Exception):
            raise title
        return title

    def content(self):
        return self.current.html

    def query_selector_all(self, selector):
        return list(self.current.links)

    def query_selector(self, selector):
        return self.current.links[0] if self.current.links else None

    def wait_for_timeout(self, milliseconds):
        self.waits += 1

    def wait_for_load_state(self, state=None, timeout=None):
        pass


class ProbeScraper(BaseScraper):
    SOURCE_NAME = "probe"

    def _list_versions(self, page, config):
        return []

    def _app_name(self, config):
        return "probe"

    def _list_variants(self, page, config, version):
        return []

    def _download_variant(self, page, app_name, version, variant):
        raise NotImplementedError


_OUTPUT = tempfile.TemporaryDirectory()


def _scraper(scraper_class=ProbeScraper, headless=True):
    scraper = scraper_class(output_dir=_OUTPUT.name, headless=headless)
    scraper._wait = lambda *_: None
    return scraper


class ChallengeTitleTests(unittest.TestCase):
    def test_recognises_cloudflare_interstitials(self):
        for title in ("Just a moment...", "JUST A MOMENT...",
                      "Attention Required! | Cloudflare"):
            with self.subTest(title):
                self.assertTrue(is_challenge_title(title))

    def test_real_page_titles_are_not_challenges(self):
        for title in ("Telegram 13.2.1 APK Download by Telegram FZ-LLC - APKMirror",
                      "Download Telegram 13.2.1 for Android | Uptodown.com",
                      "Page not found",
                      ""):
            with self.subTest(title):
                self.assertFalse(is_challenge_title(title))


class OpenPageTests(unittest.TestCase):
    def test_ordinary_page_opens(self):
        page = FakePage([Visit(200, ["Telegram"])])

        _scraper()._open(page, "https://example.test/")

        self.assertEqual(page.url, "https://example.test/")

    def test_missing_page_is_left_to_the_scraper(self):
        _scraper()._open(FakePage([Visit(404, ["Page not found"])]),
                         "https://example.test/")

    def test_challenge_in_headless_mode_is_blocked_without_waiting(self):
        page = FakePage([Visit(403, [CHALLENGE])])

        with self.assertRaises(SourceBlockedError):
            _scraper()._open(page, "https://example.test/")

        self.assertEqual(page.waits, 0)

    def test_challenge_served_with_status_200_is_still_blocked(self):
        with self.assertRaises(SourceBlockedError):
            _scraper()._open(FakePage([Visit(200, [CHALLENGE])]),
                             "https://example.test/")

    def test_refusals_are_blocked(self):
        for status in (403, 503):
            with self.subTest(status):
                with self.assertRaises(SourceBlockedError):
                    _scraper()._open(FakePage([Visit(status, ["Forbidden"])]),
                                     "https://example.test/")

    def test_429_is_a_rate_limit(self):
        with self.assertRaises(RateLimitError):
            _scraper()._open(FakePage([Visit(429, ["Too many"])]),
                             "https://example.test/")

    def test_visible_browser_waits_for_a_person_to_clear_the_check(self):
        page = FakePage([Visit(403, [CHALLENGE, CHALLENGE, "Telegram"])])
        scraper = _scraper(headless=False)
        scraper.CHALLENGE_WAIT_SECONDS = 30

        scraper._open(page, "https://example.test/")

        self.assertGreater(page.waits, 0)

    def test_navigation_during_the_wait_is_not_a_failure(self):
        closed = PlaywrightError("Execution context was destroyed")
        page = FakePage([Visit(403, [CHALLENGE, closed, "Telegram"])])
        scraper = _scraper(headless=False)
        scraper.CHALLENGE_WAIT_SECONDS = 30

        scraper._open(page, "https://example.test/")

    def test_visible_browser_gives_up_when_the_check_never_clears(self):
        scraper = _scraper(headless=False)
        scraper.CHALLENGE_WAIT_SECONDS = 0

        with self.assertRaises(SourceBlockedError):
            scraper._open(FakePage([Visit(403, [CHALLENGE])]),
                          "https://example.test/")


class SourcesReportBlocksTests(unittest.TestCase):
    CASES = (
        (APKMirrorScraper, {"developer": "d", "app_name": "telegram"}),
        (UptodownScraper, {"app_slug": "telegram"}),
        (APKPureScraper, {"package": "org.telegram.messenger", "app_slug": "telegram"}),
    )

    def test_scrape_reports_a_challenge_as_blocked_not_missing(self):
        for scraper_class, config in self.CASES:
            with self.subTest(scraper_class.__name__):
                scraper = _scraper(scraper_class)
                page = FakePage([Visit(403, [CHALLENGE])])
                scraper._get_browser = lambda: (None, None, page)
                scraper._close_browser = lambda *_: None

                with self.assertRaises(SourceBlockedError):
                    scraper.scrape(config, "13.2.1")

    def test_apkpure_block_on_the_version_page_is_reported(self):
        link = FakeLink("Telegram 13.2.1", "https://apkpure.com/t/download/13.2.1")
        page = FakePage([
            Visit(200, ["Versions"], links=[link]),
            Visit(403, [CHALLENGE]),
        ])

        with self.assertRaises(SourceBlockedError):
            _scraper(APKPureScraper)._list_variants(
                page, {"package": "org.telegram.messenger"}, "13.2.1"
            )

    def test_block_on_a_variant_page_is_reported(self):
        variant = VariantRef(variant_id="1", url="https://example.test/variant")
        for scraper_class in (APKMirrorScraper, UptodownScraper):
            with self.subTest(scraper_class.__name__):
                with self.assertRaises(SourceBlockedError):
                    _scraper(scraper_class)._download_variant(
                        FakePage([Visit(403, [CHALLENGE])]),
                        "telegram", "13.2.1", variant,
                    )


    def test_apkmirror_block_on_the_final_download_page_is_reported(self):
        variant = VariantRef(variant_id="1", url="https://example.test/variant")
        button = FakeLink("Download APK", "/apk/t/download/?key=abc")
        page = FakePage([
            Visit(200, ["Telegram variant"], links=[button]),
            Visit(403, [CHALLENGE]),
        ])

        with self.assertRaises(SourceBlockedError):
            _scraper(APKMirrorScraper)._download_variant(
                page, "telegram", "13.2.1", variant
            )


class _NeverDownloads:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise PlaywrightTimeout("Timeout exceeded while waiting for event")
        return False


class _Button:
    def click(self, **kwargs):
        pass


class NoDownloadPage:
    url = "https://telegram.en.uptodown.com/android/download/1"

    def __init__(self, turnstile):
        self.turnstile = turnstile
        self.timeouts = []

    def wait_for_selector(self, selector, timeout=None):
        return _Button()

    def expect_download(self, timeout=None):
        self.timeouts.append(timeout)
        return _NeverDownloads()

    def query_selector(self, selector):
        return object() if self.turnstile and "turnstile" in selector else None

    def title(self):
        return "Download Telegram"


class UptodownDownloadCheckTests(unittest.TestCase):
    def test_missing_download_on_a_turnstile_page_is_reported_as_blocked(self):
        with self.assertRaises(SourceBlockedError):
            _scraper(UptodownScraper)._start_download(NoDownloadPage(turnstile=True))

    def test_missing_download_without_a_check_stays_a_timeout(self):
        with self.assertRaises(PlaywrightTimeout):
            _scraper(UptodownScraper)._start_download(NoDownloadPage(turnstile=False))

    def test_visible_browser_allows_time_to_complete_the_check(self):
        headless_page = NoDownloadPage(turnstile=False)
        visible_page = NoDownloadPage(turnstile=False)

        with self.assertRaises(PlaywrightTimeout):
            _scraper(UptodownScraper)._start_download(headless_page)
        with self.assertRaises(PlaywrightTimeout):
            _scraper(UptodownScraper, headless=False)._start_download(visible_page)

        self.assertGreaterEqual(visible_page.timeouts[0], 120000)
        self.assertLess(headless_page.timeouts[0], 120000)


if __name__ == "__main__":
    unittest.main()
