from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timedelta
import re
from typing import Iterable

import pandas as pd
from playwright.sync_api import Page, sync_playwright

from config import Settings
from download_report import _click_by_text


def _debug_dump(page: Page, name: str, html: bool = False) -> None:
    """Save quick screenshot + visible text to output/ for GitHub artifact debugging.

    Full HTML dumps can be slow on Angular pages, so they are disabled by default.
    """
    out = Path("output")
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    try:
        page.screenshot(path=str(out / f"{safe}.png"), full_page=False, timeout=3000)
    except Exception as exc:
        print(f"Could not save screenshot {safe}: {exc}", flush=True)
    try:
        (out / f"{safe}.txt").write_text(page.locator("body").inner_text(timeout=2000), encoding="utf-8")
    except Exception as exc:
        print(f"Could not save text {safe}: {exc}", flush=True)
    if html:
        try:
            (out / f"{safe}.html").write_text(page.content(), encoding="utf-8")
        except Exception as exc:
            print(f"Could not save html {safe}: {exc}", flush=True)


def _click_text_robust(page: Page, texts: Iterable[str], timeout: int = 12000) -> None:
    """Click by text using several strategies, including exact matching first.

    Exact matching is important in Syrve because the menu contains both "Reports" and
    "Reports 2.0". A loose text search for "Reports" may click the wrong section.
    """
    last_error = None
    for text in texts:
        if not text:
            continue
        strategies = [
            # Exact text first
            lambda t=text: page.get_by_text(t, exact=True).first.click(timeout=timeout),
            lambda t=text: page.locator(f"xpath=//*[normalize-space(.)={repr(text)}]").first.click(timeout=timeout),
            lambda t=text: page.get_by_role("button", name=re.compile(rf"^{re.escape(t)}$", re.I)).first.click(timeout=3000),
            # Then partial text
            lambda t=text: page.get_by_text(t, exact=False).first.click(timeout=timeout),
            lambda t=text: page.locator(f"text={t}").first.click(timeout=timeout),
            lambda t=text: page.get_by_role("button", name=re.compile(re.escape(t), re.I)).first.click(timeout=3000),
        ]
        for action in strategies:
            try:
                action()
                return
            except Exception as exc:
                last_error = exc
        # Last resort: JS click exact innerText first, then contains.
        try:
            clicked = page.evaluate(
                """(needle) => {
                    const n = String(needle).trim().toLowerCase();
                    const els = Array.from(document.querySelectorAll('button,a,div,span,li,md-list-item,mat-list-item,*'));
                    let el = els.find(e => (e.innerText || '').trim().toLowerCase() === n);
                    if (!el) el = els.find(e => (e.innerText || '').toLowerCase().includes(n));
                    if (el) { el.click(); return true; }
                    return false;
                }""",
                text,
            )
            if clicked:
                return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Could not click any of these texts: {list(texts)}. Last error: {last_error}")


def _click_reports_section(page: Page, timeout: int = 12000) -> None:
    """Click the legacy Reports section specifically, not Reports 2.0."""
    last_error = None
    # Try exact visible text "Reports".
    for locator in [
        page.get_by_text("Reports", exact=True),
        page.locator("xpath=//*[normalize-space(.)='Reports']"),
        page.get_by_text("التقارير", exact=True),
        page.locator("xpath=//*[normalize-space(.)='التقارير']"),
    ]:
        try:
            locator.first.click(timeout=timeout)
            return
        except Exception as exc:
            last_error = exc

    # If not visible, scroll the left menu downward and try again.
    try:
        page.evaluate("""() => {
            const candidates = Array.from(document.querySelectorAll('aside, nav, md-sidenav, mat-sidenav, div'));
            for (const el of candidates) {
              if ((el.innerText || '').includes('Reports 2.0') || (el.innerText || '').includes('Routine Restaurant')) {
                el.scrollTop = el.scrollHeight;
              }
            }
        }""")
        page.wait_for_timeout(800)
        page.get_by_text("Reports", exact=True).first.click(timeout=timeout)
        return
    except Exception as exc:
        last_error = exc

    raise RuntimeError(f"Could not click the legacy Reports section. Last error: {last_error}")


