# PowerShell wrapper - one-command law query
# Usage: .\law.ps1 "关键词" [-TopK 5]
param(
    [Parameter(Mandatory=$true)][string]$Query,
    [int]$TopK = 5
)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$scriptDir\query.py" $Query --top-k $TopK --format prompt
