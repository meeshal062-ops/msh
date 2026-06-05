from pathlib import Path
from playwright.sync_api import sync_playwright
from config import Settings, validate
from scrape_key_metrics import _debug_dump

RGM_URL = 'https://half-million-co.syrve.app/storeops/index.html#/rgm/dashboard/185485'


def main():
    settings = Settings()
    validate(settings)
    out = Path('output')
    out.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale='ar-SA', viewport={'width': 1920, 'height': 1080})
        page = context.new_page()
        page.set_default_timeout(15000)
        print('Opening RGM dashboard direct URL...', flush=True)
        page.goto(RGM_URL, wait_until='domcontentloaded')
        try:
            page.get_by_label('Username').fill(settings.syrve_username, timeout=10000)
            page.get_by_label('Password').fill(settings.syrve_password)
            page.get_by_role('button', name='Sign in').click()
            page.wait_for_timeout(8000)
        except Exception as exc:
            print(f'Login form not used/visible: {exc}', flush=True)
        print('Waiting for widgets...', flush=True)
        try:
            page.wait_for_function(
                """() => {
                    const t = document.body.innerText || '';
                    return t.includes('Key Metrics') || t.includes('المبيعات') || t.includes('Top 10 items') || t.includes('BILLS') || t.includes('Sales vs Forecast');
                }""",
                timeout=60000,
            )
        except Exception as exc:
            print(f'Widgets not detected: {exc}', flush=True)
        _debug_dump(page, 'rgm_dashboard_direct', html=True)
        txt = page.locator('body').inner_text(timeout=5000)
        (out / 'rgm_dashboard_direct_text.txt').write_text(txt, encoding='utf-8')
        print(txt[:2000], flush=True)
        browser.close()

if __name__ == '__main__':
    main()
