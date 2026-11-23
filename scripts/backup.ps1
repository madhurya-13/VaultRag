param(
  [string]$OutputDirectory = "backups"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $projectRoot $OutputDirectory
New-Item -ItemType Directory -Force -Path $target | Out-Null

$dump = Join-Path $target "vaultrag-$timestamp.dump"
Write-Host "Writing PostgreSQL custom-format dump to $dump"
docker exec vaultrag-postgres pg_dump -U vaultrag -d vaultrag -Fc -f /tmp/vaultrag.dump
docker cp vaultrag-postgres:/tmp/vaultrag.dump $dump
docker exec vaultrag-postgres rm -f /tmp/vaultrag.dump

Write-Host "Verifying the dump can be listed by pg_restore"
docker cp $dump vaultrag-postgres:/tmp/verify.dump
$listFile = Join-Path $target "vaultrag-$timestamp.toc.txt"
"" | Out-File -FilePath $listFile -Encoding utf8
docker exec vaultrag-postgres pg_restore --list /tmp/verify.dump | Out-File -FilePath $listFile -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Dump verification failed" }
docker exec vaultrag-postgres rm -f /tmp/verify.dump

Write-Host "Archiving the private document volume"
$docs = Join-Path $target "documents-$timestamp.tar.gz"
docker run --rm -v vaultrag_documents:/data:ro -v "${target}:/backup" alpine tar czf "/backup/documents-$timestamp.tar.gz" -C /data .

Write-Host "Backup complete:"
Get-ChildItem $dump, $docs | Select-Object FullName, Length
