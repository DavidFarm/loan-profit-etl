-- Monthly ETL: CarInformation_yyyymm -> staging -> LoanProfitEstimates_yyyymm.
-- Re-runnable: CREATE OR ALTER, and each run rebuilds that month's output table.
--
--   EXEC dbo.usp_LoanProfitEstimates;                           -- current month (what the Agent job runs)
--   EXEC dbo.usp_LoanProfitEstimates @RunMonth = '2026-09-01';  -- re-run / test another month
USE LoanETL;
GO

-- Small helper so every step logs the same way.
CREATE OR ALTER PROCEDURE dbo.usp_EtlLog
    @RunID    UNIQUEIDENTIFIER,
    @RunMonth DATE,
    @Severity VARCHAR(10),
    @Step     VARCHAR(30),
    @Message  NVARCHAR(1000),
    @Rows     INT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    INSERT dbo.EtlLog (RunID, RunMonth, Severity, Step, Message, [Rows])
    VALUES (@RunID, @RunMonth, @Severity, @Step, @Message, @Rows);
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_LoanProfitEstimates
    @RunMonth       DATE          = NULL,                          -- NULL = current month
    @MailProfile    SYSNAME       = N'LoanETL',
    @MailRecipients NVARCHAR(500) = N'etl.analyst@loanetl.local'   -- local SMTP sink, see 01_setup_database_mail.sql
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;   -- any runtime error dooms the transaction, so the old output table survives a failed run

    DECLARE @RunID         UNIQUEIDENTIFIER = NEWID(),
            @yyyymm        CHAR(6),
            @SourceName    SYSNAME,
            @TargetName    SYSNAME,
            @Source        NVARCHAR(300),
            @Target        NVARCHAR(300),
            @Rate          DECIMAL(5,2),
            @RateDate      DATE,
            @RowsRead      INT = 0,
            @RowsRejected  INT = 0,
            @RowsLoaded    INT = 0,
            @Warnings      INT = 0,
            @Msg           NVARCHAR(1000),
            @Sql           NVARCHAR(MAX),
            @Subject       NVARCHAR(255),
            @Body          NVARCHAR(MAX);

    -- Normalise to the first of the month so "current month" is unambiguous.
    SET @RunMonth   = DATEFROMPARTS(YEAR(COALESCE(@RunMonth, GETDATE())), MONTH(COALESCE(@RunMonth, GETDATE())), 1);
    SET @yyyymm     = FORMAT(@RunMonth, 'yyyyMM');
    SET @SourceName = N'CarInformation_' + @yyyymm;
    SET @TargetName = N'LoanProfitEstimates_' + @yyyymm;
    SET @Source     = QUOTENAME(N'dbo') + N'.' + QUOTENAME(@SourceName);
    SET @Target     = QUOTENAME(N'dbo') + N'.' + QUOTENAME(@TargetName);

    BEGIN TRY
        SET @Msg = N'Run started for ' + @yyyymm + N'.';
        EXEC dbo.usp_EtlLog @RunID, @RunMonth, 'INFO', 'START', @Msg;

        -- 1. Pick the source table for the month. Missing file = hard stop.
        IF OBJECT_ID(@Source, N'U') IS NULL
        BEGIN
            SET @Msg = @SourceName + N' table for the current month not found.';
            THROW 50001, @Msg, 1;
        END;

        -- 2. Base rate: this month's rate, else the most recent earlier month. No rate at all = hard stop.
        SELECT TOP (1) @Rate = BaseInterestRate, @RateDate = EffectiveDate
        FROM dbo.InterestRates
        WHERE EffectiveDate >= @RunMonth AND EffectiveDate < DATEADD(MONTH, 1, @RunMonth)
        ORDER BY EffectiveDate DESC;

        IF @Rate IS NULL
        BEGIN
            SELECT TOP (1) @Rate = BaseInterestRate, @RateDate = EffectiveDate
            FROM dbo.InterestRates
            WHERE EffectiveDate < @RunMonth
            ORDER BY EffectiveDate DESC;

            IF @Rate IS NULL
            BEGIN
                SET @Msg = N'No base interest rate found for ' + @yyyymm + N' or any earlier month.';
                THROW 50002, @Msg, 1;
            END;

            SET @Msg = N'Base interest rate for the current month is missing. Value from the previous month was used'
                     + N' (' + CONVERT(NVARCHAR(10), @RateDate, 23) + N', ' + CONVERT(NVARCHAR(10), @Rate) + N'%).';
            EXEC dbo.usp_EtlLog @RunID, @RunMonth, 'WARNING', 'INTEREST_RATE', @Msg;
        END
        ELSE
        BEGIN
            SET @Msg = N'Using base interest rate ' + CONVERT(NVARCHAR(10), @Rate) + N'% effective '
                     + CONVERT(NVARCHAR(10), @RateDate, 23) + N'.';
            EXEC dbo.usp_EtlLog @RunID, @RunMonth, 'INFO', 'INTEREST_RATE', @Msg;
        END;

        -- 3. Extract: copy the agency file into staging as text.
        TRUNCATE TABLE dbo.Stg_CarInformation;
        SET @Sql = N'INSERT dbo.Stg_CarInformation
                         (RunID, RecordID, CarModel, EnergyClass, ManufactureYear, BasePrice, FileMonth, CustomerRiskTier)
                     SELECT @RunID, CAST(RecordID AS NVARCHAR(50)), CAST(CarModel AS NVARCHAR(200)),
                            CAST(EnergyClass AS NVARCHAR(50)), CAST(ManufactureYear AS NVARCHAR(50)),
                            CAST(BasePrice AS NVARCHAR(50)), CAST(FileMonth AS NVARCHAR(50)),
                            CAST(CustomerRiskTier AS NVARCHAR(50))
                     FROM ' + @Source + N';';
        EXEC sp_executesql @Sql, N'@RunID UNIQUEIDENTIFIER', @RunID = @RunID;
        SET @RowsRead = (SELECT COUNT(*) FROM dbo.Stg_CarInformation);
        SET @Msg = N'Rows read from ' + @SourceName + N'.';
        EXEC dbo.usp_EtlLog @RunID, @RunMonth, 'INFO', 'EXTRACT', @Msg, @RowsRead;

        -- 4. Transform: trim, fix types, decimal commas, and map energy class / risk tier onto the lookup spelling.
        UPDATE s
        SET RecordID_Clean         = TRY_CONVERT(INT, s.RecordID),
            CarModel_Clean         = NULLIF(LTRIM(RTRIM(s.CarModel)), ''),
            EnergyClass_Clean      = m.EnergyClass,
            ManufactureYear_Clean  = TRY_CONVERT(INT, s.ManufactureYear),
            BasePrice_Clean        = TRY_CONVERT(DECIMAL(10,2), REPLACE(REPLACE(s.BasePrice, N' ', N''), N',', N'.')),
            FileMonth_Clean        = TRY_CONVERT(DATE, s.FileMonth),
            CustomerRiskTier_Clean = t.RiskTier
        FROM dbo.Stg_CarInformation s
        LEFT JOIN dbo.EnergyClassMargin m ON m.EnergyClass = LTRIM(RTRIM(s.EnergyClass))
        LEFT JOIN dbo.CreditRiskTier    t ON t.RiskTier    = LTRIM(RTRIM(s.CustomerRiskTier));

        -- 5. Validate: first failing rule wins. Rejected rows stay in staging, flagged.
        UPDATE dbo.Stg_CarInformation
        SET RejectReason = CASE
                WHEN RecordID_Clean IS NULL                                        THEN 'Invalid RecordID'
                WHEN CarModel_Clean IS NULL                                        THEN 'Missing CarModel'
                WHEN EnergyClass_Clean IS NULL                                     THEN 'Unknown EnergyClass'
                WHEN CustomerRiskTier_Clean IS NULL                                THEN 'Unknown CustomerRiskTier'
                WHEN ManufactureYear_Clean IS NULL
                  OR ManufactureYear_Clean NOT BETWEEN 1980 AND YEAR(@RunMonth) + 1 THEN 'Invalid ManufactureYear'
                WHEN BasePrice_Clean IS NULL OR BasePrice_Clean <= 0               THEN 'Missing or non-positive BasePrice'
            END;

        ;WITH ranked AS (
            SELECT RejectReason, rn = ROW_NUMBER() OVER (PARTITION BY RecordID_Clean ORDER BY StageID)
            FROM dbo.Stg_CarInformation
            WHERE RejectReason IS NULL
        )
        UPDATE ranked SET RejectReason = 'Duplicate RecordID' WHERE rn > 1;

        SET @RowsRejected = (SELECT COUNT(*) FROM dbo.Stg_CarInformation WHERE RejectReason IS NOT NULL);

        INSERT dbo.EtlLog (RunID, RunMonth, Severity, Step, Message, [Rows])
        SELECT @RunID, @RunMonth, 'WARNING', 'VALIDATE', N'Rows rejected: ' + RejectReason, COUNT(*)
        FROM dbo.Stg_CarInformation
        WHERE RejectReason IS NOT NULL
        GROUP BY RejectReason;

        -- 6. Load: rebuild the output table in one transaction so the analyst never sees a half-built table.
        --    EstimatedProfit = BasePrice * (BaseInterestRate + BankMargin) / 100
        BEGIN TRANSACTION;

        SET @Sql = N'DROP TABLE IF EXISTS ' + @Target + N';
            SELECT s.RecordID_Clean AS RecordID, s.CarModel_Clean AS CarModel, s.EnergyClass_Clean AS EnergyClass,
                   s.ManufactureYear_Clean AS ManufactureYear, s.BasePrice_Clean AS BasePrice,
                   s.CustomerRiskTier_Clean AS CustomerRiskTier, @RunMonth AS FileMonth,
                   @Rate AS BaseInterestRate, m.MarginRate AS BankMargin,
                   CAST(@Rate + m.MarginRate AS DECIMAL(5,2)) AS TotalInterestRate,
                   CAST(s.BasePrice_Clean * (@Rate + m.MarginRate) / 100 AS DECIMAL(12,2)) AS EstimatedProfit,
                   @RateDate AS RateEffectiveDate, SYSDATETIME() AS ProcessedAt
            INTO ' + @Target + N'
            FROM dbo.Stg_CarInformation s
            JOIN dbo.EnergyClassMargin m ON m.EnergyClass = s.EnergyClass_Clean
            WHERE s.RejectReason IS NULL;
            SET @n = @@ROWCOUNT;
            ALTER TABLE ' + @Target + N' ALTER COLUMN RecordID INT NOT NULL;
            ALTER TABLE ' + @Target + N' ADD CONSTRAINT ' + QUOTENAME(N'PK_' + @TargetName) + N' PRIMARY KEY (RecordID);';
        EXEC sp_executesql @Sql,
             N'@Rate DECIMAL(5,2), @RateDate DATE, @RunMonth DATE, @n INT OUTPUT',
             @Rate = @Rate, @RateDate = @RateDate, @RunMonth = @RunMonth, @n = @RowsLoaded OUTPUT;

        COMMIT TRANSACTION;

        SET @Msg = N'Rows written to ' + @TargetName + N'.';
        EXEC dbo.usp_EtlLog @RunID, @RunMonth, 'INFO', 'LOAD', @Msg, @RowsLoaded;

        -- 7. Confirmation email. A mail failure is logged but must not fail an otherwise good run.
        SET @Warnings = (SELECT COUNT(*) FROM dbo.EtlLog WHERE RunID = @RunID AND Severity = 'WARNING');
        SET @Subject = N'LoanETL ' + @yyyymm + CASE WHEN @Warnings > 0 THEN N': SUCCESS with warnings' ELSE N': SUCCESS' END;
        SET @Body = N'The LoanProfitEstimates job completed successfully.' + CHAR(13) + CHAR(10)
                  + N'Run month:        ' + @yyyymm + CHAR(13) + CHAR(10)
                  + N'Output table:     dbo.' + @TargetName + CHAR(13) + CHAR(10)
                  + N'Rows processed:   ' + CAST(@RowsRead AS NVARCHAR(20)) + CHAR(13) + CHAR(10)
                  + N'Rows loaded:      ' + CAST(@RowsLoaded AS NVARCHAR(20)) + CHAR(13) + CHAR(10)
                  + N'Rows rejected:    ' + CAST(@RowsRejected AS NVARCHAR(20)) + CHAR(13) + CHAR(10)
                  + N'Warnings:         ' + CAST(@Warnings AS NVARCHAR(20)) + N' (see dbo.EtlLog, RunID ' + CAST(@RunID AS NVARCHAR(36)) + N')' + CHAR(13) + CHAR(10)
                  + N'Query the result: SELECT * FROM ' + @TargetName + N';';
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;

        SET @Msg = LEFT(ERROR_MESSAGE(), 1000);
        EXEC dbo.usp_EtlLog @RunID, @RunMonth, 'ERROR', 'FAILED', @Msg;

        SET @Subject = N'LoanETL ' + @yyyymm + N': FAILED';
        SET @Body = N'The LoanProfitEstimates job failed.' + CHAR(13) + CHAR(10)
                  + N'Run month:      ' + @yyyymm + CHAR(13) + CHAR(10)
                  + N'Output table:   dbo.' + @TargetName + N' (not created or not updated)' + CHAR(13) + CHAR(10)
                  + N'Rows processed: ' + CAST(@RowsRead AS NVARCHAR(20)) + CHAR(13) + CHAR(10)
                  + N'Error:          ' + @Msg + CHAR(13) + CHAR(10)
                  + N'Details:        dbo.EtlLog, RunID ' + CAST(@RunID AS NVARCHAR(36));
    END CATCH;

    -- Mail is sent after the try/catch so success and failure share one path.
    BEGIN TRY
        EXEC msdb.dbo.sp_send_dbmail @profile_name = @MailProfile, @recipients = @MailRecipients,
                                     @subject = @Subject, @body = @Body;
    END TRY
    BEGIN CATCH
        SET @Msg = N'Confirmation email could not be sent: ' + LEFT(ERROR_MESSAGE(), 900);
        EXEC dbo.usp_EtlLog @RunID, @RunMonth, 'WARNING', 'MAIL', @Msg;
    END CATCH;

    -- Surface a failed run to the caller / SQL Agent so the job history shows it as failed.
    IF EXISTS (SELECT 1 FROM dbo.EtlLog WHERE RunID = @RunID AND Severity = 'ERROR')
    BEGIN
        SET @Msg = (SELECT TOP (1) Message FROM dbo.EtlLog WHERE RunID = @RunID AND Severity = 'ERROR' ORDER BY LogID DESC);
        THROW 50000, @Msg, 1;
    END;
END;
GO
