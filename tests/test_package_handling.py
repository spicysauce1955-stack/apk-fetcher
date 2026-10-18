import os
import tempfile
import unittest
import zipfile

from src.base import BaseScraper


class ProbeScraper(BaseScraper):
    def _app_name(self, config):
        return "probe"

    def _list_variants(self, page, config, version):
        return []

    def _download_variant(self, page, app_name, version, variant):
        raise NotImplementedError


class PackageDetectionTests(unittest.TestCase):
    def setUp(self):
        self.output_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.output_dir.cleanup)
        self.scraper = ProbeScraper(output_dir=self.output_dir.name)

    def _write_zip(self, directory, filename, entries):
        path = os.path.join(directory, filename)
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return path

    def test_detects_single_apk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp, "single.apk", {
                "AndroidManifest.xml": b"manifest",
                "classes.dex": b"dex",
            })

            self.assertEqual(
                self.scraper._identify_package_file(path),
                ("apk", ".apk"),
            )

    def test_detects_xapk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp, "bundle.xapk", {
                "manifest.json": b"{}",
                "base.apk": b"apk",
            })

            self.assertEqual(
                self.scraper._identify_package_file(path),
                ("xapk", ".xapk"),
            )

    def test_preserves_known_split_apk_extension_from_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp, "bundle.zip", {
                "base.apk": b"apk",
                "split_config.arm64_v8a.apk": b"apk",
            })

            self.assertEqual(
                self.scraper._identify_package_file(path, "download.apkm"),
                ("apkm", ".apkm"),
            )

    def test_defaults_unknown_split_apk_archive_to_apks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp, "bundle.zip", {
                "base.apk": b"apk",
                "split_config.arm64_v8a.apk": b"apk",
            })

            self.assertEqual(
                self.scraper._identify_package_file(path),
                ("apks", ".apks"),
            )

    def test_detects_aab(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp, "app.aab", {
                "BundleConfig.pb": b"pb",
                "base/manifest/AndroidManifest.xml": b"manifest",
            })

            self.assertEqual(
                self.scraper._identify_package_file(path),
                ("aab", ".aab"),
            )

    def test_unknown_file_uses_download_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "unknown.bin")
            with open(path, "wb") as f:
                f.write(b"not a zip")

            self.assertEqual(
                self.scraper._identify_package_file(path),
                ("unknown", ".download"),
            )


if __name__ == "__main__":
    unittest.main()
