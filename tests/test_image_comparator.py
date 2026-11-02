#!/usr/bin/env python3
"""Tests for the screenshot comparison."""
import os
import sys
import tempfile
import unittest

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from image_comparator import ImageComparator  # noqa: E402


class ImageComparatorTest(unittest.TestCase):
    def compare(self, live_size, dev_size, live_colour="black", dev_colour="black"):
        """Return the score and the size of the diff image."""
        with tempfile.TemporaryDirectory() as temp_dir:
            live = os.path.join(temp_dir, "live.png")
            dev = os.path.join(temp_dir, "dev.png")
            diff = os.path.join(temp_dir, "diff.png")
            Image.new("RGB", live_size, live_colour).save(live)
            Image.new("RGB", dev_size, dev_colour).save(dev)
            score = ImageComparator(threshold=0).compare_screenshots(live, dev, diff, always_generate_diff=True)
            with Image.open(diff) as image:
                return score, image.size

    def test_page_that_grew_at_the_bottom_differs(self):
        for colour in ("black", "white"):
            with self.subTest(colour=colour):
                score, diff_size = self.compare((100, 100), (100, 150), colour, colour)
                # 50 of 150 rows exist on one side only and count as fully different.
                self.assertAlmostEqual(score, 85.0)
                self.assertEqual(diff_size, (100, 150))

    def test_page_that_shrank_differs(self):
        score, _ = self.compare((100, 150), (100, 100))
        self.assertAlmostEqual(score, 85.0)

    def test_page_with_other_width_differs(self):
        score, diff_size = self.compare((120, 100), (100, 100))
        self.assertGreater(score, 0)
        self.assertEqual(diff_size, (120, 100))

    def test_identical_images_score_zero(self):
        score, _ = self.compare((100, 100), (100, 100))
        self.assertEqual(score, 0.0)

    def test_same_size_difference_is_unchanged(self):
        score, _ = self.compare((100, 100), (100, 100), "black", "white")
        self.assertAlmostEqual(score, 255.0)


if __name__ == "__main__":
    unittest.main()
