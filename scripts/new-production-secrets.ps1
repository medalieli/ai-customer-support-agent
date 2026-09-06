param([switch]$DevelopmentSelfSignedCertificate)
$ErrorActionPreference = "Stop"
$target = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\secrets"))
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe secrets target" }
New-Item -ItemType Directory -Force -Path $target | Out-Null
function New-RandomSecret([string]$Name) {
  $bytes = [byte[]]::new(48)
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
  $value = [Convert]::ToBase64String($bytes)
  [IO.File]::WriteAllText((Join-Path $target $Name), $value, [Text.UTF8Encoding]::new($false))
}
New-RandomSecret "postgres_admin_password.txt"
New-RandomSecret "postgres_runtime_password.txt"
New-RandomSecret "redis_password.txt"
if ($DevelopmentSelfSignedCertificate) {
  if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) { throw "openssl is required for the development certificate" }
  & openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 30 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" -keyout (Join-Path $target "tls.key") -out (Join-Path $target "tls.crt") 2>$null
  if ($LASTEXITCODE) { throw "Certificate generation failed" }
}
Write-Output "Generated gitignored mounted secrets. Replace or provision tls.crt/tls.key from a trusted CA."
