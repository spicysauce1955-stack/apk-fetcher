import os
import tempfile
import unittest
import zipfile

from src.base import (
    BaseScraper,
    DownloadError,
    ScrapeResult,
    VariantRef,
    VersionNotFoundError,
    arch_label,
    find_existing_variant,
    normalize_variant_ids,
)


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


def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _package(path):
    """Write a minimal real package, which is what skip checks look for."""
    return _write_zip(path, {"AndroidManifest.xml": b"manifest"})


class ArchLabelTests(unittest.TestCase):
    def test_no_native_code_is_universal(self):
        self.assertEqual(arch_label(["universal"]), "universal")

    def test_all_main_abis_is_universal(self):
        self.assertEqual(
            arch_label(["arm64-v8a", "armeabi-v7a", "x86", "x86_64"]),
            "universal",
        )

    def test_superset_of_main_abis_is_universal(self):
        self.assertEqual(
            arch_label(["arm64-v8a", "armeabi", "armeabi-v7a", "x86", "x86_64"]),
            "universal",
        )

    def test_single_abi(self):
        self.assertEqual(arch_label(["arm64-v8a"]), "arm64-v8a")

    def test_partial_abis_joined_sorted(self):
        self.assertEqual(
            arch_label(["armeabi-v7a", "arm64-v8a"]),
            "arm64-v8a+armeabi-v7a",
        )

    def test_unrecognised_package_is_unknown(self):
        self.assertEqual(arch_label([]), "unknown")


class FindExistingVariantTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = tmp.name
        self.app_dir = os.path.join(self.output_dir, "telegram")
        os.makedirs(self.app_dir)

    def _file(self, name):
        return _package(os.path.join(self.app_dir, name))

    def _find(self, source, variant_id):
        return find_existing_variant(
            self.output_dir, "telegram", "13.0.3", source, variant_id
        )

    def test_matches_variant_with_any_package_extension(self):
        expected = self._file("telegram-13.0.3-apkmirror-2-universal.apkm")
        self.assertEqual(self._find("apkmirror", "2"), expected)

    def test_empty_file_is_not_a_saved_variant(self):
        open(os.path.join(self.app_dir,
                          "telegram-13.0.3-apkmirror-2-universal.apk"), "wb").close()

        self.assertIsNone(self._find("apkmirror", "2"))

    def test_non_package_file_is_not_a_saved_variant(self):
        for name in ("telegram-13.0.3-apkpure-70242-unknown.apk",
                     "telegram-13.0.3-apkpure-70242-unknown.download"):
            with self.subTest(name):
                path = os.path.join(self.app_dir, name)
                with open(path, "wb") as f:
                    f.write(b"<html>Just a moment...</html>")
                self.addCleanup(os.remove, path)

                self.assertIsNone(self._find("apkpure", "70242"))

    def test_id_is_not_a_prefix_match(self):
        self._file("telegram-13.0.3-apkmirror-12-universal.apk")
        self._file("telegram-13.0.3-apkmirror-1_2-universal.apk")
        self.assertIsNone(self._find("apkmirror", "1"))

    def test_ignores_legacy_unversioned_file(self):
        self._file("telegram-13.0.3-apkmirror.apkm")
        self.assertIsNone(self._find("apkmirror", "1"))

    def test_ignores_other_source_and_temp_files(self):
        self._file("telegram-13.0.3-uptodown-1-universal.apk")
        self._file(".telegram-13.0.3-apkmirror-1-x.apk")
        self.assertIsNone(self._find("apkmirror", "1"))

    def test_missing_app_dir_returns_none(self):
        self.assertIsNone(
            find_existing_variant(self.output_dir, "nope", "1.0", "apkmirror", "1")
        )


class FinalizeVariantTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = tmp.name
        self.scraper = ProbeScraper(output_dir=self.output_dir)
        self.app_dir = os.path.join(self.output_dir, "telegram")
        os.makedirs(self.app_dir)

    def _temp(self, entries):
        return _write_zip(os.path.join(self.app_dir, ".tmp-download"), entries)

    def test_names_file_with_variant_and_arch(self):
        temp_path = self._temp({
            "AndroidManifest.xml": b"manifest",
            "lib/arm64-v8a/a.so": b"so",
        })

        info = self.scraper._finalize_downloaded_file(
            temp_path, "telegram", "13.0.3", "7"
        )

        self.assertEqual(
            os.path.basename(info.filepath),
            "telegram-13.0.3-probe-7-arm64-v8a.apk",
        )
        self.assertEqual(info.variant_id, "7")
        self.assertEqual(info.package_type, "apk")
        self.assertEqual(info.architectures, ["arm64-v8a"])
        self.assertEqual(info.size_bytes, os.path.getsize(info.filepath))
        self.assertFalse(os.path.exists(temp_path))

    def test_split_bundle_keeps_suggested_extension(self):
        temp_path = self._temp({
            "base.apk": b"apk",
            "split_config.arm64_v8a.apk": b"apk",
        })

        info = self.scraper._finalize_downloaded_file(
            temp_path, "telegram", "13.0.3", "2", "x.apkm"
        )

        self.assertEqual(
            os.path.basename(info.filepath),
            "telegram-13.0.3-probe-2-arm64-v8a.apkm",
        )

    def test_force_replaces_same_variant_with_new_arch_label(self):
        _package(os.path.join(self.app_dir, "telegram-13.0.3-probe-7-arm64-v8a.apk"))
        neighbor = _package(
            os.path.join(self.app_dir, "telegram-13.0.3-probe-70-universal.apk")
        )
        temp_path = self._temp({"AndroidManifest.xml": b"manifest"})

        self.scraper._finalize_downloaded_file(temp_path, "telegram", "13.0.3", "7")

        variant_files = sorted(
            name for name in os.listdir(self.app_dir)
            if name.startswith("telegram-13.0.3-probe-7-")
        )
        self.assertEqual(variant_files, ["telegram-13.0.3-probe-7-universal.apk"])
        self.assertTrue(os.path.exists(neighbor))


class FakeScraper(BaseScraper):
    SOURCE_NAME = "fake"

    def __init__(self, output_dir, variant_ids, fail_ids=(), list_error=None):
        super().__init__(output_dir=output_dir)
        self.variant_ids = variant_ids
        self.fail_ids = set(fail_ids)
        self.list_error = list_error
        self.download_calls = []

    def _get_browser(self):
        return None, None, None

    def _close_browser(self, playwright, browser):
        pass

    def _list_versions(self, page, config):
        return []

    def _app_name(self, config):
        return "app"

    def _list_variants(self, page, config, version):
        if self.list_error:
            raise self.list_error
        return [
            VariantRef(variant_id=v, url=f"https://example.test/{v}")
            for v in self.variant_ids
        ]

    def _download_variant(self, page, app_name, version, variant):
        self.download_calls.append(variant.variant_id)
        if variant.variant_id in self.fail_ids:
            raise DownloadError("boom")
        app_dir = os.path.join(self.output_dir, app_name)
        os.makedirs(app_dir, exist_ok=True)
        temp_path = _write_zip(
            os.path.join(app_dir, f".tmp-{variant.variant_id}"),
            {"AndroidManifest.xml": b"manifest"},
        )
        return self._finalize_downloaded_file(
            temp_path, app_name, version, variant.variant_id
        )


class ScrapeLoopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.output_dir = tmp.name

    def _scraper(self, variant_ids, fail_ids=(), list_error=None):
        return FakeScraper(self.output_dir, variant_ids, fail_ids, list_error)

    def _existing(self, variant_id):
        return self._saved(f"app-13.0.3-fake-{variant_id}-universal.apk")

    def _saved(self, filename):
        app_dir = os.path.join(self.output_dir, "app")
        os.makedirs(app_dir, exist_ok=True)
        return _package(os.path.join(app_dir, filename))

    def _saved_empty(self, filename):
        app_dir = os.path.join(self.output_dir, "app")
        os.makedirs(app_dir, exist_ok=True)
        open(os.path.join(app_dir, filename), "wb").close()

    def test_listing_failure_does_not_count_an_empty_file(self):
        self._saved_empty("app-13.0.3-fake.apkm")
        scraper = self._scraper([], list_error=DownloadError("blocked"))

        with self.assertRaises(DownloadError):
            scraper.scrape({}, "13.0.3")

    def test_empty_variant_file_is_downloaded_again(self):
        self._saved_empty("app-13.0.3-fake-1-universal.apk")
        scraper = self._scraper(["1"])

        scraper.scrape({}, "13.0.3")

        self.assertEqual(scraper.download_calls, ["1"])

    def test_listing_failure_keeps_files_already_on_disk(self):
        existing = sorted([self._existing("1"), self._existing("2")])
        scraper = self._scraper([], list_error=VersionNotFoundError("blocked"))

        with self.assertLogs("FakeScraper", level="WARNING"):
            result = scraper.scrape({}, "13.0.3")

        self.assertTrue(result.succeeded)
        self.assertEqual(sorted(result.existing), existing)
        self.assertEqual(scraper.download_calls, [])

    def test_empty_listing_keeps_legacy_file_on_disk(self):
        legacy = self._saved("app-13.0.3-fake.apkm")

        with self.assertLogs("FakeScraper", level="WARNING"):
            result = self._scraper([]).scrape({}, "13.0.3")

        self.assertEqual(result.existing, [legacy])

    def test_listing_failure_ignores_other_versions_and_sources(self):
        self._saved("app-13.0.31-fake-1-universal.apk")
        self._saved("app-13.0.3-other-1-universal.apk")
        scraper = self._scraper([], list_error=DownloadError("blocked"))

        with self.assertRaises(DownloadError):
            scraper.scrape({}, "13.0.3")

    def test_listing_failure_with_force_still_raises(self):
        self._existing("1")
        scraper = self._scraper([], list_error=VersionNotFoundError("blocked"))

        with self.assertRaises(VersionNotFoundError):
            scraper.scrape({}, "13.0.3", force=True)

    def test_downloads_every_variant(self):
        result = self._scraper(["1", "2"]).scrape({}, "13.0.3")

        self.assertIsInstance(result, ScrapeResult)
        self.assertEqual([i.variant_id for i in result.downloaded], ["1", "2"])
        for info in result.downloaded:
            self.assertTrue(os.path.exists(info.filepath))
        self.assertTrue(result.succeeded)

    def test_skips_existing_variant(self):
        existing = self._existing("1")
        scraper = self._scraper(["1", "2"])

        result = scraper.scrape({}, "13.0.3")

        self.assertEqual(scraper.download_calls, ["2"])
        self.assertEqual(result.existing, [existing])

    def test_force_redownloads_existing(self):
        self._existing("1")
        scraper = self._scraper(["1", "2"])

        result = scraper.scrape({}, "13.0.3", force=True)

        self.assertEqual(scraper.download_calls, ["1", "2"])
        self.assertEqual(result.existing, [])

    def test_only_existing_variants_still_succeeds(self):
        self._existing("1")

        result = self._scraper(["1"]).scrape({}, "13.0.3")

        self.assertTrue(result.succeeded)
        self.assertEqual(result.downloaded, [])

    def test_one_failure_does_not_stop_others(self):
        with self.assertLogs("FakeScraper", level="WARNING") as logs:
            result = self._scraper(["1", "2"], fail_ids={"1"}).scrape({}, "13.0.3")

        self.assertIn("Failed variant 1: boom", logs.output[0])

        self.assertEqual([i.variant_id for i in result.downloaded], ["2"])
        self.assertEqual(result.failed, [("1", "boom")])

    def test_all_failures_raise_download_error(self):
        scraper = self._scraper(["1", "2"], fail_ids={"1", "2"})

        with self.assertLogs("FakeScraper", level="WARNING"):
            with self.assertRaises(DownloadError):
                scraper.scrape({}, "13.0.3")

    def test_no_variants_raises_version_not_found(self):
        with self.assertRaises(VersionNotFoundError):
            self._scraper([]).scrape({}, "13.0.3")

    def test_normalize_variant_ids(self):
        variants = [
            VariantRef(variant_id="1", url="u1"),
            VariantRef(variant_id="1", url="u2"),
            VariantRef(variant_id="a-b", url="u3"),
        ]

        with self.assertLogs("variants", level="WARNING"):
            normalized = normalize_variant_ids(variants)

        self.assertEqual(
            [v.variant_id for v in normalized],
            ["1", "1_2", "a_b"],
        )


if __name__ == "__main__":
    unittest.main()
