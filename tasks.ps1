# ReclaimOS task runner for Windows PowerShell. Mirrors the Makefile exactly.
#   ./tasks.ps1 check
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'fmt', 'lint', 'types', 'test', 'check', 'gen', 'eval', 'report', 'clean')]
    [string]$Task = 'check'
)

$ErrorActionPreference = 'Stop'

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

switch ($Task) {
    'setup' { Invoke-Step 'sync' { uv sync --extra dev } }
    'fmt' { Invoke-Step 'format' { uv run ruff format . } }
    'lint' { Invoke-Step 'lint' { uv run ruff check --fix . } }
    'types' { Invoke-Step 'mypy' { uv run mypy } }
    'test' { Invoke-Step 'pytest' { uv run pytest } }
    'gen' { Invoke-Step 'generate' { uv run reclaimos gen --n 250 --seed 42 } }
    'eval' { Invoke-Step 'evaluate' { uv run reclaimos eval --policy all } }
    'report' { Invoke-Step 'report' { uv run reclaimos report } }
    'clean' {
        Write-Host '==> clean' -ForegroundColor Cyan
        Get-ChildItem -Path . -Include __pycache__, .pytest_cache, .mypy_cache, .ruff_cache -Recurse -Directory |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    'check' {
        Invoke-Step 'format --check' { uv run ruff format --check . }
        Invoke-Step 'lint' { uv run ruff check . }
        Invoke-Step 'mypy' { uv run mypy }
        Invoke-Step 'pytest' { uv run pytest }
        Write-Host 'all checks passed' -ForegroundColor Green
    }
}
