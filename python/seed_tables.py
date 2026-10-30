"""Seed LoanETL with source data for Assignment 1.

- InterestRates: STIBOR 3M, one row per month = the fixing on the last day of the previous month (the latest
  one published before the ETL runs on the 1st). Read from the SFBF export in the data folder (the site blocks scripts);
  without that file it falls back to the Riksbank SSVX 3M T-bill rate, labelled as such in RateSource.
- Lookup tables: energy class margins, credit risk tiers, depreciation bands (annual rate by car age).
- CarInformation_yyyymm: real model/fuel data from fueleconomy.gov, synthetic prices and
  risk tiers (fixed random seed), with a few deliberately dirty rows for the validation step.

Safe to re-run: every table is cleared/recreated before loading.
"""
import argparse
import csv
import io
import os
import random
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import pyodbc
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
VEHICLES_URL = "https://www.fueleconomy.gov/feg/epadata/vehicles.csv.zip"
# SSVX 3M T-bill: open API, used as a STIBOR 3M proxy when the SFBF export is missing (e.g. in CI).
SSVX_URL = "https://api.riksbank.se/swea/v1/Observations/SETB3MBENCH/{start}/{end}"
STIBOR_SHEET = "Monthly statistics"

MARGINS = [("Electric", 1.0), ("Hybrid", 1.5), ("Gasoline", 2.5), ("Diesel", 2.5)]
RISK_TIERS = [("A", 0.00), ("B", 0.50), ("C", 1.00), ("D", 2.00)]
# (MinAge, MaxAge, share of value lost over the coming year); MaxAge None = open-ended.
DEPRECIATION = [(0, 2, 0.15), (3, 5, 0.12), (6, 9, 0.10), (10, None, 0.07)]
# Cars sampled per energy class per monthly file, and a rough SEK price range per class.
SAMPLE = {"Gasoline": 70, "Diesel": 25, "Hybrid": 45, "Electric": 40}
PRICE_RANGE = {"Gasoline": (180_000, 450_000), "Diesel": (220_000, 500_000),
               "Hybrid": (280_000, 550_000), "Electric": (350_000, 700_000)}


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def connect():
    """Windows auth by default; SQL auth when SQL_UID/SQL_PWD are set (used by CI)."""
    auth = ("Trusted_Connection=yes;" if not os.environ.get("SQL_UID")
            else f"UID={os.environ['SQL_UID']};PWD={os.environ['SQL_PWD']};")
    conn_str = (f"DRIVER={{{os.environ.get('SQL_DRIVER', 'ODBC Driver 18 for SQL Server')}}};"
                f"SERVER={os.environ.get('SQL_SERVER', 'localhost')};"
                f"DATABASE={os.environ.get('SQL_DATABASE', 'LoanETL')};"
                f"{auth}TrustServerCertificate=yes;")
    return pyodbc.connect(conn_str, autocommit=False)


