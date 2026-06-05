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

def _select_branch(page: Page, branch_code: str) -> None:
    """Select branch by code from the Select location page.

    This version uses coordinate/JS fallbacks because Syrve keeps hidden search inputs in the DOM,
    and locator.fill can wait forever on a hidden input.
    """
    if not branch_code:
        return
    branch_code = branch_code.upper().strip()
    print(f"Selecting branch {branch_code}...", flush=True)
    try:
        print(f"{branch_code}: saving before-click debug...", flush=True)
        _debug_dump(page, f"branch_{branch_code}_before_click")

        print(f"{branch_code}: opening location selector...", flush=True)
        opened = False
        # Best selector from Syrve HTML: <resto-store-selector><div class="store-picker">...
        for selector in [
            "resto-store-selector .store-picker",
            ".store-picker",
            "resto-store-selector",
            ".store-name",
        ]:
            try:
                page.locator(selector).first.click(timeout=4000, force=True)
                page.wait_for_timeout(2500)
                body_text = page.locator("body").inner_text(timeout=3000)
                if "Select location" in body_text or "Al Khobar WH" in body_text or "B01 RIY" in body_text or "بحث" in body_text:
                    opened = True
                    print(f"{branch_code}: opened selector using {selector}", flush=True)
                    break
            except Exception as exc:
                print(f"{branch_code}: selector {selector} did not open location page: {exc}", flush=True)

        if not opened:
            print(f"{branch_code}: CSS click did not open selector; trying branch-label center click...", flush=True)
            rect = page.evaluate(
                """() => {
                    const els = Array.from(document.querySelectorAll('button,a,div,span,*'));
                    const candidates = els
                      .filter(e => /B\d+/.test(e.innerText || ''))
                      .map(e => {
                        const r = e.getBoundingClientRect();
                        const style = window.getComputedStyle(e);
                        return {x:r.x, y:r.y, w:r.width, h:r.height, text:(e.innerText||'').slice(0,80), visible:r.width>0 && r.height>0 && style.display!=='none' && style.visibility!=='hidden'};
                      })
                      .filter(r => r.visible && r.y < 90 && r.x > 180 && r.x < 700)
                      .sort((a,b) => (a.w*a.h) - (b.w*b.h));
                    return candidates[0] || null;
                }"""
            )
            print(f"{branch_code}: branch label rect: {rect}", flush=True)
            if rect:
                page.mouse.click(rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2)
            else:
                page.mouse.click(300, 34)
            page.wait_for_timeout(2500)

        print(f"{branch_code}: location selector should be open; saving debug...", flush=True)
        _debug_dump(page, f"branch_{branch_code}_select_location_opened")
        try:
            opened_text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            opened_text = ""
        if "Select location" not in opened_text and "Al Khobar WH" not in opened_text and "B01 RIY" not in opened_text and "بحث" not in opened_text:
            raise RuntimeError("Location selector did not open; cannot choose branch")

        print(f"{branch_code}: filling search...", flush=True)
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
            branch_code,
        )
        print(f"{branch_code}: search JS result: {ok}", flush=True)
        if not ok or not ok.get("ok"):
            raise RuntimeError(f"Could not find visible search input. Result={ok}")
        page.wait_for_timeout(2000)
        _debug_dump(page, f"branch_{branch_code}_after_search")

        print(f"{branch_code}: clicking result row...", flush=True)
        clicked = page.evaluate(
            """(code) => {
                const c = String(code).toLowerCase();
                const els = Array.from(document.querySelectorAll('button,a,div,span,li,section'));
                let candidates = els.map(e => {
                  const text = (e.innerText || '').trim();
                  const r = e.getBoundingClientRect();
                  const style = window.getComputedStyle(e);
                  const visible = r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                  return {e, text, x:r.x, y:r.y, w:r.width, h:r.height, area:r.width*r.height, visible};
                }).filter(o =>
                  o.visible &&
                  o.text.toLowerCase().includes(c) &&
                  o.area > 20 && o.area < 90000 &&
                  o.h >= 10 && o.h <= 90 &&
                  o.w >= 20
                );
                candidates.sort((a,b) => {
                  const ae = a.text.toLowerCase().startsWith(c) ? 0 : 1;
                  const be = b.text.toLowerCase().startsWith(c) ? 0 : 1;
                  if (ae !== be) return ae - be;
                  return a.area - b.area;
                });
                const target = candidates[0];
                if (!target) return {ok:false, count:0, sample:[]};

                // Scroll the exact target into the visible area first. Returning an off-screen rect
                // such as y=4003 means Playwright/Angular may not really select it.
                target.e.scrollIntoView({block:'center', inline:'center'});
                const rr = target.e.getBoundingClientRect();
                return {
                  ok:true,
                  count:candidates.length,
                  picked:{text:target.text.slice(0,120), x:rr.x, y:rr.y, w:rr.width, h:rr.height, area:rr.width*rr.height},
                  sample:candidates.slice(0,5).map(o => ({text:o.text.slice(0,80), x:o.x, y:o.y, w:o.w, h:o.h, area:o.area}))
                };
            }""",
            branch_code,
        )
        print(f"{branch_code}: click result: {clicked}", flush=True)
        if not clicked or not clicked.get("ok"):
            raise RuntimeError(f"Could not find branch result. Result={clicked}")

        # Real mouse click after scrollIntoView. Click near the row text, then near the row arrow/right side.
        picked = clicked.get("picked") or {}
        x = float(picked.get("x", 0)); y = float(picked.get("y", 0)); w = float(picked.get("w", 0)); h = float(picked.get("h", 0))
        if w > 0 and h > 0:
            page.mouse.click(x + min(w / 2, 120), y + h / 2)
            page.wait_for_timeout(1500)
            # If still on the selector page, click the right side of the same row (where Syrve shows the arrow).
            try:
                temp_text = page.locator("body").inner_text(timeout=1500)
            except Exception:
                temp_text = ""
            if "Select location" in temp_text or "Search" in temp_text or "بحث" in temp_text:
                page.mouse.click(min(x + max(w + 35, 250), 1850), y + h / 2)

        # Do not wait for strict networkidle; Angular apps often keep requests open.
        page.wait_for_timeout(5000)
        # Verify whether the header changed to the requested branch. If not, try pressing Enter as fallback.
        try:
            after_text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            after_text = ""
        if branch_code not in after_text[:500]:
            print(f"{branch_code}: branch not clearly selected yet; pressing Enter fallback...", flush=True)
            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
            except Exception:
                pass
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
            _select_branch(page, branch_code)
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
