"""Build excel/LoanETL_CreditDesk.xlsx: a credit manager's desk tool on top of the LoanETL database.

The workbook pulls its data live from SQL Server with Power Query (Data > Refresh All) and does every
calculation in ordinary Excel formulas, so the customer can watch the numbers move.
Excel is driven through COM, so this needs Windows + desktop Excel + pywin32.

    .venv\\Scripts\\python.exe python\\make_credit_desk.py            # build (Excel stays open for a look)
    .venv\\Scripts\\python.exe python\\make_credit_desk.py --check    # verify the saved workbook against SQL
"""
import argparse
import os
import socket
import sys
from pathlib import Path

import pyodbc
import win32com.client

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "excel" / "LoanETL_CreditDesk.xlsx"
SERVER = os.environ.get("SQL_SERVER", "localhost")
DATABASE = os.environ.get("SQL_DATABASE", "LoanETL")
# Power Query always encrypts and checks the certificate name, which "localhost" does not match, so the workbook
# connects by machine name instead (no server setting touched). pyodbc below keeps using SERVER.
PQ_SERVER = os.environ.get("PQ_SERVER", socket.gethostname())

LA, PR, SC, DATA = "Loan Application", "Prognosis", "Scenarios", "Data"


def rgb(r, g, b):
    return r + g * 256 + b * 65536  # Excel colours are BGR integers


NAVY, BLUE_BAND, GREY, INPUT_FILL = rgb(31, 56, 100), rgb(217, 225, 242), rgb(242, 242, 242), rgb(255, 242, 204)
INPUT_FONT, NOTE_FONT, GREEN, RED = rgb(0, 0, 192), rgb(110, 110, 110), rgb(0, 128, 0), rgb(192, 0, 0)

# Power Query uses navigation only (no native SQL), so Excel never asks to approve a query.
# Cars = the newest LoanProfitEstimates_yyyymm that is not in the future, i.e. what the monthly job last produced.
M_SOURCE = f'Sql.Database("{PQ_SERVER}", "{DATABASE}")'
QUERIES = {
    "Cars": f"""let
    Src = {M_SOURCE},
    Tbls = Table.SelectRows(Src, each [Schema] = "dbo" and Text.StartsWith([Item], "LoanProfitEstimates_")
        and [Item] <= "LoanProfitEstimates_" & Date.ToText(Date.From(DateTime.LocalNow()), "yyyyMM")),
    Latest = Table.Max(Tbls, "Item"),
    Slim = Table.SelectColumns(Latest[Data], {{"RecordID", "CarModel", "EnergyClass", "ManufactureYear", "BasePrice",
        "CustomerRiskTier", "FileMonth", "TotalInterestRate", "EstimatedAnnualProfit", "MinAnnualAmortization"}}),
    Src2 = Table.AddColumn(Slim, "SourceTable", each Latest[Item], type text),
    Lbl = Table.AddColumn(Src2, "Label", each Text.From([RecordID]) & " · " & [CarModel] & " (" & Text.From([ManufactureYear]) & ")", type text)
in
    Lbl""",
    "Rates": f'let Src = {M_SOURCE}, T = Src{{[Schema="dbo", Item="InterestRates"]}}[Data] in Table.SelectColumns(T, {{"EffectiveDate", "BaseInterestRate", "RateSource"}})',
    "Energy": f'let Src = {M_SOURCE} in Src{{[Schema="dbo", Item="EnergyClassMargin"]}}[Data]',
    "Risk": f'let Src = {M_SOURCE} in Src{{[Schema="dbo", Item="CreditRiskTier"]}}[Data]',
    "Depr": f'let Src = {M_SOURCE} in Src{{[Schema="dbo", Item="DepreciationRates"]}}[Data]',
}
TABLES = {"Cars": ("tblCars", "A1"), "Rates": ("tblRates", "N1"), "Energy": ("tblEnergy", "S1"),
          "Risk": ("tblRisk", "V1"), "Depr": ("tblDepr", "Y1")}

# Single-cell names on the application sheet: formulas elsewhere read like the business rule they implement.
NAMES = {
    "SelCar": "C6", "CarEnergy": "C9", "CarAge": "C11", "CarPrice": "C12",
    "IndexRate": "C16", "EnergyMargin": "C17", "DbTier": "C18", "TierOverride": "C19", "TierApplied": "C20",
    "RiskAdj": "C21", "BankMargin": "C22", "RateDiscount": "C23", "CustRate": "C24",
    "MinDownPct": "C27", "DownPct": "C29", "DownSEK": "C30", "LoanAmt": "C31", "LTV0": "C32", "LtvCap": "C33",
    "TermYrs": "C34", "SetupFee": "C35", "AdminFee": "C36", "Infl": "C37", "Income": "C38", "MaxPTI": "C39",
}


def style_band(rng, text, size=11, color=NAVY, font=rgb(255, 255, 255)):
    rng.Merge()
    rng.Value = text
    rng.Interior.Color = color
    rng.Font.Color = font
    rng.Font.Bold = True
    rng.Font.Size = size
    rng.VerticalAlignment = -4108
    rng.IndentLevel = 1


