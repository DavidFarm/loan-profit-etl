-- Data quality views for the analyst. Re-runnable (CREATE OR ALTER).
USE LoanETL;
GO

-- Rows rejected by the most recent run, with the raw values exactly as the agency delivered them.
CREATE OR ALTER VIEW dbo.vw_RejectedRows
AS
SELECT StageID, RunID, RejectReason,
       RecordID, CarModel, EnergyClass, ManufactureYear, BasePrice, FileMonth, CustomerRiskTier
FROM dbo.Stg_CarInformation
WHERE RejectReason IS NOT NULL;
GO

-- One row per ETL run, built from the log: outcome, volumes and duration.
CREATE OR ALTER VIEW dbo.vw_RunSummary
AS
SELECT RunID,
       RunMonth,
       MIN(LogTime)                                                    AS StartedAt,
       DATEDIFF(SECOND, MIN(LogTime), MAX(LogTime))                    AS DurationSeconds,
       CASE WHEN MAX(CASE WHEN Severity = 'ERROR'   THEN 1 ELSE 0 END) = 1 THEN 'FAILED'
            WHEN MAX(CASE WHEN Severity = 'WARNING' THEN 1 ELSE 0 END) = 1 THEN 'SUCCESS WITH WARNINGS'
            ELSE 'SUCCESS' END                                         AS Status,
       MAX(CASE WHEN Step = 'EXTRACT' THEN [Rows] END)                 AS RowsRead,
       MAX(CASE WHEN Step = 'LOAD'    THEN [Rows] END)                 AS RowsLoaded,
       SUM(CASE WHEN Step = 'VALIDATE' THEN [Rows] ELSE 0 END)         AS RowsRejected,
       SUM(CASE WHEN Severity = 'WARNING' THEN 1 ELSE 0 END)           AS Warnings
FROM dbo.EtlLog
GROUP BY RunID, RunMonth;
GO
