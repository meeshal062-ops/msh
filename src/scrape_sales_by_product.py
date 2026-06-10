from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import pandas as pd
from playwright.sync_api import sync_playwright, Page

from config import Settings
from scrape_key_metrics import _login_if_needed, _select_branch, _click_text_robust, _debug_dump


@dataclass
class BranchSales:
    branch_code: str
    branch_name: str = ""
    gross_sales_after_discount: str = ""
    net_sales: str = ""
    vat_amount: str = ""
    discount_amount: str = ""
    avg_order_amount: str = ""
    avg_revenue_per_guest: str = ""
    cost: str = ""
    refund_amount: str = ""
    comparison_net_sales: str = ""
    net_sales_change_pct: str = ""
    sales_trend: str = ""
    raw_text_file: str = ""


@dataclass
class ProductRow:
    branch_code: str
    rank: int
    item_name: str
    gross_sales_after_discount: str = ""
    net_sales: str = ""


def _target_date(settings: Settings) -> str:
    d = datetime.now(ZoneInfo("Asia/Riyadh"))
    if settings.report_date_mode.lower().strip() == "yesterday":
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")



def _money_to_float(value: str) -> float:
    try:
        return float((value or "0").replace(",", ""))
    except Exception:
        return 0.0


def _analyze_change(current: str, previous: str) -> tuple[str, str]:
    current_value = _money_to_float(current)
    previous_value = _money_to_float(previous)
    if previous_value <= 0:
        return "", "No comparison available"
    pct = ((current_value - previous_value) / previous_value) * 100
    pct_text = f"{pct:+.2f}%"
    if pct < 0:
        trend = f"Net sales decreased by {abs(pct):.2f}% vs previous day"
    elif pct > 0:
        trend = f"Net sales increased by {pct:.2f}% vs previous day"
    else:
        trend = "Net sales were unchanged vs previous day"
    return pct_text, trend

def _read_syrve_date_value(page: Page) -> str:
    """Read the visible Syrve date input value, e.g. 06/06/26."""
    try:
        return page.evaluate(
            """() => {
                const inputs = Array.from(document.querySelectorAll('resto-range-selector-input input, input'));
                const visible = inputs.filter(i => {
                  const r = i.getBoundingClientRect();
                  const style = window.getComputedStyle(i);
                  return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                });
                for (const i of visible) {
                  const v = i.value || i.getAttribute('value') || '';
                  if (/\d{2}\/\d{2}\/\d{2}/.test(v)) return v;
                }
                return '';
            }"""
        ) or ""
    except Exception:
        return ""


def _click_previous_day_arrow(page: Page) -> bool:
    """Click the left chevron in the Syrve date control."""
    try:
        clicked = page.evaluate(
            """() => {
                const root = document.querySelector('resto-range-selector-input') || document;
                const icons = Array.from(root.querySelectorAll('mat-icon'));
                const icon = icons.find(i =>
                    (i.textContent || '').trim() === 'chevron_left' &&
                    (i.className || '').toString().includes('prev-period-icon')
                ) || icons.find(i => (i.textContent || '').trim() === 'chevron_left');
                if (!icon) return false;
                const button = icon.closest('button') || icon.parentElement;
                if (!button) return false;
                button.click();
                return true;
            }"""
        )
        if clicked:
            return True
    except Exception:
        pass

    for selector in [
        "resto-range-selector-input .control.arrow.prev button",
        ".prev-period-icon",
        "mat-icon.prev-period-icon",
    ]:
        try:
            page.locator(selector).first.click(timeout=3000, force=True)
            return True
        except Exception:
            continue

    # Coordinate fallback from the user's screenshot / 1920px GitHub viewport.
    for x, y in [(665, 32), (650, 32), (675, 32)]:
        try:
            page.mouse.click(x, y)
            return True
        except Exception:
            pass
    return False