def cf(rng, formula):
    """Expression-based conditional format.
    Excel parses CF and validation formulas in the user's locale (function names, ';' separators, decimal comma),
    unlike Formula2, so the English formula is round-tripped through a scratch cell to get the local text.
    Relative references in a CF formula are anchored to the active cell, hence the Select."""
    ws = rng.Worksheet
    ws.Activate()
    rng.Cells(1, 1).Select()
    scratch = ws.Range("AZ1")
    scratch.Formula2 = formula
    local = scratch.FormulaLocal
    scratch.Clear()
    return rng.FormatConditions.Add(2, 1, local)


_SEP = {"dec": ".", "thou": ","}


def init_locale(xl):
    intl = xl.International  # 0-based tuple of Excel's International(index) values
    _SEP["dec"], _SEP["thou"] = intl[2], intl[3]


def L(fmt):
    """US number-format code -> the local one. Through COM from Python, NumberFormat is read in the user's locale
    (on sv-SE '#,##0' becomes a 3-decimal format), so formats are written with NumberFormatLocal instead."""
    out, quoted = [], False
    for ch in fmt:
        if ch == '"':
            quoted = not quoted
        if not quoted and ch == ".":
            ch = _SEP["dec"]
        elif not quoted and ch == ",":
            ch = _SEP["thou"]
        out.append(ch)
    return "".join(out)


def num(xl, x):
    """A number as the locale's validation dialog expects it (decimal comma on sv-SE)."""
    return str(x).replace(".", _SEP["dec"])


def section(ws, addr, text):
    r = ws.Range(addr)
    r.Interior.Color = BLUE_BAND
    r.Font.Bold = True
    r.Font.Color = NAVY
    r.Cells(1, 1).Value = text


def row(ws, r, label, formula=None, fmt=None, note=None, kind="ro", col="B", valcol="C", notecol="D"):
    """One label | value | note line. kind: 'ro' = grey calculated, 'in' = yellow input."""
    ws.Range(f"{col}{r}").Value = label
    c = ws.Range(f"{valcol}{r}")
    if formula is not None:
        if isinstance(formula, str) and formula.startswith("="):
            c.Formula2 = formula
        else:
            c.Value = formula
    if fmt:
        c.NumberFormatLocal = L(fmt)
    c.HorizontalAlignment = -4152
    if kind == "in":
        c.Interior.Color, c.Font.Color, c.Locked = INPUT_FILL, INPUT_FONT, False
        c.Borders.LineStyle, c.Borders.Color = 1, rgb(191, 143, 0)
    else:
        c.Interior.Color = GREY
    if note:
        n = ws.Range(f"{notecol}{r}")
        n.Value = note
        n.Font.Italic, n.Font.Size, n.Font.Color = True, 9, NOTE_FONT


def build_data(wb, xl):
    ws = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
    ws.Name = DATA
    for name, m in QUERIES.items():
        wb.Queries.Add(Name=name, Formula=m)
        tbl, anchor = TABLES[name]
        conn = f'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location={name};Extended Properties=""'
        lo = ws.ListObjects.Add(SourceType=0, Source=conn, LinkSource=True, XlListObjectHasHeaders=1, Destination=ws.Range(anchor))
        qt = lo.QueryTable
        qt.CommandType, qt.CommandText = 2, f"SELECT * FROM [{name}]"
        qt.BackgroundQuery = False  # block until loaded: the formulas below need the tables
        qt.Refresh(False)
        lo.Name = tbl
    return ws


