$python = 'C:\Users\jamil\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe'
$workDir = 'F:\Dev\YoutubeUploaderV2'

$Action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument '-m src split-watch' `
    -WorkingDirectory $workDir

# Run at login AND repeat every 3 hours as a safety net if the daemon dies
$TriggerLogon  = New-ScheduledTaskTrigger -AtLogOn
$TriggerRepeat = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Hours 3) -Once -At (Get-Date)

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName 'YoutubeUploaderV2-Splitter' `
    -Action $Action `
    -Trigger $TriggerLogon, $TriggerRepeat `
    -Settings $Settings `
    -Principal $Principal `
    -Force

Write-Host "Splitter task registered for user $env:USERNAME with logon + 3h repeat triggers."
