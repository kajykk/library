$apiUrl = 'http://localhost:8000/api/health'
try {
  # 非 2xx 时 Invoke-WebRequest 本身即抛异常，无需再判 StatusCode 死分支
  $response = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -TimeoutSec 10
  $body = $response.Content | ConvertFrom-Json
  if ($body.status -ne 'ok') {
    throw "health status = $($body.status); checks = $($body.checks | ConvertTo-Json -Compress)"
  }
  Write-Output "OK $(Get-Date -Format o)"
} catch {
  Write-Output "ALERT $(Get-Date -Format o): $($_.Exception.Message)"
  exit 1
}