def build_application(wb, xl, default_car):
    ws = wb.Worksheets(LA)
    # Names first: every formula below (and on the other sheets) is written in terms of them.
    for n, addr in NAMES.items():
        wb.Names.Add(Name=n, RefersTo=f"='{LA}'!${addr[0]}${addr[1:]}")
    # Names cannot hold a table reference directly: INDIRECT for the dropdown, a helper cell for the row index.
    wb.Names.Add(Name="CarList", RefersTo='=INDIRECT("tblCars[Label]")')
    wb.Worksheets(DATA).Range("AD1").Value = "Selected car: row in tblCars"
    wb.Worksheets(DATA).Range("AD2").Formula2 = "=MATCH(SelCar,tblCars[Label],0)"
    wb.Names.Add(Name="CarIdx", RefersTo=f"='{DATA}'!$AD$2")
    for col, w in zip("ABCDEFGH", (2, 36, 46, 38, 3, 42, 24, 34)):
        ws.Columns(col).ColumnWidth = w
    ws.Cells.Font.Name, ws.Cells.Font.Size = "Segoe UI", 10
    ws.Rows(1).RowHeight = 34
    style_band(ws.Range("B1:H1"), "Credit Desk  |  Car loan assessment", 20)
    style_band(ws.Range("B2:H2"), "Educational tool. Figures are indicative and come from the LoanETL database (Data > Refresh All).", 9)

    car = "INDEX(tblCars[{}],CarIdx)"
    section(ws, "B5:D5", "1  |  Customer's car")
    row(ws, 6, "Select car", default_car, None, "Pick from the list; everything below follows", "in")
    ws.Range("C6").HorizontalAlignment = -4131
    row(ws, 7, "Record ID", "=" + car.format("RecordID"), "0")
    row(ws, 8, "Model", "=" + car.format("CarModel"))
    row(ws, 9, "Energy class", "=" + car.format("EnergyClass"))
    row(ws, 10, "Model year", "=" + car.format("ManufactureYear"), "0")
    row(ws, 11, "Car age today (years)", "=MAX(0,YEAR(" + car.format("FileMonth") + ")-C10)", "0", "Same age rule as the ETL")
    row(ws, 12, "Purchase price today (SEK)", "=" + car.format("BasePrice"), "#,##0", "Equals the amount financed before any down payment")
    row(ws, 13, "Depreciation rate, coming year",
        "=XLOOKUP(CarAge,tblDepr[MinAge],tblDepr[AnnualRate],0,-1)", "0.0%", "Age band from DepreciationRates")

    section(ws, "B15:D15", "2  |  Pricing")
    row(ws, 16, "Index: STIBOR 3M", "=XLOOKUP(" + car.format("FileMonth") + ",tblRates[EffectiveDate],tblRates[BaseInterestRate],0,-1)/100",
        "0.00%", None)
    ws.Range("D16").Formula2 = ('="Source "&XLOOKUP(' + car.format("FileMonth") +
                                ',tblRates[EffectiveDate],tblRates[RateSource],"",-1)&"; latest month at or before the file month"')
    ws.Range("D16").Font.Italic, ws.Range("D16").Font.Size, ws.Range("D16").Font.Color = True, 9, NOTE_FONT
    row(ws, 17, "Energy-class margin", "=XLOOKUP(CarEnergy,tblEnergy[EnergyClass],tblEnergy[MarginRate])/100", "0.00%")
    row(ws, 18, "Risk tier in database", "=" + car.format("CustomerRiskTier"))
    row(ws, 19, "Risk tier override (optional)", None, None, "Leave blank to use the database tier", "in")
    row(ws, 20, "Risk tier applied", '=IF(TierOverride="",DbTier,TierOverride)')
    row(ws, 21, "Risk adjustment", "=XLOOKUP(TierApplied,tblRisk[RiskTier],tblRisk[RiskAdjustment])/100", "0.00%")
    row(ws, 22, "Bank margin", "=EnergyMargin+RiskAdj", "0.00%", "Energy margin + risk adjustment")
    row(ws, 23, "Rate discount (pp)", 0, "0.00%", "Negotiating room; customer rate never below 0%", "in")
    row(ws, 24, "Customer interest rate", "=MAX(0,IndexRate+BankMargin-RateDiscount)", "0.00%", "Index + bank margin - discount")
    ws.Range("B24:C24").Font.Bold = True

    section(ws, "B26:D26", "3  |  Loan structure")
    row(ws, 27, "Minimum down payment (policy)", 0.20, "0%", "Read-only; Swedish consumer-credit practice")
    row(ws, 28, "Minimum down payment (SEK)", "=CarPrice*MinDownPct", "#,##0")
    row(ws, 29, "Down payment (% of price)", 0.20, "0.0%", "Try 0% to 50%: watch minimum amortization", "in")
    row(ws, 30, "Down payment (SEK)", "=CarPrice*DownPct", "#,##0")
    row(ws, 31, "Loan amount (SEK)", "=CarPrice-DownSEK", "#,##0")
    row(ws, 32, "Loan-to-value at start", "=LoanAmt/CarPrice", "0.0%")
    row(ws, 33, "Max LTV during the loan", 1.0, "0%", "Loan may not exceed this share of the car's value", "in")
    row(ws, 34, "Loan term (years)", 5, "0", "1 to 15", "in")
    row(ws, 35, "Setup fee (SEK)", 600, "#,##0", "Paid at signing", "in")
    row(ws, 36, "Monthly admin fee (SEK)", 35, "#,##0", "Indexed by inflation each year", "in")
    row(ws, 37, "Inflation (index), per year", 0.02, "0.0%", "Indexes fees and income; the loan rate is not CPI-linked", "in")
    row(ws, 38, "Customer net income (SEK/month)", 45000, "#,##0", None, "in")
    row(ws, 39, "Max payment-to-income (policy)", 0.20, "0%", "Year-1 payment / net income", "in")

    kf = dict(col="F", valcol="G", notecol="H")
    section(ws, "F5:H5", "4  |  Key figures")
    row(ws, 6, "Loan amount (SEK)", "=LoanAmt", "#,##0", **kf)
    row(ws, 7, "Customer interest rate", "=CustRate", "0.00%", **kf)
    row(ws, 8, "Minimum amortization, year 1", f"='{PR}'!I11", "#,##0", "Keeps loan within the LTV cap", **kf)
    row(ws, 9, "Planned amortization, year 1", f"='{PR}'!J11", "#,##0", "Higher of minimum and loan/term", **kf)
    row(ws, 10, "Interest, year 1 (bank income)", f"='{PR}'!K11", "#,##0", **kf)
    row(ws, 11, "Monthly payment, year 1", f"='{PR}'!N11/12", "#,##0", "Amortization + interest + admin fee", **kf)
    row(ws, 12, "Total interest over the term", f"='{PR}'!K26", "#,##0", **kf)
    row(ws, 13, "Total fees", f"='{PR}'!M26", "#,##0", **kf)
    row(ws, 14, "Total payments (nominal)", f"='{PR}'!N26", "#,##0", "Includes setup fee", **kf)
    row(ws, 15, "Effective annual rate (IRR)", f"=IRR('{PR}'!U10:U25,0.05)", "0.00%", "Interest rate including fees", **kf)
    row(ws, 16, "Payment-to-income, year 1", f"='{PR}'!P11", "0.0%", **kf)

    section(ws, "F18:H18", "5  |  Indicative assessment")
    ok, bad = '"✓ Pass"', '"✗ Fail"'
    row(ws, 19, "Down payment at least the minimum", f"=IF(DownPct>=MinDownPct-0.00001,{ok},{bad})", None,
        '=ROUND(DownPct*100,1)&"%"&" vs min "&ROUND(MinDownPct*100,0)&"%"', **kf)
    row(ws, 20, "Loan stays within the LTV cap", f"=IF(MAX('{PR}'!S10:S25)<=LtvCap+0.00001,{ok},{bad})", None,
        f'="Peak LTV "&ROUND(MAX(\'{PR}\'!S10:S25)*100,1)&"%"&" vs cap "&ROUND(LtvCap*100,0)&"%"', **kf)
    row(ws, 21, "Payment-to-income within limit", f"=IF(G16<=MaxPTI,{ok},{bad})", None,
        '=ROUND(G16*100,1)&"%"&" vs max "&ROUND(MaxPTI*100,0)&"%"', **kf)
    row(ws, 22, "Car age at maturity of 15 yrs or less", f"=IF(CarAge+TermYrs<=15,{ok},{bad})", None,
        '=(CarAge+TermYrs)&" years at maturity"', **kf)
    row(ws, 23, "Outcome", '=IF(COUNTIF(G19:G22,"✓*")=4,"Indicative: OK","Refer to credit committee")', **kf)
    ws.Range("F23:G23").Font.Bold = True
    for addr in ("G19:G22",):
        rng = ws.Range(addr)
        cf(rng, '=LEFT(G19,1)="✓"').Font.Color = GREEN
        cf(rng, '=LEFT(G19,1)="✗"').Font.Color = RED
    out = ws.Range("G23")
    cf(out, '=G23="Indicative: OK"').Interior.Color = rgb(198, 239, 206)
    cf(out, '=G23<>"Indicative: OK"').Interior.Color = rgb(255, 199, 206)

    section(ws, "F25:H25", "6  |  Check against the ETL output")
    risk_db = "XLOOKUP(DbTier,tblRisk[RiskTier],tblRisk[RiskAdjustment])/100"
    row(ws, 26, "Source table", "=" + car.format("SourceTable"), None, **kf)
    row(ws, 27, "ETL estimated annual profit", "=" + car.format("EstimatedAnnualProfit"), "#,##0.00", **kf)
    row(ws, 28, "Recomputed: price x (index + margin)", f"=CarPrice*(IndexRate+EnergyMargin+{risk_db})", "#,##0.00",
        "No down payment, no discount", **kf)
    row(ws, 29, "ETL min. annual amortization", "=" + car.format("MinAnnualAmortization"), "#,##0.00", **kf)
    row(ws, 30, "Recomputed: price x depreciation", "=CarPrice*C13", "#,##0.00", **kf)
    row(ws, 31, "Status", '=IF(AND(ABS(G27-G28)<=1,ABS(G29-G30)<=1),"✓ Reconciles with ETL","✗ Differs: refresh data")', None, **kf)
    cf(ws.Range("G31"), '=LEFT(G31,1)="✓"').Font.Color = GREEN
    cf(ws.Range("G31"), '=LEFT(G31,1)="✗"').Font.Color = RED

    notes = ["Yellow cells are inputs; grey cells are calculated and locked. Sheet protection has no password.",
             "Interest is charged yearly on the opening balance, the same simple-interest rule as the ETL profit estimate.",
             "Inflation does not change the contractual rate or amortization; it indexes fees and income and deflates payments to today's SEK."]
    for i, t in enumerate(notes):
        c = ws.Range(f"B{41 + i}")
        c.Value, c.Font.Italic, c.Font.Size, c.Font.Color = t, True, 9, NOTE_FONT

    # Validation: dropdown for the car (and tier), sane ranges for the free inputs.
    ws.Range("C6").Validation.Add(3, 1, 1, "=CarList")
    wb.Names.Add(Name="TierList", RefersTo='=INDIRECT("tblRisk[RiskTier]")')
    ws.Range("C19").Validation.Add(3, 1, 1, "=TierList")
    ws.Range("C19").Validation.IgnoreBlank = True
    for addr, lo, hi in (("C23", 0, 0.05), ("C29", 0, 0.9), ("C33", 0.5, 1), ("C37", 0, 0.2),
                         ("C38", 0, 10000000), ("C39", 0, 1), ("C35", 0, 100000), ("C36", 0, 10000)):
        ws.Range(addr).Validation.Add(2, 1, 1, num(xl, lo), num(xl, hi))
    ws.Range("C34").Validation.Add(1, 1, 1, "1", "15")
    for addr in ("C6", "C19", "C23", "C29", "C33", "C34", "C35", "C36", "C37", "C38", "C39"):
        v = ws.Range(addr).Validation
        v.ErrorTitle, v.ErrorMessage = "Out of range", "Please enter a value inside the allowed range."

    return ws


