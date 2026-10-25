-- Dedicated database for Assignment 1 (the existing ETL database belongs to other exercises).
IF DB_ID(N'LoanETL') IS NULL
    CREATE DATABASE LoanETL;
GO
