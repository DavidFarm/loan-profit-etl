-- SQL Server Agent job: runs the ETL on the 1st of every month at 06:00.
-- Re-runnable: an existing job with the same name is dropped and recreated.
USE msdb;
GO

DECLARE @job SYSNAME = N'LoanETL - Monthly LoanProfitEstimates';

IF EXISTS (SELECT 1 FROM dbo.sysjobs WHERE name = @job)
    EXEC dbo.sp_delete_job @job_name = @job, @delete_unused_schedule = 1;

EXEC dbo.sp_add_job @job_name = @job, @description = N'Builds LoanProfitEstimates_yyyymm for the current month.';

EXEC dbo.sp_add_jobstep
    @job_name = @job, @step_name = N'Run ETL', @subsystem = N'TSQL', @database_name = N'LoanETL',
    @command = N'EXEC dbo.usp_LoanProfitEstimates;', @on_success_action = 1, @on_fail_action = 2;

EXEC dbo.sp_add_jobschedule
    @job_name = @job, @name = N'Monthly, day 1 at 06:00',
    @freq_type = 16, @freq_interval = 1, @freq_recurrence_factor = 1,   -- monthly, day 1, every month
    @active_start_time = 060000;

EXEC dbo.sp_add_jobserver @job_name = @job, @server_name = N'(LOCAL)';
GO
