param(
  [string]$BundlePath = '.\stage4_stix_bundle.json',
  [string]$GraphqlPath = '/graphql'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail([string]$msg) {
  Write-Host ('❌ ' + $msg) -ForegroundColor Red
  exit 1
}

# ---- .env 読み込み（このps1と同じフォルダ）----
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $scriptDir '.env'

if (Test-Path $envPath) {
  Get-Content $envPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    if ($line.StartsWith('#')) { return }

    $parts = $line.Split('=', 2)
    if ($parts.Count -ne 2) { return }

    $k = $parts[0].Trim()
    $v = $parts[1].Trim()

    if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
      $v = $v.Substring(1, $v.Length - 2)
    }

    [Environment]::SetEnvironmentVariable($k, $v, 'Process')
  }
  Write-Host ('✅ Loaded .env from: ' + $envPath)
} else {
  Write-Host ('ℹ️ .env not found at: ' + $envPath)
}

$OpenCtiUrl = $env:OPENCTI_URL
$Token      = $env:OPENCTI_TOKEN

if ([string]::IsNullOrWhiteSpace($OpenCtiUrl)) { Fail 'OPENCTI_URL が未設定です（.envに設定）' }
if ([string]::IsNullOrWhiteSpace($Token))      { Fail 'OPENCTI_TOKEN が未設定です（.envに設定）' }

if (-not (Test-Path $BundlePath)) { Fail ('Bundle が見つかりません: ' + $BundlePath) }

$OpenCtiUrl = $OpenCtiUrl.TrimEnd('/')
$GraphqlPath = '/' + $GraphqlPath.TrimStart('/')
$endpoint = $OpenCtiUrl + $GraphqlPath

Write-Host ('OpenCTI GraphQL: ' + $endpoint)
Write-Host ('Bundle: ' + (Resolve-Path $BundlePath).Path)

# ---- GraphQL mutation（シングルクォートで固定）----
$query = 'mutation FileUploaderGlobalMutation($file: Upload!) { uploadImport(file: $file) { id name uploadStatus } }'

$operationsObj = @{
  query = $query
  variables = @{ file = $null }
}
$operationsJson = $operationsObj | ConvertTo-Json -Depth 10 -Compress

$mapObj = @{ '0' = @('variables.file') }
$mapJson = $mapObj | ConvertTo-Json -Compress

Add-Type -AssemblyName System.Net.Http

$http = New-Object System.Net.Http.HttpClient
$http.DefaultRequestHeaders.Authorization =
  New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $Token)

$multipart = New-Object System.Net.Http.MultipartFormDataContent

$opContent  = New-Object System.Net.Http.StringContent($operationsJson, [System.Text.Encoding]::UTF8, 'application/json')
$mapContent = New-Object System.Net.Http.StringContent($mapJson,        [System.Text.Encoding]::UTF8, 'application/json')
$multipart.Add($opContent, 'operations')
$multipart.Add($mapContent, 'map')

$fileBytes = [System.IO.File]::ReadAllBytes((Resolve-Path $BundlePath).Path)
$fileContent = [System.Net.Http.ByteArrayContent]::new($fileBytes)
$fileContent.Headers.ContentType = New-Object System.Net.Http.Headers.MediaTypeHeaderValue('application/json')
$multipart.Add($fileContent, '0', [System.IO.Path]::GetFileName($BundlePath))

Write-Host '🚀 Uploading bundle...'
$resp = $http.PostAsync($endpoint, $multipart).Result
$body = $resp.Content.ReadAsStringAsync().Result
# --- ここから置換（$body 取得直後） ---
Write-Host "🔍 Response body:"
Write-Host $body

# JSON パース（必ず $json を定義）
try {
  $json = $body | ConvertFrom-Json
} catch {
  Fail "レスポンスのJSONパースに失敗しました。bodyを確認してください。"
}

# GraphQL errors がある場合のみエラー扱い（StrictMode対応）
if (($json.PSObject.Properties.Name -contains 'errors') -and ($null -ne $json.errors)) {
  Write-Host ($json.errors | ConvertTo-Json -Depth 10)
  Fail 'GraphQL errors が返りました。'
}

# data.uploadImport を読む（存在チェックも StrictMode 対応）
if (-not ($json.PSObject.Properties.Name -contains 'data')) {
  Fail "レスポンスに data がありません。bodyを確認してください。"
}
if (-not ($json.data.PSObject.Properties.Name -contains 'uploadImport')) {
  Fail "レスポンスに data.uploadImport がありません。bodyを確認してください。"
}

$importInfo = $json.data.uploadImport
if ($null -eq $importInfo) {
  Fail 'uploadImport の戻りが null です。'
}
# --- ここまで置換 ---

Write-Host '✅ uploadImport accepted'
Write-Host ('id: ' + $importInfo.id)
Write-Host ('name: ' + $importInfo.name)
Write-Host ('uploadStatus: ' + $importInfo.uploadStatus)

