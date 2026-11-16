"""Uptodown reveals older versions in batches behind a "See more" button."""

import tempfile
import unittest

from src.base import VersionNotFoundError
from src.uptodown import UptodownScraper, listing_is_past_version


def _row(version_id, version):
    return (
        f'<div data-url="https://telegram.en.uptodown.com/android" '
        f'data-version-id="{version_id}" data-extra-url="download">'
        f'<span class="type apk">apk</span>'
        f'<span class="version">{version}</span></div>'
    )


def _page(rows):
    return "<html><body>" + "".join(_row(*row) for row in rows) + "</body></html>"


class FakeResponse:
    status = 200


class FakeButton:
    def __init__(self, page):
        self.page = page

    def is_visible(self):
        return True

    def click(self, **kwargs):
        self.page.clicks += 1
        if self.page.grows:
            self.page.shown += 1


class FakeVersionsPage:
    """Shows one batch of rows at first and one more per click."""

    url = "https://telegram.en.uptodown.com/android/versions"

    def __init__(self, batches, grows=True):
        self.batches = batches
        self.shown = 1
        self.clicks = 0
        self.grows = grows

    def _rows(self):
        return [row for batch in self.batches[: self.shown] for row in batch]

    def goto(self, url, wait_until=None, timeout=None):
        return FakeResponse()

    def title(self):
        return "Older versions of Telegram (Android) | Uptodown"

    def content(self):
        return _page(self._rows())

    def query_selector(self, selector):
        if selector == "#button-list-more" and self.shown < len(self.batches):
            return FakeButton(self)
        return None

    def query_selector_all(self, selector):
        return self._rows()


class ListingIsPastVersionTests(unittest.TestCase):
    def _past(self, shown, version):
        rows = [(str(i), v) for i, v in enumerate(shown)]
        return listing_is_past_version(_page(rows), version)

    def test_last_row_older_than_target(self):
        self.assertTrue(self._past(["13.0.0", "12.9.0"], "12.9.5"))

    def test_last_row_is_the_target_so_more_rows_may_follow(self):
        self.assertFalse(self._past(["13.0.0", "12.9.0"], "12.9.0"))

    def test_last_row_newer_than_target(self):
        self.assertFalse(self._past(["13.0.0", "12.9.0"], "12.4.0"))

    def test_versions_compare_numerically(self):
        self.assertTrue(self._past(["12.10.0", "12.9.2"], "12.10.0"))
        self.assertFalse(self._past(["12.10.0"], "12.9.2"))

    def test_empty_listing_is_not_past_anything(self):
        self.assertFalse(self._past([], "12.9.2"))


class VersionsListPagingTests(unittest.TestCase):
    def _list(self, page, version):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scraper = UptodownScraper(output_dir=tmp.name)
        scraper._wait = lambda *_: None
        variants = scraper._list_variants(page, {"app_slug": "telegram"}, version)
        return [v.variant_id for v in variants]

    def test_version_on_the_first_batch_needs_no_clicks(self):
        page = FakeVersionsPage([
            [("1", "13.2.1"), ("2", "13.2.0")],
            [("3", "13.1.0")],
        ])

        self.assertEqual(self._list(page, "13.2.1"), ["1"])
        self.assertEqual(page.clicks, 0)

    def test_finds_a_version_behind_see_more(self):
        page = FakeVersionsPage([
            [("1", "13.2.1")],
            [("2", "12.8.0")],
            [("3", "12.4.0"), ("4", "12.3.0")],
        ])

        self.assertEqual(self._list(page, "12.4.0"), ["3"])
        self.assertEqual(page.clicks, 2)

    def test_collects_variants_split_across_batches(self):
        page = FakeVersionsPage([
            [("1", "13.2.1"), ("2", "12.4.0")],
            [("3", "12.4.0"), ("4", "12.3.0")],
        ])

        self.assertEqual(self._list(page, "12.4.0"), ["2", "3"])

    def test_stops_once_the_list_is_past_a_missing_version(self):
        page = FakeVersionsPage([
            [("1", "13.2.1")],
            [("2", "12.3.0")],
            [("3", "12.1.0")],
            [("4", "11.0.0")],
        ])

        with self.assertRaises(VersionNotFoundError):
            self._list(page, "12.2.0")
        self.assertEqual(page.clicks, 2)

    def test_stops_when_the_button_is_gone(self):
        page = FakeVersionsPage([[("1", "13.2.1")], [("2", "13.0.0")]])

        with self.assertRaises(VersionNotFoundError):
            self._list(page, "12.0.0")
        self.assertEqual(page.clicks, 1)

    def test_stops_when_a_click_reveals_nothing(self):
        page = FakeVersionsPage([[("1", "13.2.1")], [("2", "13.0.0")]],
                                grows=False)

        with self.assertLogs("UptodownScraper", level="WARNING"):
            with self.assertRaises(VersionNotFoundError):
                self._list(page, "12.0.0")
        self.assertEqual(page.clicks, 1)


if __name__ == "__main__":
    unittest.main()
