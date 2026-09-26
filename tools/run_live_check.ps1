# Attach the EA to a live chart briefly, capture its preflight, then put the
# terminal back exactly as it was.
#
# Why this exists: the Strategy Tester runs in a simulated environment, so it
# cannot validate the things that only differ on a live server - the broker's
# real contract figures, current spread, margin, calendar availability, symbol
# trade mode. That is precisely where the tick_value bug lived (MetaQuotes-Demo
# reports 0.1 for XAUUSD while its own OrderCalcProfit says 1.00, which would
# have sized every position 10x too large).
#
# It is a two-minute check, not a forward test. It proves the EA initialises,
# warms up and agrees with the broker about money. It proves nothing about the
# strategy.
#
# The chart profile is backed up and restored, so nothing is left behind.

param(
    [string]$Symbol = "XAUUSD",
    [ValidateSet("M1", "M5", "M15", "M30", "H1", "H4", "D1")] [string]$Period = "M15",
    [int]$WatchSeconds = 90,
    [string]$Profile = "Default",
    [string]$Terminal = "C:\Program Files\MetaTrader 5\terminal64.exe",
    [string]$DataFolder = "$env:APPDATA\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Terminal)) { throw "terminal not found: $Terminal" }

$periodMap = @{
    "M1" = @(0, 1); "M5" = @(0, 5); "M15" = @(0, 15); "M30" = @(0, 30)
    "H1" = @(1, 1); "H4" = @(1, 4); "D1" = @(2, 1)
}
$ptype = $periodMap[$Period][0]
$psize = $periodMap[$Period][1]

$profileDir = Join-Path $DataFolder "MQL5\Profiles\Charts\$Profile"
if (-not (Test-Path $profileDir)) { throw "profile not found: $profileDir" }

$backupDir = Join-Path $env:TEMP "GoldSignalsAI_livecheck_backup"
if (Test-Path $backupDir) { Remove-Item $backupDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item (Join-Path $profileDir "*") $backupDir -Recurse -Force
"backed up profile '$Profile'"

# note the log position so we only read what THIS run produces
$logDir = Join-Path $DataFolder "MQL5\Logs"
$logName = (Get-Date).ToString("yyyyMMdd") + ".log"
$logPath = Join-Path $logDir $logName
$startLen = 0
if (Test-Path $logPath) { $startLen = (Get-Item $logPath).Length }

$chart = @(
    "<chart>", "id=133700000000000777", "symbol=$Symbol",
    "period_type=$ptype", "period_size=$psize", "digits=2",
    "shift=0", "shift_size=20.000000", "fore=0", "grid=0", "volume=0",
    "scroll=1", "scale=8", "graph=1", "fixed_pos=0.000000",
    "window_left=40", "window_top=40", "window_right=1200", "window_bottom=700",
    "window_type=3", "windows_total=1", "",
    "<expert>", "name=GoldSignalsAI_EA",
    "path=Experts\GoldSignalsAI\GoldSignalsAI_EA.ex5",
    "expertmode=5", "<inputs>", "</inputs>", "</expert>", "",
    "<window>", "height=100.000000", "fix_min=0", "fix_max=0",
    "<indicator>", "name=Main", "path=", "apply=1", "show_data=1",
    "scale_inherit=0", "scale_line=0", "scale_line_percent=50",
    "scale_line_value=0.000000", "scale_fix_min=0", "scale_fix_min_val=0.000000",
    "scale_fix_max=0", "scale_fix_max_val=0.000000", "expertmode=0",
    "fixed_height=-1", "</indicator>", "</window>", "</chart>"
)

# .chr is UTF-16LE with a BOM (unlike the tester .ini, which must have none)
$chartPath = Join-Path $profileDir "chart98.chr"
[System.IO.File]::WriteAllText($chartPath, ($chart -join "`r`n") + "`r`n",
    (New-Object System.Text.UnicodeEncoding($false, $true)))
"wrote $chartPath  ($Symbol $Period, EA attached)"

foreach ($p in (Get-Process -Name terminal64 -ErrorAction SilentlyContinue)) {
    try { $p.Kill(); $p.WaitForExit(5000) } catch { }
}
Start-Sleep -Seconds 2

"launching terminal; watching for $WatchSeconds seconds"
$proc = Start-Process -FilePath $Terminal -PassThru
$deadline = (Get-Date).AddSeconds($WatchSeconds)
while ((Get-Date) -lt $deadline -and -not $proc.HasExited) { Start-Sleep -Seconds 5 }
if (-not $proc.HasExited) { try { $proc.Kill(); $proc.WaitForExit(10000) } catch { } }
Start-Sleep -Seconds 2

# restore the profile exactly
Get-ChildItem $profileDir -File | ForEach-Object {
    try { [System.IO.File]::Delete($_.FullName) } catch { }
}
Copy-Item (Join-Path $backupDir "*") $profileDir -Recurse -Force
"restored profile '$Profile'"

""
"================ EA output from this run ================"
if (Test-Path $logPath) {
    $fs = [System.IO.File]::Open($logPath, 'Open', 'Read', 'ReadWrite')
    $fs.Seek($startLen, 'Begin') | Out-Null
    $sr = New-Object System.IO.StreamReader($fs)
    $new = $sr.ReadToEnd()
    $sr.Close(); $fs.Close()
    $lines = $new -split "`r?`n" | Where-Object { $_ -match "GoldSignalsAI|preflight|LiveGuards|sizing|FAIL|WARNING|order" }
    if ($lines) { $lines | ForEach-Object { $_ } } else { "(no EA lines; full tail below)"; ($new -split "`r?`n" | Select-Object -Last 25) }
}
else { "no log at $logPath" }
