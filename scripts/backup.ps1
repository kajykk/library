<#
.SYNOPSIS
  项目数据备份：SQLite/文件数据目录 + Postgres（compose 部署时）。

.PARAMETER Full
  全量镜像（/MIR，目标与源完全一致）；默认增量模式（仅复制较新/新增文件，不删除）。

.EXAMPLE
  ./scripts/backup.ps1            # 增量
  ./scripts/backup.ps1 -Full      # 全量
#>
param(
  [switch]$Full,
  # 数据根目录：默认取本仓库的 server\data（此前硬编码指向旧 checkout 路径，备份的不是本项目数据）
  [string]$DataRoot = (Join-Path $PSScriptRoot '..\server\data'),
  [string]$BackupRoot = (Join-Path $PSScriptRoot '..\backups')
)

$ErrorActionPreference = 'Stop'

$dataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$backupRoot = [System.IO.Path]::GetFullPath($BackupRoot)

if (-not (Test-Path -LiteralPath $dataRoot)) {
  Write-Error "数据目录不存在: $dataRoot"
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$mode = if ($Full) { 'full' } else { 'incremental' }
$target = Join-Path $backupRoot "$mode-$stamp"
New-Item -ItemType Directory -Force -Path $target | Out-Null

# 增量 /XO：源较旧则跳过；全量 /MIR：镜像（会删除目标多余项）
$robocopyArgs = @($dataRoot, (Join-Path $target 'data'), '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS')
if ($Full) { $robocopyArgs += '/MIR' } else { $robocopyArgs += '/XO' }

& robocopy @robocopyArgs | Out-Null
# robocopy 退出码：0-7 成功（1=有复制），>=8 失败。此前未校验，失败也打印 completed。
if ($LASTEXITCODE -ge 8) {
  Write-Error "robocopy failed with exit code $LASTEXITCODE"
}
Write-Output "[$mode] data directory backed up: $(Join-Path $target 'data')"

# Postgres 逻辑备份：检测到 compose 服务在运行时执行 pg_dump（best-effort）
$pgRunning = docker compose ps --services --filter status=running 2>$null | Select-String -SimpleMatch 'db'
if ($pgRunning) {
  $dumpFile = Join-Path $target "postgres_knowledge_base_$stamp.sql"
  & docker compose exec -T db pg_dump -U kb -d knowledge_base > $dumpFile
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "pg_dump failed (exit $LASTEXITCODE)，本次仅完成文件备份"
  } else {
    Write-Output "[$mode] postgres dump: $dumpFile"
  }
} else {
  Write-Output '[skip] compose db 服务未运行，跳过 Postgres 备份'
}

Write-Output "Backup completed: $target"
