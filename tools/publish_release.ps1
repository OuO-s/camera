# 把 RealSense 驱动包发到 GitHub Release（不进 git，绕开 100MB 单文件限制）
#
#   pwsh -File tools\publish_release.ps1
#   pwsh -File tools\publish_release.ps1 -Tag v1.0-driver -Files "C:\path\D435_SDK_v2.58.4_Windows.zip"
#
# 令牌来源: 1) 环境变量 GH_TOKEN  2) git 凭据管理器里的 github.com 凭据
param(
    [string]$Repo = "OuO-s/camera",
    [string]$Tag = "v1.0-driver",
    [string]$Title = "D435 相机驱动包 (Windows x64)",
    [string[]]$Files
)

$ErrorActionPreference = "Stop"

function Get-Token {
    if ($env:GH_TOKEN) { return $env:GH_TOKEN }
    $out = "protocol=https`nhost=github.com`n`n" | git credential fill 2>$null
    $line = $out | Select-String '^password=(.+)$'
    if (-not $line) { throw "取不到令牌: 请设置 `$env:GH_TOKEN, 或先 git 登录过 github.com" }
    return $line.Matches.Groups[1].Value
}

if (-not $Files) {
    $cands = @(
        "$env:USERPROFILE\Desktop\D435_SDK_v2.58.4_Windows.zip",
        "$env:USERPROFILE\Downloads\D435_SDK_v2.58.4_Windows.zip"
    )
    $Files = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Files) { throw "没找到驱动包, 请用 -Files 指定路径" }
}
$Files = $Files | Where-Object { Test-Path $_ } | ForEach-Object { (Resolve-Path $_).Path }
if (-not $Files) { throw "指定的文件都不存在" }

$tok = Get-Token
$H = @{ Authorization = "Bearer $tok"; Accept = "application/vnd.github+json"; "User-Agent" = "release-upload" }

Write-Host "== 目标仓库 $Repo  标签 $Tag" -ForegroundColor Cyan
foreach ($f in $Files) {
    $mb = [math]::Round((Get-Item $f).Length / 1MB, 1)
    Write-Host ("   附件: {0}  ({1} MB)" -f (Split-Path $f -Leaf), $mb)
}

# 1) 找已有 release, 没有就建
$rel = $null
try { $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/tags/$Tag" -Headers $H } catch { }
if (-not $rel) {
    $body = @{ tag_name = $Tag; name = $Title; target_commitish = "main"; draft = $false; prerelease = $false;
               body = "Intel RealSense D435 官方 SDK 安装包（Windows x64）。`n`n" +
                      "仓库本身只放免安装 Viewer 与自检脚本；完整安装包装这里，避免超过 GitHub 100MB 单文件限制。`n`n" +
                      "安装步骤见 `driver/README_先看我.md`。" } | ConvertTo-Json
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases" -Headers $H -Method Post `
        -Body $body -ContentType "application/json"
    Write-Host "== 已创建 Release: $($rel.html_url)" -ForegroundColor Green
} else {
    Write-Host "== 复用已有 Release: $($rel.html_url)" -ForegroundColor Yellow
}

# 2) 逐个上传附件(已存在同名则先删)
$uploadBase = "https://uploads.github.com/repos/$Repo/releases/$($rel.id)/assets"
foreach ($f in $Files) {
    $name = Split-Path $f -Leaf
    $old = $rel.assets | Where-Object { $_.name -eq $name }
    if ($old) {
        Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/assets/$($old.id)" -Headers $H -Method Delete
        Write-Host "   已删除同名旧附件: $name"
    }
    Write-Host "   上传中: $name ..." -NoNewline
    $r = Invoke-RestMethod -Uri "$uploadBase`?name=$([uri]::EscapeDataString($name))" -Headers $H -Method Post `
        -InFile $f -ContentType "application/octet-stream"
    Write-Host (" 完成  {0} MB" -f [math]::Round($r.size / 1MB, 1)) -ForegroundColor Green
    Write-Host "   下载地址: $($r.browser_download_url)"
}

Write-Host "`n== 全部完成。把上面的下载地址填进 README.md 与 driver/README_先看我.md" -ForegroundColor Cyan
