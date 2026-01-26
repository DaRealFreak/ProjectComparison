import base64
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.service import Service


class SiteComparator:
    DEFAULT_FULLSCREEN_SIZE = (1920, 1080)
    driver = None

    def __init__(self, driver_path, chrome_binary, user_agent=None):
        self.driver_path = driver_path
        self.chrome_binary = chrome_binary
        self.user_agent = user_agent
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        # create a dedicated data directory for Chrome under tmp/chrome-data
        self.chrome_data_dir = os.path.join(self.base_dir, 'tmp', 'chrome-data')
        os.makedirs(self.chrome_data_dir, exist_ok=True)
        self._init_driver()

    def __del__(self):
        """
        Cleans up the WebDriver session when the object is deleted.
        """
        self.quit_driver()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Cleans up the WebDriver session when exiting the context manager.
        """
        self.quit_driver()

    def _init_driver(self):
        """
        Initializes the Chrome WebDriver with the specified options.
        """
        service = Service(executable_path=self.driver_path)
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument(f"--user-data-dir={self.chrome_data_dir}")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        if self.user_agent:
            options.add_argument(f"--user-agent={self.user_agent}")

        # Accept self-signed / untrusted certs
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--ignore-ssl-errors=yes")
        options.add_argument("--allow-insecure-localhost")

        # Selenium capability (works across browsers; for Chrome it complements the flags)
        options.set_capability("acceptInsecureCerts", True)

        options.binary_location = self.chrome_binary

        self.driver = webdriver.Chrome(service=service, options=options)

    def quit_driver(self):
        """
        Quits the WebDriver session and cleans up resources.
        """
        if self.driver:
            self.driver.close()
            self.driver.quit()
            self.driver = None

    def fetch_html(self, url: str):
        """
        Uses Selenium with the downloaded chromedriver and chrome binary to load the URL

        :param url:
        :return:
        """
        self.driver.get(url)
        time.sleep(1)
        return self.driver.page_source

    def take_screenshot(self, width: int, height: int, screenshot_filename: str):
        """
        Takes a screenshot of the current page in the browser.

        :param width:
        :param height:
        :param screenshot_filename:
        :return:
        """
        self.driver.set_window_size(width, height)

        # refresh for a clean state (sliders, etc.)
        self.driver.refresh()
        time.sleep(0.5)

        self.driver.save_screenshot(screenshot_filename)

    def take_fullscreen_screenshot(self, screenshot_filename: str):
        """
        Takes a full-page screenshot of the current page in the browser.

        :param screenshot_filename:
        :return:
        """
        # set the window size to a large value to ensure a consistent full-page capture
        self.driver.set_window_size(self.DEFAULT_FULLSCREEN_SIZE[0], self.DEFAULT_FULLSCREEN_SIZE[1])
        # refresh for a clean state (sliders, etc.)
        self.driver.refresh()
        time.sleep(0.5)

        # use Chrome DevTools Protocol to capture a full-page screenshot
        metrics = self.driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
        content_size = metrics["contentSize"]
        screenshot_config = {
            "captureBeyondViewport": True,
            "fromSurface": True,
            "clip": {
                "x": 0,
                "y": 0,
                "width": content_size["width"],
                "height": content_size["height"],
                "scale": 1
            }
        }
        result = self.driver.execute_cdp_cmd("Page.captureScreenshot", screenshot_config)
        data = result.get("data")
        with open(screenshot_filename, "wb") as f:
            f.write(base64.b64decode(data))

    def load_cookies(self, cookies_file: str):
        """
        Loads cookies from a JSON file into the current driver session.

        :param cookies_file:
        :return:
        """
        import json
        with open(cookies_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        for cookie in cookies:
            try:
                self.driver.add_cookie(cookie)
            except Exception as e:
                print(f"Could not add cookie: {cookie} -> {e}")
