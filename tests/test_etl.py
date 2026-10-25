"""End-to-end tests for dbo.usp_LoanProfitEstimates.

Each test builds its own tiny CarInformation file for a far-future month (2030-xx) with known values,
so the real seed data is never touched; the fixture removes everything it created afterwards.
Requires the lookups to be seeded (python/seed_tables.py). Mail uses a non-existent profile so no
mail is sent and the tests behave the same locally and in CI.
"""
import sys
from pathlib import Path

import pyodbc
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from seed_tables import connect, load_env  # noqa: E402

NO_MAIL = "no_such_profile"

# Four good rows (one with messy casing/padding) and five that must be rejected, one per rule.
CAR_ROWS = [
    (1, "Test EV", " electric ", 2024, 400000.00, "a"),
    (2, "Test Hybrid", "Hybrid", 2022, 300000.00, "B"),
    (3, "Test Gas", "Gasoline", 2020, 200000.00, "C"),
    (4, "Test Diesel", "Diesel", 2019, 250000.00, "D"),
    (5, "Bad price", "Gasoline", 2020, None, "A"),
    (6, "Bad class", "Steam", 2020, 100000.00, "A"),
    (7, "Bad tier", "Gasoline", 2020, 100000.00, "X"),
    (8, "Zero price", "Gasoline", 2020, 0, "A"),
    (9, "Bad year", "Gasoline", 1900, 100000.00, "A"),
]
# Expected profit = BasePrice * (rate + margin) / 100, with a 3.00% base rate.
EXPECTED_PROFIT_AT_3_PCT = {1: 16000.00, 2: 13500.00, 3: 11000.00, 4: 13750.00}


@pytest.fixture(scope="module")
def db():
    load_env()
    conn = connect()
    conn.autocommit = True
    yield conn
    cur = conn.cursor()
    for yyyymm in ("203001", "203002", "203003"):
        cur.execute(f"DROP TABLE IF EXISTS dbo.CarInformation_{yyyymm}; DROP TABLE IF EXISTS dbo.LoanProfitEstimates_{yyyymm};")
    cur.execute("DELETE FROM dbo.InterestRates WHERE EffectiveDate >= '2029-12-01'")
    cur.execute("DELETE FROM dbo.EtlLog WHERE RunMonth >= '2029-12-01'")
    conn.close()


