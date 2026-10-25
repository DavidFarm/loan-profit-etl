"""Seed LoanETL with source data for Assignment 1.

- InterestRates: Riksbank policy rate (SECBREPOEFF), one row per month.
- Lookup tables: energy class margins, credit risk tiers, depreciation bands.
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
from datetime import date
from pathlib import Path

import pyodbc
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
VEHICLES_URL = "https://www.fueleconomy.gov/feg/epadata/vehicles.csv.zip"
RIKSBANK_URL = "https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF/{start}/{end}"

MARGINS = [("Electric", 1.0), ("Hybrid", 1.5), ("Gasoline", 2.5), ("Diesel", 2.5)]
RISK_TIERS = [("A", 0.00), ("B", 0.50), ("C", 1.00), ("D", 2.00)]
DEPRECIATION = [(2024, 2026, 0.10), (2020, 2023, 0.20), (2015, 2019, 0.35), (1980, 2014, 0.50)]
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


def fetch_monthly_rates(first_month, today):
    """Daily policy rate -> the rate in force on the first observation of each month."""
    url = RIKSBANK_URL.format(start=first_month.isoformat(), end=today.isoformat())
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    rates = {}
    for obs in sorted(response.json(), key=lambda o: o["date"]):
        rates.setdefault(obs["date"][:7], obs["value"])
    return {date.fromisoformat(f"{k}-01"): v for k, v in rates.items()}


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
            price = round(rng.uniform(low, high) * (0.85 + 0.03 * (year - 2015)), 2)
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

    rates = fetch_monthly_rates(month_add(this_month, -args.rate_months), today)
    rates = {m: r for m, r in rates.items() if m not in skip}
    pool = load_vehicle_pool()

    conn = connect()
    cur = conn.cursor()
    cur.fast_executemany = True

    cur.execute("TRUNCATE TABLE dbo.InterestRates")
    cur.executemany("INSERT INTO dbo.InterestRates (EffectiveDate, BaseInterestRate) VALUES (?, ?)",
                    sorted(rates.items()))
    cur.execute("TRUNCATE TABLE dbo.EnergyClassMargin")
    cur.executemany("INSERT INTO dbo.EnergyClassMargin (EnergyClass, MarginRate) VALUES (?, ?)", MARGINS)
    cur.execute("TRUNCATE TABLE dbo.CreditRiskTier")
    cur.executemany("INSERT INTO dbo.CreditRiskTier (RiskTier, RiskAdjustment) VALUES (?, ?)", RISK_TIERS)
    cur.execute("TRUNCATE TABLE dbo.DepreciationRates")
    cur.executemany("INSERT INTO dbo.DepreciationRates (MinYear, MaxYear, DepreciationRate) VALUES (?, ?, ?)",
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
    print(f"InterestRates: {len(rates)} months ({min(rates):%Y-%m} to {max(rates):%Y-%m})"
          + (f", skipped {sorted(f'{m:%Y%m}' for m in skip)}" if skip else ""))


if __name__ == "__main__":
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--car-months", type=int, default=2, help="monthly car files to create (current backwards)")
    parser.add_argument("--rate-months", type=int, default=18, help="months of rate history to load")
    parser.add_argument("--skip-rate", nargs="*", default=[], metavar="YYYYMM",
                        help="leave these months out of InterestRates to exercise the fallback")
    seed(parser.parse_args())
