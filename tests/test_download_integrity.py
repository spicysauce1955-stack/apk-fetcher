"""Only complete Android packages may be saved, and good files must survive."""

import io
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest import mock

from patchright.sync_api import Error as PlaywrightError

from src.base import BaseScraper, DownloadError, package_problem


def _zip_bytes(entries, compression=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


APK = _zip_bytes({"AndroidManifest.xml": b"manifest", "classes.dex": b"dex" * 200})
HTML = b"<html><title>Just a moment...</title></html>"


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


class PackageProblemTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def _file(self, data):
        path = os.path.join(self.dir, "candidate")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_complete_packages_have_no_problem(self):
        deflated = _zip_bytes({"AndroidManifest.xml": b"m" * 500},
                              zipfile.ZIP_DEFLATED)
        bundle = _zip_bytes({"base.apk": APK, "split_config.x86.apk": APK})
        for data in (APK, deflated, bundle):
            self.assertIsNone(package_problem(self._file(data)))

    def test_rejects_files_that_are_not_packages(self):
        cases = {
            "empty": b"",
            "html": HTML,
            "zip without android contents": _zip_bytes({"readme.txt": b"hi"}),
            "front half of a zip": APK[: len(APK) // 2],
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertIsNotNone(package_problem(self._file(data)))

    def test_rejects_bundle_cut_just_after_an_inner_apk(self):
        # A stored bundle cut here ends with the inner APK's own zip footer,
        # so the fragment opens cleanly as if it were that APK.
        bundle = _zip_bytes({"base.apk": APK, "split_config.x86.apk": APK})
        fragment = bundle[: bundle.index(APK) + len(APK)]

        self.assertTrue(zipfile.is_zipfile(self._file(fragment)))
        self.assertIsNotNone(package_problem(self._file(fragment)))

    def test_rejects_corrupt_entry(self):
        data = bytearray(_zip_bytes({
            "AndroidManifest.xml": b"manifest",
            "classes.dex": b"d" * 5000,
        }))
        data[data.index(b"ddddd") + 10] = ord("x")

        self.assertIsNotNone(package_problem(self._file(bytes(data))))


class FinalizeRejectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scraper = ProbeScraper(output_dir=tmp.name)
        self.app_dir = os.path.join(tmp.name, "telegram")
        os.makedirs(self.app_dir)

    def _temp(self, data):
        path = os.path.join(self.app_dir, ".tmp-download")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_junk_with_an_apk_filename_is_rejected(self):
        with self.assertRaises(DownloadError):
            self.scraper._finalize_downloaded_file(
                self._temp(HTML), "telegram", "1.0", "7", "telegram.apk"
            )

    def test_package_with_a_corrupt_entry_is_rejected(self):
        # Corrupt data still classifies as an APK, so only validation stops it.
        data = bytearray(_zip_bytes({
            "AndroidManifest.xml": b"manifest",
            "classes.dex": b"d" * 5000,
        }))
        data[data.index(b"ddddd") + 10] = ord("x")

        with self.assertRaises(DownloadError):
            self.scraper._finalize_downloaded_file(
                self._temp(bytes(data)), "telegram", "1.0", "7"
            )

    def test_old_file_survives_a_failed_replacement(self):
        old = os.path.join(self.app_dir, "telegram-1.0-probe-7-arm64-v8a.apk")
        with open(old, "wb") as f:
            f.write(APK)

        with mock.patch("src.base.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.scraper._finalize_downloaded_file(
                    self._temp(APK), "telegram", "1.0", "7"
                )

        self.assertTrue(os.path.exists(old))


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body, declared_length = self.server.routes[self.path]
        self.send_response(200)
        self.send_header("Content-Length", str(declared_length))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


class UrlDownloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scraper = ProbeScraper(output_dir=tmp.name)
        self.app_dir = os.path.join(tmp.name, "telegram")

        environment = mock.patch.dict(
            os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}
        )
        environment.start()
        self.addCleanup(environment.stop)

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        server.routes = {
            "/good": (APK, len(APK)),
            "/html": (HTML, len(HTML)),
            "/short": (APK, len(APK) + 1000),
        }
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"

    def _save(self, path):
        return self.scraper._save_url_file(
            self.base + path, "telegram", "1.0", "7"
        )

    def test_complete_package_is_saved(self):
        info = self._save("/good")

        self.assertEqual(os.path.basename(info.filepath),
                         "telegram-1.0-probe-7-universal.apk")

    def test_html_response_leaves_nothing_behind(self):
        with self.assertRaises(DownloadError):
            self._save("/html")

        self.assertEqual(os.listdir(self.app_dir), [])

    def test_body_shorter_than_declared_is_rejected(self):
        # The body is a valid package, so only the length check can catch it.
        with self.assertRaises(DownloadError):
            self._save("/short")

        self.assertEqual(os.listdir(self.app_dir), [])

    def test_force_style_replacement_keeps_good_file_when_response_is_junk(self):
        os.makedirs(self.app_dir)
        good = os.path.join(self.app_dir, "telegram-1.0-probe-7-arm64-v8a.apk")
        with open(good, "wb") as f:
            f.write(APK)

        with self.assertRaises(DownloadError):
            self._save("/html")

        with open(good, "rb") as f:
            self.assertEqual(f.read(), APK)
        self.assertEqual(os.listdir(self.app_dir), [os.path.basename(good)])


class FakeTimerPage:
    def __init__(self):
        self.listeners = []
        self.closed = False

    def on(self, event, handler):
        self.listeners.append((event, handler))

    def evaluate(self, script):
        self.script = script

    def close(self):
        self.closed = True

    def tick(self):
        # Patchright delivers no console events, so only a page that really
        # navigates can report back while save_as blocks.
        if "location.hash" not in self.script:
            return
        for event, handler in self.listeners:
            if event == "framenavigated":
                handler(SimpleNamespace(url="about:blank#tick"))


class FakeDownload:
    """Mirrors the parts of Playwright's Download that saving relies on."""

    suggested_filename = "telegram.apk"

    def __init__(self, body=APK, stalls=False):
        self.body = body
        self.stalls = stalls
        self.cancelled = False
        self.timer_page = FakeTimerPage()
        context = SimpleNamespace(new_page=lambda: self.timer_page)
        self.page = SimpleNamespace(context=context)

    def cancel(self):
        self.cancelled = True

    def save_as(self, path):
        # Ticks keep arriving while Playwright blocks on the transfer.
        self.timer_page.tick()
        if self.stalls and self.cancelled:
            raise PlaywrightError("Download.save_as: canceled")
        with open(path, "wb") as f:
            f.write(self.body)


class BrowserDownloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scraper = ProbeScraper(output_dir=tmp.name)
        self.app_dir = os.path.join(tmp.name, "telegram")

    def _save(self, download):
        return self.scraper._save_downloaded_file(
            download, "telegram", "1.0", "7"
        )

    def test_complete_package_is_saved(self):
        info = self._save(FakeDownload())

        self.assertEqual(os.path.basename(info.filepath),
                         "telegram-1.0-probe-7-universal.apk")

    def test_html_attachment_leaves_nothing_behind(self):
        with self.assertRaises(DownloadError):
            self._save(FakeDownload(body=HTML))

        self.assertEqual(os.listdir(self.app_dir), [])

    def test_stalled_transfer_is_cancelled_at_the_deadline(self):
        self.scraper.DOWNLOAD_SAVE_TIMEOUT_SECONDS = 0
        download = FakeDownload(stalls=True)

        with self.assertRaises(DownloadError):
            self._save(download)

        self.assertTrue(download.cancelled)
        self.assertEqual(os.listdir(self.app_dir), [])

    def test_transfer_inside_the_deadline_is_left_alone(self):
        self.scraper.DOWNLOAD_SAVE_TIMEOUT_SECONDS = 600
        download = FakeDownload()

        self._save(download)

        self.assertFalse(download.cancelled)

    def test_timer_tab_is_closed_afterwards(self):
        download = FakeDownload()

        self._save(download)

        self.assertTrue(download.timer_page.closed)


if __name__ == "__main__":
    unittest.main()