def _set_previous_business_date(page: Page, settings: Settings) -> None:
    """Force Syrve UI date to previous business day.

    This is intentionally done after opening the report page because Syrve may reset the
    date control when changing store/report. We first try to set the exact date in the
    input, then fall back to the left chevron.
    """
    if settings.report_date_mode.lower().strip() != "yesterday":
        return

    now = datetime.now(ZoneInfo("Asia/Riyadh"))
    today_ui = now.strftime("%d/%m/%y")
    target_ui = (now - timedelta(days=1)).strftime("%d/%m/%y")
    print(f"Forcing Syrve report date. target={target_ui}, today={today_ui}", flush=True)

    page.wait_for_timeout(1000)
    current = _read_syrve_date_value(page)
    print(f"Current Syrve date value before change: {current or 'unknown'}", flush=True)

    if target_ui in current:
        print("Syrve date already equals previous business day.", flush=True)
        return

    # Try exact input assignment first. This avoids going back two days if the control is already not today.
    try:
        result = page.evaluate(
            """(target) => {
                const inputs = Array.from(document.querySelectorAll('resto-range-selector-input input, input'));
                const visible = inputs.filter(i => {
                  const r = i.getBoundingClientRect();
                  const style = window.getComputedStyle(i);
                  return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && !i.disabled && !i.readOnly;
                });
                const input = visible.find(i => /\d{2}\/\d{2}\/\d{2}/.test(i.value || '')) || visible[0];
                if (!input) return {ok:false, reason:'no_visible_input'};
                input.focus();
                const proto = Object.getPrototypeOf(input);
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(input, target); else input.value = target;
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.dispatchEvent(new Event('change', {bubbles:true}));
                input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true}));
                input.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', bubbles:true}));
                input.blur();
                return {ok:true, value:input.value};
            }""",
            target_ui,
        )
        print(f"Direct date input set result: {result}", flush=True)
        page.wait_for_timeout(1500)
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(2000)
    except Exception as exc:
        print(f"Direct date input set failed: {exc}", flush=True)

    after_direct = _read_syrve_date_value(page)
    print(f"Syrve date after direct set: {after_direct or 'unknown'}", flush=True)
    if target_ui in after_direct:
        return

    # Fallback: if the control is on today (or unknown), click previous day once.
    clicked = _click_previous_day_arrow(page)
    print(f"Previous-day arrow clicked={clicked}", flush=True)
    page.wait_for_timeout(3000)
    after_click = _read_syrve_date_value(page)
    print(f"Syrve date after previous click: {after_click or 'unknown'}", flush=True)



def _set_specific_report_date(page: Page, target_dt: datetime, label: str = "target") -> None:
    """Force Syrve date input to a specific date."""
    target_ui = target_dt.strftime("%d/%m/%y")
    print(f"Setting Syrve date for {label}: {target_ui}", flush=True)
    page.wait_for_timeout(800)
    current = _read_syrve_date_value(page)
    print(f"Current Syrve date before {label} set: {current or 'unknown'}", flush=True)
    if target_ui in current:
        print(f"Syrve date already equals {target_ui}", flush=True)
        return

    try:
        result = page.evaluate(
            """(target) => {
                const inputs = Array.from(document.querySelectorAll('resto-range-selector-input input, input'));
                const visible = inputs.filter(i => {
                  const r = i.getBoundingClientRect();
                  const style = window.getComputedStyle(i);
                  return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && !i.disabled && !i.readOnly;
                });
                const input = visible.find(i => /\d{2}\/\d{2}\/\d{2}/.test(i.value || '')) || visible[0];
                if (!input) return {ok:false, reason:'no_visible_input'};
                input.focus();
                const proto = Object.getPrototypeOf(input);
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(input, target); else input.value = target;
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.dispatchEvent(new Event('change', {bubbles:true}));
                input.blur();
                return {ok:true, value:input.value};
            }""",
            target_ui,
        )
        print(f"Direct date set result for {label}: {result}", flush=True)
        page.wait_for_timeout(1200)
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(2000)
    except Exception as exc:
        print(f"Direct date set failed for {label}: {exc}", flush=True)

    after = _read_syrve_date_value(page)
    print(f"Syrve date after {label} direct set: {after or 'unknown'}", flush=True)
    if target_ui in after:
        return

    # Fallback: click previous arrow up to 3 times until target appears.
    for i in range(3):
        if _read_syrve_date_value(page) == target_ui:
            return
        clicked = _click_previous_day_arrow(page)
        print(f"Fallback previous click {i+1} for {label}: {clicked}", flush=True)
        page.wait_for_timeout(2000)
        if target_ui in _read_syrve_date_value(page):
            return

