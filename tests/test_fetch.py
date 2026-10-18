import logging
import os
import tempfile
import unittest
from unittest import mock

import main
from src.base import APKInfo, DownloadError, ScrapeResult
from src.config import AppConfig


def _fake_scraper(name, calls, outcome):
    """Build a scraper class that records scrape calls and returns outcome."""

    class FakeScraper:
        def __init__(self, output_dir, headless):
            pass

        def scrape(self, config, version, force=False):
            calls.append((name, version, force))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    return FakeScraper


def _downloaded():
    return ScrapeResult(downloaded=[APKInfo(
        filepath="/tmp/telegram-13.0.3-apkmirror-1-universal.apk",
        version="13.0.3",
        source="apkmirror",
        size_bytes=1,
        app_name="telegram",
        package_type="apk",
        architectures=["universal"],
        variant_id="1",
    )])


class FetchApkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = tmp.name
        self.calls = []
        self.app = AppConfig(
            name="telegram",
            versions=[],
            sources={"apkmirror": {"k": 1}, "uptodown": {"k": 1}},
        )
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _fetch(self, apkmirror, uptodown, **kwargs):
        scrapers = {
            "apkmirror": _fake_scraper("apkmirror", self.calls, apkmirror),
            "uptodown": _fake_scraper("uptodown", self.calls, uptodown),
        }
        with mock.patch.dict(main.SCRAPERS, scrapers, clear=True):
            return main.fetch_apk(self.app, "13.0.3", self.output_dir, True,
                                  **kwargs)

    def _sources_called(self):
        return [name for name, _version, _force in self.calls]

    def test_fallback_stops_at_first_success(self):
        ok = self._fetch(_downloaded(), _downloaded())

        self.assertTrue(ok)
        self.assertEqual(self._sources_called(), ["apkmirror"])

    def test_existing_only_counts_as_success(self):
        ok = self._fetch(ScrapeResult(existing=["p"]), _downloaded())

        self.assertTrue(ok)
        self.assertEqual(self._sources_called(), ["apkmirror"])

    def test_falls_through_on_error(self):
        ok = self._fetch(DownloadError("boom"), _downloaded())

        self.assertTrue(ok)
        self.assertEqual(self._sources_called(), ["apkmirror", "uptodown"])

    def test_all_sources_tries_every_source(self):
        ok = self._fetch(_downloaded(), _downloaded(), all_sources=True)

        self.assertTrue(ok)
        self.assertEqual(self._sources_called(), ["apkmirror", "uptodown"])

    def test_force_is_passed_through(self):
        self._fetch(_downloaded(), _downloaded(), force=True)

        self.assertEqual(self.calls, [("apkmirror", "13.0.3", True)])

    def test_scrapes_even_when_a_file_for_the_version_exists(self):
        app_dir = os.path.join(self.output_dir, "telegram")
        os.makedirs(app_dir)
        open(os.path.join(app_dir, "telegram-13.0.3-apkmirror.apkm"), "wb").close()

        self._fetch(_downloaded(), _downloaded())

        self.assertEqual(self._sources_called(), ["apkmirror"])


if __name__ == "__main__":
    unittest.main()
