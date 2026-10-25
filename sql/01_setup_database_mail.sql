-- Database Mail pointed at the local SMTP sink (python\smtp_sink.py, port 2525).
-- Nothing is sent to a real mailbox. To go live, change @mailserver_name/@port/@enable_ssl
-- and add credentials to the account.
USE msdb;
GO

EXEC sp_configure 'show advanced options', 1;
RECONFIGURE;
EXEC sp_configure 'Database Mail XPs', 1;
RECONFIGURE;
GO

IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysmail_account WHERE name = N'LoanETL_Local')
    EXEC msdb.dbo.sysmail_add_account_sp
        @account_name    = N'LoanETL_Local',
        @description     = N'Local SMTP sink for development',
        @email_address   = N'loanetl@localhost.test',
        @display_name    = N'LoanETL Job',
        @mailserver_name = N'localhost',
        @port            = 2525,
        @enable_ssl      = 0;

IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysmail_profile WHERE name = N'LoanETL')
    EXEC msdb.dbo.sysmail_add_profile_sp
        @profile_name = N'LoanETL',
        @description  = N'Profile used by usp_LoanProfitEstimates';

IF NOT EXISTS (SELECT 1
               FROM msdb.dbo.sysmail_profileaccount pa
               JOIN msdb.dbo.sysmail_profile p ON p.profile_id = pa.profile_id
               WHERE p.name = N'LoanETL')
    EXEC msdb.dbo.sysmail_add_profileaccount_sp
        @profile_name = N'LoanETL', @account_name = N'LoanETL_Local', @sequence_number = 1;
GO
