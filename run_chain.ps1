param(
    [Parameter(Mandatory=$true, Position=0)]
    [string[]]$Pdf,

    [Parameter(Mandatory=$true, Position=1)]
    [string]$RootLabel,

    [string]$RootModel = "",

    [ValidateSet("full", "dry-run")]
    [string]$Mode = "full",

    [switch]$RebuildRootModel
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $ProjectRoot "scripts\run_industry_chain_one_click.py"

$ArgsList = @(
  "run", "--no-capture-output", "-n", "chain", "python", $ScriptPath,
  "--pdf"
) + $Pdf + @(
  "--root-label", $RootLabel,
  "--mode", $Mode
)

if ($RootModel) {
  $ArgsList += @("--root-model", $RootModel)
}

if ($RebuildRootModel) {
  $ArgsList += "--rebuild-root-model"
}

& conda @ArgsList
