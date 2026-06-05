from pathlib import Path
from playwright.sync_api import sync_playwright


def build_pdf_report(html_body: str, output_dir: Path, filename: str = "sales_dashboard.pdf") -> Path:
    """Render the HTML report into a polished PDF dashboard."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / filename

    full_html = f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8" />
      <style>
        @page {{ size: A4; margin: 14mm; }}
        body {{
          font-family: Arial, Helvetica, sans-serif;
          color: #111827;
          background: #ffffff;
          margin: 0;
        }}
        .page {{ max-width: 100%; }}
        .header {{
          background: linear-gradient(135deg, #111827, #1f2937);
          color: white;
          padding: 22px 26px;
          border-radius: 18px;
          margin-bottom: 22px;
        }}
        .header h1 {{ margin: 0 0 6px; font-size: 26px; }}
        .header p {{ margin: 0; color: #d1d5db; font-size: 13px; }}
        h2 {{ color: #111827; margin-top: 24px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 12px; }}
        th {{ background: #f3f4f6; text-align: left; color: #374151; }}
        th, td {{ border: 1px solid #e5e7eb; padding: 9px 8px; }}
        tr:nth-child(even) td {{ background: #fafafa; }}
        .footer {{ margin-top: 22px; font-size: 11px; color: #6b7280; }}
      </style>
    </head>
    <body>
      <div class="page">
        <div class="header">
          <h1>Sales Dashboard</h1>
          <p>Automatically generated from Syrve - Previous Business Day</p>
        </div>
        {html_body}
        <div class="footer">Generated automatically by GitHub Actions.</div>
      </div>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(full_html, wait_until="load")
        page.pdf(path=str(pdf_path), format="A4", print_background=True)
        browser.close()

    return pdf_path
