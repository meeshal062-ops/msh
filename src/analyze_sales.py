from __future__ import annotations

from pathlib import Path
from datetime import datetime
import pandas as pd

AMOUNT_CANDIDATES = [
    "sales", "sale", "revenue", "total", "amount", "net sales", "gross sales",
    "المبيعات", "اجمالي", "إجمالي", "المبلغ", "الصافي", "الاجمالي", "الإجمالي",
]
ORDER_CANDIDATES = ["order", "order id", "check", "receipt", "invoice", "رقم الطلب", "الفاتورة", "الطلب"]
DISCOUNT_CANDIDATES = ["discount", "خصم", "الخصم"]
TAX_CANDIDATES = ["tax", "vat", "ضريبة", "الضريبة", "القيمة المضافة"]
DATE_CANDIDATES = ["date", "business date", "created", "time", "التاريخ", "تاريخ"]


def _norm(name: object) -> str:
    return str(name).strip().lower()


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {_norm(c): c for c in df.columns}
    for candidate in candidates:
        c = candidate.lower()
        for norm, original in normalized.items():
            if c == norm or c in norm:
                return original
    return None


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        # Try all sheets and keep the first non-empty sheet.
        sheets = pd.read_excel(path, sheet_name=None)
        for _, df in sheets.items():
            if not df.empty:
                return df
        raise RuntimeError("Excel file has no data sheets.")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise RuntimeError(f"Unsupported file type: {path.suffix}")


def analyze_sales(input_file: Path, output_dir: Path) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = _read_table(input_file)
    df = df.dropna(how="all")

    amount_col = _find_col(df, AMOUNT_CANDIDATES)
    order_col = _find_col(df, ORDER_CANDIDATES)
    discount_col = _find_col(df, DISCOUNT_CANDIDATES)
    tax_col = _find_col(df, TAX_CANDIDATES)
    date_col = _find_col(df, DATE_CANDIDATES)

    if not amount_col:
        raise RuntimeError(
            "Could not detect sales amount column. Send a sample Excel file or column names so we can map it correctly."
        )

    amounts = pd.to_numeric(df[amount_col], errors="coerce").fillna(0)
    discounts = pd.to_numeric(df[discount_col], errors="coerce").fillna(0) if discount_col else pd.Series([0] * len(df))
    taxes = pd.to_numeric(df[tax_col], errors="coerce").fillna(0) if tax_col else pd.Series([0] * len(df))

    total_sales = float(amounts.sum())
    total_discount = float(discounts.sum())
    total_tax = float(taxes.sum())
    orders_count = int(df[order_col].nunique()) if order_col else int(len(df))
    avg_ticket = total_sales / orders_count if orders_count else 0

    summary_rows = [
        ["إجمالي المبيعات", total_sales],
        ["عدد الطلبات/الفواتير", orders_count],
        ["متوسط الفاتورة", avg_ticket],
        ["إجمالي الخصومات", total_discount],
        ["إجمالي الضرائب", total_tax],
        ["عمود المبيعات المستخدم", amount_col],
        ["عمود الطلب المستخدم", order_col or "غير موجود"],
        ["عمود التاريخ المستخدم", date_col or "غير موجود"],
    ]
    summary_df = pd.DataFrame(summary_rows, columns=["المؤشر", "القيمة"])

    report_path = output_dir / f"sales_summary_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        df.to_excel(writer, index=False, sheet_name="Raw Data")

    html = f"""
    <div dir="rtl" style="font-family: Arial, sans-serif; line-height: 1.7">
      <h2>تقرير المبيعات اليومي</h2>
      <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse">
        <tr><th>المؤشر</th><th>القيمة</th></tr>
        <tr><td>إجمالي المبيعات</td><td>{total_sales:,.2f}</td></tr>
        <tr><td>عدد الطلبات/الفواتير</td><td>{orders_count:,}</td></tr>
        <tr><td>متوسط الفاتورة</td><td>{avg_ticket:,.2f}</td></tr>
        <tr><td>إجمالي الخصومات</td><td>{total_discount:,.2f}</td></tr>
        <tr><td>إجمالي الضرائب</td><td>{total_tax:,.2f}</td></tr>
      </table>
      <p>تم إرفاق ملف Excel التفصيلي.</p>
    </div>
    """
    return report_path, html
