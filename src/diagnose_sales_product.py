from pathlib import Path
from playwright.sync_api import sync_playwright
from config import Settings, validate
from scrape_key_metrics import _login_if_needed, _select_branch, _click_text_robust, _debug_dump


def main():
    settings = Settings()
    validate(settings)
    output = Path('output')
    output.mkdir(exist_ok=True)
    branch = 'B60'
    branches = [b.strip().upper() for b in settings.branch_codes.split(',') if b.strip()]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale='ar-SA', viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(12000)
        _login_if_needed(page, settings)
        _select_branch(page, branch, branches)
        _debug_dump(page, 'diag_01_after_branch')
        # Open Routine Restaurant Operations submenu if needed
        try:
            _click_text_robust(page, [settings.syrve_main_menu_text, 'Routine Restaurant Ope', 'Routine Restaurant Operations'], timeout=8000)
            page.wait_for_timeout(1200)
        except Exception as exc:
            print(f'main menu click skipped/failed: {exc}', flush=True)
        _debug_dump(page, 'diag_02_after_main_menu')
        # Expand Reports 2.0
        _click_text_robust(page, ['Reports 2.0'], timeout=8000)
        page.wait_for_timeout(1500)
        _debug_dump(page, 'diag_03_reports2_open')
        # Open Sales by Product
        _click_text_robust(page, ['Sales by Product'], timeout=10000)
        page.wait_for_timeout(8000)
        _debug_dump(page, 'diag_04_sales_by_product_open', html=True)
        text = page.locator('body').inner_text(timeout=5000)
        (output / 'sales_by_product_text.txt').write_text(text, encoding='utf-8')
        browser.close()

if __name__ == '__main__':
    main()
