$apiUrl = 'http://localhost:8000/api/health'
try {
  $response = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -TimeoutSec 10
  if ($response.StatusCode -ne 200) {
    throw "Unexpected status code $($response.StatusCode)"
  }
  Write-Output "OK $(Get-Date -Format o)"
} catch {
  Write-Output "ALERT $(Get-Date -Format o): $($_.Exception.Message)"
  exit 1
}
