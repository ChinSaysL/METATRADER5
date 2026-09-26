# Copy the GoldSignalsAI sources into the MetaTrader 5 data folder.
# Plain copy, so no administrator rights and no junctions are needed.
# Run this after every source change, before compiling.

param(
    [string]$DataFolder = "$env:APPDATA\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "mt5"

if (-not (Test-Path $DataFolder)) { throw "MT5 data folder not found: $DataFolder" }
if (-not (Test-Path $src)) { throw "source folder not found: $src" }

$pairs = @(
    @{ From = "Experts\GoldSignalsAI";    To = "MQL5\Experts\GoldSignalsAI" },
    @{ From = "Indicators\GoldSignalsAI"; To = "MQL5\Indicators\GoldSignalsAI" },
    @{ From = "Include\GoldSignalsAI";    To = "MQL5\Include\GoldSignalsAI" },
    @{ From = "Scripts\GoldSignalsAI";    To = "MQL5\Scripts\GoldSignalsAI" }
)

foreach ($p in $pairs) {
    $from = Join-Path $src $p.From
    $to = Join-Path $DataFolder $p.To
    if (-not (Test-Path $from)) { continue }
    if (-not (Test-Path $to)) { New-Item -ItemType Directory -Force -Path $to | Out-Null }
    Copy-Item -Path (Join-Path $from "*") -Destination $to -Recurse -Force
    $n = (Get-ChildItem $from -File).Count
    "copied $n file(s)  $($p.From)  ->  $($p.To)"
}

"`ndeployed to $DataFolder"