def month_add(d, n):
    index = d.year * 12 + d.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def find_stibor_export(explicit):
    """The SFBF 'Summary Statistics' workbook, saved by hand into data\\ (newest wins)."""
    if explicit:
        return Path(explicit)
    files = sorted(DATA.glob("STIBOR*.xlsx"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def read_stibor_rates(path, first_month):
    """SFBF monthly sheet -> {1st of month: 3M fixing on the last day of the month before}.

    The job runs on the 1st at 06:00, before that day's fixing exists, so the previous month-end
    fixing is the latest one a real run could have used.
    """
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)[STIBOR_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    header = next(r for r in rows if r and r[0] == "Date")
    col = header.index("3 Months")          # first block = fixing on the last day of the month
    rates = {}
    for r in rows:
        day, value = r[0], r[col]
        if isinstance(day, (int, float)):   # unformatted cells arrive as Excel serial numbers
            day = datetime(1899, 12, 30) + timedelta(days=day)
        if isinstance(day, datetime) and isinstance(value, (int, float)):
            rates[month_add(date(day.year, day.month, 1), 1)] = round(value, 2)
    return {m: v for m, v in rates.items() if m >= first_month}


def fetch_ssvx_rates(first_month, today):
    """Daily SSVX 3M -> the last observation before the 1st of each month (same rule as STIBOR)."""
    url = SSVX_URL.format(start=month_add(first_month, -1).isoformat(), end=today.isoformat())
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    obs = sorted(response.json(), key=lambda o: o["date"])
    rates = {}
    month = first_month
    while month <= today:
        prior = [o["value"] for o in obs if o["date"] < month.isoformat()]
        if prior:
            rates[month] = round(prior[-1], 2)
        month = month_add(month, 1)
    return rates


def monthly_rates(args, first_month, today):
    """-> (rates, RateSource). STIBOR when the export is available, otherwise SSVX with a warning."""
    if args.rate_source == "stibor":
        path = find_stibor_export(args.stibor_file)
        if path and path.exists():
            print(f"STIBOR 3M from {path.name}")
            return read_stibor_rates(path, first_month), "STIBOR3M"
        print("WARNING: no STIBOR export in data\\ - using Riksbank SSVX 3M as a proxy.")
    return fetch_ssvx_rates(first_month, today), "SSVX3M"


def energy_class(row):
    atv, fuel = row["atvType"], row["fuelType1"]
    if fuel == "Electricity" or atv == "EV":
        return "Electric"
    if "Hybrid" in atv:
        return "Hybrid"
    if fuel == "Diesel" or atv == "Diesel":
        return "Diesel"
    return "Gasoline"


def load_vehicle_pool():
    DATA.mkdir(exist_ok=True)
    zip_path = DATA / "vehicles.csv.zip"
    if not zip_path.exists():
        zip_path.write_bytes(requests.get(VEHICLES_URL, timeout=120).content)
    pool = {cls: {} for cls in SAMPLE}
    with zipfile.ZipFile(zip_path) as z, z.open("vehicles.csv") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
            year = int(row["year"])
            if year >= 2015:
                pool[energy_class(row)][(f"{row['make']} {row['model']}", year)] = None
    return {cls: sorted(models) for cls, models in pool.items()}


def build_car_rows(pool, month):
    """One monthly agency file. Seeded per month so re-runs give identical data."""
    rng = random.Random(42 + month.year * 100 + month.month)
    cars = []
    for cls, count in SAMPLE.items():
        for model, year in rng.sample(pool[cls], min(count, len(pool[cls]))):
            low, high = PRICE_RANGE[cls]
            # Today's purchase price: newer models cost more, older ones are already depreciated.
            price =round(rng.uniform(low, high) * (0.85 + 0.03 * (year - 2015)), 2)
            tier = rng.choices("ABCD", weights=[40, 30, 20, 10])[0]
            cars.append([model, cls, year, price, month, tier])
    rng.shuffle(cars)
    rows = [[i + 1, *car] for i, car in enumerate(cars)]
    # Dirty rows an agency file realistically contains; the ETL must clean or reject them.
    for row in rng.sample(rows, 8):
        row[2] = rng.choice([row[2].lower(), row[2].upper(), f"  {row[2]} "])  # casing / padding
    rows[rng.randrange(len(rows))][4] = None                                  # missing price
    rows[rng.randrange(len(rows))][4] = 0                                     # non-positive price
    rows[rng.randrange(len(rows))][6] = "X"                                   # unknown risk tier
    rows[rng.randrange(len(rows))][2] = "Steam"                               # unknown energy class
    return rows


def seed(args):
    today = date.today()
    this_month = date(today.year, today.month, 1)
    skip = {date(int(s[:4]), int(s[4:]), 1) for s in args.skip_rate}

    rates, rate_source = monthly_rates(args, month_add(this_month, -args.rate_months), today)
    rates = {m: r for m, r in rates.items() if m not in skip}
    pool = load_vehicle_pool()

    conn = connect()
    cur = conn.cursor()
    cur.fast_executemany = True

    cur.execute("TRUNCATE TABLE dbo.InterestRates")
    cur.executemany("INSERT INTO dbo.InterestRates (EffectiveDate, BaseInterestRate, RateSource) VALUES (?, ?, ?)",
                    [(month, rate, rate_source) for month, rate in sorted(rates.items())])
    cur.execute("TRUNCATE TABLE dbo.EnergyClassMargin")
    cur.executemany("INSERT INTO dbo.EnergyClassMargin (EnergyClass, MarginRate) VALUES (?, ?)", MARGINS)
    cur.execute("TRUNCATE TABLE dbo.CreditRiskTier")
    cur.executemany("INSERT INTO dbo.CreditRiskTier (RiskTier, RiskAdjustment) VALUES (?, ?)", RISK_TIERS)
    cur.execute("TRUNCATE TABLE dbo.DepreciationRates")
    cur.executemany("INSERT INTO dbo.DepreciationRates (MinAge, MaxAge, AnnualRate) VALUES (?, ?, ?)",
                    DEPRECIATION)

    for n in range(args.car_months):
        month = month_add(this_month, -n)
        table = f"dbo.CarInformation_{month:%Y%m}"
        rows = build_car_rows(pool, month)
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        # No FKs to the lookup tables (as in the ERD): the agency file must be able to hold bad values.
        cur.execute(f"""CREATE TABLE {table} (
            RecordID INT NOT NULL PRIMARY KEY, CarModel VARCHAR(100) NULL, EnergyClass VARCHAR(20) NULL,
            ManufactureYear INT NULL, BasePrice DECIMAL(10,2) NULL, FileMonth DATE NULL,
            CustomerRiskTier VARCHAR(20) NULL)""")
        cur.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        print(f"{table}: {len(rows)} rows")
    conn.commit()
    print(f"InterestRates ({rate_source}): {len(rates)} months ({min(rates):%Y-%m} to {max(rates):%Y-%m})"
          + (f", skipped {sorted(f'{m:%Y%m}' for m in skip)}" if skip else ""))


if __name__ == "__main__":
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--car-months", type=int, default=2, help="monthly car files to create (current backwards)")
    parser.add_argument("--rate-months", type=int, default=18, help="months of rate history to load")
    parser.add_argument("--rate-source", choices=["stibor", "ssvx"], default="stibor",
                        help="stibor = SFBF export in data\\ (falls back to ssvx if missing); ssvx = Riksbank API only")
    parser.add_argument("--stibor-file", metavar="XLSX", help="SFBF workbook; default: newest data\\STIBOR*.xlsx")
    parser.add_argument("--skip-rate", nargs="*", default=[], metavar="YYYYMM",
                        help="leave these months out of InterestRates to exercise the fallback")
    seed(parser.parse_args())
