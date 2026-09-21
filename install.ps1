param([string]$BlenderVersion = '5.2')
$ErrorActionPreference = 'Stop'
if ($BlenderVersion -notmatch '^\d+\.\d+$') { throw 'Blender のバージョン形式が不正です。' }
$source = $PSScriptRoot
$base = Join-Path $env:APPDATA "Blender Foundation\Blender\$BlenderVersion\extensions\user_default"
$target = Join-Path $base 'suketto'
New-Item -ItemType Directory -Path $base -Force | Out-Null
if (Test-Path -LiteralPath $target) {
    $existing = Get-Item -LiteralPath $target
    if ($existing.LinkType -ne 'Junction' -or $existing.Target -ne $source) {
        throw "既存のインストールがあるため上書きしません: $target"
    }
} else {
    New-Item -ItemType Junction -Path $target -Target $source | Out-Null
}
Write-Host "助人のインストール先: $target"
Write-Host 'Blender のプリファレンス → アドオンで「助人」を有効にしてください。'
