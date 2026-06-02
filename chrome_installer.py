import logging
import os
import stat
import zipfile

import requests

from utils import detect_platform

logger = logging.getLogger(__name__)


class ChromeInstaller:
    LATEST_VERSION_URL = "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_STABLE"

    def __init__(self, version="latest"):
        self.platform_str = detect_platform()
        # if version is "latest", fetch it from the API
        if version.lower() == "latest":
            self.version = self.get_latest_version()
            logger.debug(f"Latest stable version detected: {self.version}")
        else:
            self.version = version
        self.base_dir = os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_latest_version():
        """
        Fetches the latest stable version of Chrome from the API

        :return:
        """
        response = requests.get(ChromeInstaller.LATEST_VERSION_URL)
        response.raise_for_status()
        return response.text.strip()

    @staticmethod
    def _download(expected_path: str, tmp_dir: str, url: str, zip_path: str) -> str:
        """
        Downloads the chromedriver zip from Chrome for Testing and extracts it

        :param expected_path:
        :param tmp_dir:
        :param url:
        :param zip_path:
        :return:
        """
        logger.info(f"Downloading chromedriver from {url} ...")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmp_dir)
        os.chmod(expected_path, os.stat(expected_path).st_mode | stat.S_IEXEC)
        return expected_path

    def install_chromedriver(self) -> str:
        """
        Downloads the chromedriver zip from Chrome for Testing and extracts it

        :return:
        """
        tmp_dir = os.path.join(self.base_dir, 'tmp', 'chromedriver')
        os.makedirs(tmp_dir, exist_ok=True)
        exe_name = 'chromedriver.exe' if self.platform_str.startswith("win") else 'chromedriver'
        expected_path = os.path.join(tmp_dir, f'chromedriver-{self.platform_str}', exe_name)
        if os.path.exists(expected_path):
            logger.debug(f"Chromedriver zip already downloaded: {expected_path}")
            return expected_path

        url = f"https://storage.googleapis.com/chrome-for-testing-public/{self.version}/{self.platform_str}/chromedriver-{self.platform_str}.zip"
        zip_path = os.path.join(tmp_dir, 'chromedriver.zip')
        return self._download(expected_path, tmp_dir, url, zip_path)

    def _chrome_exe_relpath(self) -> str:
        """
        Returns the Chrome executable path relative to the extracted
        chrome-<platform> folder. On macOS, Chrome for Testing ships as an .app
        bundle, so the binary lives several levels deep rather than as a bare file.

        :return:
        """
        if self.platform_str.startswith("win"):
            return 'chrome.exe'
        if self.platform_str.startswith("mac"):
            return os.path.join(
                'Google Chrome for Testing.app', 'Contents', 'MacOS', 'Google Chrome for Testing'
            )
        return 'chrome'

    def install_chrome(self) -> str:
        """
        Downloads the chrome binary zip from Chrome for Testing and extracts it

        :return:
        """
        tmp_dir = os.path.join(self.base_dir, 'tmp', 'chrome')
        os.makedirs(tmp_dir, exist_ok=True)
        expected_path = os.path.join(tmp_dir, f'chrome-{self.platform_str}', self._chrome_exe_relpath())
        if os.path.exists(expected_path):
            logger.debug(f"Chrome binary zip already downloaded: {expected_path}")
            return expected_path

        url = f"https://storage.googleapis.com/chrome-for-testing-public/{self.version}/{self.platform_str}/chrome-{self.platform_str}.zip"
        zip_path = os.path.join(tmp_dir, 'chrome.zip')
        return self._download(expected_path, tmp_dir, url, zip_path)
