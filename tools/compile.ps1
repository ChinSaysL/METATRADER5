# Compile the GoldSignalsAI EA and indicator with the MetaEditor CLI.
# MetaEditor's exit code is the number of errors; its log is UTF-16.
# Compiles the copies inside the MT5 data folder, so run deploy.ps1 first.

param(
    [string]$DataFolder = "$env:APPDATA\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075",
    [string]$MetaEditor = "C:\Program Files\MetaTrader 5\metaeditor64.exe",
    [switch]$FailOnWarning
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $MetaEditor)) { throw "MetaEditor not found: $MetaEditor" }

$targets = @(
    "MQL5\Experts\GoldSignalsAI\GoldSignalsAI_EA.mq5",
    "MQL5\Indicators\GoldSignalsAI\GoldSignalsAI_Signals.mq5",
    "MQL5\Scripts\GoldSignalsAI\GoldSignalsAI_Dump.mq5"
)

$totalErrors = 0
$totalWarnings = 0

foreach ($t in $targets) {
    $file = Join-Path $DataFolder $t
    if (-not (Test-Path $file)) { throw "source not deployed: $file" }
    $log = [System.IO.Path]::ChangeExtension($file, ".compile.log")
    if (Test-Path $log) { Remove-Item $log -Force }

    "=== compiling $t ==="
    $proc = Start-Process -FilePath $MetaEditor `
        -ArgumentList "/compile:`"$file`"", "/log:`"$log`"" `
        -Wait -PassThru -WindowStyle Hidden
    $code = $proc.ExitCode

    if (Test-Path $log) {
        $text = Get-Content $log -Encoding Unicode
        # Keep the diagnostics; drop MetaEditor's banner noise.
        $lines = $text | Where-Object { $_ -match "error|warning|information: (compil|check)" }
        if ($lines) { $lines | ForEach-Object { "  $_" } }
        $errs = @($text | Where-Object { $_ -match ":\s*error\s" }).Count
        $warns = @($text | Where-Object { $_ -match ":\s*warning\s" }).Count
        $totalErrors += $errs
        $totalWarnings += $warns
        "  -> exit $code, $errs error(s), $warns warning(s)"
    }
    else {
        "  -> exit $code, no log produced"
        $totalErrors += $code
    }
}

""
"TOTAL: $totalErrors error(s), $totalWarnings warning(s)"
if ($totalErrors -gt 0) { exit 1 }
if ($FailOnWarning -and $totalWarnings -gt 0) { exit 2 }
exit 0
