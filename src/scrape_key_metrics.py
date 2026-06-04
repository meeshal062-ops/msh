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


def _debug_dump(page: Page, name: str) -> None:
    """Save screenshot + visible text + html to output/ for GitHub artifact debugging."""
    out = Path("output")
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    try:
        page.screenshot(path=str(out / f"{safe}.png"), full_page=True)
    except Exception as exc:
        print(f"Could not save screenshot {safe}: {exc}")
    try:
        (out / f"{safe}.txt").write_text(page.locator("body").inner_text(timeout=5000), encoding="utf-8")
    except Exception as exc:
        print(f"Could not save text {safe}: {exc}")
    try:
        (out / f"{safe}.html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        print(f"Could not save html {safe}: {exc}")


def _click_text_robust(page: Page, texts: Iterable[str], timeout: int = 12000) -> None:
    """Click by text using several strategies, including JS innerText search."""
    last_error = None
    for text in texts:
        if not text:
            continue
        strategies = [
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
        # Last resort: JS click first visible-ish element whose innerText contains the target text.
        try:
            clicked = page.evaluate(
                """(needle) => {
                    const els = Array.from(document.querySelectorAll('button,a,div,span,li,md-list-item,mat-list-item,*'));
                    const el = els.find(e => (e.innerText || '').toLowerCase().includes(String(needle).toLowerCase()));
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
        page.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        print("Login form was not visible; continuing.")


def _open_key_metrics(page: Page, settings: Settings) -> None:
    """Open Routine Restaurant Operation -> Reports -> Key Metrics.

    Syrve UI changes text/language and sometimes the report list is already expanded.
    This function tries multiple visible labels and saves debug artifacts if it fails.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=60000)
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

        # Try the report directly first. If not visible, expand possible report sections and try again.
        try:
            _click_text_robust(page, [settings.syrve_report_name, "Key Metrics"], timeout=7000)
        except Exception:
            _click_text_robust(page, [
                settings.syrve_reports_section_text,
                "التقارير",
                "Reports",
                "Reports 2.0",
            ], timeout=12000)
            page.wait_for_timeout(1500)
            _debug_dump(page, "03_after_reports_expand")
            _click_text_robust(page, [settings.syrve_report_name, "Key Metrics"], timeout=15000)

        page.wait_for_load_state("networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        _debug_dump(page, "04_key_metrics_opened")
    except Exception:
        _debug_dump(page, "open_key_metrics_failed")
        raise


def _apply_report_date(page: Page, settings: Settings) -> str:
    """Set report date. Current implementation uses the previous-day arrow if REPORT_DATE_MODE=yesterday.

    From the screenshots, the top bar has left/right arrows around the date. If you start from today's
    date, clicking the previous arrow once selects yesterday. This can be adjusted later if the site
    opens on another date by default.
    """
    mode = settings.report_date_mode.lower().strip()
    target_date = datetime.now()
    if mode == "yesterday":
        target_date = target_date - timedelta(days=1)
        try:
            # The previous-date arrow is usually the left arrow near the date in the top bar.
            page.locator("i, button, a").filter(has_text=re.compile("chevron_left|‹|<|previous", re.I)).first.click(timeout=4000)
        except Exception:
            try:
                page.get_by_text("‹").first.click(timeout=4000)
            except Exception as exc:
                print(f"Could not click previous-day arrow automatically. Continuing with visible date. Error: {exc}")
        page.wait_for_load_state("networkidle", timeout=60000)
        page.wait_for_timeout(2500)
    return target_date.strftime("%Y-%m-%d")


def _select_branch(page: Page, branch_code: str) -> None:
    """Select branch by code from the Select location page.

    The screenshot shows that clicking the current branch opens a 'Select location' screen with a search box.
    We search by branch code, then click the matching row/arrow. Syrve updates data automatically.
    """
    if not branch_code:
        return
    branch_code = branch_code.upper().strip()
    print(f"Selecting branch {branch_code}...")

    # Click current branch selector in top bar, e.g. B26 Buraidah, Rehab.
    page.get_by_text(re.compile(r"B\d+", re.I)).first.click(timeout=15000)
    page.wait_for_timeout(1500)

    # Fill search field on Select location page.
    search = page.get_by_placeholder(re.compile("بحث|Search", re.I)).first
    search.fill(branch_code, timeout=15000)
    page.wait_for_timeout(1200)

    # Click matching location row. If only an arrow is clickable, click near/inside the row.
    try:
        page.get_by_text(re.compile(rf"\b{re.escape(branch_code)}\b", re.I)).first.click(timeout=10000)
    except Exception:
        _click_by_text(page, branch_code, timeout=10000)

    page.wait_for_load_state("networkidle", timeout=60000)
    page.wait_for_timeout(3500)


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
        page.set_default_timeout(30000)

        _login_if_needed(page, settings)
        _open_key_metrics(page, settings)
        report_date = _apply_report_date(page, settings)

        for branch_code in branch_codes:
            _select_branch(page, branch_code)
            # Branch selection may reset/open report date. Apply yesterday again defensively only if needed.
            # If the selected date is retained, the extra click may be wrong, so we do not repeat it here.
            text = _get_page_text_after_scroll(page)
            raw_text_path = output_dir / f"raw_text_{branch_code}.txt"
            raw_text_path.write_text(text, encoding="utf-8")
            results.append(_extract_metrics(text, branch_code, raw_text_path))
            all_items.extend(_extract_top_items_by_qty_from_svg(page, branch_code))

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
