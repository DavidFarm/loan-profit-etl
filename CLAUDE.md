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

Formula: `EstimatedAnnualProfit = BasePrice * TotalInterestRate / 100`, `TotalInterestRate = BaseInterestRate + BankMargin`,
`BankMargin = EnergyMargin + RiskAdjustment` (the brief names only the energy margin; the risk tier is priced on purpose, user decision).
Margins: Electric 1.0, Hybrid 1.5, Gasoline/Diesel 2.5 (`EnergyClassMargin`); risk A/B/C/D 0/0.5/1.0/2.0 (`CreditRiskTier`).
`BasePrice` = today's purchase price = amount financed. `DepreciationRates` = annual rate by car age (`MinAge`/`MaxAge`/`AnnualRate`);
the output adds `CarAge`, `DepreciationRate`, `DepreciatedValue`, `MinAnnualAmortization`, `CustomerAnnualPayment` (informational, no effect on margin/profit).
Other tables in the ERD: `InterestRates`, `CreditRiskTier`, `DepreciationRates`, `CarInformation_yyyymm`.

## Environment
- SQL Server 2022 Developer, local default instance `localhost`, Windows auth. SQL Agent service exists but may be stopped.
- Dedicated database `LoanETL` (the pre-existing `ETL` database belongs to other exercises - do not touch it).
- Python 3.11 venv in `.venv` (`requests`, `pyodbc`, `openpyxl`) for seeding only. Run Python with `.venv\Scripts\python.exe`.
- Shell is PowerShell on Windows; `sqlcmd` is on PATH.

## Layout
- `sql/` numbered scripts, each **idempotent** (safe to re-run): 00 database, 01 mail (local sink), 01b optional live Gmail profile + recipient in `EtlSettings` (needs `-v GmailUser/GmailAppPassword/MailTo`; the user runs it, never CI), 02 tables, 03 ETL procedure, 04 Agent job, 05 data-quality views, 06 rate-sensitivity view.
- `python/seed_tables.py` loads seed data into `LoanETL`; `smtp_sink.py` local mail; `make_charts.py` / `make_proof.py` render `docs/` images from live data.
- `tests/test_etl.py` end-to-end pytest suite (own 2030-xx test months, cleans up after itself; it overwrites staging, so re-run the ETL for the current month afterwards if you need the real portfolio in `vw_RateSensitivity`).
- `.github/workflows/ci.yml` runs scripts 00/02/03/05/06, the seed and pytest against a SQL Server container (SQL auth via `SQL_UID`/`SQL_PWD`).
- `python/make_credit_desk.py` builds `excel/LoanETL_CreditDesk.xlsx` (git-ignored; embeds STIBOR rows) through Excel COM (`pywin32`, desktop Excel, Swedish locale handled via `NumberFormatLocal`/`cf()`). Power Query connects by machine name (`PQ_SERVER`) because `localhost` fails the certificate check. `--check` verifies it against SQL; not in CI (no Excel).
- `marketing/` HTML sources for the carousel, deck and social preview. Render PDFs with headless Chrome using an **absolute** `--print-to-pdf` path.
- `data/` downloaded source files and saved mail (git-ignored). `Documents/` holds the course brief (git-ignored, not ours to publish).

## Commands
Run from the project root; all SQL scripts are idempotent and run in order 00-04.
```
sqlcmd -S localhost -E -b -I -i sql\00_create_database.sql      # then 01, 02, 03, 04
.venv\Scripts\python.exe python\seed_tables.py [--skip-rate 202610] [--rate-source ssvx]   # lookups + CarInformation_yyyymm
.venv\Scripts\python.exe python\smtp_sink.py                     # local mail sink, port 2525 -> data\mail\*.eml
sqlcmd -S localhost -E -d LoanETL -Q "EXEC dbo.usp_LoanProfitEstimates [@RunMonth='2026-09-01']"
```
Log: `SELECT * FROM LoanETL.dbo.EtlLog ORDER BY LogID`. Mail: Database Mail profile `LoanETL` -> localhost:2525 (no real mailbox).
The Agent job `LoanETL - Monthly LoanProfitEstimates` runs on day 1 at 06:00 and needs the SQL Server Agent service running (starting it needs an elevated shell).

## Data sources (the teacher gave none)
- Base interest rate: **STIBOR 3M** (user decision). The Riksbank API's STIBOR series ended in 2020 and swfbf.se/stibor/rates is behind a bot checkpoint (do not script around it), so the user saves the SFBF "Summary Statistics" workbook as `data/STIBOR*.xlsx` (sheet "Monthly statistics", 3M column of the month-end fixings). One `InterestRates` row per month = the fixing on the last day of the *previous* month, `RateSource='STIBOR3M'`. Fallback when the file is missing (and in CI, `--rate-source ssvx`): Riksbank SSVX 3M `SETB3MBENCH`, `RateSource='SSVX3M'`.
- Car models/energy class: US fueleconomy.gov `vehicles.csv` (fuel type, year, model). It has no price, so `BasePrice` and `CustomerRiskTier` are synthesized with a fixed random seed.
- Seed at least two months of `CarInformation_yyyymm` (current + previous) and leave one interest-rate month absent so the fallback can be tested.

## Conventions
- Match existing code style; comment the *why* (business rules, fallbacks), not the obvious.
- Dynamic SQL must use `QUOTENAME` and `sp_executesql` with parameters.
- Do not send real email during development unless the user asks; default to a test recipient they provide.
- Ask before changing server-level settings (start Agent, enable Database Mail, `sp_configure`).
- **Live mail is on hold (user decision, 2026-10-28):** do not run `sql/01b_setup_live_mail.sql` or touch the mail setup; development mail goes to the local sink (`smtp_sink.py`, started and stopped by hand).
- **Licences:** the README "Licences and attribution" table must stay in step with the data used. STIBOR 3M is SFBF data under their non-commercial, educational licence (never commit the workbook; it stays in git-ignored `data/`). Add a row for any new data source or library.
- **Marketing numbers** (`marketing/`, README headline) come from real runs only: 180 read / 176 loaded / 4 rejected, 10 tests, STIBOR 3M −7.4% estimated annual profit over 18 months (3,416k → 3,163k SEK, Apr 2025 → Oct 2026). Re-run `make_charts.py` and `make_proof.py` first, then update the HTML and render: deck → `docs/LoanETL_presentation.pdf`, social preview → `docs/social_preview.png`, carousel PDF → the LinkedIn post folder outside the repo. Nothing is pushed or posted without the user's go.
