# -*- coding: utf-8 -*-
# inject_safe.ps1 - 安全注入「解卷积」工作表到真实 xlsm
# 安全检查：若已有 Excel 进程 / 文件被占用，则拒绝（避免连到用户实例并关闭其窗口）
$ErrorActionPreference = "Stop"
$dir = "D:\code test\chem\同位素计算及解卷积"
$xlsm = Join-Path $dir "ExactMass_Impurities_Deconv.xlsm"
$log  = Join-Path $dir "_deconv_inject_log.txt"

# 1) 已有 Excel 进程 → 拒绝（会连到用户实例，可能关闭其窗口/丢失未保存内容）
$xl = Get-Process -Name "excel" -ErrorAction SilentlyContinue
if ($xl) {
    Set-Content -Path $log -Value "ABORT: 检测到 Excel 正在运行（可能含其他打开的工作簿）。请先关闭所有 Excel 窗口，再重试注入。" -Encoding UTF8
    Write-Host "ABORT: Excel is running. Please close all Excel windows first, then retry."
    exit 0
}

# 2) 文件被其他进程占用 → 拒绝
try {
    $fs = [System.IO.File]::Open($xlsm, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
    $fs.Close()
} catch {
    Set-Content -Path $log -Value ("ABORT: 文件被占用（可能被其他程序打开）：" + $_.Exception.Message) -Encoding UTF8
    Write-Host "ABORT: target file is locked by another process. Close it and retry."
    exit 0
}

# 3) 安全：调用注入脚本
Write-Host "SAFE: no Excel running, file not locked. Injecting..."
& (Join-Path $dir "add_deconv_sheet.ps1") -xlsmPath $xlsm