def build_prognosis(wb):
    ws = wb.Worksheets(PR)
    ws.Cells.Font.Name, ws.Cells.Font.Size = "Segoe UI", 10
    ws.Columns("A").ColumnWidth = 2
    ws.Range("B:U").ColumnWidth = 13.5
    ws.Rows(1).RowHeight = 34
    style_band(ws.Range("B1:U1"), "Prognosis  |  15-year view of car value, loan and cash flow", 20)
    ws.Range("B3").Formula2 = '="Car: "&SelCar&"   |   rates and fees from the Loan Application sheet"'
    ws.Range("B3").Font.Bold = True
    strip = [("Loan amount", "=LoanAmt", "#,##0"), ("Down payment", "=DownSEK", "#,##0"), ("Customer rate", "=CustRate", "0.00%"),
             ("Index (STIBOR)", "=IndexRate", "0.00%"), ("Bank margin", "=BankMargin", "0.00%"), ("Inflation", "=Infl", "0.0%"),
             ("Term (years)", "=TermYrs", "0"), ("LTV cap", "=LtvCap", "0%"), ("Setup fee", "=SetupFee", "#,##0"),
             ("Admin fee / month", "=AdminFee", "#,##0")]
    for i, (lab, f, fmt) in enumerate(strip):
        col = chr(ord("B") + i)
        ws.Range(f"{col}4").Value = lab
        ws.Range(f"{col}4").Font.Size, ws.Range(f"{col}4").Font.Color = 9, NOTE_FONT
        c = ws.Range(f"{col}5")
        c.Formula2, c.Font.Bold, c.Interior.Color = f, True, GREY
        c.NumberFormatLocal = L(fmt)

    for rng, text in (("C8:G8", "Car"), ("H8:L8", "Loan"), ("M8:Q8", "Cash flow"), ("R8:T8", "Position at year end"), ("U8:U8", "IRR basis")):
        r = ws.Range(rng)
        if rng != "U8:U8":
            r.Merge()
        r.Cells(1, 1).Value = text
        r.Interior.Color, r.Font.Color, r.Font.Bold, r.HorizontalAlignment = NAVY, rgb(255, 255, 255), True, -4108
    heads = ["Year", "Car age", "Depreciation rate", "Car value, start", "Depreciation", "Car value, end",
             "Loan, opening", "Min. amortization", "Planned amortization", "Interest", "Interest / car value",
             "Fees", "Total payment", "Payment, today's SEK", "Payment / income", "Cumulative paid",
             "Loan, closing", "LTV", "Equity (value - loan)", "Customer net cash flow"]
    for i, h in enumerate(heads):
        c = ws.Cells(9, 2 + i)
        c.Value = h
    hr = ws.Range("B9:U9")
    hr.Interior.Color, hr.Font.Bold, hr.Font.Color, hr.WrapText, hr.HorizontalAlignment = BLUE_BAND, True, NAVY, True, -4108
    ws.Rows(9).RowHeight = 32

    ws.Range("B10").Value = 0
    ws.Range("C10").Formula2 = "=CarAge"
    ws.Range("G10").Formula2 = "=CarPrice"
    ws.Range("M10").Formula2 = "=SetupFee"  # setup fee is paid at signing, so it sits in year 0
    ws.Range("N10").Formula2 = "=M10"
    ws.Range("O10").Formula2 = "=N10"
    ws.Range("Q10").Formula2 = "=N10"
    ws.Range("R10").Formula2 = "=LoanAmt"
    ws.Range("S10").Formula2 = "=IF(G10>0,R10/G10,0)"
    ws.Range("T10").Formula2 = "=G10-R10"
    ws.Range("U10").Formula2 = "=R10-N10"
    for y in range(1, 16):
        r, p = 10 + y, 9 + y
        f = {
            "B": y,
            "C": f"=CarAge+B{r}-1",  # age at the start of the year drives that year's depreciation band
            "D": f"=XLOOKUP(C{r},tblDepr[MinAge],tblDepr[AnnualRate],0,-1)",
            "E": f"=G{p}", "F": f"=E{r}*D{r}", "G": f"=E{r}-F{r}",
            "H": f"=R{p}",
            "I": f"=MAX(0,H{r}-LtvCap*G{r})",  # amortization needed to end the year at or below the LTV cap
            "J": f"=MIN(H{r},MAX(I{r},IF(B{r}<=TermYrs,LoanAmt/TermYrs,H{r})))",
            "K": f"=H{r}*CustRate",
            "L": f"=IF(E{r}>0,K{r}/E{r},0)",
            "M": f"=IF(H{r}>0,AdminFee*12*(1+Infl)^(B{r}-1),0)",
            "N": f"=J{r}+K{r}+M{r}",
            "O": f"=N{r}/(1+Infl)^(B{r}-1)",  # year 1 = today's money
            "P": f"=IF(Income>0,N{r}/(Income*12*(1+Infl)^(B{r}-1)),0)",
            "Q": f"=Q{p}+N{r}",
            "R": f"=ROUND(H{r}-J{r},2)",
            "S": f"=IF(R{r}<=0,0,R{r}/G{r})",
            "T": f"=G{r}-R{r}",
            "U": f"=-N{r}",
        }
        for col, v in f.items():
            c = ws.Range(f"{col}{r}")
            if isinstance(v, str):
                c.Formula2 = v
            else:
                c.Value = v
    ws.Range("B26").Value = "Total"
    for col in "FJKMNO":
        ws.Range(f"{col}26").Formula = f"=SUM({col}{10 if col in 'MNO' else 11}:{col}25)"
    tot = ws.Range("B26:U26")
    tot.Font.Bold, tot.Interior.Color = True, BLUE_BAND
    tot.Borders(8).LineStyle = 1

    body = ws.Range("B10:U25")
    body.Interior.Color = GREY
    for rng, fmt in (("C10:C25", "0"), ("D10:D25", "0.0%"), ("E10:K26", "#,##0"), ("L10:L25", "0.0%"), ("M10:O26", "#,##0"),
                     ("P10:P25", "0.0%"), ("Q10:R25", "#,##0"), ("S10:S25", "0.0%"), ("T10:U25", "#,##0")):
        ws.Range(rng).NumberFormatLocal = L(fmt)
    ws.Range("B10:C25").HorizontalAlignment = -4108
    ws.Range("B10:U10").Font.Italic = True
    ws.Range("B27").Value = ("Year 0 is signing. Years 10-14 are grouped: use the + / - buttons at the left edge. "
                             "Car value falls by the age-band depreciation rate; interest is on the opening loan balance.")
    ws.Range("B27").Font.Italic, ws.Range("B27").Font.Size, ws.Range("B27").Font.Color = True, 9, NOTE_FONT
    # Red when the loan exceeds the car's value (negative equity), the thing a credit manager watches.
    ws.Range("T10:T25").FormatConditions.Add(1, 6, "=0").Font.Color = RED

    ws.Rows("20:24").Group()
    ws.Outline.ShowLevels(1)

    shp = ws.Shapes.AddChart2(227, 65, ws.Range("B29").Left, ws.Range("B29").Top, 760, 300)
    shp.Name = "ValueVsLoan"
    ch = shp.Chart
    while ch.SeriesCollection().Count:
        ch.SeriesCollection(1).Delete()
    for name, rng, color in (("Car value (end of year)", "G10:G25", NAVY), ("Loan balance (end of year)", "R10:R25", rgb(197, 90, 17))):
        s = ch.SeriesCollection().NewSeries()
        s.Name, s.Values, s.XValues = name, ws.Range(rng), ws.Range("B10:B25")
        s.Format.Line.ForeColor.RGB = color
        s.MarkerBackgroundColor = s.MarkerForegroundColor = color
    ch.PlotVisibleOnly = False  # keep the collapsed years 10-14 on the curve
    ch.HasTitle, ch.HasLegend = True, True
    ch.ChartTitle.Text = "Car value vs loan balance (SEK)"
    ch.Legend.Position = -4107
    ch.Axes(1).HasTitle = True
    ch.Axes(1).AxisTitle.Text = "Year"
    return ws


