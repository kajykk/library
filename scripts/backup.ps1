param(
  [switch]$Full
)

$backupRoot = 'E:\code\library\backups'
$dataRoot = 'E:\code\library\server\data'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$mode = if ($Full) { 'full' } else { 'incremental' }
$target = Join-Path $backupRoot "$mode-$stamp"
New-Item -ItemType Directory -Force -Path $target | Out-Null

if (-not $Full) {
  robocopy "$dataRoot" "$target\data" /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
} else {
  robocopy "$dataRoot" "$target\data" /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
}

Write-Output "Backup completed: $target"
