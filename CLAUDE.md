# ETL Assignment 1 - Banking Loan Profit Estimates

Assignment spec: `Documents/ETL_Assignment_1.docx` (read-only, do not edit). Target grade: **VG** (includes G).

## What the job must do
Monthly T-SQL ETL in SSMS / SQL Server that:
1. Picks `CarInformation_yyyymm` dynamically for the current month (log + stop if missing).
2. Ingests it into a staging table, validates/transforms (types, formats).
3. Looks up the base interest rate for the current month; if missing, falls back to the most recent earlier month and logs a warning.
4. Creates `LoanProfitEstimates_yyyymm` (analyst runs `SELECT * FROM LoanProfitEstimates_yyyymm;`).
5. Logs errors/warnings with descriptive messages to a log table.
6. Sends a Database Mail confirmation (success/failure, rows processed, output table name).

Formula: `EstimatedProfit = BasePrice * TotalInterestRate / 100`, `TotalInterestRate = BaseInterestRate + BankMargin`.
Margins: Electric 1.0, Hybrid 1.5, Gasoline/Diesel 2.5 (`EnergyClassMargin`).
Other tables in the ERD: `InterestRates`, `CreditRiskTier`, `DepreciationRates`, `CarInformation_yyyymm`.

## Environment
- SQL Server 2022 Developer, local default instance `localhost`, Windows auth. SQL Agent service exists but may be stopped.
- Dedicated database `LoanETL` (the pre-existing `ETL` database belongs to other exercises - do not touch it).
- Python 3.11 venv in `.venv` (`requests`, `pyodbc`) for seeding only. Run Python with `.venv\Scripts\python.exe`.
- Shell is PowerShell on Windows; `sqlcmd` is on PATH.

## Layout
- `sql/` numbered scripts, each **idempotent** (safe to re-run): 00 database, 01 mail, 02 tables, 03 ETL procedure, 04 Agent job, 05 data-quality views, 06 rate-sensitivity view.
- `python/seed_tables.py` loads seed data into `LoanETL`; `smtp_sink.py` local mail; `make_charts.py` / `make_proof.py` render `docs/` images from live data.
- `tests/test_etl.py` end-to-end pytest suite (own 2030-xx test months, cleans up after itself; it overwrites staging, so re-run the ETL for the current month afterwards if you need the real portfolio in `vw_RateSensitivity`).
- `.github/workflows/ci.yml` runs scripts 00/02/03/05/06, the seed and pytest against a SQL Server container (SQL auth via `SQL_UID`/`SQL_PWD`).
- `marketing/` HTML sources for the carousel, deck and social preview. Render PDFs with headless Chrome using an **absolute** `--print-to-pdf` path.
- `data/` downloaded source files and saved mail (git-ignored). `Documents/` holds the course brief (git-ignored, not ours to publish).

## Commands
Run from the project root; all SQL scripts are idempotent and run in order 00-04.
```
sqlcmd -S localhost -E -b -I -i sql\00_create_database.sql      # then 01, 02, 03, 04
.venv\Scripts\python.exe python\seed_tables.py [--skip-rate 202610]   # lookups + CarInformation_yyyymm
.venv\Scripts\python.exe python\smtp_sink.py                     # local mail sink, port 2525 -> data\mail\*.eml
sqlcmd -S localhost -E -d LoanETL -Q "EXEC dbo.usp_LoanProfitEstimates [@RunMonth='2026-09-01']"
```
Log: `SELECT * FROM LoanETL.dbo.EtlLog ORDER BY LogID`. Mail: Database Mail profile `LoanETL` -> localhost:2525 (no real mailbox).
The Agent job `LoanETL - Monthly LoanProfitEstimates` runs on day 1 at 06:00 and needs the SQL Server Agent service running (starting it needs an elevated shell).

## Data sources (the teacher gave none)
- Base interest rate: Riksbank open API, no key. Series `SECBREPOEFF` (policy rate), `https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF/{from}/{to}`. Daily values, aggregated to one row per month in `InterestRates`.
- Car models/energy class: US fueleconomy.gov `vehicles.csv` (fuel type, year, model). It has no price, so `BasePrice` and `CustomerRiskTier` are synthesized with a fixed random seed.
- Seed at least two months of `CarInformation_yyyymm` (current + previous) and leave one interest-rate month absent so the fallback can be tested.

## Conventions
- Match existing code style; comment the *why* (business rules, fallbacks), not the obvious.
- Dynamic SQL must use `QUOTENAME` and `sp_executesql` with parameters.
- Do not send real email during development unless the user asks; default to a test recipient they provide.
- Ask before changing server-level settings (start Agent, enable Database Mail, `sp_configure`).
