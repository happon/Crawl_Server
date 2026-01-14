<# 
stage4d_import_to_opencti_direct.ps1

USAGE:
  powershell -ExecutionPolicy Bypass -File .\stage4d_import_to_opencti_direct.ps1 -BundlePath ".\stage4_stix_bundle.json"
  powershell -ExecutionPolicy Bypass -File .\stage4d_import_to_opencti_direct.ps1 -BundlePath ".\stage4_stix_bundle.json" -NoAutoValidate

ENV (.env in the same folder):
  OPENCTI_URL=http://localhost:8080
  OPENCTI_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxx
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [string]$BundlePath = ".\stage4_stix_bundle.json",

  [Parameter(Mandatory = $false)]
  [string]$GraphqlUrl,

  [Parameter(Mandatory = $false)]
  [string]$Token,

  [Parameter(Mandatory = $false)]
  [switch]$NoAutoValidate,

  [Parameter(Mandatory = $false)]
  [int]$HttpTimeoutSec = 300
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.IO

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------

function Import-DotEnv {
  param([Parameter(Mandatory=$true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $false }
  $lines = Get-Content -LiteralPath $Path -Encoding UTF8
  foreach ($line in $lines) {
    $trim = $line.Trim()
    if ($trim.Length -eq 0) { continue }
    if ($trim.StartsWith("#")) { continue }
    $parts = $trim.Split('=', 2)
    if ($parts.Count -ne 2) { continue }
    $key = $parts[0].Trim()
    $val = $parts[1].Trim()
    if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
      if ($val.Length -ge 2) { $val = $val.Substring(1, $val.Length - 2) }
    }
    [Environment]::SetEnvironmentVariable($key, $val, "Process")
  }
  return $true
}

# --- curl.exe を使ったアップロード関数 ---
function Invoke-GraphQLUpload-Curl {
  param(
    [string]$Url,
    [string]$TokenValue,
    [string]$Mutation,
    [string]$FilePath
  )

  $curlPath = "curl.exe"
  try {
    $cmd = Get-Command "curl.exe" -ErrorAction Stop
    $curlPath = $cmd.Source
  } catch {
    Write-Host "❌ 'curl.exe' not found. This script requires Windows 10/11 built-in curl." -ForegroundColor Red
    throw "curl.exe missing"
  }

  $fullPath = (Resolve-Path -LiteralPath $FilePath).Path
  if (-not (Test-Path $fullPath)) { throw "File not found" }

  $tmpOps = [System.IO.Path]::GetTempFileName()
  $tmpMap = [System.IO.Path]::GetTempFileName()

  try {
    $operations = @{
      query     = $Mutation
      variables = @{ file = $null }
    } | ConvertTo-Json -Depth 10 -Compress

    $map = @{ "0" = @("variables.file") } | ConvertTo-Json -Compress

    [System.IO.File]::WriteAllText($tmpOps, $operations)
    [System.IO.File]::WriteAllText($tmpMap, $map)

    Write-Host "🚀 Executing curl.exe for robust upload..." -ForegroundColor Cyan

    $argsList = @(
      "-s",              
      "-S",              
      "-X", "POST",
      "-H", "`"Authorization: Bearer $TokenValue`"",
      "-F", "`"operations=<$tmpOps`"",
      "-F", "`"map=<$tmpMap`"",
      "-F", "`"0=@$fullPath`"",
      "`"$Url`""
    )

    $pinfo = New-Object System.Diagnostics.ProcessStartInfo
    $pinfo.FileName = $curlPath
    $pinfo.Arguments = $argsList -join " "
    $pinfo.RedirectStandardOutput = $true
    $pinfo.RedirectStandardError = $true
    $pinfo.UseShellExecute = $false
    $pinfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $pinfo
    $p.Start() | Out-Null
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()

    if ($p.ExitCode -ne 0) {
      Write-Host "❌ curl failed (ExitCode: $($p.ExitCode))" -ForegroundColor Red
      Write-Host $stderr -ForegroundColor Yellow
      throw "curl execution failed"
    }

    return [pscustomobject]@{
      StatusCode = 200
      Reason     = "OK"
      Body       = $stdout
    }

  } finally {
    if (Test-Path $tmpOps) { Remove-Item $tmpOps -ErrorAction SilentlyContinue }
    if (Test-Path $tmpMap) { Remove-Item $tmpMap -ErrorAction SilentlyContinue }
  }
}

function Invoke-GraphQLJson {
  param(
    [string]$Url,
    [string]$TokenValue,
    [string]$Query,
    $Variables
  )
  [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
  
  $payload = @{ query = $Query; variables = $Variables } | ConvertTo-Json -Depth 30
  $handler = New-Object System.Net.Http.HttpClientHandler
  $client  = New-Object System.Net.Http.HttpClient($handler)
  $client.Timeout = [TimeSpan]::FromSeconds($HttpTimeoutSec)

  try {
    $client.DefaultRequestHeaders.Add("Authorization", "Bearer $TokenValue")
    $content = New-Object System.Net.Http.StringContent($payload, [System.Text.Encoding]::UTF8, "application/json")
    $resp = $client.PostAsync($Url, $content).GetAwaiter().GetResult()
    $body = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    return [pscustomobject]@{ StatusCode = [int]$resp.StatusCode; Body = $body }
  } catch {
    Write-Host "❌ API Request Failed: $($_.Exception.Message)" -ForegroundColor Red
    throw
  } finally {
    $client.Dispose()
  }
}

# ----------------- MAIN -----------------

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath   = Join-Path $scriptDir ".env"
Import-DotEnv -Path $envPath | Out-Null

# Token & URL Setup
if (-not $Token) { $Token = $env:OPENCTI_TOKEN }
if (-not $Token) { Write-Host "❌ OPENCTI_TOKEN missing." -f Red; exit 1 }

if (-not $GraphqlUrl) { $GraphqlUrl = $env:OPENCTI_GRAPHQL_URL }
if (-not $GraphqlUrl) {
    $base = $env:OPENCTI_URL
    if ($base) { $GraphqlUrl = $base.TrimEnd('/') + "/graphql" }
    else { $GraphqlUrl = "http://localhost:8080/graphql" }
}

# localhost fix
if ($GraphqlUrl -match "localhost") {
    $GraphqlUrl = $GraphqlUrl -replace "localhost", "127.0.0.1"
    Write-Host "ℹ️ Switching to 127.0.0.1" -f Cyan
}

$bundleFull = (Resolve-Path -LiteralPath $BundlePath).Path
Write-Host "Target: $GraphqlUrl"
Write-Host "Bundle: $bundleFull"

# Connection Check
try {
    $uri = [Uri]$GraphqlUrl
    $t = Test-NetConnection -ComputerName $uri.Host -Port $uri.Port -WarningAction SilentlyContinue
    if (-not $t.TcpTestSucceeded) { throw "Port closed" }
    Write-Host "✅ Connectivity Check: OK" -f Green
} catch {
    Write-Host "⛔ Cannot reach server. Is OpenCTI running?" -f Red
    exit 1
}

$uploadMutation = @'
mutation UploadImport($file: Upload!) {
  uploadImport(file: $file) {
    id
    uploadStatus
  }
}
'@

# --- Run Upload via curl ---
try {
    $up = Invoke-GraphQLUpload-Curl -Url $GraphqlUrl -TokenValue $Token -Mutation $uploadMutation -FilePath $bundleFull
} catch {
    Write-Host "⛔ Upload failed." -f Red
    exit 1
}

$json = $up.Body | ConvertFrom-Json
if ($json.errors) {
    Write-Host "❌ GraphQL Errors:" -f Red
    $json.errors | ConvertTo-Json -Depth 5
    exit 1
}

$importInfo = $json.data.uploadImport
if (-not $importInfo) { Write-Host "❌ No data returned."; exit 1 }

$importId = $importInfo.id
Write-Host "✅ Upload Success! ID: $importId" -f Green

if ($NoAutoValidate) { exit 0 }

# --- Introspection & Validation (Robust) ---
Write-Host "🔎 Starting Auto-Validation..."
$schemaQ = @'
query { __schema { mutationType { fields { name args { name type { kind name ofType { kind name } } } } } } }
'@

$schemaResp = Invoke-GraphQLJson -Url $GraphqlUrl -TokenValue $Token -Query $schemaQ -Variables $null

# エラーハンドリング修正
$schemaJson = $null
try {
    $schemaJson = $schemaResp.Body | ConvertFrom-Json
} catch {
    Write-Host "⚠️ Introspection response is not valid JSON. Skipping auto-validation." -ForegroundColor Yellow
    exit 0
}

# Introspectionが無効化されている場合のハンドリング
if ($schemaJson.errors) {
    # RESOURCE_NOT_FOUND または introspection not authorized が一般的
    Write-Host "ℹ️ Server security prevents Schema Introspection." -ForegroundColor Cyan
    Write-Host "   Upload is COMPLETE. Please verify in OpenCTI Console (Data > Import)." -ForegroundColor Green
    exit 0
}

if (-not $schemaJson.data) {
    Write-Host "⚠️ Unexpected response format (no data). Skipping auto-validation." -ForegroundColor Yellow
    exit 0
}

$fields = $schemaJson.data.__schema.mutationType.fields

# Validate mutationを探す
$best = $null
foreach ($f in $fields) {
    if ($f.name -match 'import.*validate|validate.*import') {
        $best = $f; break
    }
}
# 見つからなければ汎用的な validate を探す
if (-not $best) {
    foreach ($f in $fields) { if ($f.name -eq 'validate') { $best = $f; break } }
}

if (-not $best) { 
    Write-Host "⚠️ Validation mutation not found in schema. Please validate manually." -ForegroundColor Yellow
    exit 0 
}

$mName = $best.name
$argName = "id" # default guess
if ($best.args) {
    foreach ($a in $best.args) { if ($a.name -eq 'id') { $argName = 'id'; break } else { $argName = $a.name } }
}

Write-Host ("📝 Validating with: {0}({1}: {2})" -f $mName, $argName, $importId)

$valQ = 'mutation { {0}({1}: "{2}") }' -f $mName, $argName, $importId

$valRes = Invoke-GraphQLJson -Url $GraphqlUrl -TokenValue $Token -Query $valQ -Variables $null

if ($valRes.Body -match "errors") {
    Write-Host "❌ Validation Error:" -f Red
    Write-Host $valRes.Body
} else {
    Write-Host "✅ Validation Complete!" -f Green
}