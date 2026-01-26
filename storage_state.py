from playwright.sync_api import sync_playwright

START_URL = "https://www.historycolorado.org/events-experiences#event=holiday-tea-4;instance=20251129000000?popup=1&lang=en-US"
STORAGE_PATH = "storage_state.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        locale="en-US",
        timezone_id="America/Denver",
        viewport={"width": 1280, "height": 800},
    )
    page = context.new_page()
    page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)

    print("If you see a verification page, complete it in the browser window.")
    input("Press Enter here once the site loads normally...")

    context.storage_state(path=STORAGE_PATH)
    browser.close()