def _open_sales_by_product(page: Page, settings: Settings) -> None:
    print("Opening Reports 2.0 -> Sales by Product...", flush=True)
    try:
        _click_text_robust(page, [settings.syrve_main_menu_text, "Routine Restaurant Ope", "Routine Restaurant Operations"], timeout=6000)
        page.wait_for_timeout(1000)
    except Exception as exc:
        print(f"Main menu click skipped/failed: {exc}", flush=True)

    # Expand Reports 2.0, then click Sales by Product.
    try:
        _click_text_robust(page, ["Reports 2.0"], timeout=8000)
        page.wait_for_timeout(1200)
    except Exception as exc:
        print(f"Reports 2.0 may already be open: {exc}", flush=True)

    _click_text_robust(page, ["Sales by Product"], timeout=10000)
    page.wait_for_timeout(5000)

    # Click report refresh button if present.
    for locator in [page.get_by_text("sync", exact=True), page.locator("mat-icon:has-text('sync')")]:
        try:
            locator.first.click(timeout=2000, force=True)
            page.wait_for_timeout(4000)
            break
        except Exception:
            continue

    page.wait_for_function(
        """() => (document.body.innerText || '').includes('Sales by Product') &&
                  (document.body.innerText || '').includes('Gross Sales after Discount')""",
        timeout=30000,
    )


def _parse_branch_summary(text: str, branch_code: str, raw_path: Path) -> BranchSales:
    # Example row:
    # B60 Hail, King Abdullahر.س18,706.00ر.س16,266.09ر.س2,439.91ر.س1,235.00ر.س20.33...
    line = ""
    for ln in text.splitlines():
        if branch_code in ln and "ر.س" in ln:
            line = ln.strip()
            break

    if not line:
        # Sometimes table row is not separated by newlines; search in full text.
        m = re.search(rf"({re.escape(branch_code)}[^\n]*?ر\.س[^\n]+)", text)
        line = m.group(1).strip() if m else ""

    amounts = re.findall(r"ر\.س\s*([\d,]+\.\d{2})", line)
    name = line.split("ر.س", 1)[0].strip() if line else ""

    return BranchSales(
        branch_code=branch_code,
        branch_name=name,
        gross_sales_after_discount=amounts[0] if len(amounts) > 0 else "",
        net_sales=amounts[1] if len(amounts) > 1 else "",
        vat_amount=amounts[2] if len(amounts) > 2 else "",
        discount_amount=amounts[3] if len(amounts) > 3 else "",
        avg_order_amount=amounts[4] if len(amounts) > 4 else "",
        avg_revenue_per_guest=amounts[5] if len(amounts) > 5 else "",
        cost=amounts[6] if len(amounts) > 6 else "",
        refund_amount=amounts[-1] if len(amounts) > 7 else "",
        raw_text_file=str(raw_path),
    )


def _try_expand_and_extract_products(page: Page, branch_code: str) -> list[ProductRow]:
    """Best-effort product extraction from expanded Sales by Product table.

    This report reliably gives branch totals. Product rows depend on Syrve's virtual table rendering;
    if they are not present, we return an empty list instead of blocking the summary.
    """
    try:
        # Small arrow before branch row in screenshot is around x 528, y 267.
        page.mouse.click(530, 267)
        page.wait_for_timeout(3000)
    except Exception:
        return []

    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return []

    rows: list[ProductRow] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or branch_code in ln or "ر.س" not in ln:
            continue
        amounts = re.findall(r"ر\.س\s*([\d,]+\.\d{2})", ln)
        if len(amounts) >= 2:
            name = ln.split("ر.س", 1)[0].strip("›> ")[:120]
            if name and name.lower() not in {"sales by product", "store"}:
                rows.append(ProductRow(branch_code=branch_code, rank=len(rows) + 1, item_name=name, gross_sales_after_discount=amounts[0], net_sales=amounts[1]))
        if len(rows) >= 10:
            break
    return rows


