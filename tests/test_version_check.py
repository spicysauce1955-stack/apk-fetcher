"""Finding out whether a source lists something newer than a given version."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import main
from src.apkmirror import APKMirrorScraper, parse_apkmirror_versions
from src.apkpure import APKPureScraper, parse_apkpure_versions
from src.base import (
    BaseScraper,
    ScraperError,
    SourceBlockedError,
    is_version_number,
    version_key,
)
from src.config import AppConfig
from src.uptodown import UptodownScraper, parse_uptodown_versions


def _apkmirror_row(href, title):
    return (f'<div class="appRow"><h5 class="appRowTitle">'
            f'<a class="fontBlack" href="{href}">{title}</a></h5>'
            f'<a class="downloadLink" href="{href}"></a></div>')


RELEASES = "/apk/telegram-fz-llc/telegram"
APKMIRROR_PAGE = "".join((
    _apkmirror_row("/apk/telegram-fz-llc/telegram-android-wear/", "Telegram (Wear OS)"),
    _apkmirror_row(f"{RELEASES}/telegram-13-2-1-release/", "Telegram 13.2.1"),
    _apkmirror_row(f"{RELEASES}/telegram-13-2-0-release/", "Telegram 13.2.0"),
    _apkmirror_row(f"{RELEASES}/telegram-13-3-0-beta-release/", "Telegram 13.3.0 beta"),
    _apkmirror_row("/apk/google-inc/youtube/youtube-21-40-161-release/", "YouTube 21.40.161"),
    _apkmirror_row(f"{RELEASES}/telegram-13-2-1-release/telegram-13-2-1-android-apk-download/",
                   "Telegram 99.0.0"),
))


def _apkpure_row(href, title):
    return f'<a class="ver-item-n dt-version-name-link" href="{href}">{title}\n    </a>'


PACKAGE = "org.telegram.messenger"
APKPURE_PAGE = "".join((
    _apkpure_row(f"https://apkpure.com/telegram/{PACKAGE}/download", "Telegram\n  13.2.0"),
    _apkpure_row(f"https://apkpure.com/telegram/{PACKAGE}/download/11.2.1", "Telegram\n  11.2.1"),
    _apkpure_row("https://apkpure.com/other/com.other.app/download/99.0.0", "Other\n  99.0.0"),
    '<a class="ver_download_btn" href="https://apkpure.com/x">Download</a>',
))

UPTODOWN_PAGE = """
<div data-version-id="1"><span class="version">13.2.1</span></div>
<div data-version-id="2"><span class="version">13.2.0</span></div>
"""


class VersionNumberTests(unittest.TestCase):
    def test_parts_compare_as_numbers(self):
        self.assertGreater(version_key("13.2.10"), version_key("13.2.9"))

    def test_trailing_zeros_do_not_make_a_version_newer(self):
        self.assertEqual(version_key("13.2.0"), version_key("13.2"))

    def test_plain_numbers_are_versions(self):
        for text in ("13.2.0", "13", "2024.11.3.1"):
            self.assertTrue(is_version_number(text), text)

    def test_anything_else_is_not(self):
        for text in ("", "abc", "13..2", "13.2.", "13.3.0 beta", "13.2.0-rc1"):
            self.assertFalse(is_version_number(text), text)


class VersionParserTests(unittest.TestCase):
    def test_apkmirror_keeps_only_this_apps_releases(self):
        self.assertEqual(
            parse_apkmirror_versions(APKMIRROR_PAGE, "telegram-fz-llc", "telegram"),
            ["13.2.1", "13.2.0", "beta"],
        )

    def test_apkpure_keeps_only_this_package(self):
        self.assertEqual(parse_apkpure_versions(APKPURE_PAGE, PACKAGE),
                         ["13.2.0", "11.2.1"])

    def test_uptodown_reads_every_row(self):
        self.assertEqual(parse_uptodown_versions(UPTODOWN_PAGE), ["13.2.1", "13.2.0"])


class ListedScraper(BaseScraper):
    SOURCE_NAME = "listed"

    def __init__(self, output_dir, listed=(), list_error=None):
        super().__init__(output_dir=output_dir)
        self.listed = list(listed)
        self.list_error = list_error
        self.closed = False

    def _get_browser(self):
        return None, None, None

    def _close_browser(self, playwright, browser):
        self.closed = True

    def _app_name(self, config):
        return "app"

    def _list_versions(self, page, config):
        if self.list_error:
            raise self.list_error
        return self.listed

    def _list_variants(self, page, config, version):
        return []

    def _download_variant(self, page, app_name, version, variant):
        raise NotImplementedError


class NewestVersionTests(unittest.TestCase):
    def _scraper(self, *args, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return ListedScraper(tmp.name, *args, **kwargs)

    def test_highest_number_wins_wherever_it_is_listed(self):
        scraper = self._scraper(["13.2.0", "11.2.1", "13.2.10", "13.2.9"])

        self.assertEqual(scraper.newest_version({}), "13.2.10")

    def test_betas_do_not_count(self):
        scraper = self._scraper(["13.2.0", "13.3.0 beta", "beta"])

        self.assertEqual(scraper.newest_version({}), "13.2.0")

    def test_a_list_without_versions_is_an_error(self):
        with self.assertRaises(ScraperError):
            self._scraper(["beta"]).newest_version({})

    def test_browser_is_closed_when_the_listing_fails(self):
        scraper = self._scraper(list_error=SourceBlockedError("blocked"))

        with self.assertRaises(SourceBlockedError):
            scraper.newest_version({})

        self.assertTrue(scraper.closed)


class FakePage:
    def __init__(self, html):
        self.html = html
        self.visited = []

    def goto(self, url, **kwargs):
        self.visited.append(url)
        return SimpleNamespace(status=200)

    def title(self):
        return "Telegram"

    def content(self):
        return self.html


class SourceListingTests(unittest.TestCase):
    def _read(self, scraper_class, html, config):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scraper = scraper_class(output_dir=tmp.name)
        scraper._wait = lambda *_args: None
        page = FakePage(html)
        return scraper._list_versions(page, config), page.visited

    def test_apkmirror_reads_the_app_page(self):
        versions, visited = self._read(
            APKMirrorScraper, APKMIRROR_PAGE,
            {"developer": "telegram-fz-llc", "app_name": "telegram"},
        )

        self.assertIn("13.2.1", versions)
        self.assertEqual(visited,
                         ["https://www.apkmirror.com/apk/telegram-fz-llc/telegram/"])

    def test_apkpure_reads_the_versions_page(self):
        versions, visited = self._read(
            APKPureScraper, APKPURE_PAGE,
            {"package": PACKAGE, "app_slug": "telegram"},
        )

        self.assertIn("13.2.0", versions)
        self.assertEqual(
            visited, [f"https://m.apkpure.com/telegram/{PACKAGE}/versions"])

    def test_uptodown_reads_the_versions_page(self):
        versions, visited = self._read(
            UptodownScraper, UPTODOWN_PAGE, {"app_slug": "telegram"})

        self.assertIn("13.2.1", versions)
        self.assertEqual(
            visited, ["https://telegram.en.uptodown.com/android/versions"])


def _source(outcome):
    class Source:
        def __init__(self, output_dir, headless):
            pass

        def newest_version(self, config):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    return Source


class CheckForNewerTests(unittest.TestCase):
    def _check(self, outcomes, version="13.2.0", source_filter=None, app=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = app or AppConfig(name="telegram", versions=[],
                               sources={name: {"k": 1} for name in outcomes})
        scrapers = {name: _source(result) for name, result in outcomes.items()}
        with mock.patch.dict(main.SCRAPERS, scrapers, clear=True), \
                mock.patch.object(main, "SOURCE_PRIORITY", list(outcomes)), \
                self.assertLogs("check", level="INFO") as logs:
            code = main.check_for_newer(app, version, tmp.name, True, source_filter)
        return code, "\n".join(logs.output)

    def test_newer_version_has_its_own_exit_code(self):
        # 0 is up to date, 1 is could not check, 2 is argparse's bad usage.
        self.assertNotIn(main.EXIT_NEWER_VERSION, (0, 1, 2))

    def test_newer_version_is_announced_with_where_it_was_found(self):
        code, log = self._check({"apkmirror": "13.2.1", "apkpure": "13.2.0"})

        self.assertEqual(code, main.EXIT_NEWER_VERSION)
        self.assertIn("13.2.1", log)
        self.assertIn("Newer version", log)
        self.assertRegex(log, r"Newer version.*apkmirror")
        self.assertNotRegex(log, r"Newer version.*apkpure")

    def test_the_newest_of_several_is_the_one_announced(self):
        _, log = self._check({"apkmirror": "13.2.1", "uptodown": "13.3.0"})

        self.assertRegex(log, r"Newer version.*13\.3\.0.*uptodown")

    def test_same_version_everywhere_is_up_to_date(self):
        code, log = self._check({"apkmirror": "13.2.0", "uptodown": "13.2"})

        self.assertEqual(code, 0)
        self.assertIn("No source lists anything newer", log)

    def test_older_listings_are_up_to_date(self):
        code, _ = self._check({"apkmirror": "13.1.0"})

        self.assertEqual(code, 0)

    def test_unreadable_source_means_the_answer_is_not_certain(self):
        code, log = self._check({"apkmirror": "13.2.0",
                                 "uptodown": SourceBlockedError("blocked")})

        self.assertEqual(code, 1)
        self.assertIn("uptodown", log)

    def test_newer_version_is_still_announced_if_another_source_fails(self):
        code, _ = self._check({"apkmirror": "13.2.1",
                               "uptodown": SourceBlockedError("blocked")})

        self.assertEqual(code, main.EXIT_NEWER_VERSION)

    def test_every_source_failing_is_a_failure(self):
        code, _ = self._check({"apkmirror": ValueError("boom")})

        self.assertEqual(code, 1)

    def test_source_filter_reads_only_that_source(self):
        code, _ = self._check({"apkmirror": "13.2.1", "uptodown": "13.2.0"},
                              source_filter="uptodown")

        self.assertEqual(code, 0)

    def test_sources_the_app_does_not_configure_are_skipped(self):
        app = AppConfig(name="telegram", versions=[], sources={"uptodown": {"k": 1}})

        code, _ = self._check({"apkmirror": "99.0", "uptodown": "13.2.0"}, app=app)

        self.assertEqual(code, 0)

    def test_no_configured_source_is_a_failure(self):
        app = AppConfig(name="telegram", versions=[], sources={})

        code, _ = self._check({"apkmirror": "99.0"}, app=app)

        self.assertEqual(code, 1)


APP_BLOCK = (
    "  {name}:\n"
    '    versions: ["13.0.3"]\n'
    "    sources:\n"
    "      apkmirror: {{k: 1}}\n"
)


class CheckFlagTests(unittest.TestCase):
    def _config(self, *apps):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.yaml")
        with open(path, "w") as f:
            f.write(f"output_dir: {tmp.name}\napps:\n")
            f.write("".join(APP_BLOCK.format(name=name) for name in apps))
        return path

    def _run(self, *args, apps=("telegram",), exit_code=0):
        argv = ["main.py", "--config", self._config(*apps), *args]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(main, "setup_logging"), \
                mock.patch.object(main, "check_for_newer",
                                  return_value=exit_code) as self.check, \
                mock.patch.object(main, "fetch_apk") as self.fetch, \
                contextlib.redirect_stderr(io.StringIO()) as self.stderr, \
                self.assertRaises(SystemExit) as caught:
            main.main()
        return caught.exception.code

    def test_exit_code_is_the_checks_result(self):
        self.assertEqual(self._run("--check", "13.2.0", exit_code=3), 3)

    def test_the_only_configured_app_needs_no_app_flag(self):
        self._run("--check", "13.2.0")

        self.assertEqual(self.check.call_args.args[0].name, "telegram")
        self.assertEqual(self.check.call_args.args[1], "13.2.0")

    def test_nothing_is_downloaded(self):
        self._run("--check", "13.2.0")

        self.fetch.assert_not_called()

    def test_source_flag_limits_the_check(self):
        self._run("--check", "13.2.0", "--source", "apkmirror")

        self.assertEqual(self.check.call_args.args[4], "apkmirror")

    def test_several_apps_need_the_app_flag(self):
        self.assertEqual(self._run("--check", "13.2.0", apps=("a", "b")), 2)
        self.check.assert_not_called()
        self.assertIn("--app", self.stderr.getvalue())

    def test_app_flag_picks_one_of_several(self):
        self._run("--check", "13.2.0", "--app", "b", apps=("a", "b"))

        self.assertEqual(self.check.call_args.args[0].name, "b")

    def test_a_non_version_is_refused(self):
        self.assertEqual(self._run("--check", "latest"), 2)
        self.assertIn("latest", self.stderr.getvalue())

    def test_flags_that_mean_a_download_are_refused(self):
        for extra in (["--version", "13.2.0"], ["--bulk", "v.json"],
                      ["--force"], ["--platform", "x86"]):
            self.assertEqual(self._run("--check", "13.2.0", *extra), 2, extra)
            self.assertIn(extra[0], self.stderr.getvalue())
            self.check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
