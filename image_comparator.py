import logging

from PIL import Image, ImageChops, ImageStat

logger = logging.getLogger(__name__)


class ImageComparator:

    def __init__(self, threshold: int = 5):
        """
        Initializes the ImageComparator with a threshold for pixel difference.

        :param threshold:
        """
        self.threshold = threshold

    def compare_screenshots(self, img1_path: str, img2_path: str, diff_output_path: str,
                            always_generate_diff: bool = False) -> float:
        """
        Compares two screenshots and saves a diff image if the average pixel difference
        exceeds the threshold.

        :param img1_path: Path to first image.
        :param img2_path: Path to second image.
        :param diff_output_path: Path to save the diff image.
        :param always_generate_diff: If True, always generate the diff image regardless of the threshold.
        :return: Average pixel difference between the two images.
        """
        # open images and convert them to RGB for consistency
        img1 = Image.open(img1_path).convert('RGB')
        img2 = Image.open(img2_path).convert('RGB')
        if img1.size == img2.size:
            diff = ImageChops.difference(img1, img2)
        else:
            diff = self.difference_on_common_canvas(img1, img2)
        stat = ImageStat.Stat(diff)
        avg_diff = sum(stat.mean) / len(stat.mean)
        logger.info(f"Average difference for {img1_path} vs {img2_path}: {avg_diff}")
        if avg_diff > self.threshold or always_generate_diff:
            diff.save(diff_output_path)

        return avg_diff

    @staticmethod
    def difference_on_common_canvas(img1: Image.Image, img2: Image.Image) -> Image.Image:
        """
        Returns the difference on a canvas of the larger size. ImageChops.difference
        compares only the overlap, so a page that grew would pass. No pad colour is safe
        (the extra area can have any colour), so the area outside the overlap counts as
        fully different.

        :param img1: First image (RGB).
        :param img2: Second image (RGB).
        :return: The difference image.
        """
        width = max(img1.width, img2.width)
        height = max(img1.height, img2.height)
        overlap = (0, 0, min(img1.width, img2.width), min(img1.height, img2.height))
        diff = Image.new('RGB', (width, height), (255, 255, 255))
        diff.paste(ImageChops.difference(img1.crop(overlap), img2.crop(overlap)), (0, 0))
        return diff