def _format_plain_text(report_date: str, metrics: list[BranchSales], products: list[ProductRow], sales_target: float) -> str:
    """Format WhatsApp report in clean English."""
    lines = [
        "Sales by Product Report - Previous Business Day",
        f"Business date: {report_date}",
        "",
    ]

    by_branch: dict[str, list[ProductRow]] = {}
    for p in products:
        by_branch.setdefault(p.branch_code, []).append(p)

    total_gross = 0.0
    total_net = 0.0
    total_vat = 0.0
    total_discount = 0.0

    def to_float(value: str) -> float:
        try:
            return float((value or "0").replace(',', ''))
        except Exception:
            return 0.0

    for m in metrics:
        total_gross += to_float(m.gross_sales_after_discount)
        total_net += to_float(m.net_sales)
        total_vat += to_float(m.vat_amount)
        total_discount += to_float(m.discount_amount)

    target_achievement = (total_net / sales_target * 100) if sales_target else 0
    target_gap = total_net - sales_target

    if total_gross or total_net:
        lines.extend([
            "Overall Summary",
            f"Total Gross Sales After Discount: {total_gross:,.2f} SAR",
            f"Total Net Sales: {total_net:,.2f} SAR",
            f"Sales Target: {sales_target:,.2f} SAR",
            f"Target Achievement: {target_achievement:.2f}%",
            f"Target Gap: {target_gap:+,.2f} SAR",
            f"Total VAT: {total_vat:,.2f} SAR",
            f"Total Discount: {total_discount:,.2f} SAR",
            "",
            "=" * 34,
        ])

    for m in metrics:
        branch_title = m.branch_name or m.branch_code
        lines.extend([
            f"Branch: {m.branch_code}",
            f"Name: {branch_title}",
            f"Gross Sales After Discount: {m.gross_sales_after_discount or '-'} SAR",
            f"Net Sales: {m.net_sales or '-'} SAR",
            f"Previous Day Net Sales: {m.comparison_net_sales or '-'} SAR",
            f"Sales Analysis: {m.sales_trend or '-'}",
            f"VAT Amount: {m.vat_amount or '-'} SAR",
            f"Discount Amount: {m.discount_amount or '-'} SAR",
            f"Average Order Amount: {m.avg_order_amount or '-'} SAR",
            f"Average Revenue per Guest: {m.avg_revenue_per_guest or '-'} SAR",
            f"Cost: {m.cost or '-'} SAR",
        ])

        items = by_branch.get(m.branch_code, [])
        if items:
            lines.append("")
            lines.append("Top Visible Products by Sales:")
            for item in items:
                lines.append(f"{item.rank}. {item.item_name} - {item.gross_sales_after_discount} SAR")
        else:
            lines.append("")
            lines.append("Product details were not visible in the table. Branch totals were extracted successfully.")

        lines.extend(["-" * 34, ""])

    return "\n".join(lines).strip()


