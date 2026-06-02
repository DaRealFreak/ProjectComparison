#!/usr/bin/env python3
"""Regression tests for ChromeInstaller's zip extraction.

These guard the bug where ``ZipFile.extractall`` dropped the unix permission bits
(and symlinks) stored in the Chrome for Testing archives, leaving the extracted
chrome/chromedriver binaries non-executable -- which made Chrome exit on launch
on Linux and macOS ("session not created: Chrome instance exited").

Pure stdlib (``unittest``); run with:

    python -m unittest discover -s tests -p "test_*.py"
"""
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chrome_installer import ChromeInstaller  # noqa: E402

# Symlinks and the unix exec bit only exist on POSIX; the Windows Chrome build
# ships neither, so those assertions are skipped there.
POSIX = os.name != "nt"


def _build_zip(path: str) -> None:
    """Write a small zip that mimics how Chrome for Testing stores unix modes."""
    with zipfile.ZipFile(path, "w") as zf:
        executable = zipfile.ZipInfo("pkg/runme")
        executable.external_attr = 0o100755 << 16
        zf.writestr(executable, b"#!/bin/sh\necho hi\n")

        plain = zipfile.ZipInfo("pkg/data.txt")
        plain.external_attr = 0o100644 << 16
        zf.writestr(plain, b"plain data\n")

        if POSIX:
            link = zipfile.ZipInfo("pkg/link")
            link.external_attr = 0o120777 << 16  # S_IFLNK
            zf.writestr(link, "runme")


class ExtractZipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cft-extract-")
        self.dest = os.path.join(self.tmp, "out")
        zip_path = os.path.join(self.tmp, "pkg.zip")
        _build_zip(zip_path)
        ChromeInstaller._extract_zip(zip_path, self.dest)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_contents_preserved(self):
        runme = os.path.join(self.dest, "pkg", "runme")
        self.assertTrue(os.path.isfile(runme))
        with open(runme, "rb") as f:
            self.assertEqual(f.read(), b"#!/bin/sh\necho hi\n")

    @unittest.skipUnless(POSIX, "exec bit is POSIX-only")
    def test_executable_bit_restored(self):
        runme = os.path.join(self.dest, "pkg", "runme")
        self.assertTrue(os.access(runme, os.X_OK), "extracted binary must be executable")

    @unittest.skipUnless(POSIX, "exec bit is POSIX-only")
    def test_non_executable_entry_stays_plain(self):
        data = os.path.join(self.dest, "pkg", "data.txt")
        self.assertFalse(os.stat(data).st_mode & stat.S_IXUSR)

    @unittest.skipUnless(POSIX, "symlinks not represented in the Windows build")
    def test_symlink_restored(self):
        link = os.path.join(self.dest, "pkg", "link")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), "runme")


if __name__ == "__main__":
    unittest.main()
