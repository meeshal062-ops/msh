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


def _set_previous_business_date(page: Page, settings: Settings) -> None:
    """Set Syrve's visible date selector to the previous business day.

    The report label was already using yesterday, but the Syrve UI date was not forced in
    the Sales by Product flow. This function clicks the previous-day arrow once only when
    needed, before processing branches.
    """
    if settings.report_date_mode.lower().strip() != "yesterday":
        return

    now = datetime.now(ZoneInfo("Asia/Riyadh"))
    today_ui = now.strftime("%d/%m/%y")
    target_ui = (now - timedelta(days=1)).strftime("%d/%m/%y")
    print(f"Setting report date to previous business day: target={target_ui}, today={today_ui}", flush=True)

    # Wait for the date selector to exist after login.
    page.wait_for_timeout(1500)

    current_value = ""
    try:
        current_value = page.evaluate(
            """() => {
                const inputs = Array.from(document.querySelectorAll('resto-range-selector-input input, input'));
                const visible = inputs.filter(i => {
                  const r = i.getBoundingClientRect();
                  const style = window.getComputedStyle(i);
                  return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                });
                return visible.map(i => i.value || i.getAttribute('value') || '').find(v => /\d{2}\/\d{2}\/\d{2}/.test(v)) || '';
            }"""
        )
    except Exception as exc:
        print(f"Could not read current date input: {exc}", flush=True)

    print(f"Current Syrve date selector value: {current_value or 'unknown'}", flush=True)

    if target_ui in current_value:
        print("Syrve date is already set to previous business day.", flush=True)
        return

    # If date is today or unknown, click previous arrow once.
    clicked = False
    for selector in [
        "resto-range-selector-input .control.arrow.prev button",
        ".prev-period-icon",
        "mat-icon.prev-period-icon",
    ]:
        try:
            page.locator(selector).first.click(timeout=4000, force=True)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        # Coordinate fallback based on the top date selector location.
        try:
            page.mouse.click(665, 32)
            clicked = True
        except Exception:
            pass

    print(f"Previous-day arrow clicked={clicked}", flush=True)
    page.wait_for_timeout(3000)

    try:
        after_value = page.evaluate(
            """() => {
                const inputs = Array.from(document.querySelectorAll('resto-range-selector-input input, input'));
                const visible = inputs.filter(i => {
                  const r = i.getBoundingClientRect();
                  const style = window.getComputedStyle(i);
                  return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                });
                return visible.map(i => i.value || i.getAttribute('value') || '').find(v => /\d{2}\/\d{2}\/\d{2}/.test(v)) || '';
            }"""
        )
        print(f"Syrve date after previous click: {after_value or 'unknown'}", flush=True)
    except Exception:
        pass


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


def _format_plain_text(report_date: str, metrics: list[BranchSales], products: list[ProductRow]) -> str:
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

    if total_gross or total_net:
        lines.extend([
            "Overall Summary",
            f"Total Gross Sales After Discount: {total_gross:,.2f} SAR",
            f"Total Net Sales: {total_net:,.2f} SAR",
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
        _set_previous_business_date(page, settings)

        for branch_code in branch_codes:
            _select_branch(page, branch_code, branch_codes)
            _open_sales_by_product(page, settings)
            text = page.locator("body").inner_text(timeout=8000)
            raw_path = output_dir / f"sales_by_product_{branch_code}.txt"
            raw_path.write_text(text, encoding="utf-8")
            _debug_dump(page, f"sales_by_product_{branch_code}")
            summary = _parse_branch_summary(text, branch_code, raw_path)
            if not summary.gross_sales_after_discount:
                raise RuntimeError(f"Could not extract Sales by Product totals for {branch_code}. Check artifact screenshot/text.")
            summaries.append(summary)
            products.extend(_try_expand_and_extract_products(page, branch_code))
            print(f"{branch_code}: extracted gross={summary.gross_sales_after_discount}, net={summary.net_sales}", flush=True)

        context.close()
        browser.close()

    df = pd.DataFrame([asdict(x) for x in summaries])
    product_df = pd.DataFrame([asdict(x) for x in products])
    report_path = output_dir / "sales_by_product_summary.xlsx"
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Branch Totals")
        product_df.to_excel(writer, index=False, sheet_name="Products")

    plain_text = _format_plain_text(report_date, summaries, products)

    def _money_to_float(value: str) -> float:
        try:
            return float((value or "0").replace(",", ""))
        except Exception:
            return 0.0

    total_net_sales = sum(_money_to_float(m.net_sales) for m in summaries)
    total_gross_sales = sum(_money_to_float(m.gross_sales_after_discount) for m in summaries)
    total_vat = sum(_money_to_float(m.vat_amount) for m in summaries)
    total_discount = sum(_money_to_float(m.discount_amount) for m in summaries)

    rows = "".join(
        f"<tr><td>{m.branch_code}</td><td>{m.gross_sales_after_discount}</td><td>{m.net_sales}</td><td>{m.vat_amount}</td><td>{m.discount_amount}</td><td>{m.avg_order_amount}</td></tr>"
        for m in summaries
    )
    total_row = f"""
        <tr style="font-weight: bold; background: #ecfdf5;">
          <td>Total</td>
          <td>{total_gross_sales:,.2f}</td>
          <td>{total_net_sales:,.2f}</td>
          <td>{total_vat:,.2f}</td>
          <td>{total_discount:,.2f}</td>
          <td>-</td>
        </tr>
    """
    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.7">
      <h2>Sales by Product Report - Previous Business Day</h2>
      <p><b>Business date:</b> {report_date}</p>
      <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 18px 0;">
        <div style="background:#ecfdf5; border:1px solid #a7f3d0; border-radius:14px; padding:14px;">
          <div style="font-size:12px; color:#047857; font-weight:bold; text-transform:uppercase;">Total Net Sales</div>
          <div style="font-size:26px; font-weight:bold; color:#064e3b; margin-top:4px;">{total_net_sales:,.2f} SAR</div>
        </div>
        <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:14px; padding:14px;">
          <div style="font-size:12px; color:#1d4ed8; font-weight:bold; text-transform:uppercase;">Total Gross Sales After Discount</div>
          <div style="font-size:22px; font-weight:bold; color:#1e3a8a; margin-top:4px;">{total_gross_sales:,.2f} SAR</div>
        </div>
      </div>
      <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse">
        <tr>
          <th>Branch</th>
          <th>Gross Sales After Discount</th>
          <th>Net Sales</th>
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
