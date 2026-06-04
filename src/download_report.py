from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from config import Settings


def _click_by_text(page, text: str, timeout: int = 15000) -> None:
    candidates = [
        page.get_by_role("button", name=text),
        page.get_by_text(text, exact=False),
        page.locator(f"text={text}"),
    ]
    last_error = None
    for locator in candidates:
        try:
            locator.first.click(timeout=timeout)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"Could not click element with text '{text}'. Last error: {last_error}")


def download_report(settings: Settings, output_dir: Path) -> Path:
    """Login to Syrve and download an Excel report.

    IMPORTANT: The report page/button depends on your Syrve account UI.
    Best setup: set SYRVE_REPORT_URL to the exact report page URL after login.
    If the website needs menu navigation, add those clicks in the TODO section below.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(30000)

        print("Opening login page...")
        page.goto(settings.syrve_url, wait_until="domcontentloaded")

        print("Checking login...")
        # If the dashboard redirects to login, fill credentials. If already logged in, continue.
        try:
            page.get_by_label("Username").fill(settings.syrve_username, timeout=10000)
            page.get_by_label("Password").fill(settings.syrve_password)
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_load_state("networkidle", timeout=60000)
        except Exception:
            print("Login form was not visible; assuming an active session or different post-login state.")

        if settings.syrve_report_url:
            print("Opening direct report URL...")
            page.goto(settings.syrve_report_url, wait_until="networkidle")
        else:
            print("Navigating by menu: Routine Restaurant Operation -> التقارير -> Key Metrics")
            _click_by_text(page, settings.syrve_main_menu_text)
            page.wait_for_timeout(1000)

            # In the screenshot the reports section is Arabic: التقارير.
            # If it is already expanded, clicking may collapse it; therefore we try to click Key Metrics first.
            try:
                _click_by_text(page, settings.syrve_report_name, timeout=5000)
            except Exception:
                _click_by_text(page, settings.syrve_reports_section_text)
                page.wait_for_timeout(1000)
                _click_by_text(page, settings.syrve_report_name)

            page.wait_for_load_state("networkidle", timeout=60000)

        # TODO optional: select date range if report page requires it.
        # Usually for daily report you need yesterday/today; this depends on page controls.

        print("Downloading Excel report...")
        button_text = settings.report_download_button_text or "Excel"
        try:
            with page.expect_download(timeout=60000) as download_info:
                _click_by_text(page, button_text)
            download = download_info.value
        except PlaywrightTimeoutError as exc:
            screenshot = output_dir / "download_failed.png"
            page.screenshot(path=str(screenshot), full_page=True)
            raise RuntimeError(
                f"Download did not start after clicking '{button_text}'. "
                f"Screenshot saved to {screenshot}. Check REPORT_DOWNLOAD_BUTTON_TEXT or page steps."
            ) from exc

        suggested = download.suggested_filename or "syrve_report.xlsx"
        target = output_dir / suggested
        download.save_as(str(target))
        print(f"Downloaded: {target}")

        context.close()
        browser.close()
        return target
