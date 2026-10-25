"""Render proof images for the README/slides from live data: docs/proof_*.png.

Reads dbo.vw_RunSummary, dbo.vw_RejectedRows, the latest LoanProfitEstimates table and the newest
confirmation mail the local SMTP sink saved. Nothing is typed by hand. Needs Chrome or Edge.
"""
import email
import html
import subprocess
import tempfile
from email import policy
from pathlib import Path

from PIL import Image, ImageChops

from seed_tables import connect, load_env

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
CSS = """
body{margin:0;padding:28px 32px;background:#fcfcfb;color:#0b0b0b;font:15px/1.45 'Segoe UI',Lato,Arial,sans-serif;width:1136px}
h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#52514e;margin:0 0 10px}
table{border-collapse:collapse;width:100%;margin-bottom:26px}
th{text-align:left;font-weight:600;color:#52514e;border-bottom:2px solid #e6e5e0;padding:7px 12px;font-size:13px}
td{padding:7px 12px;border-bottom:1px solid #eeede8;font-variant-numeric:tabular-nums}
td.n,th.n{text-align:right}
.ok{color:#0b6b2e;font-weight:600}.warn{color:#8a5a00;font-weight:600}.bad{color:#b3261e;font-weight:600}
pre{background:#f1f0ec;padding:18px 22px;border-radius:8px;font:14px/1.55 Consolas,monospace;margin:0;white-space:pre-wrap}
"""


def table(columns, rows, numeric=()):
    head = "".join(f'<th class="{"n" if c in numeric else ""}">{html.escape(c)}</th>' for c in columns)
    body = ""
    for row in rows:
        cells = ""
        for c, v in zip(columns, row):
            cls = "n" if c in numeric else ""
            text = "" if v is None else (f"{v:,.2f}".replace(",", " ") if isinstance(v, float) else str(v))
            if c == "Status":
                cls += " ok" if text == "SUCCESS" else (" bad" if text == "FAILED" else " warn")
            cells += f'<td class="{cls.strip()}">{html.escape(text)}</td>'
        body += f"<tr>{cells}</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def screenshot(body_html, out_name):
    browser = next(b for b in BROWSERS if Path(b).exists())
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "p.html"
        page.write_text(f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>{body_html}", encoding="utf-8")
        raw = Path(tmp) / "raw.png"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                        "--window-size=1200,1400", f"--screenshot={raw}", page.as_uri()], check=True,
                       capture_output=True, timeout=120)
        img = Image.open(raw).convert("RGB")
    bg = Image.new("RGB", img.size, (252, 252, 251))
    box = ImageChops.difference(img, bg).getbbox()
    pad = 56
    img.crop((0, 0, img.width, min(img.height, box[3] + pad))).save(DOCS / out_name)
    print("wrote", out_name)


def main():
    load_env()
    DOCS.mkdir(exist_ok=True)
    cur = connect().cursor()

    runs = cur.execute("SELECT TOP 4 RunMonth, StartedAt, Status, RowsRead, RowsLoaded, RowsRejected, Warnings "
                       "FROM dbo.vw_RunSummary ORDER BY StartedAt DESC").fetchall()
    rejected = cur.execute("SELECT RecordID, CarModel, EnergyClass, BasePrice, CustomerRiskTier, RejectReason "
                           "FROM dbo.vw_RejectedRows ORDER BY RecordID").fetchall()
    screenshot(
        "<h2>dbo.vw_RunSummary - latest runs</h2>"
        + table(["RunMonth", "StartedAt", "Status", "RowsRead", "RowsLoaded", "RowsRejected", "Warnings"],
                [[r.RunMonth, str(r.StartedAt)[:19], r.Status, r.RowsRead, r.RowsLoaded, r.RowsRejected, r.Warnings] for r in runs],
                numeric=("RowsRead", "RowsLoaded", "RowsRejected", "Warnings"))
        + "<h2>dbo.vw_RejectedRows - what was rejected, and why</h2>"
        + table(["RecordID", "CarModel", "EnergyClass", "BasePrice", "CustomerRiskTier", "RejectReason"],
                [list(r) for r in rejected], numeric=("RecordID", "BasePrice")),
        "proof_data_quality.png")

    latest = cur.execute("SELECT TOP 1 RunMonth FROM dbo.vw_RunSummary WHERE Status <> 'FAILED' ORDER BY StartedAt DESC").fetchval()
    target = f"LoanProfitEstimates_{latest:%Y%m}"
    out = cur.execute(f"SELECT TOP 8 RecordID, CarModel, EnergyClass, ManufactureYear, BasePrice, BaseInterestRate, BankMargin, "
                      f"TotalInterestRate, EstimatedProfit FROM dbo.[{target}] ORDER BY RecordID").fetchall()
    screenshot(
        f"<h2>SELECT TOP 8 * FROM {target}</h2>"
        + table(["RecordID", "CarModel", "EnergyClass", "ManufactureYear", "BasePrice", "BaseInterestRate", "BankMargin",
                 "TotalInterestRate", "EstimatedProfit"],
                [[r.RecordID, r.CarModel, r.EnergyClass, r.ManufactureYear, float(r.BasePrice), float(r.BaseInterestRate),
                  float(r.BankMargin), float(r.TotalInterestRate), float(r.EstimatedProfit)] for r in out],
                numeric=("RecordID", "ManufactureYear", "BasePrice", "BaseInterestRate", "BankMargin", "TotalInterestRate",
                         "EstimatedProfit")),
        "proof_output.png")

    newest = max((ROOT / "data" / "mail").glob("*.eml"), key=lambda p: p.stat().st_mtime)
    msg = email.message_from_bytes(newest.read_bytes(), policy=policy.default)
    body = "\n".join(line for line in msg.get_content().splitlines() if line.strip())
    screenshot(f"<h2>Confirmation email (local SMTP sink)</h2><p><b>Subject:</b> {html.escape(msg['Subject'])}<br>"
               f"<b>To:</b> {html.escape(msg['To'])}</p><pre>{html.escape(body)}</pre>", "proof_email.png")


if __name__ == "__main__":
    main()
