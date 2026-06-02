$TaskName = "Agent Daily Ecommerce Update"
$Python = "C:\Users\xliu9\anaconda3\envs\personal_agent\python.exe"
$ProjectDir = "C:\Projects\agent"
$Script = Join-Path $ProjectDir "run_daily_ecommerce_update.py"

$Action = New-ScheduledTaskAction `
  -Execute $Python `
  -Argument "`"$Script`"" `
  -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -Daily -At 11:30
$Settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Sync yesterday Rakuten orders, tomtoc weekly sheet, and EZLIFE/tomtoc dashboards." `
  -Force

Write-Host "Installed scheduled task: $TaskName"
