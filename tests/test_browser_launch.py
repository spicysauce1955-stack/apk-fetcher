"""Starting the browser: what it inherits and what a failure says."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from patchright.sync_api import Error as PlaywrightError
from patchright.sync_api import TimeoutError as PatchrightTimeout
from patchright.sync_api import sync_playwright as start_patchright

import src.base
import src.uptodown
from src.base import (
    BaseScraper,
    DownloadError,
    browser_environment,
    browser_launch_hint,
)


SNAP_SETTINGS = {
    "GSETTINGS_SCHEMA_DIR": "/home/u/snap/code/278/.local/share/glib-2.0/schemas",
    "GTK_PATH": "/snap/code/278/usr/lib/x86_64-linux-gnu/gtk-3.0",
}
CONFIGURED = {"PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": "/usr/bin/google-chrome"}
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ProbeScraper(BaseScraper):
    def _list_versions(self, page, config):
        return []

    def _app_name(self, config):
        return "probe"

    def _list_variants(self, page, config, version):
        return []

    def _download_variant(self, page, app_name, version, variant):
        raise NotImplementedError


class BrowserEnvironmentTests(unittest.TestCase):
    def test_drops_gui_settings_leaked_by_a_snap(self):
        cleaned = browser_environment(
            {**SNAP_SETTINGS, "HOME": "/home/u", "DISPLAY": ":0"}
        )

        self.assertEqual(cleaned, {"HOME": "/home/u", "DISPLAY": ":0"})

    def test_removes_only_snap_folders_from_the_data_dirs(self):
        cleaned = browser_environment({
            "XDG_DATA_DIRS": "/home/u/snap/code/278/.local/share:"
                             "/snap/code/278/usr/share:/usr/share/ubuntu:"
                             "/usr/local/share/:/usr/share/:/var/lib/snapd/desktop",
        })

        self.assertEqual(
            cleaned["XDG_DATA_DIRS"],
            "/usr/share/ubuntu:/usr/local/share/:/usr/share/:/var/lib/snapd/desktop",
        )

    def test_settings_outside_a_snap_are_left_alone(self):
        environ = {
            "GTK_PATH": "/usr/lib/x86_64-linux-gnu/gtk-3.0",
            "GSETTINGS_SCHEMA_DIR": "/opt/schemas",
            "XDG_DATA_DIRS": "/usr/local/share:/usr/share",
        }

        self.assertEqual(browser_environment(environ), environ)

    def test_leaves_the_original_environment_unchanged(self):
        environ = dict(SNAP_SETTINGS)

        browser_environment(environ)

        self.assertEqual(environ, SNAP_SETTINGS)


class LaunchHintTests(unittest.TestCase):
    def test_configured_browser_is_not_told_to_install_chromium(self):
        hint = browser_launch_hint(CONFIGURED)

        self.assertNotIn("patchright install", hint)
        self.assertIn("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", hint)

    def test_without_a_configured_browser_it_suggests_installing_chromium(self):
        self.assertIn("patchright install", browser_launch_hint({}))


class LaunchBrowserTests(unittest.TestCase):
    def _launch(self, launch):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scraper = ProbeScraper(output_dir=tmp.name, headless=False)
        playwright = SimpleNamespace(
            chromium=SimpleNamespace(launch_persistent_context=launch)
        )
        with mock.patch.dict(os.environ, {**SNAP_SETTINGS, **CONFIGURED}, clear=True):
            return scraper._launch_browser(playwright)

    def test_browser_starts_without_the_snap_settings(self):
        launch = mock.Mock(return_value="browser")

        self.assertEqual(self._launch(launch), "browser")

        env = launch.call_args.kwargs["env"]
        self.assertNotIn("GSETTINGS_SCHEMA_DIR", env)
        self.assertEqual(env["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"],
                         "/usr/bin/google-chrome")

    def test_failure_message_carries_the_hint(self):
        launch = mock.Mock(side_effect=PlaywrightError("browser has been closed"))

        with self.assertRaises(DownloadError) as caught:
            self._launch(launch)

        self.assertIn("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", str(caught.exception))
        self.assertNotIn("patchright install", str(caught.exception))


class BrowserSessionTests(unittest.TestCase):
    """The browser the scrapers get, as Patchright recommends setting it up."""

    def _get_browser(self, headless=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.window = mock.Mock(name="window")
        self.context = mock.Mock(pages=[self.window])
        self.launch = mock.Mock(return_value=self.context)
        self.driver = mock.Mock()
        self.driver.chromium.launch_persistent_context = self.launch
        scraper = ProbeScraper(output_dir=tmp.name, headless=headless)
        with mock.patch("src.base.sync_playwright") as start, \
                mock.patch.dict(os.environ, CONFIGURED, clear=True):
            start.return_value.start.return_value = self.driver
            return scraper, scraper._get_browser()

    def test_every_run_shares_one_profile_folder_in_the_project(self):
        self._get_browser()

        self.assertEqual(self.launch.call_args.args[0],
                         os.path.join(PROJECT_ROOT, "browser-profile"))

    def test_configured_chrome_is_launched_visibly(self):
        self._get_browser()

        kwargs = self.launch.call_args.kwargs
        self.assertIs(kwargs["headless"], False)
        self.assertEqual(kwargs["executable_path"], "/usr/bin/google-chrome")

    def test_browser_keeps_its_real_identity(self):
        self._get_browser()

        self.assertNotIn("user_agent", self.launch.call_args.kwargs)

    def test_page_size_comes_from_the_real_window(self):
        self._get_browser()

        self.assertIs(self.launch.call_args.kwargs["no_viewport"], True)

    def test_headless_keeps_a_desktop_sized_page(self):
        self._get_browser(headless=True)

        self.assertIs(self.launch.call_args.kwargs["no_viewport"], False)

    def test_scrapers_use_the_tab_the_browser_opened_with(self):
        _, (_, _, page) = self._get_browser()

        self.assertIs(page, self.window)
        self.context.new_page.assert_not_called()

    def test_closing_ends_the_browser_and_the_driver(self):
        scraper, (driver, browser, _) = self._get_browser()

        scraper._close_browser(driver, browser)

        self.context.close.assert_called_once_with()
        self.driver.stop.assert_called_once_with()


class DriverTests(unittest.TestCase):
    def test_browser_is_driven_by_patchright(self):
        self.assertIs(src.base.sync_playwright, start_patchright)

    def test_timeouts_handled_are_the_ones_patchright_raises(self):
        self.assertIs(src.base.PlaywrightTimeout, PatchrightTimeout)
        self.assertIs(src.uptodown.PlaywrightTimeout, PatchrightTimeout)


if __name__ == "__main__":
    unittest.main()