def build_scenarios(wb):
    ws = wb.Worksheets(SC)
    ws.Cells.Font.Name, ws.Cells.Font.Size = "Segoe UI", 10
    ws.Columns("A").ColumnWidth = 2
    ws.Range("B:I").ColumnWidth = 18
    ws.Rows(1).RowHeight = 34
    style_band(ws.Range("B1:I1"), "Scenarios  |  What if the index moves?", 20)
    ws.Range("B3").Formula2 = ('="Car: "&SelCar&"   |   base customer rate "&ROUND(CustRate*100,2)&"%"&", inflation "&ROUND(Infl*100,1)&"%"')
    ws.Range("B3").Font.Bold = True

    section(ws, "B5:I5", "A  |  Interest-rate shift of the index, -5 to +5 percentage points (amortization is unchanged)")
    heads = ["Rate shift (pp)", "Customer rate", "Interest, year 1", "Monthly payment, year 1", "Total interest",
             "Total cost of credit", "Total interest vs base", "Payment / income, year 1"]
    for i, h in enumerate(heads):
        ws.Cells(6, 2 + i).Value = h
    h = ws.Range("B6:I6")
    h.Interior.Color, h.Font.Bold, h.Font.Color, h.WrapText, h.HorizontalAlignment = NAVY, True, rgb(255, 255, 255), True, -4108
    ws.Rows(6).RowHeight = 32
    base = "IndexRate+BankMargin-RateDiscount"
    for i, s in enumerate(range(-5, 6)):
        r = 7 + i
        ws.Range(f"B{r}").Value = s / 100
        ws.Range(f"C{r}").Formula2 = f"=MAX(0,{base}+B{r})"  # same 0% floor as the application sheet
        ws.Range(f"D{r}").Formula2 = f"=C{r}*'{PR}'!$H$11"
        ws.Range(f"E{r}").Formula2 = f"=('{PR}'!$J$11+D{r}+'{PR}'!$M$11)/12"
        ws.Range(f"F{r}").Formula2 = f"=C{r}*SUM('{PR}'!$H$11:$H$25)"
        ws.Range(f"G{r}").Formula2 = f"=F{r}+'{PR}'!$M$26"
        ws.Range(f"H{r}").Formula2 = f"=F{r}-INDEX($F$7:$F$17,MATCH(0,$B$7:$B$17,0))"
        ws.Range(f"I{r}").Formula2 = f"=IF(Income>0,E{r}/Income,0)"
    body = ws.Range("B7:I17")
    body.Interior.Color = GREY
    ws.Range("B7:B17").NumberFormatLocal = L('+0.0%;-0.0%;0.0%')
    ws.Range("C7:C17").NumberFormatLocal = L("0.00%")
    ws.Range("D7:H17").NumberFormatLocal = L("#,##0;-#,##0;0")
    ws.Range("I7:I17").NumberFormatLocal = L("0.0%")
    ws.Range("B7:B17").HorizontalAlignment = -4108
    fc = cf(ws.Range("B7:I17"), "=$B7=0")
    fc.Interior.Color, fc.Font.Bold = rgb(255, 230, 153), True

    section(ws, "B20:I20", "B  |  Total payments in today's SEK: rate shift (rows) x inflation (columns)")
    ws.Range("B22").Value = "Rate shift \\ Inflation"
    for j in range(7):
        ws.Cells(22, 3 + j).Value = j / 100
    h = ws.Range("B22:I22")
    h.Interior.Color, h.Font.Bold, h.Font.Color, h.HorizontalAlignment = NAVY, True, rgb(255, 255, 255), -4108
    ws.Range("C22:I22").NumberFormatLocal = L("0%")
    pr = f"'{PR}'!"
    for i, s in enumerate(range(-5, 6)):
        r = 23 + i
        ws.Range(f"B{r}").Value = s / 100
        for j in range(7):
            col = chr(ord("C") + j)
            # Real cost = setup + sum of (amortization + interest)/(1+infl)^(year-1) + admin fee (its index cancels the deflator).
            ws.Range(f"{col}{r}").Formula2 = (
                f"={pr}$N$10+SUMPRODUCT(({pr}$J$11:$J$25+MAX(0,{base}+$B{r})*{pr}$H$11:$H$25)"
                f"/(1+{col}$22)^({pr}$B$11:$B$25-1))+AdminFee*12*COUNTIF({pr}$H$11:$H$25,\">0\")")
    ws.Range("B23:I33").Interior.Color = GREY
    ws.Range("B23:B33").NumberFormatLocal = L('+0.0%;-0.0%;0.0%')
    ws.Range("B23:B33").HorizontalAlignment = -4108
    ws.Range("C23:I33").NumberFormatLocal = L("#,##0")
    cf(ws.Range("B23:I33"), "=$B23=0").Interior.Color = rgb(255, 230, 153)
    cf(ws.Range("C22:I33"), "=AND(ROW()>=23,C$22=Infl)").Font.Bold = True
    ws.Range("B34").Value = "Highlighted row = today's rate; bold column = the inflation set on the Loan Application sheet. Higher inflation lowers the real burden of a fixed-rate loan."
    ws.Range("B34").Font.Italic, ws.Range("B34").Font.Size, ws.Range("B34").Font.Color = True, 9, NOTE_FONT

    shp = ws.Shapes.AddChart2(201, 51, ws.Range("B36").Left, ws.Range("B36").Top, 720, 280)
    shp.Name = "InterestByShift"
    ch = shp.Chart
    while ch.SeriesCollection().Count:
        ch.SeriesCollection(1).Delete()
    s = ch.SeriesCollection().NewSeries()
    s.Name, s.Values, s.XValues = "Total interest (SEK)", ws.Range("F7:F17"), ws.Range("B7:B17")
    s.Format.Fill.ForeColor.RGB = NAVY
    ch.HasTitle, ch.HasLegend = True, False
    ch.ChartTitle.Text = "Total interest over the term by index shift (SEK)"
    ch.Axes(1).HasTitle = True
    ch.Axes(1).AxisTitle.Text = "Index shift (pp)"
    return ws


