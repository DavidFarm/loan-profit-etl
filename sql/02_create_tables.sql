-- Lookup, staging and log tables. Data for the lookups is loaded by python\seed_tables.py.
-- Re-runnable: each table is only created when missing.
USE LoanETL;
GO

IF OBJECT_ID(N'dbo.InterestRates', N'U') IS NULL
    CREATE TABLE dbo.InterestRates (
        RateID           INT IDENTITY(1,1) PRIMARY KEY,
        EffectiveDate    DATE          NOT NULL,
        BaseInterestRate DECIMAL(5,2)  NOT NULL
    );

-- Where the monthly rate came from (STIBOR3M, SSVX3M), so a proxy is never mistaken for the real benchmark.
IF COL_LENGTH(N'dbo.InterestRates', N'RateSource') IS NULL
    ALTER TABLE dbo.InterestRates ADD RateSource VARCHAR(30) NULL;

IF OBJECT_ID(N'dbo.EnergyClassMargin', N'U') IS NULL
    CREATE TABLE dbo.EnergyClassMargin (
        EnergyClass VARCHAR(20)  PRIMARY KEY,
        MarginRate  DECIMAL(5,2) NOT NULL
    );

IF OBJECT_ID(N'dbo.CreditRiskTier', N'U') IS NULL
    CREATE TABLE dbo.CreditRiskTier (
        RiskTier       VARCHAR(20)  PRIMARY KEY,
        RiskAdjustment DECIMAL(5,2) NOT NULL
    );

-- BasePrice in the agency file is today's purchase price (new or used) = the amount financed.
-- DepreciationRates holds the share of that value lost over the coming year, by car age (MaxAge NULL = open-ended).
-- The first version of this table used model-year bands; it is lookup data only, so recreate and reseed it.
IF COL_LENGTH(N'dbo.DepreciationRates', N'MinYear') IS NOT NULL
    DROP TABLE dbo.DepreciationRates;

IF OBJECT_ID(N'dbo.DepreciationRates', N'U') IS NULL
    CREATE TABLE dbo.DepreciationRates (
        MinAge     INT          NOT NULL PRIMARY KEY,
        MaxAge     INT          NULL,
        AnnualRate DECIMAL(5,4) NOT NULL
    );

-- Optional overrides for the mail profile/recipient, written by 01b_setup_live_mail.sql so the real address
-- lives in the database, not in git. No rows = the local sink defaults in the procedure.
IF OBJECT_ID(N'dbo.EtlSettings', N'U') IS NULL
    CREATE TABLE dbo.EtlSettings (
        Name  VARCHAR(50)    PRIMARY KEY,
        Value NVARCHAR(500)  NOT NULL
    );

-- One row per event. RunID groups everything from a single execution.
IF OBJECT_ID(N'dbo.EtlLog', N'U') IS NULL
    CREATE TABLE dbo.EtlLog (
        LogID    INT IDENTITY(1,1) PRIMARY KEY,
        RunID    UNIQUEIDENTIFIER NOT NULL,
        RunMonth DATE             NOT NULL,
        LogTime  DATETIME2(0)     NOT NULL DEFAULT SYSDATETIME(),
        Severity VARCHAR(10)      NOT NULL CHECK (Severity IN ('INFO', 'WARNING', 'ERROR')),
        Step     VARCHAR(30)      NOT NULL,
        Message  NVARCHAR(1000)   NOT NULL,
        [Rows]   INT              NULL
    );

-- Staging mirrors the agency file as text so a bad value can never fail the load itself.
-- The *_Clean columns hold the validated, typed values; RejectReason stays NULL for good rows.
-- Rejected rows are kept here until the next run for inspection.
-- Staging is truncated every run, so a layout change is applied by recreating it.
IF COL_LENGTH(N'dbo.Stg_CarInformation', N'DepreciationRate_Clean') IS NULL
    DROP TABLE IF EXISTS dbo.Stg_CarInformation;

IF OBJECT_ID(N'dbo.Stg_CarInformation', N'U') IS NULL
    CREATE TABLE dbo.Stg_CarInformation (
        StageID                INT IDENTITY(1,1) PRIMARY KEY,
        RunID                  UNIQUEIDENTIFIER NOT NULL,
        RecordID               NVARCHAR(50)  NULL,
        CarModel               NVARCHAR(200) NULL,
        EnergyClass            NVARCHAR(50)  NULL,
        ManufactureYear        NVARCHAR(50)  NULL,
        BasePrice              NVARCHAR(50)  NULL,
        FileMonth              NVARCHAR(50)  NULL,
        CustomerRiskTier       NVARCHAR(50)  NULL,
        RecordID_Clean         INT           NULL,
        CarModel_Clean         VARCHAR(100)  NULL,
        EnergyClass_Clean      VARCHAR(20)   NULL,
        ManufactureYear_Clean  INT           NULL,
        BasePrice_Clean        DECIMAL(10,2) NULL,
        FileMonth_Clean        DATE          NULL,
        CustomerRiskTier_Clean VARCHAR(20)   NULL,
        CarAge_Clean           INT           NULL,
        DepreciationRate_Clean DECIMAL(5,4)  NULL,
        RejectReason           VARCHAR(60)   NULL
    );
GO
