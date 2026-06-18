import io
import os
import tempfile
import unittest
import zipfile

from src.base import detect_architectures


def _zip(entries):
    """Write a zip with the given entry names to a temp file, return its path."""
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        for name in entries:
            zf.writestr(name, b"x")
    return path


def _nested_zip(outer_entries, inner_name, inner_entries):
    """Zip containing a nested apk (inner_name) that itself holds inner_entries."""
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner:
        for name in inner_entries:
            inner.writestr(name, b"x")
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        for name in outer_entries:
            zf.writestr(name, b"x")
        zf.writestr(inner_name, inner_buf.getvalue())
    return path


class TestDetectArchitectures(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            if os.path.exists(p):
                os.remove(p)

    def track(self, path):
        self._paths.append(path)
        return path

    def test_plain_apk_single_abi(self):
        path = self.track(_zip(["AndroidManifest.xml", "lib/arm64-v8a/libfoo.so"]))
        self.assertEqual(detect_architectures(path), ["arm64-v8a"])

    def test_plain_apk_multiple_abis_sorted(self):
        path = self.track(_zip([
            "AndroidManifest.xml",
            "lib/x86_64/libfoo.so",
            "lib/arm64-v8a/libfoo.so",
        ]))
        self.assertEqual(detect_architectures(path), ["arm64-v8a", "x86_64"])

    def test_xapk_config_split_name(self):
        path = self.track(_zip(["manifest.json", "base.apk", "config.x86_64.apk"]))
        self.assertEqual(detect_architectures(path), ["x86_64"])

    def test_split_bundle_nested_lib(self):
        path = self.track(_nested_zip(
            ["base.apk"],
            "split_extra.apk",
            ["AndroidManifest.xml", "lib/armeabi-v7a/libbar.so"],
        ))
        self.assertEqual(detect_architectures(path), ["armeabi-v7a"])

    def test_split_config_underscore_and_hyphen_names(self):
        # Locks the slug logic: x86_64 keeps its underscore, arm64_v8a/armeabi_v7a
        # normalize to hyphens. Uses the split_config.<abi>.apk prefix form.
        path = self.track(_zip([
            "manifest.json",
            "base.apk",
            "split_config.x86_64.apk",
            "split_config.arm64_v8a.apk",
            "split_config.armeabi_v7a.apk",
        ]))
        self.assertEqual(
            detect_architectures(path),
            ["arm64-v8a", "armeabi-v7a", "x86_64"],
        )

    def test_aab_base_lib(self):
        path = self.track(_zip(["BundleConfig.pb", "base/lib/x86/libfoo.so"]))
        self.assertEqual(detect_architectures(path), ["x86"])

    def test_pure_java_apk_universal(self):
        path = self.track(_zip(["AndroidManifest.xml", "classes.dex"]))
        self.assertEqual(detect_architectures(path), ["universal"])

    def test_config_named_apk_does_not_crash(self):
        # Regression: entries like "config.apk"/"xconfig.apk" match "config."
        # only via the extension boundary and must not raise IndexError.
        path = self.track(_zip([
            "manifest.json",
            "base.apk",
            "config.apk",
            "xconfig.apk",
            "feature.config.apk",
        ]))
        self.assertEqual(detect_architectures(path), ["universal"])

    def test_non_zip_returns_empty(self):
        fd, path = tempfile.mkstemp(suffix=".download")
        os.write(fd, b"not a zip")
        os.close(fd)
        self.track(path)
        self.assertEqual(detect_architectures(path), [])


if __name__ == "__main__":
    unittest.main()
