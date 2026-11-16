"""Keeping only the packages that support the requested platforms."""

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import main
from src.apkpure import APKPureScraper
from src.base import (
    BaseScraper,
    DownloadError,
    PlatformMismatchError,
    ScrapeResult,
    VariantRef,
    label_excludes_platforms,
    supports_platforms,
)
from src.config import AppConfig
from test_download_integrity import FakeDownload


ARM64 = "arm64-v8a"
ARM32 = "armeabi-v7a"
EVERY_ABI = (ARM64, ARM32, "x86", "x86_64")


def _package(path, abis=()):
    """Write a small APK with native libraries for abis."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        for abi in abis:
            archive.writestr(f"lib/{abi}/libapp.so", b"native")
    return path


class PlatformNameTests(unittest.TestCase):
    def test_short_names_become_android_abis(self):
        self.assertEqual(main.parse_platforms("arm"), (ARM32,))
        self.assertEqual(main.parse_platforms("arm64"), (ARM64,))
        self.assertEqual(main.parse_platforms("x86"), ("x86",))
        self.assertEqual(main.parse_platforms("x86_64"), ("x86_64",))

    def test_android_abi_names_are_accepted(self):
        self.assertEqual(main.parse_platforms(ARM32), (ARM32,))
        self.assertEqual(main.parse_platforms(ARM64), (ARM64,))

    def test_several_platforms_are_comma_separated(self):
        self.assertEqual(main.parse_platforms("x86, ARM64"), (ARM64, "x86"))

    def test_the_same_platform_twice_counts_once(self):
        self.assertEqual(main.parse_platforms(f"arm,{ARM32}"), (ARM32,))

    def test_unknown_names_are_refused_with_the_choices(self):
        for text in ("mips", "x86,", ""):
            with self.assertRaises(argparse.ArgumentTypeError) as caught:
                main.parse_platforms(text)
            self.assertIn("arm64", str(caught.exception))


class SupportsPlatformsTests(unittest.TestCase):
    def test_every_requested_platform_must_be_present(self):
        self.assertTrue(supports_platforms([ARM64, "x86"], (ARM64, "x86")))
        self.assertFalse(supports_platforms([ARM64], (ARM64, "x86")))

    def test_a_package_without_native_code_runs_anywhere(self):
        self.assertTrue(supports_platforms(["universal"], ("x86",)))

    def test_32_and_64_bit_are_different_platforms(self):
        self.assertFalse(supports_platforms(["x86_64"], ("x86",)))
        self.assertFalse(supports_platforms([ARM64], (ARM32,)))

    def test_an_unrecognised_file_supports_nothing_requested(self):
        self.assertFalse(supports_platforms([], ("x86",)))

    def test_no_request_accepts_everything(self):
        self.assertTrue(supports_platforms([], ()))


class ListingLabelTests(unittest.TestCase):
    def test_label_naming_other_abis_rules_the_variant_out(self):
        self.assertTrue(label_excludes_platforms("XAPK, arm64-v8a", ("x86",)))

    def test_label_naming_the_abi_keeps_it(self):
        self.assertFalse(label_excludes_platforms("XAPK, arm64-v8a", (ARM64,)))

    def test_label_must_cover_every_requested_abi(self):
        label = "arm64-v8a + armeabi-v7a"
        self.assertFalse(label_excludes_platforms(label, (ARM64, ARM32)))
        self.assertTrue(label_excludes_platforms(label, (ARM64, "x86")))

    def test_similar_abi_names_are_not_confused(self):
        self.assertTrue(label_excludes_platforms("x86_64", ("x86",)))
        self.assertTrue(label_excludes_platforms("x86", ("x86_64",)))

    def test_label_without_an_abi_proves_nothing(self):
        for label in ("", "apk", "BUNDLE, universal, Android 6.0+, 120-640dpi"):
            self.assertFalse(label_excludes_platforms(label, ("x86",)), label)

    def test_no_request_excludes_nothing(self):
        self.assertFalse(label_excludes_platforms("arm64-v8a", ()))


class FakeSource(BaseScraper):
    """Lists variants as {id: (label, abis in the real file)}."""

    SOURCE_NAME = "fake"

    def __init__(self, output_dir, listing, platforms=(), fail_ids=(), list_error=None):
        super().__init__(output_dir=output_dir, platforms=platforms)
        self.listing = listing
        self.fail_ids = set(fail_ids)
        self.list_error = list_error
        self.download_calls = []

    def _get_browser(self):
        return None, None, None

    def _close_browser(self, playwright, browser):
        pass

    def _app_name(self, config):
        return "app"

    def _list_versions(self, page, config):
        return []

    def _list_variants(self, page, config, version):
        if self.list_error:
            raise self.list_error
        return [VariantRef(variant_id=variant_id, url="https://example.test/",
                           label=label)
                for variant_id, (label, _) in self.listing.items()]

    def _download_variant(self, page, app_name, version, variant):
        self.download_calls.append(variant.variant_id)
        if variant.variant_id in self.fail_ids:
            raise DownloadError("boom")
        app_dir = os.path.join(self.output_dir, app_name)
        os.makedirs(app_dir, exist_ok=True)
        temp = _package(os.path.join(app_dir, f".tmp-{variant.variant_id}"),
                        self.listing[variant.variant_id][1])
        try:
            return self._finalize_downloaded_file(temp, app_name, version,
                                                  variant.variant_id)
        except Exception:
            os.remove(temp)
            raise


class ScrapeFilterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = tmp.name
        self.app_dir = os.path.join(tmp.name, "app")
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _source(self, listing, platforms=("x86",), **kwargs):
        return FakeSource(self.output_dir, listing, platforms, **kwargs)

    def _saved(self, filename, abis):
        os.makedirs(self.app_dir, exist_ok=True)
        return _package(os.path.join(self.app_dir, filename), abis)

    def test_variant_ruled_out_by_its_label_is_never_downloaded(self):
        source = self._source({"1": ("arm64-v8a", (ARM64,)),
                               "2": ("x86", ("x86",))})

        result = source.scrape({}, "1.0")

        self.assertEqual(source.download_calls, ["2"])
        self.assertEqual([info.variant_id for info in result.downloaded], ["2"])
        self.assertEqual(result.failed, [])

    def test_variant_with_an_unhelpful_label_is_judged_by_its_file(self):
        source = self._source({"1": ("apk", (ARM64,)),
                               "2": ("apk", EVERY_ABI)})

        result = source.scrape({}, "1.0")

        self.assertEqual(source.download_calls, ["1", "2"])
        self.assertEqual([info.variant_id for info in result.downloaded], ["2"])
        self.assertEqual(result.failed, [])
        self.assertEqual(os.listdir(self.app_dir),
                         ["app-1.0-fake-2-universal.apk"])

    def test_package_without_native_code_is_kept(self):
        result = self._source({"1": ("apk", ())}).scrape({}, "1.0")

        self.assertEqual(len(result.downloaded), 1)

    def test_without_a_request_everything_is_kept(self):
        source = self._source({"1": ("arm64-v8a", (ARM64,))}, platforms=())

        self.assertEqual(len(source.scrape({}, "1.0").downloaded), 1)

    def test_saved_file_for_another_platform_does_not_count(self):
        self._saved(f"app-1.0-fake-1-{ARM64}.apk", (ARM64,))
        source = self._source({"1": ("apk", (ARM64,))})

        with self.assertRaises(PlatformMismatchError):
            source.scrape({}, "1.0")

        self.assertEqual(source.download_calls, [])

    def test_saved_file_for_the_platform_is_not_fetched_again(self):
        saved = self._saved("app-1.0-fake-1-x86.apk", ("x86",))
        source = self._source({"1": ("apk", ("x86",))})

        self.assertEqual(source.scrape({}, "1.0").existing, [saved])
        self.assertEqual(source.download_calls, [])

    def test_nothing_suitable_is_reported_as_a_mismatch(self):
        source = self._source({"1": ("arm64-v8a", (ARM64,))})

        with self.assertRaises(PlatformMismatchError) as caught:
            source.scrape({}, "1.0")

        self.assertIn("x86", str(caught.exception))

    def test_real_failures_are_not_hidden_by_the_filter(self):
        source = self._source({"1": ("arm64-v8a", (ARM64,)),
                               "2": ("x86", ("x86",))}, fail_ids=["2"])

        with self.assertRaises(DownloadError) as caught:
            source.scrape({}, "1.0")

        self.assertIn("All 1 variant(s) failed", str(caught.exception))

    def test_blocked_listing_keeps_only_suitable_saved_files(self):
        self._saved(f"app-1.0-fake-1-{ARM64}.apk", (ARM64,))
        suitable = self._saved("app-1.0-fake-2-x86.apk", ("x86",))
        source = self._source({}, list_error=DownloadError("blocked"))

        self.assertEqual(source.scrape({}, "1.0").existing, [suitable])

    def test_blocked_listing_without_a_suitable_file_still_fails(self):
        self._saved(f"app-1.0-fake-1-{ARM64}.apk", (ARM64,))
        source = self._source({}, list_error=DownloadError("blocked"))

        with self.assertRaises(DownloadError):
            source.scrape({}, "1.0")


class SavingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = tmp.name
        self.app_dir = os.path.join(tmp.name, "app")
        os.makedirs(self.app_dir)

    def _bytes_of_package(self, abis):
        path = _package(os.path.join(self.output_dir, "built.apk"), abis)
        with open(path, "rb") as f:
            content = f.read()
        os.remove(path)
        return content

    def test_wrong_platform_leaves_the_folder_as_it_was(self):
        earlier = _package(os.path.join(self.app_dir, "app-1.0-fake-7-arm64-v8a.apk"), (ARM64,))
        source = FakeSource(self.output_dir, {}, ("x86",))
        download = FakeDownload(body=self._bytes_of_package((ARM64,)))

        with self.assertRaises(PlatformMismatchError):
            source._save_downloaded_file(download, "app", "1.0", "7")

        self.assertEqual(os.listdir(self.app_dir), [os.path.basename(earlier)])

    def test_right_platform_is_saved(self):
        source = FakeSource(self.output_dir, {}, ("x86",))
        download = FakeDownload(body=self._bytes_of_package(("x86",)))

        info = source._save_downloaded_file(download, "app", "1.0", "7")

        self.assertEqual(os.path.basename(info.filepath), "app-1.0-fake-7-x86.apk")

    def test_apkpure_does_not_report_a_mismatch_as_a_failed_download(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scraper = APKPureScraper(output_dir=tmp.name)
        page = mock.Mock(url="https://m.apkpure.com/x")
        variant = VariantRef(variant_id="1", url="https://d.apkpure.com/b/APK/x")

        with mock.patch.object(scraper, "_save_url_file",
                               side_effect=PlatformMismatchError("x86 only")), \
                self.assertRaises(PlatformMismatchError):
            scraper._download_variant(page, "app", "1.0", variant)


def _fake(outcome, seen):
    class Source:
        def __init__(self, output_dir, headless, platforms=()):
            seen.append(platforms)

        def scrape(self, config, version, force=False):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    return Source


class FetchApkTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = tmp.name
        self.seen = []
        self.app = AppConfig(name="telegram", versions=[],
                             sources={"first": {"k": 1}, "second": {"k": 1}})

    def _fetch(self, first, second, **kwargs):
        scrapers = {"first": _fake(first, self.seen), "second": _fake(second, self.seen)}
        with mock.patch.dict(main.SCRAPERS, scrapers, clear=True), \
                mock.patch.object(main, "SOURCE_PRIORITY", ["first", "second"]), \
                self.assertLogs("fetcher", level="INFO") as self.logs:
            return main.fetch_apk(self.app, "1.0", self.output_dir, True,
                                  platforms=("x86",), **kwargs)

    def test_requested_platforms_reach_the_scraper(self):
        self._fetch(ScrapeResult(existing=["a"]), ScrapeResult())

        self.assertEqual(self.seen, [("x86",)])

    def test_next_source_is_tried_when_one_has_no_match(self):
        status = self._fetch(PlatformMismatchError("none for x86"),
                             ScrapeResult(existing=["a"]))

        self.assertIs(status, main.FetchStatus.COMPLETE)
        self.assertEqual(len(self.seen), 2)

    def test_log_says_what_happened(self):
        self._fetch(PlatformMismatchError("none for x86"), ScrapeResult(existing=["a"]))

        output = "\n".join(self.logs.output)
        self.assertIn("none for x86", output)
        self.assertNotIn("Version not found", output)

    def test_no_match_anywhere_is_a_failure(self):
        status = self._fetch(PlatformMismatchError("no"), PlatformMismatchError("no"))

        self.assertIs(status, main.FetchStatus.FAILED)

    def test_with_all_sources_no_match_is_not_a_source_error(self):
        status = self._fetch(PlatformMismatchError("no"), ScrapeResult(existing=["a"]),
                             all_sources=True)

        self.assertIs(status, main.FetchStatus.COMPLETE)


class PlatformFlagTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = os.path.join(tmp.name, "config.yaml")
        with open(self.config, "w") as f:
            f.write(f"output_dir: {tmp.name}\napps:\n  telegram:\n"
                    '    versions: ["13.0.3"]\n    sources:\n      apkmirror: {k: 1}\n')
        self.versions = os.path.join(tmp.name, "versions.json")
        with open(self.versions, "w") as f:
            json.dump([{"version": "13.0.3"}], f)
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _run(self, *args):
        argv = ["main.py", "--config", self.config, *args]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(main, "setup_logging"), \
                mock.patch.object(main, "fetch_apk",
                                  return_value=main.FetchStatus.COMPLETE) as self.fetch, \
                contextlib.redirect_stderr(io.StringIO()) as self.stderr, \
                self.assertRaises(SystemExit) as caught:
            main.main()
        return caught.exception.code

    def test_flag_reaches_each_fetch(self):
        self.assertEqual(self._run("--version", "13.0.3", "--platform", "x86,arm"), 0)

        self.assertEqual(self.fetch.call_args.kwargs["platforms"], (ARM32, "x86"))

    def test_flag_reaches_bulk_fetches(self):
        self._run("--bulk", self.versions, "--platform", "arm64")

        self.assertEqual(self.fetch.call_args.kwargs["platforms"], (ARM64,))

    def test_without_the_flag_nothing_is_filtered(self):
        self._run("--version", "13.0.3")

        self.assertEqual(self.fetch.call_args.kwargs["platforms"], ())

    def test_unknown_platform_stops_before_fetching(self):
        self.assertEqual(self._run("--platform", "mips"), 2)

        self.fetch.assert_not_called()
        self.assertIn("mips", self.stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
