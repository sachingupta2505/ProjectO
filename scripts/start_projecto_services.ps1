<#
Starts missing ProjectO services after Windows logon or before market open.
Safe to run repeatedly: existing bot, Telegram bridge, and Streamlit dashboard
processes are left untouched.
#>

$ErrorActionPreference = "Stop"
$projectRoot = "C:\projectO"
$pythonExe = "C:\Users\Sachin Gupta\AppData\Local\Programs\Python\Python312\python.exe"
$logFile = Join-Path $projectRoot "logs\service_launcher.log"

function Write-LauncherLog([string]$message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $message" | Add-Content -LiteralPath $logFile
}

function Test-ProjectOProcess([string]$needle) {
    return [bool](Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$needle*" } |
        Select-Object -First 1)
}

# Let networking and the desktop session settle after a Windows restart.
Start-Sleep -Seconds 15

if (-not (Test-ProjectOProcess "telegram_bridge/bot.py")) {
    Start-Process -FilePath $pythonExe -ArgumentList "telegram_bridge/bot.py" -WorkingDirectory $projectRoot -WindowStyle Hidden
    Write-LauncherLog "Started Telegram bridge."
}

if (-not (Test-ProjectOProcess "main.py --strategy orion")) {
    Start-Process -FilePath $pythonExe -ArgumentList "main.py --strategy orion --index NIFTY --mode paper --lots 3 --no-telegram-listener" -WorkingDirectory $projectRoot -WindowStyle Hidden
    Write-LauncherLog "Started ORION paper bot."
}

if (-not (Test-ProjectOProcess "streamlit run dashboard/streamlit_app.py")) {
    Start-Process -FilePath $pythonExe -ArgumentList "-m streamlit run dashboard/streamlit_app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true" -WorkingDirectory $projectRoot -WindowStyle Hidden
    Write-LauncherLog "Started Streamlit dashboard."
}

Write-LauncherLog "ProjectO service health check completed."
