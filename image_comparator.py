from PIL import Image, ImageChops, ImageStat


def compare_screenshots(img1_path, img2_path, diff_output_path, threshold=5) -> bool:
    """
    Compares two screenshots and saves a diff image if the average pixel difference
    exceeds the threshold.

    :param img1_path: Path to first image.
    :param img2_path: Path to second image.
    :param diff_output_path: Path to save the diff image.
    :param threshold: Average difference threshold.
    :return: True if diff image was created, otherwise False.
    """
    # Open images and convert them to RGB for consistency.
    img1 = Image.open(img1_path).convert('RGB')
    img2 = Image.open(img2_path).convert('RGB')
    diff = ImageChops.difference(img1, img2)
    stat = ImageStat.Stat(diff)
    avg_diff = sum(stat.mean) / len(stat.mean)
    print(f"Average difference for {img1_path} vs {img2_path}: {avg_diff}")
    if avg_diff > threshold:
        diff.save(diff_output_path)
        return True
    return False
