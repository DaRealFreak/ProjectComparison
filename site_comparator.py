import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.service import Service


class SiteComparator:
    def __init__(self, driver_path, chrome_binary):
        self.driver_path = driver_path
        self.chrome_binary = chrome_binary
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        # Create a dedicated data directory for Chrome under tmp/chrome-data
        self.chrome_data_dir = os.path.join(self.base_dir, 'tmp', 'chrome-data')
        os.makedirs(self.chrome_data_dir, exist_ok=True)
        self._init_driver()

    def _init_driver(self):
        service = Service(executable_path=self.driver_path)
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument(f"--user-data-dir={self.chrome_data_dir}")
        options.binary_location = self.chrome_binary
        self.driver = webdriver.Chrome(service=service, options=options)

    def quit_driver(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

    def fetch_html(self, url):
        self.driver.get(url)
        time.sleep(3)  # Adjust delay as needed for dynamic content
        return self.driver.page_source

    def take_screenshot(self, url, width, height, screenshot_filename):
        # Assumes the page is already loaded (driver.get(url) has been called).
        self.driver.set_window_size(width, height)
        # No need to reload the page
        time.sleep(1)  # A short delay after resizing if needed
        self.driver.save_screenshot(screenshot_filename)
