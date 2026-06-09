# GenAI Stack CLI launcher (PowerShell)
# Usage: .\switch-model.ps1 status
#        .\switch-model.ps1 use genai

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ScriptDir
try {
    uv run python cli.py @Args
} finally {
    Pop-Location
}
