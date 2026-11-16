import json
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock

import main
from src.base import (
    APKInfo,
    DownloadError,
    ScrapeResult,
    SourceBlockedError,
    VersionNotFoundError,
)
from src.config import AppConfig


def _fake_scraper(name, calls, outcome):
    """Build a scraper class that records scrape calls and returns outcome."""

    class FakeScraper:
        def __init__(self, output_dir, headless, platforms=()):
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


def _partial():
    result = _downloaded()
    result.failed.append(("2", "timeout"))
    return result


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
        status = self._fetch(_downloaded(), _downloaded())

        self.assertEqual(status, main.FetchStatus.COMPLETE)
        self.assertEqual(self._sources_called(), ["apkmirror"])

    def test_existing_only_counts_as_success(self):
        status = self._fetch(ScrapeResult(existing=["p"]), _downloaded())

        self.assertEqual(status, main.FetchStatus.COMPLETE)
        self.assertEqual(self._sources_called(), ["apkmirror"])

    def test_falls_through_on_error(self):
        status = self._fetch(DownloadError("boom"), _downloaded())

        self.assertEqual(status, main.FetchStatus.COMPLETE)
        self.assertEqual(self._sources_called(), ["apkmirror", "uptodown"])

    def test_all_sources_tries_every_source(self):
        status = self._fetch(_downloaded(), _downloaded(), all_sources=True)

        self.assertEqual(status, main.FetchStatus.COMPLETE)
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

    def test_failed_variant_makes_the_result_partial(self):
        status = self._fetch(_partial(), _downloaded())

        self.assertEqual(status, main.FetchStatus.PARTIAL)
        self.assertEqual(self._sources_called(), ["apkmirror"])

    def test_every_source_failing_is_failed(self):
        status = self._fetch(DownloadError("boom"), VersionNotFoundError("no"))

        self.assertEqual(status, main.FetchStatus.FAILED)

    def test_all_sources_with_a_failing_source_is_partial(self):
        for error in (DownloadError("boom"), SourceBlockedError("403")):
            with self.subTest(type(error).__name__):
                status = self._fetch(error, _downloaded(), all_sources=True)

                self.assertEqual(status, main.FetchStatus.PARTIAL)

    def test_all_sources_with_a_version_one_source_lacks_is_complete(self):
        status = self._fetch(VersionNotFoundError("no"), _downloaded(),
                             all_sources=True)

        self.assertEqual(status, main.FetchStatus.COMPLETE)

    def test_blocked_source_falls_through_and_is_logged_as_blocked(self):
        logging.disable(logging.NOTSET)

        with self.assertLogs("fetcher", level="WARNING") as logs:
            self._fetch(SourceBlockedError("HTTP 403"), _downloaded())

        self.assertEqual(self._sources_called(), ["apkmirror", "uptodown"])
        self.assertIn("blocked", logs.output[0].lower())
        self.assertNotIn("not found", logs.output[0].lower())


class ExitCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = os.path.join(tmp.name, "config.yaml")
        with open(self.config, "w") as f:
            f.write(
                f"output_dir: {tmp.name}\n"
                "apps:\n"
                "  telegram:\n"
                '    versions: ["13.0.3"]\n'
                "    sources:\n"
                "      apkmirror: {k: 1}\n"
            )
        self.versions = os.path.join(tmp.name, "versions.json")
        with open(self.versions, "w") as f:
            json.dump([{"version": "13.0.3"}], f)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _run(self, outcome, *args):
        scrapers = {"apkmirror": _fake_scraper("apkmirror", [], outcome)}
        argv = ["main.py", "--config", self.config, "--app", "telegram", *args]
        with mock.patch.dict(main.SCRAPERS, scrapers, clear=True):
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(main, "setup_logging"):
                    with self.assertRaises(SystemExit) as caught:
                        main.main()
        return caught.exception.code

    def test_complete_run_exits_zero(self):
        self.assertEqual(self._run(_downloaded()), 0)

    def test_partial_run_exits_non_zero(self):
        self.assertEqual(self._run(_partial()), 1)

    def test_failed_run_exits_non_zero(self):
        self.assertEqual(self._run(DownloadError("boom")), 1)

    def test_partial_bulk_run_exits_non_zero(self):
        self.assertEqual(self._run(_partial(), "--bulk", self.versions), 1)

    def test_complete_bulk_run_exits_zero(self):
        self.assertEqual(self._run(_downloaded(), "--bulk", self.versions), 0)

    def test_summary_counts_partial_versions(self):
        logging.disable(logging.NOTSET)

        with self.assertLogs("fetcher", level="WARNING"):
            with self.assertLogs("main", level="INFO") as logs:
                self._run(_partial())

        self.assertTrue(any("1 partial" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