def scrape_sales_by_product(settings: Settings, output_dir: Path) -> tuple[Path, str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    branch_codes = [b.strip().upper() for b in settings.branch_codes.split(",") if b.strip()]
    report_date = _target_date(settings)
    summaries: list[BranchSales] = []
    products: list[ProductRow] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ar-SA", viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.set_default_timeout(12000)
        _login_if_needed(page, settings)

        for branch_code in branch_codes:
            _select_branch(page, branch_code, branch_codes)
            _open_sales_by_product(page, settings)
            # Opening/changing reports can reset the date, so force previous business day here.
            _set_previous_business_date(page, settings)
            # Refresh report data after changing the date.
            try:
                page.get_by_text("sync", exact=True).first.click(timeout=2000, force=True)
                page.wait_for_timeout(4000)
            except Exception:
                pass
            text = page.locator("body").inner_text(timeout=8000)
            raw_path = output_dir / f"sales_by_product_{branch_code}.txt"
            raw_path.write_text(text, encoding="utf-8")
            _debug_dump(page, f"sales_by_product_{branch_code}")
            summary = _parse_branch_summary(text, branch_code, raw_path)
            if not summary.gross_sales_after_discount:
                raise RuntimeError(f"Could not extract Sales by Product totals for {branch_code}. Check artifact screenshot/text.")

            # Comparison: previous business day vs the day before it.
            target_dt = datetime.now(ZoneInfo("Asia/Riyadh")) - timedelta(days=1)
            comparison_dt = target_dt - timedelta(days=1)
            _set_specific_report_date(page, comparison_dt, label=f"comparison for {branch_code}")
            try:
                page.get_by_text("sync", exact=True).first.click(timeout=2000, force=True)
                page.wait_for_timeout(3500)
            except Exception:
                pass
            comparison_text = page.locator("body").inner_text(timeout=8000)
            comparison_path = output_dir / f"sales_by_product_{branch_code}_comparison.txt"
            comparison_path.write_text(comparison_text, encoding="utf-8")
            comparison_summary = _parse_branch_summary(comparison_text, branch_code, comparison_path)
            summary.comparison_net_sales = comparison_summary.net_sales
            summary.net_sales_change_pct, summary.sales_trend = _analyze_change(summary.net_sales, comparison_summary.net_sales)

            summaries.append(summary)
            products.extend(_try_expand_and_extract_products(page, branch_code))
            print(f"{branch_code}: extracted gross={summary.gross_sales_after_discount}, net={summary.net_sales}, comparison_net={summary.comparison_net_sales}, trend={summary.sales_trend}", flush=True)

        context.close()
        browser.close()

    df = pd.DataFrame([asdict(x) for x in summaries])
    product_df = pd.DataFrame([asdict(x) for x in products])
    report_path = output_dir / "sales_by_product_summary.xlsx"
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Branch Totals")
        product_df.to_excel(writer, index=False, sheet_name="Products")

    plain_text = _format_plain_text(report_date, summaries, products, settings.sales_target)

    def _money_to_float(value: str) -> float:
        try:
            return float((value or "0").replace(",", ""))
        except Exception:
            return 0.0

    total_net_sales = sum(_money_to_float(m.net_sales) for m in summaries)
    total_gross_sales = sum(_money_to_float(m.gross_sales_after_discount) for m in summaries)
    total_vat = sum(_money_to_float(m.vat_amount) for m in summaries)
    total_discount = sum(_money_to_float(m.discount_amount) for m in summaries)
    total_previous_net_sales = sum(_money_to_float(m.comparison_net_sales) for m in summaries)
    total_net_change_pct, total_net_trend = _analyze_change(f"{total_net_sales:.2f}", f"{total_previous_net_sales:.2f}")
    sales_target = float(settings.sales_target or 80500)
    target_achievement = (total_net_sales / sales_target * 100) if sales_target else 0
    target_gap = total_net_sales - sales_target
    target_color = "#047857" if target_achievement >= 100 else "#b45309"
    target_bg = "#ecfdf5" if target_achievement >= 100 else "#fffbeb"
    target_border = "#a7f3d0" if target_achievement >= 100 else "#fde68a"

    rows = "".join(
        f"<tr><td>{m.branch_code}</td><td>{m.gross_sales_after_discount}</td><td>{m.net_sales}</td><td>{m.comparison_net_sales}</td><td>{m.net_sales_change_pct}</td><td>{m.sales_trend}</td><td>{m.vat_amount}</td><td>{m.discount_amount}</td><td>{m.avg_order_amount}</td></tr>"
        for m in summaries
    )
    total_row = f"""
        <tr style="font-weight: bold; background: #ecfdf5;">
          <td>Total</td>
          <td>{total_gross_sales:,.2f}</td>
          <td>{total_net_sales:,.2f}</td>
          <td>-</td>
          <td>{total_net_change_pct}</td>
          <td>{total_net_trend}</td>
          <td>{total_vat:,.2f}</td>
          <td>{total_discount:,.2f}</td>
          <td>-</td>
        </tr>
    """
    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.7">
      <h2>Sales by Product Report - Previous Business Day</h2>
      <p><b>Business date:</b> {report_date}</p>
      <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 18px 0;">
        <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:14px; padding:14px;">
          <div style="font-size:12px; color:#047857; font-weight:bold; text-transform:uppercase;">Total Net Sales</div>
          <div style="font-size:26px; font-weight:bold; color:#064e3b; margin-top:4px;">{total_net_sales:,.2f} SAR</div>
        </div>
        <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:14px; padding:14px;">
          <div style="font-size:12px; color:#1d4ed8; font-weight:bold; text-transform:uppercase;">Total Gross Sales After Discount</div>
          <div style="font-size:22px; font-weight:bold; color:#1e3a8a; margin-top:4px;">{total_gross_sales:,.2f} SAR</div>
        </div>
        <div style="background:{target_bg}; border:1px solid {target_border}; border-radius:14px; padding:14px;">
          <div style="font-size:12px; color:{target_color}; font-weight:bold; text-transform:uppercase;">Target Achievement</div>
          <div style="font-size:26px; font-weight:bold; color:{target_color}; margin-top:4px;">{target_achievement:.2f}%</div>
          <div style="font-size:11px; color:#6b7280; margin-top:3px;">Target: {sales_target:,.2f} SAR | Gap: {target_gap:+,.2f} SAR</div>
        </div>
      </div>
      <div style="background:#fff7ed; border:1px solid #fed7aa; border-radius:14px; padding:14px; margin: 10px 0 18px;">
        <div style="font-size:12px; color:#c2410c; font-weight:bold; text-transform:uppercase;">Sales Analysis</div>
        <div style="font-size:20px; font-weight:bold; color:#7c2d12; margin-top:4px;">{total_net_trend}</div>
        <div style="font-size:12px; color:#9a3412; margin-top:4px;">Previous Day Net Sales: {total_previous_net_sales:,.2f} SAR</div>
      </div>
      <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse">
        <tr>
          <th>Branch</th>
          <th>Gross Sales After Discount</th>
          <th>Net Sales</th>
          <th>Previous Net Sales</th>
          <th>Net Change %</th>
          <th>Sales Analysis</th>
          <th>VAT</th>
          <th>Discount</th>
          <th>Average Order</th>
        </tr>
        {rows}
        {total_row}
      </table>
    </div>
    """
    return report_path, html, plain_text
