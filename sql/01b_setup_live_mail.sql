-- Optional: real confirmation mail through Gmail SMTP. Not part of the 00-06 run, and not run by CI.
-- Adds a second Database Mail profile (LoanETL_Live) and points the ETL at it; the local-sink profile LoanETL
-- from 01_setup_database_mail.sql stays the default for development and tests (they pass @MailProfile explicitly).
--
-- The password is a Gmail app password (Google account > Security > 2-step verification > App passwords).
-- It is stored only in msdb, never in git. Run it yourself so the password does not pass through anyone else:
--   sqlcmd -S localhost -E -b -i sql\01b_setup_live_mail.sql -v GmailUser="you@gmail.com" GmailAppPassword="xxxxxxxxxxxxxxxx" MailTo="recipient@example.com"
-- Re-runnable: an existing account is updated, so the password can be rotated by running it again.
USE msdb;
GO

IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysmail_account WHERE name = N'LoanETL_Gmail')
    EXEC msdb.dbo.sysmail_add_account_sp
        @account_name    = N'LoanETL_Gmail',
        @description     = N'Gmail SMTP for real confirmation mail',
        @email_address   = N'$(GmailUser)',
        @display_name    = N'LoanETL Job',
        @mailserver_name = N'smtp.gmail.com',
        @port            = 587,
        @enable_ssl      = 1,
        @username        = N'$(GmailUser)',
        @password        = N'$(GmailAppPassword)';
ELSE
    EXEC msdb.dbo.sysmail_update_account_sp
        @account_name    = N'LoanETL_Gmail',
        @email_address   = N'$(GmailUser)',
        @mailserver_name = N'smtp.gmail.com',
        @port            = 587,
        @enable_ssl      = 1,
        @username        = N'$(GmailUser)',
        @password        = N'$(GmailAppPassword)';

IF NOT EXISTS (SELECT 1 FROM msdb.dbo.sysmail_profile WHERE name = N'LoanETL_Live')
    EXEC msdb.dbo.sysmail_add_profile_sp
        @profile_name = N'LoanETL_Live',
        @description  = N'Real mail for the scheduled LoanProfitEstimates job';

IF NOT EXISTS (SELECT 1
               FROM msdb.dbo.sysmail_profileaccount pa
               JOIN msdb.dbo.sysmail_profile p ON p.profile_id = pa.profile_id
               WHERE p.name = N'LoanETL_Live')
    EXEC msdb.dbo.sysmail_add_profileaccount_sp
        @profile_name = N'LoanETL_Live', @account_name = N'LoanETL_Gmail', @sequence_number = 1;
GO

-- From now on a run without explicit mail parameters (the Agent job) mails the real recipient.
USE LoanETL;
GO

MERGE dbo.EtlSettings AS t
USING (VALUES ('MailProfile', N'LoanETL_Live'), ('MailRecipients', N'$(MailTo)')) AS s (Name, Value)
   ON t.Name = s.Name
WHEN MATCHED THEN UPDATE SET Value = s.Value
WHEN NOT MATCHED THEN INSERT (Name, Value) VALUES (s.Name, s.Value);
GO
