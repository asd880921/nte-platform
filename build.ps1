# NTE-Platform — 一鍵建置腳本 (onedir + zip)
# 會自動：建立 .venv（若無）→ 安裝 requirements → 打包 → 複製 scripts → 壓成 zip
# 用法（在專案根目錄）：  powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
$appOut = Join-Path $root "dist\NTE-Platform"
$zipOut = Join-Path $root "dist\NTE-Platform.zip"

# 關掉可能佔用交付資料夾的執行中 exe（僅比對本專案 dist 路徑，不影響其他程式）
function Stop-BuiltExe {
    Get-Process | Where-Object { $_.Path -like "*\dist\NTE-Platform\*" } |
        ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
}

Write-Host "==> 1/5 準備虛擬環境 .venv" -ForegroundColor Cyan
if (-not (Test-Path $py)) {
    python -m venv $venv
}

Write-Host "==> 2/5 安裝相依套件 (requirements.txt)" -ForegroundColor Cyan
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r (Join-Path $root "requirements.txt")

Write-Host "==> 3/5 打包 (PyInstaller onedir)" -ForegroundColor Cyan
Stop-BuiltExe
Start-Sleep -Milliseconds 500
& $py -m PyInstaller --clean --noconfirm (Join-Path $root "nte_platform.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失敗 (exit $LASTEXITCODE)" }

Write-Host "==> 4/5 複製 scripts 進交付資料夾" -ForegroundColor Cyan
Copy-Item (Join-Path $root "scripts") (Join-Path $appOut "scripts") -Recurse -Force

Write-Host "==> 5/5 壓成 zip" -ForegroundColor Cyan
# 壓縮前再關一次執行中的 exe（避免 _internal 檔案被佔用），並加重試處理防毒短暫鎖檔
Stop-BuiltExe
Start-Sleep -Milliseconds 800
Remove-Item $zipOut -Force -ErrorAction SilentlyContinue
$zipped = $false
for ($i = 1; $i -le 5; $i++) {
    try {
        Compress-Archive -Path $appOut -DestinationPath $zipOut -CompressionLevel Optimal -Force
        $zipped = $true
        break
    } catch {
        Write-Host ("   檔案被佔用，重試 {0}/5 ..." -f $i) -ForegroundColor Yellow
        Start-Sleep -Seconds 1
    }
}
if (-not $zipped) {
    throw "壓縮失敗：檔案持續被佔用。請關閉執行中的 NTE-Platform.exe（或暫停防毒即時掃描）後重試。"
}

$size = (Get-ChildItem $appOut -Recurse -File | Measure-Object -Property Length -Sum).Sum
$zsize = (Get-Item $zipOut).Length
Write-Host ("完成:") -ForegroundColor Green
Write-Host ("  資料夾 → {0}  ({1:N1} MB)" -f $appOut, ($size / 1MB)) -ForegroundColor Green
Write-Host ("  壓縮包 → {0}  ({1:N1} MB)" -f $zipOut, ($zsize / 1MB)) -ForegroundColor Green
Write-Host "把 zip 交給使用者，解壓後進資料夾雙擊 NTE-Platform.exe 即可。"
