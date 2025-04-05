import os
import stat
import zipfile

import requests

from utils import detect_platform


class ChromeInstaller:
    def __init__(self, version="latest"):
        self.platform_str = detect_platform()
        # If version is "latest", fetch it from the API.
        if version.lower() == "latest":
            self.version = self.get_latest_version()
            print(f"Latest stable version detected: {self.version}")
        else:
            self.version = version
        self.base_dir = os.path.dirname(os.path.abspath(__file__))

    def get_latest_version(self):
        url = "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_STABLE"
        response = requests.get(url)
        response.raise_for_status()
        return response.text.strip()

    def install_chromedriver(self):
        tmp_dir = os.path.join(self.base_dir, 'tmp', 'chromedriver')
        os.makedirs(tmp_dir, exist_ok=True)
        exe_name = 'chromedriver.exe' if self.platform_str.startswith("win") else 'chromedriver'
        expected_path = os.path.join(tmp_dir, f'chromedriver-{self.platform_str}', exe_name)
        if os.path.exists(expected_path):
            print(f"Found existing chromedriver at: {expected_path}")
            return expected_path

        url = f"https://storage.googleapis.com/chrome-for-testing-public/{self.version}/{self.platform_str}/chromedriver-{self.platform_str}.zip"
        zip_path = os.path.join(tmp_dir, 'chromedriver.zip')
        print(f"Downloading chromedriver from {url} ...")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmp_dir)
        os.chmod(expected_path, os.stat(expected_path).st_mode | stat.S_IEXEC)
        return expected_path

    def install_chrome(self):
        tmp_dir = os.path.join(self.base_dir, 'tmp', 'chrome')
        os.makedirs(tmp_dir, exist_ok=True)
        exe_name = 'chrome.exe' if self.platform_str.startswith("win") else 'chrome'
        expected_path = os.path.join(tmp_dir, f'chrome-{self.platform_str}', exe_name)
        if os.path.exists(expected_path):
            print(f"Found existing chrome binary at: {expected_path}")
            return expected_path

        url = f"https://storage.googleapis.com/chrome-for-testing-public/{self.version}/{self.platform_str}/chrome-{self.platform_str}.zip"
        zip_path = os.path.join(tmp_dir, 'chrome.zip')
        print(f"Downloading chrome from {url} ...")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmp_dir)
        os.chmod(expected_path, os.stat(expected_path).st_mode | stat.S_IEXEC)
        return expected_path