def finish_sheet(xl, ws, freeze=None, protect=False):
    ws.Activate()
    xl.ActiveWindow.DisplayGridlines = False
    if freeze:
        ws.Range(freeze).Select()
        xl.ActiveWindow.FreezePanes = True
    ws.Range("A1").Select()
    try:
        ws.PageSetup.Orientation = 2
        ws.PageSetup.Zoom = False
        ws.PageSetup.FitToPagesWide = 1
        ws.PageSetup.FitToPagesTall = False
    except Exception:
        pass  # no default printer: page setup is cosmetic
    if protect:
        ws.Protect(DrawingObjects=True, Contents=True, Scenarios=True, AllowFormattingColumns=True)


def build():
    OUT.parent.mkdir(exist_ok=True)
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible = True  # first refresh may ask once for credentials / encryption; the user answers in the window
    xl.DisplayAlerts = False
    init_locale(xl)
    try:
        wb = xl.Workbooks.Add()
        for s in list(wb.Worksheets)[1:]:
            s.Delete()
        wb.Worksheets(1).Name = LA
        # Create every sheet up front: formulas on one sheet point at the others.
        wb.Worksheets.Add(After=wb.Worksheets(LA)).Name = PR
        wb.Worksheets.Add(After=wb.Worksheets(PR)).Name = SC
        data = build_data(wb, xl)
        cars = data.ListObjects("tblCars")
        ids, lbls = cars.ListColumns("RecordID").DataBodyRange.Value, cars.ListColumns("Label").DataBodyRange.Value
        default_car = next((l[0] for i, l in zip(ids, lbls) if int(i[0]) == 2), lbls[0][0])
        app = build_application(wb, xl, default_car)
        prog = build_prognosis(wb)
        scen = build_scenarios(wb)
        xl.Calculate()
        data.Visible = 0
        finish_sheet(xl, app, protect=True)
        finish_sheet(xl, prog, freeze="C10")  # left unprotected so the year 10-14 outline buttons work
        finish_sheet(xl, scen, protect=True)
        app.Tab.Color, prog.Tab.Color, scen.Tab.Color = NAVY, rgb(47, 117, 181), rgb(197, 90, 17)
        app.Activate()
        wb.SaveAs(str(OUT), FileFormat=51)
        print(f"saved {OUT}")
    except Exception:
        xl.Quit()
        raise
    wb.Close(False)  # saved above; closing lets --check (or the user) open the file
    xl.Quit()


