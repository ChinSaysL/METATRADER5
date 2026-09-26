# Export cached XAUUSD history AND the ported engine's per-bar output to one CSV.
#
# Why this route: with no valid trade-server session the Python bridge refuses to
# initialise, the Strategy Tester never starts an agent, the MCP endpoint answers
# 401, and [StartUp] in the start config is ignored by this build. What DOES work
# is an indicator on a chart - it recalculates over cached history the moment the
# chart opens, with no ticks and no login. MT5 chart files (.chr) are UTF-16 text,
# so a chart with the indicator already attached can simply be written to disk.
#
# The Default profile is the active one and already holds an XAUUSD chart, so a
# second chart is added to it, the terminal is launched, the indicator dumps and
# closes the terminal itself, and the profile is then restored untouched.
#
# The resulting CSV is both the parity reference for research/parity_check.py and
# the price data for research/sweep.py.

param(
    [string]$Symbol = "XAUUSD",
    [ValidateSet("M1", "M5", "M15", "M30", "H1", "H4", "D1")] [string]$Period = "M15",
    [double]$Sensitivity = 5.0,
    [int]$AtrLength = 10,
    [int]$MinSignalStrength = 1,
    [bool]$FilterFakeouts = $false,
    [int]$FakeoutStrength = 1,          # 0 Light, 1 Medium, 2 Strong, 3 Custom
    [int]$ReversalSens = 1,             # 0 Early, 1 Normal, 2 Late
    [string]$OutFile = "GoldSignalsAI_diag.csv",
    [string]$Profile = "Default",
    [string]$Terminal = "C:\Program Files\MetaTrader 5\terminal64.exe",
    [string]$DataFolder = "$env:APPDATA\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075",
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Terminal)) { throw "terminal not found: $Terminal" }

# period_type: 0 = minutes, 1 = hours, 2 = days ... ; period_size = the multiplier
$periodMap = @{
    "M1" = @(0, 1); "M5" = @(0, 5); "M15" = @(0, 15); "M30" = @(0, 30)
    "H1" = @(1, 1); "H4" = @(1, 4); "D1" = @(2, 1)
}
$ptype = $periodMap[$Period][0]
$psize = $periodMap[$Period][1]

$outPath = Join-Path $DataFolder "MQL5\Files\$OutFile"
if ([System.IO.File]::Exists($outPath)) { [System.IO.File]::Delete($outPath) }

$profileDir = Join-Path $DataFolder "MQL5\Profiles\Charts\$Profile"
if (-not (Test-Path $profileDir)) { throw "profile not found: $profileDir" }

# Back the whole profile up, because MT5 rewrites it when it exits.
$backupDir = Join-Path $env:TEMP "GoldSignalsAI_profile_backup"
if (Test-Path $backupDir) { Remove-Item $backupDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item (Join-Path $profileDir "*") $backupDir -Recurse -Force
"backed up profile '$Profile' -> $backupDir"

# Inputs must be listed in declaration order, matching the .mq5.
$inputs = @(
    "InpSensitivity=$Sensitivity",
    "InpAtrLength=$AtrLength",
    "InpMinSignalStrength=$MinSignalStrength",
    "InpFilterFakeouts=$(if ($FilterFakeouts) { 'true' } else { 'false' })",
    "InpFakeoutStrength=$FakeoutStrength",
    "InpCustomMaxChop=60",
    "InpCustomMinQuality=0.0",
    "InpCustomBodyOverride=0.0",
    "InpMarkFiltered=false",
    "InpShowTrail=true",
    "InpShowDashboard=false",
    "InpReversalSens=$ReversalSens",
    "InpDumpFile=$OutFile",
    "InpDumpCloseTerminal=true"
)

$indicatorTail = @(
    "apply=0", "show_data=1", "scale_inherit=0", "scale_line=0",
    "scale_line_percent=50", "scale_line_value=0.000000",
    "scale_fix_min=0", "scale_fix_min_val=0.000000",
    "scale_fix_max=0", "scale_fix_max_val=0.000000",
    "expertmode=0", "fixed_height=-1"
)

$chart = @()
$chart += "<chart>"
$chart += "id=133700000000000001"
$chart += "symbol=$Symbol"
$chart += "period_type=$ptype"
$chart += "period_size=$psize"
$chart += "digits=2"
$chart += "shift=0"
$chart += "shift_size=20.000000"
$chart += "fore=0"
$chart += "grid=0"
$chart += "volume=0"
$chart += "scroll=1"
$chart += "scale=8"
$chart += "graph=1"
$chart += "fixed_pos=0.000000"
$chart += "window_left=40"
$chart += "window_top=40"
$chart += "window_right=1200"
$chart += "window_bottom=700"
$chart += "window_type=3"
$chart += "windows_total=1"
$chart += ""
$chart += "<window>"
$chart += "height=100.000000"
$chart += "fix_min=0"
$chart += "fix_max=0"
$chart += "<indicator>"
$chart += "name=Main"
$chart += "path="
$chart += "apply=1"
$chart += $indicatorTail[1..($indicatorTail.Count - 1)]
$chart += "</indicator>"
$chart += "<indicator>"
$chart += "name=Custom Indicator"
$chart += "path=Indicators\GoldSignalsAI\GoldSignalsAI_Signals.ex5"
$chart += $indicatorTail
$chart += "<inputs>"
$chart += $inputs
$chart += "</inputs>"
$chart += "</indicator>"
$chart += "</window>"
$chart += "</chart>"

# .chr is UTF-16LE with a BOM and CRLF line endings.
$chartPath = Join-Path $profileDir "chart99.chr"
[System.IO.File]::WriteAllText($chartPath, ($chart -join "`r`n") + "`r`n",
    (New-Object System.Text.UnicodeEncoding($false, $true)))
"wrote $chartPath  ($Symbol $Period, indicator attached, dump -> $OutFile)"

foreach ($p in (Get-Process -Name terminal64 -ErrorAction SilentlyContinue)) {
    try { $p.Kill(); $p.WaitForExit(5000) } catch { }
}
Start-Sleep -Seconds 2

"launching terminal; the indicator dumps on chart load and closes it"
$proc = Start-Process -FilePath $Terminal -PassThru
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$done = $false
while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) { $done = $true; break }
    if ([System.IO.File]::Exists($outPath)) {
        # give the indicator a moment to flush and close
        Start-Sleep -Seconds 3
        $done = $true
        break
    }
    Start-Sleep -Seconds 3
}
if (-not $proc.HasExited) {
    try { $proc.Kill(); $proc.WaitForExit(10000) } catch { }
}
if ($done) { "terminal finished" } else { "TIMEOUT after $TimeoutSeconds s" }

Start-Sleep -Seconds 2

# Restore the profile exactly as it was.
Get-ChildItem $profileDir -File | ForEach-Object {
    try { [System.IO.File]::Delete($_.FullName) } catch { }
}
Copy-Item (Join-Path $backupDir "*") $profileDir -Recurse -Force
"restored profile '$Profile'"

""
if ([System.IO.File]::Exists($outPath)) {
    $lines = (Get-Content $outPath | Measure-Object -Line).Lines
    "OK  $outPath"
    "    $((Get-Item $outPath).Length) bytes, $lines lines ($($lines - 1) bars)"
}
else { "FAILED: $outPath was not produced" }

"--- expert log tail ---"
$log = Get-ChildItem (Join-Path $DataFolder "MQL5\Logs") -Filter *.log -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -Last 1
if ($log) { Get-Content $log.FullName -Tail 12 } else { "no expert log found" }