@dataclass
class ItemMetric:
    branch_code: str
    rank: int
    item_name: str
    qty: str


@dataclass
class BranchMetrics:
    branch_code: str
    sales: str = ""
    net_revenue: str = ""
    bills: str = ""
    average_spend: str = ""
    vat: str = ""
    discount: str = ""
    raw_text_file: str = ""


def _clean_value(value: str) -> str:
    return " ".join(value.replace("\u200f", "").replace("\u200e", "").split())


def _value_after_label(text: str, labels: Iterable[str]) -> str:
    """Extract the first visible value appearing after one of the labels."""
    for label in labels:
        pattern = rf"{re.escape(label)}\s*\n?\s*([^\n]+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            line = _clean_value(match.group(1))
            line = re.split(r"\(|\+\d+%|-\d+%|↑|↓", line)[0].strip()
            return line
    return ""


def _get_page_text_after_scroll(page: Page) -> str:
    """Scroll down so lazy-loaded widgets/charts render, then return page text."""
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(700)
    previous_height = 0
    for _ in range(16):
        height = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
        if height == previous_height:
            break
        previous_height = height
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    return page.locator("body").inner_text(timeout=30000)


def _login_if_needed(page: Page, settings: Settings) -> None:
    page.goto(settings.syrve_url, wait_until="domcontentloaded")
    try:
        page.get_by_label("Username").fill(settings.syrve_username, timeout=10000)
        page.get_by_label("Password").fill(settings.syrve_password)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        print("Login form was not visible; continuing.")