def sql():
    auth = ("Trusted_Connection=yes;" if not os.environ.get("SQL_UID")
            else f"UID={os.environ['SQL_UID']};PWD={os.environ['SQL_PWD']};")
    return pyodbc.connect(f"DRIVER={{{os.environ.get('SQL_DRIVER', 'ODBC Driver 18 for SQL Server')}}};"
                          f"SERVER={SERVER};DATABASE={DATABASE};{auth}TrustServerCertificate=yes;")


def check():
    """Drive the saved workbook like a credit manager and compare with what SQL says."""
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible, xl.DisplayAlerts = False, False
    try:
        wb = xl.Workbooks.Open(str(OUT))
        app, prog, scen = wb.Worksheets(LA), wb.Worksheets(PR), wb.Worksheets(SC)
        cars = wb.Worksheets(DATA).ListObjects("tblCars")
        ids = cars.ListColumns("RecordID").DataBodyRange.Value
        lbl = cars.ListColumns("Label").DataBodyRange.Value
        labels = {int(i[0]): l[0] for i, l in zip(ids, lbl)}
        table = app.Range("G26").Value
        con = sql()
        rows = {r.RecordID: r for r in con.cursor().execute(
            f"SELECT RecordID, TotalInterestRate, EstimatedAnnualProfit, MinAnnualAmortization FROM dbo.[{table}]")}

        def setv(name, v):
            wb.Names(name).RefersToRange.Value = v

        failures = []

        def expect(what, got, want, tol=0.011):
            ok = abs(float(got) - float(want)) <= tol
            print(("ok   " if ok else "FAIL ") + f"{what}: workbook {got} vs expected {want}")
            if not ok:
                failures.append(what)

        # With no down payment, no fees and a 100% LTV cap the desk must reproduce the ETL row exactly.
        for rid in (1, 2, 3):
            setv("SelCar", labels[rid]); setv("DownPct", 0); setv("LtvCap", 1); setv("TierOverride", "")
            setv("RateDiscount", 0); setv("SetupFee", 0); setv("AdminFee", 0); setv("TermYrs", 5); setv("Infl", 0.02)
            xl.Calculate()
            e = rows[rid]
            expect(f"#{rid} customer rate %", wb.Names("CustRate").RefersToRange.Value * 100, e.TotalInterestRate, 1e-6)
            expect(f"#{rid} year-1 interest", prog.Range("K11").Value, e.EstimatedAnnualProfit)
            expect(f"#{rid} min amortization", prog.Range("I11").Value, e.MinAnnualAmortization)
            expect(f"#{rid} IRR = rate when fee-free", app.Range("G15").Value, wb.Names("CustRate").RefersToRange.Value, 1e-6)
            if app.Range("G31").Value.startswith("✗"):
                failures.append(f"#{rid} reconciliation banner")

        base_min = prog.Range("I11").Value
        setv("DownPct", 0.2); xl.Calculate()
        ok = prog.Range("I11").Value < base_min
        print(("ok   " if ok else "FAIL ") + f"20% down lowers min amortization: {base_min} -> {prog.Range('I11').Value}")
        if not ok:
            failures.append("down payment effect")

        setv("DownPct", 0.2); setv("SetupFee", 600); setv("AdminFee", 35); xl.Calculate()
        expect("scenario 0pp total interest = prognosis", scen.Range("F12").Value, prog.Range("K26").Value)
        expect("matrix (0pp, 2%) = prognosis real total", scen.Range("E28").Value, prog.Range("O26").Value)
        expect("IRR above nominal rate once fees apply", app.Range("G15").Value > wb.Names("CustRate").RefersToRange.Value, 1, 0)

        for ws in (app, prog, scen):
            try:
                errs = ws.UsedRange.SpecialCells(-4123, 16)
                print(f"FAIL error cells on {ws.Name}: {errs.Address}")
                failures.append(f"errors on {ws.Name}")
            except Exception:
                print(f"ok   no error cells on {ws.Name}")
        wb.Close(SaveChanges=False)
    finally:
        xl.Quit()
    if failures:
        sys.exit(f"{len(failures)} check(s) failed: {failures}")
    print("all checks passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify the saved workbook against SQL instead of building")
    if ap.parse_args().check:
        check()
    else:
        build()
