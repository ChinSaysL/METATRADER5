# Drive the MetaTrader 5 Strategy Tester from the command line.
#
# Two uses:
#   -Mode Parity    fast open-prices run whose only purpose is the per-bar
#                   diagnostics CSV that research/parity_check.py consumes.
#   -Mode Backtest  1-minute-OHLC (or real-tick) run that produces a real report.
#
# The terminal must not already be running: MT5 ignores /config when an instance
# of the same installation is up, so this closes it first.

param(
    [ValidateSet("Parity", "Backtest")] [string]$Mode = "Backtest",
    [string]$Symbol = "XAUUSD",
    [string]$Period = "M15",
    [string]$FromDate = "2026.01.05",
    [string]$ToDate = "2026.09.01",
    # 0 every tick, 1 one-minute OHLC, 2 open prices only, 3 math calc, 4 real ticks
    [int]$TickModel = -1,
    [double]$Deposit = 10000,
    [string]$Leverage = "1:100",
    [string]$Terminal = "C:\Program Files\MetaTrader 5\terminal64.exe",
    [string]$DataFolder = "$env:APPDATA\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075",
    [hashtable]$Inputs = @{},
    [int]$TimeoutSeconds = 1800
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Terminal)) { throw "terminal not found: $Terminal" }

if ($TickModel -lt 0) { if ($Mode -eq "Parity") { $TickModel = 2 } else { $TickModel = 1 } }

$reportName = "GoldSignalsAI_$Mode"
$reportPath = Join-Path $DataFolder "$reportName.htm"
$diagName = "GoldSignalsAI_diag.csv"

# The Strategy Tester PERSISTS input values per expert between runs: any input
# NOT listed in [TesterInputs] silently reuses the last saved value instead of
# the newly compiled default. That once caused a whole run to execute the
# previous configuration while reporting success. So emit the WHOLE config,
# sourced from research/shipped.py to keep one source of truth.
$eaInputs = [ordered]@{}
$shipped = Join-Path (Split-Path -Parent $PSScriptRoot) "research\shipped.py"
if (Test-Path $shipped) {
    $emitted = & python $shipped --tester-inputs 2>$null
    if ($LASTEXITCODE -eq 0 -and $emitted) {
        foreach ($line in $emitted) {
            if ($line -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$") {
                $eaInputs[$Matches[1]] = $Matches[2]
            }
        }
        "config from shipped.py: $($eaInputs.Count) inputs pinned"
    }
    else { "WARNING: could not read shipped.py; tester may reuse stale inputs" }
}
$eaInputs["InpDumpDiagnostics"] = $(if ($Mode -eq "Parity") { "true" } else { "false" })
$eaInputs["InpDumpFile"] = $diagName
foreach ($k in $Inputs.Keys) { $eaInputs[$k] = $Inputs[$k] }

$ini = @()
$ini += "[Tester]"
$ini += "Expert=GoldSignalsAI\GoldSignalsAI_EA.ex5"
$ini += "Symbol=$Symbol"
$ini += "Period=$Period"
$ini += "Model=$TickModel"
$ini += "Optimization=0"
$ini += "FromDate=$FromDate"
$ini += "ToDate=$ToDate"
$ini += "ForwardMode=0"
$ini += "Deposit=$Deposit"
$ini += "Currency=USD"
$ini += "Leverage=$Leverage"
$ini += "ExecutionMode=0"
$ini += "Visual=0"
$ini += "Report=$reportName"
$ini += "ReplaceReport=1"
$ini += "ShutdownTerminal=1"
$ini += ""
$ini += "[TesterInputs]"
foreach ($k in $eaInputs.Keys) { $ini += "$k=$($eaInputs[$k])" }

$iniPath = Join-Path $DataFolder "GoldSignalsAI_$Mode.ini"
# Write UTF-8 WITHOUT a BOM. PowerShell 5.1's "Out-File -Encoding utf8" prepends
# EF BB BF, which makes the first line "\ufeff[Tester]" - not a section header -
# so MT5 ignores the entire /config and silently starts with the last profile.
[IO.File]::WriteAllText($iniPath, ($ini -join "`r`n"), (New-Object Text.UTF8Encoding($false)))
"wrote $iniPath"
$ini | ForEach-Object { "  $_" }

# MT5 ignores /config while an instance of the same install is running.
$running = Get-Process -Name terminal64 -ErrorAction SilentlyContinue
if ($running) {
    "closing the running terminal first (pid $($running.Id -join ','))"
    $running | Stop-Process -Force
    Start-Sleep -Seconds 3
}

if (Test-Path $reportPath) { Remove-Item $reportPath -Force }
$diagCandidates = @(
    (Join-Path $DataFolder "MQL5\Files\$diagName"),
    (Join-Path $DataFolder "Tester\Agent-127.0.0.1-3000\MQL5\Files\$diagName")
)
foreach ($d in $diagCandidates) { if (Test-Path $d) { Remove-Item $d -Force } }

"launching tester ($Mode, model $TickModel, $Symbol $Period, $FromDate .. $ToDate)"
$proc = Start-Process -FilePath $Terminal -ArgumentList "/config:`"$iniPath`"" -PassThru
if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
    "TIMEOUT after $TimeoutSeconds s - killing the terminal"
    $proc | Stop-Process -Force
}
"terminal exited with code $($proc.ExitCode)"

""
"--- artifacts ---"
if (Test-Path $reportPath) {
    "report : $reportPath ($((Get-Item $reportPath).Length) bytes)"
}
else { "report : NOT produced" }

$found = $false
foreach ($d in (Get-ChildItem -Path $DataFolder -Filter $diagName -Recurse -ErrorAction SilentlyContinue)) {
    "diag   : $($d.FullName) ($($d.Length) bytes)"
    $found = $true
}
if (-not $found) { "diag   : not produced" }

"--- tester log tail ---"
$logDir = Join-Path $DataFolder "Tester\logs"
$latest = Get-ChildItem $logDir -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
if ($latest) {
    $latest.FullName
    Get-Content $latest.FullName -Tail 30
}
else { "no tester log found" }