def _open_key_metrics(page: Page, settings: Settings) -> None:
    print("Opening Key Metrics page/menu...", flush=True)
    """Open Routine Restaurant Operation -> Reports -> Key Metrics.

    Syrve UI changes text/language and sometimes the report list is already expanded.
    This function tries multiple visible labels and saves debug artifacts if it fails.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    _debug_dump(page, "01_before_open_key_metrics")

    # If we are already inside Store Ops / Key Metrics, do nothing.
    try:
        if page.get_by_text("Key Metrics", exact=False).first.is_visible(timeout=3000):
            # It could be the menu item, but if the page title exists this is also fine.
            pass
    except Exception:
        pass

    try:
        _click_text_robust(page, [
            settings.syrve_main_menu_text,
            "Routine Restaurant Ope",
            "Routine Restaurant Operation",
            "Store Ops",
        ], timeout=15000)
        page.wait_for_timeout(2500)
        _debug_dump(page, "02_after_main_menu_click")

        # Try the report directly first. In some Syrve accounts the dashboard URL already opens
        # the Key Metrics dashboard and there is no separate "Key Metrics" menu item.
        try:
            _click_text_robust(page, [settings.syrve_report_name, "Key Metrics"], timeout=4000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_timeout(5000)
            _debug_dump(page, "04_key_metrics_opened_from_menu")
            print("Key Metrics opened from menu.", flush=True)
            return
        except Exception as first_exc:
            print(f"Key Metrics menu item was not visible; trying report sections. Details: {first_exc}")

        try:
            _click_reports_section(page, timeout=5000)
            page.wait_for_timeout(1000)
            _debug_dump(page, "03_after_reports_expand")
            _click_text_robust(page, [settings.syrve_report_name, "Key Metrics"], timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_timeout(5000)
            _debug_dump(page, "04_key_metrics_opened_from_reports")
            print("Key Metrics opened from Reports section.", flush=True)
            return
        except Exception as second_exc:
            # Do NOT fail here. The supplied URL is already a dashboard URL (/dashboard/174327).
            # The artifact showed that the account menu does not contain Key Metrics, so we continue
            # with the currently opened dashboard and scrape whatever is visible after branch selection.
            print(f"Could not open Key Metrics from menu; continuing with current dashboard URL. Details: {second_exc}")
            try:
                page.goto(settings.syrve_url, wait_until="networkidle")
            except Exception:
                pass
            page.wait_for_timeout(5000)
            _debug_dump(page, "04_using_current_dashboard")
            print("Continuing with current dashboard.", flush=True)
            return
    except Exception:
        _debug_dump(page, "open_key_metrics_failed")
        raise


def _apply_report_date(page: Page, settings: Settings) -> str:
    """Return target report date.

    The Syrve dashboard opened by the supplied URL already shows the previous business date in
    GitHub Actions. To avoid accidentally moving two days back, we do not click date arrows here.
    If a fixed date picker is needed later, we can add it after seeing the date picker HTML.
    """
    target_date = datetime.now()
    if settings.report_date_mode.lower().strip() == "yesterday":
        target_date = target_date - timedelta(days=1)
    return target_date.strftime("%Y-%m-%d")

def _select_branch(page: Page, branch_code: str, all_branch_codes: list[str] | None = None) -> None:
    """Select exactly one store/branch in Syrve's multi-select Store drawer.

    Syrve's store picker is not a single-select dropdown; it is a checkbox drawer.
    If we only tick B60 while B111 is already ticked, the dashboard becomes "2 of 131"
    and the report is aggregated/empty for our purpose. Therefore we clear known selected
    stores first, tick the requested branch, then press CLOSE.
    """
    if not branch_code:
        return
    branch_code = branch_code.upper().strip()
    all_branch_codes = [c.upper().strip() for c in (all_branch_codes or []) if c.strip()]
    clear_codes = []
    for c in ["B111", *all_branch_codes, branch_code]:
        if c and c not in clear_codes:
            clear_codes.append(c)

    print(f"Selecting branch {branch_code}...", flush=True)

    def search_store(code: str) -> None:
        ok = page.evaluate(
            """(code) => {
                const inputs = Array.from(document.querySelectorAll('input'));
                const visible = inputs.filter(i => {
                  const r = i.getBoundingClientRect();
                  const style = window.getComputedStyle(i);
                  return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && !i.disabled && !i.readOnly;
                });
                const input = visible[0];
                if (!input) return {ok:false, count: inputs.length, visible: visible.length};
                input.focus();
                input.value = '';
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.value = code;
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.dispatchEvent(new Event('change', {bubbles:true}));
                return {ok:true, count: inputs.length, visible: visible.length};
            }""",
            code,
        )
        print(f"{branch_code}: search {code} result: {ok}", flush=True)
        if not ok or not ok.get("ok"):
            raise RuntimeError(f"Could not search store {code}. Result={ok}")
        page.wait_for_timeout(900)

    def set_checkbox(code: str, desired: bool) -> dict:
        # Scroll row into view, inspect checkbox state, click only if a change is needed.
        result = page.evaluate(
            """({code, desired}) => {
                const c = String(code).toLowerCase();
                const all = Array.from(document.querySelectorAll('div,li,section,mat-checkbox,label,span'));
                let rows = all.map(e => {
                  const text = (e.innerText || '').trim();
                  const r = e.getBoundingClientRect();
                  const style = window.getComputedStyle(e);
                  const visible = r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                  return {e, text, r, area:r.width*r.height, visible};
                }).filter(o =>
                  o.visible && o.text.toLowerCase().includes(c) &&
                  o.area > 100 && o.area < 120000 && o.r.height >= 18 && o.r.height <= 90
                );
                rows.sort((a,b) => {
                  const as = a.text.toLowerCase().startsWith(c) ? 0 : 1;
                  const bs = b.text.toLowerCase().startsWith(c) ? 0 : 1;
                  if (as !== bs) return as - bs;
                  return b.r.width - a.r.width; // prefer full row over text span
                });
                const row = rows[0];
                if (!row) return {found:false, changed:false, reason:'row_not_found'};
                row.e.scrollIntoView({block:'center', inline:'center'});

                // Recompute after scroll.
                const rr = row.e.getBoundingClientRect();
                let container = row.e;
                for (let i=0; i<4 && container && !container.querySelector('input[type="checkbox"], [role="checkbox"], mat-checkbox'); i++) {
                  container = container.parentElement;
                }
                if (!container) container = row.e;
                let checkbox = container.querySelector('input[type="checkbox"], [role="checkbox"], mat-checkbox');
                if (!checkbox) {
                  // Try nearest checkbox horizontally in the same row.
                  const boxes = Array.from(document.querySelectorAll('input[type="checkbox"], [role="checkbox"], mat-checkbox'))
                    .map(e => ({e, r:e.getBoundingClientRect()}))
                    .filter(o => Math.abs((o.r.y + o.r.height/2) - (rr.y + rr.height/2)) < 30 && o.r.width > 0 && o.r.height > 0)
                    .sort((a,b) => a.r.x - b.r.x);
                  if (boxes[0]) checkbox = boxes[0].e;
                }
                if (!checkbox) return {found:true, changed:false, reason:'checkbox_not_found', text:row.text.slice(0,100)};

                let checked = false;
                if ('checked' in checkbox) checked = !!checkbox.checked;
                else if (checkbox.getAttribute('aria-checked') != null) checked = checkbox.getAttribute('aria-checked') === 'true';
                else checked = /checked|selected/.test(checkbox.className || '') || !!checkbox.querySelector('.mat-mdc-checkbox-checked,.mdc-checkbox--selected,input:checked');

                if (checked !== desired) {
                  checkbox.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, view:window}));
                  checkbox.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, view:window}));
                  checkbox.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, view:window}));
                  checkbox.dispatchEvent(new MouseEvent('click', {bubbles:true, view:window}));
                  return {found:true, changed:true, wasChecked:checked, desired, text:row.text.slice(0,100), x:rr.x, y:rr.y};
                }
                return {found:true, changed:false, wasChecked:checked, desired, text:row.text.slice(0,100), x:rr.x, y:rr.y};
            }""",
            {"code": code, "desired": desired},
        )
        print(f"{branch_code}: set {code} -> {desired}: {result}", flush=True)
        page.wait_for_timeout(700)
        return result

    try:
        print(f"{branch_code}: saving before-click debug...", flush=True)
        _debug_dump(page, f"branch_{branch_code}_before_click")

        print(f"{branch_code}: opening location selector...", flush=True)
        opened = False
        for selector in ["resto-store-selector .store-picker", ".store-picker", "resto-store-selector", ".store-name"]:
            try:
                page.locator(selector).first.click(timeout=4000, force=True)
                page.wait_for_timeout(1800)
                body_text = page.locator("body").inner_text(timeout=3000)
                if "Store" in body_text and ("CLOSE" in body_text or "B60" in body_text or "B01" in body_text):
                    opened = True
                    print(f"{branch_code}: opened selector using {selector}", flush=True)
                    break
            except Exception as exc:
                print(f"{branch_code}: selector {selector} did not open location page: {exc}", flush=True)
        if not opened:
            raise RuntimeError("Location selector did not open; cannot choose branch")

        _debug_dump(page, f"branch_{branch_code}_select_location_opened")

        # Clear known stores first, then tick the requested one.
        for code in clear_codes:
            search_store(code)
            set_checkbox(code, False)
        search_store(branch_code)
        selected = set_checkbox(branch_code, True)
        if not selected.get("found"):
            raise RuntimeError(f"Could not find requested branch {branch_code}")

        _debug_dump(page, f"branch_{branch_code}_after_checkbox")

        print(f"{branch_code}: closing store selector...", flush=True)
        closed = False
        for locator in [page.get_by_text("CLOSE", exact=True), page.get_by_text("Close", exact=True), page.locator("button:has-text('CLOSE')")]:
            try:
                locator.first.click(timeout=4000)
                closed = True
                break
            except Exception:
                continue
        if not closed:
            # Bottom-left of drawer close button in screenshot.
            page.mouse.click(1535, 1040)
        page.wait_for_timeout(6000)

        try:
            body_after = page.locator("body").inner_text(timeout=3000)
        except Exception:
            body_after = ""
        print(f"{branch_code}: header/body starts with: {body_after[:180]!r}", flush=True)
        print(f"{branch_code}: selected; saving quick debug...", flush=True)
        _debug_dump(page, f"branch_{branch_code}_selected")
        print(f"{branch_code}: branch step finished.", flush=True)
    except Exception:
        print(f"{branch_code}: branch selection failed; saving failure debug...", flush=True)
        _debug_dump(page, f"branch_{branch_code}_failed")
        raise

def _extract_metrics(text: str, branch_code: str, raw_text_path: Path) -> BranchMetrics:
    return BranchMetrics(
        branch_code=branch_code,
        sales=_value_after_label(text, ["المبيعات", "Sales"]),
        net_revenue=_value_after_label(text, ["صافي الإيرادات", "Net Revenue", "Net sales"]),
        bills=_value_after_label(text, ["BILLS", "Bills", "الفواتير"]),
        average_spend=_value_after_label(text, ["متوسط الإنفاق", "Average spend", "Avg spend"]),
        vat=_value_after_label(text, ["ضريبة القيمة المضافة", "VAT", "Tax"]),
        discount=_value_after_label(text, ["خصم", "Discount"]),
        raw_text_file=str(raw_text_path),
    )


def _extract_top_items_by_qty_from_svg(page: Page, branch_code: str) -> list[ItemMetric]:
    """Extract Top 10 items by quantity from the rendered chart text.

    The chart in your screenshot is rendered as SVG text. We collect visible SVG text values near the
    'By Number Sold' section and pair item names with numeric quantities. This is heuristic but works
    well for this Syrve chart shape.
    """
    # Ensure the Top 10 chart is rendered.
    try:
        page.get_by_text("Top 10 items").scroll_into_view_if_needed(timeout=8000)
        page.wait_for_timeout(1000)
    except Exception:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)

    svg_texts = page.locator("svg text").all_inner_texts()
    cleaned = [_clean_value(t) for t in svg_texts if _clean_value(t)]

    # Drop axis/legend/title labels. Keep Arabic/English item labels and numeric bar labels.
    ignored = {
        "Top 10 items", "By Number Sold", "By Revenue (with VAT)",
        "Sales by Item, qty", "Sales by item, value",
    }
    candidates = [t for t in cleaned if t not in ignored]

    # The qty chart usually appears before the revenue chart. We only need first 10 item-name + qty pairs.
    # Remove axis ticks like 0, 100, 200, 300, 400.
    no_axis = [t for t in candidates if not re.fullmatch(r"[\d,]+(?:\.\d+)?", t) or int(t.replace(",", "")) not in {0, 100, 200, 300, 400, 500}]

    names: list[str] = []
    qtys: list[str] = []
    for t in no_axis:
        if re.fullmatch(r"[\d,]+(?:\.\d+)?", t):
            qtys.append(t)
        else:
            # Avoid revenue chart labels after we already have enough names and quantities.
            if len(names) < 10:
                names.append(t)
        if len(names) >= 10 and len(qtys) >= 10:
            break

    items: list[ItemMetric] = []
    for idx, (name, qty) in enumerate(zip(names[:10], qtys[:10]), start=1):
        items.append(ItemMetric(branch_code=branch_code, rank=idx, item_name=name, qty=qty))
    return items


def _format_plain_text(report_date: str, metrics: list[BranchMetrics], items: list[ItemMetric]) -> str:
    lines = [f"تقرير Key Metrics - اليوم السابق", f"التاريخ: {report_date}", ""]
    by_branch_items: dict[str, list[ItemMetric]] = {}
    for item in items:
        by_branch_items.setdefault(item.branch_code, []).append(item)

    for m in metrics:
        lines.extend([
            f"الفرع: {m.branch_code}",
            f"إجمالي المبيعات: {m.sales or '-'}",
            f"صافي الإيرادات: {m.net_revenue or '-'}",
            f"عدد الفواتير/BILLS: {m.bills or '-'}",
            f"متوسط الإنفاق: {m.average_spend or '-'}",
            f"VAT: {m.vat or '-'}",
            f"الخصم: {m.discount or '-'}",
            "أكثر 10 أصناف مبيعًا حسب الكمية:",
        ])
        branch_items = by_branch_items.get(m.branch_code, [])
        if branch_items:
            for item in branch_items:
                lines.append(f"{item.rank}. {item.item_name} - {item.qty}")
            lines.append(f"أقل صنف ضمن قائمة Top 10: {branch_items[-1].item_name} - {branch_items[-1].qty}")
        else:
            lines.append("لم يتم استخراج الأصناف من الرسم.")
        lines.append("-" * 35)
    return "\n".join(lines)


def scrape_key_metrics(settings: Settings, output_dir: Path) -> tuple[Path, str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    branch_codes = [b.strip().upper() for b in settings.branch_codes.split(",") if b.strip()]
    results: list[BranchMetrics] = []
    all_items: list[ItemMetric] = []
    report_date = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ar-SA", viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.set_default_timeout(12000)

        _login_if_needed(page, settings)
        report_date = _apply_report_date(page, settings)

        for branch_code in branch_codes:
            # Select the branch first. Some branches expose the legacy Reports -> Key Metrics menu,
            # while the default branch may expose only Reports 2.0.
            _select_branch(page, branch_code, branch_codes)
            _open_key_metrics(page, settings)
            print(f"{branch_code}: scraping visible dashboard text...", flush=True)
            text = _get_page_text_after_scroll(page)
            raw_text_path = output_dir / f"raw_text_{branch_code}.txt"
            raw_text_path.write_text(text, encoding="utf-8")
            results.append(_extract_metrics(text, branch_code, raw_text_path))
            print(f"{branch_code}: extracting top items...", flush=True)
            all_items.extend(_extract_top_items_by_qty_from_svg(page, branch_code))
            print(f"{branch_code}: done.", flush=True)

        context.close()
        browser.close()

    df = pd.DataFrame([asdict(r) for r in results])
    items_df = pd.DataFrame([asdict(i) for i in all_items])
    report_path = output_dir / "key_metrics_summary.xlsx"
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Key Metrics")
        items_df.to_excel(writer, index=False, sheet_name="Top Items Qty")

    plain_text = _format_plain_text(report_date, results, all_items)
    rows = "".join(
        f"<tr><td>{r.branch_code}</td><td>{r.sales}</td><td>{r.net_revenue}</td><td>{r.bills}</td><td>{r.average_spend}</td><td>{r.vat}</td><td>{r.discount}</td></tr>"
        for r in results
    )
    items_html = "".join(
        f"<tr><td>{i.branch_code}</td><td>{i.rank}</td><td>{i.item_name}</td><td>{i.qty}</td></tr>"
        for i in all_items
    )
    html = f"""
    <div dir="rtl" style="font-family: Arial, sans-serif; line-height: 1.7">
      <h2>تقرير Key Metrics - اليوم السابق</h2>
      <p><b>التاريخ:</b> {report_date}</p>
      <h3>ملخص الفروع</h3>
      <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse">
        <tr><th>الفرع</th><th>إجمالي المبيعات</th><th>صافي الإيرادات</th><th>Bills</th><th>متوسط الإنفاق</th><th>VAT</th><th>الخصم</th></tr>
        {rows}
      </table>
      <h3>أكثر 10 أصناف مبيعًا حسب الكمية</h3>
      <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse">
        <tr><th>الفرع</th><th>الترتيب</th><th>الصنف</th><th>الكمية</th></tr>
        {items_html}
      </table>
      <p>ملاحظة: الأقل هنا هو آخر صنف داخل قائمة Top 10 المعروضة في Syrve، وليس أقل صنف من جميع الأصناف إلا إذا وفر النظام تقريرًا شاملًا بكل الأصناف.</p>
    </div>
    """
    return report_path, html, plain_text
