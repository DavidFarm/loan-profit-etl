-- What would the loan book's estimated annual profit have been under each month's base rate?
-- The portfolio is the valid rows of the most recent run (dbo.Stg_CarInformation), so the view always
-- reflects the latest processed file. Same formula as the ETL:
-- BasePrice * (BaseRate + EnergyMargin + RiskAdjustment) / 100.
-- Re-runnable (CREATE OR ALTER).
USE LoanETL;
GO

CREATE OR ALTER VIEW dbo.vw_RateSensitivity
AS
WITH portfolio AS (
    SELECT m.MarginRate + t.RiskAdjustment AS MarginRate,   -- BankMargin
           COUNT(*)                   AS Loans,
           SUM(s.BasePrice_Clean)     AS BasePrice
    FROM dbo.Stg_CarInformation s
    JOIN dbo.EnergyClassMargin m ON m.EnergyClass = s.EnergyClass_Clean
    JOIN dbo.CreditRiskTier    t ON t.RiskTier    = s.CustomerRiskTier_Clean
    WHERE s.RejectReason IS NULL
    GROUP BY m.MarginRate + t.RiskAdjustment
),
per_month AS (
    SELECT r.EffectiveDate,
           r.BaseInterestRate,
           SUM(p.Loans)                                                       AS Loans,
           SUM(p.BasePrice)                                                   AS PortfolioBasePrice,
           CAST(SUM(p.BasePrice * (r.BaseInterestRate + p.MarginRate) / 100) AS DECIMAL(14,2)) AS EstimatedAnnualProfit
    FROM dbo.InterestRates r
    CROSS JOIN portfolio p
    GROUP BY r.EffectiveDate, r.BaseInterestRate
)
SELECT EffectiveDate, BaseInterestRate, Loans, PortfolioBasePrice, EstimatedAnnualProfit,
       BaseInterestRate      - LAG(BaseInterestRate)      OVER (ORDER BY EffectiveDate)  AS RateChange,
       EstimatedAnnualProfit - LAG(EstimatedAnnualProfit) OVER (ORDER BY EffectiveDate)  AS ProfitChange
FROM per_month;
GO
