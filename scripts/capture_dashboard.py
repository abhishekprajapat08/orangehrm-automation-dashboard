import argparse
import os
import sys
import time

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:5000")
    p.add_argument("--username", default=os.getenv("ORANGEHRM_USERNAME"))
    p.add_argument("--password", default=os.getenv("ORANGEHRM_PASSWORD"))
    p.add_argument("--first", required=True)
    p.add_argument("--middle", default="")
    p.add_argument("--last", required=True)
    p.add_argument("--emp-id", default="E" + str(int(time.time()))[-6:])
    args = p.parse_args()
    if not args.username or not args.password:
        p.error("username/password required (args or environment)")

    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(args.url)

        page.fill("input[name=username]", args.username)
        page.fill("input[name=password]", args.password)
        page.fill("input[name=first_name]", args.first)
        page.fill("input[name=middle_name]", args.middle)
        page.fill("input[name=last_name]", args.last)
        page.fill("input[name=employee_id]", args.emp_id)
        page.screenshot(path=os.path.join(OUT_DIR, "01_dashboard_before.png"), full_page=True)

        page.click("#submit-btn")
        page.wait_for_selector("#overall-status.badge-running")
        page.wait_for_timeout(8000)
        page.screenshot(path=os.path.join(OUT_DIR, "02_dashboard_running.png"), full_page=True)

        page.wait_for_selector("#overall-status.badge-success, #overall-status.badge-failed", timeout=180_000)
        page.evaluate("document.getElementById('log-box').open = true")
        page.screenshot(path=os.path.join(OUT_DIR, "03_dashboard_after.png"), full_page=True)
        print("Status:", page.inner_text("#overall-status"), "|", page.inner_text("#result-message"))
        browser.close()
    print("Screenshots saved to", OUT_DIR)


if __name__ == "__main__":
    main()