def make_car_file(db, yyyymm):
    cur = db.cursor()
    cur.execute(f"DROP TABLE IF EXISTS dbo.CarInformation_{yyyymm}")
    cur.execute(f"""CREATE TABLE dbo.CarInformation_{yyyymm} (
        RecordID INT NOT NULL PRIMARY KEY, CarModel VARCHAR(100), EnergyClass VARCHAR(20),
        ManufactureYear INT, BasePrice DECIMAL(10,2), FileMonth DATE, CustomerRiskTier VARCHAR(20))""")
    for rid, model, cls, year, price, tier in CAR_ROWS:
        cur.execute(f"INSERT INTO dbo.CarInformation_{yyyymm} VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rid, model, cls, year, price, f"{yyyymm[:4]}-{yyyymm[4:]}-01", tier)


def set_rate(db, effective_date, rate):
    cur = db.cursor()
    cur.execute("DELETE FROM dbo.InterestRates WHERE EffectiveDate = ?", effective_date)
    cur.execute("INSERT INTO dbo.InterestRates (EffectiveDate, BaseInterestRate) VALUES (?, ?)", effective_date, rate)


def run_etl(db, month):
    db.cursor().execute("EXEC dbo.usp_LoanProfitEstimates @RunMonth = ?, @MailProfile = ?", month, NO_MAIL)


def last_run_log(db, month):
    rows = db.cursor().execute(
        "SELECT Severity, Step, Message, [Rows] FROM dbo.EtlLog "
        "WHERE RunID = (SELECT TOP 1 RunID FROM dbo.EtlLog WHERE RunMonth = ? ORDER BY LogID DESC) ORDER BY LogID",
        month).fetchall()
    return rows


def test_rate_present_loads_clean_rows_without_fallback_warning(db):
    make_car_file(db, "203002")
    set_rate(db, "2030-02-01", 3.00)
    run_etl(db, "2030-02-01")

    log = last_run_log(db, "2030-02-01")
    assert not any(r.Step == "INTEREST_RATE" and r.Severity == "WARNING" for r in log)
    assert db.cursor().execute("SELECT COUNT(*) FROM dbo.LoanProfitEstimates_203002").fetchval() == 4


def test_missing_rate_falls_back_to_previous_month_and_warns(db):
    make_car_file(db, "203001")
    set_rate(db, "2029-12-01", 3.00)          # December only; January has no rate
    run_etl(db, "2030-01-01")

    warnings = [r.Message for r in last_run_log(db, "2030-01-01") if r.Step == "INTEREST_RATE"]
    assert len(warnings) == 1
    assert warnings[0].startswith("Base interest rate for the current month is missing. Value from the previous month was used")
    assert "2029-12-01" in warnings[0]


def test_estimated_profit_matches_formula(db):
    profits = dict(db.cursor().execute("SELECT RecordID, EstimatedProfit FROM dbo.LoanProfitEstimates_203001").fetchall())
    assert {k: float(v) for k, v in profits.items()} == EXPECTED_PROFIT_AT_3_PCT


def test_energy_class_is_normalised(db):
    # " electric " (padded, lower case) must come out as the canonical 'Electric' with its 1.0% margin.
    row = db.cursor().execute("SELECT EnergyClass, BankMargin FROM dbo.LoanProfitEstimates_203001 WHERE RecordID = 1").fetchone()
    assert row.EnergyClass == "Electric" and float(row.BankMargin) == 1.0


def test_rejects_are_flagged_with_reasons_and_logged(db):
    reasons = dict(db.cursor().execute(
        "SELECT RecordID_Clean, RejectReason FROM dbo.Stg_CarInformation WHERE RejectReason IS NOT NULL").fetchall())
    assert reasons == {
        5: "Missing or non-positive BasePrice",
        6: "Unknown EnergyClass",
        7: "Unknown CustomerRiskTier",
        8: "Missing or non-positive BasePrice",
        9: "Invalid ManufactureYear",
    }
    validate_rows = sum(r.Rows for r in last_run_log(db, "2030-01-01") if r.Step == "VALIDATE")
    assert validate_rows == 5


def test_rerun_is_idempotent(db):
    cur = db.cursor()
    before = cur.execute("SELECT COUNT(*), COUNT(DISTINCT RecordID) FROM dbo.LoanProfitEstimates_203001").fetchone()
    run_etl(db, "2030-01-01")
    after = cur.execute("SELECT COUNT(*), COUNT(DISTINCT RecordID) FROM dbo.LoanProfitEstimates_203001").fetchone()
    assert tuple(before) == tuple(after) == (4, 4)


def test_missing_source_table_logs_error_and_raises(db):
    with pytest.raises(pyodbc.Error) as exc:
        run_etl(db, "2030-03-01")
    assert "CarInformation_203003 table for the current month not found." in str(exc.value)

    log = last_run_log(db, "2030-03-01")
    assert any(r.Severity == "ERROR" and "CarInformation_203003" in r.Message for r in log)
    assert db.cursor().execute("SELECT OBJECT_ID('dbo.LoanProfitEstimates_203003')").fetchval() is None


def test_mail_failure_is_logged_but_does_not_fail_the_run(db):
    # Every run here uses a non-existent mail profile, so the earlier runs already hit this path.
    mail_warnings = [r for r in last_run_log(db, "2030-01-01") if r.Step == "MAIL"]
    assert len(mail_warnings) == 1 and mail_warnings[0].Severity == "WARNING"
    assert "Confirmation email could not be sent" in mail_warnings[0].Message
