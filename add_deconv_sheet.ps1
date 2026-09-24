# -*- coding: utf-8 -*-
# add_deconv_sheet.ps1
# 通过 Excel COM 打开指定 xlsm，新增「Deconvolution」工作表（布局+输入区），
# 注入 DeconvModule 宏（来自 deconv_macro.bas），添加「运行解卷积」按钮，保存。
param(
    [string]$xlsmPath = "D:\code test\chem\同位素计算及解卷积\ExactMass_Impurities_Deconv.xlsm"
)

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $xlsmPath
$bas = Join-Path $dir "deconv_macro.bas"
$log = Join-Path $dir "_deconv_inject_log.txt"
$diag = Join-Path $dir "_inject_diag.txt"

function Log($m) { Set-Content -Path $log -Value $m -Encoding UTF8; Add-Content -Path $diag -Value $m -Encoding UTF8 }
function Diag($m) { Add-Content -Path $diag -Value $m -Encoding UTF8 }

Set-Content -Path $diag -Value ("DIAG_START " + (Get-Date).ToString("o")) -Encoding UTF8
Diag ("TARGET=" + $xlsmPath)
Diag ("EXISTS=" + (Test-Path $xlsmPath))

Log "START $xlsmPath"

$pythonDefault = "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false

try {
    Log ("OPENING " + $xlsmPath)
    $wb = $excel.Workbooks.Open($xlsmPath)
    Log "OPENED"

    # 若已存在 Deconvolution 表则先删除（便于重复注入）
    foreach ($s in $wb.Sheets) {
        if ($s.Name -eq "Deconvolution") {
            $s.Delete()
            Log "DELETED EXISTING Deconvolution"
            break
        }
    }

    # 新增工作表
    $ws = $wb.Sheets.Add()
    $ws.Name = "Deconvolution"
    Log "SHEET ADDED"

    # ---- 静态布局 ----
    $ws.Cells.Item(1, 1).Value = "解卷积 / 富集度分析（质谱图解卷积）"
    $ws.Cells.Item(3, 1).Value = "输入区"
    $ws.Cells.Item(4, 1).Value = "分子式与电荷：自动读取『杂质枚举器』Calculator（第3-5行 + B9），无需重填"

    $ws.Cells.Item(5, 1).Value = "实测谱 CSV 路径："
    $ws.Cells.Item(5, 3).Value = "例: 解卷积\SPH20291_MS+_deconv\spec_msplus_from_jdx.csv"
    $ws.Cells.Item(6, 1).Value = "质心窗口下限 m/z（可选；不填则按理论质心±2Da 自动隔离电荷簇）"
    $ws.Cells.Item(7, 1).Value = "质心窗口上限 m/z（可选）"
    $ws.Cells.Item(8, 1).Value = "运行 NNLS 逐杂质（TRUE/FALSE）"
    $ws.Cells.Item(8, 2).Value = "TRUE"
    $ws.Cells.Item(9, 1).Value = "Python 可执行文件路径"
    $ws.Cells.Item(9, 2).Value = $pythonDefault

    # 按钮
    $btn = $ws.Buttons().Add(460, 18, 150, 38)
    $btn.OnAction = "RunDeconv"
    $btn.Caption = "运行解卷积"
    $btn.Font.Size = 12
    $btn.Font.Bold = $true
    Log "BUTTON ADDED"

    # 结果区标签
    $ws.Cells.Item(11, 1).Value = "状态："
    $ws.Cells.Item(12, 1).Value = "实测质心 m/z"
    $ws.Cells.Item(13, 1).Value = "天然质心 m/z"
    $ws.Cells.Item(14, 1).Value = "全标记质心 m/z"
    $ws.Cells.Item(15, 1).Value = "平均标记原子数"
    $ws.Cells.Item(16, 1).Value = "平均富集度 %"
    $ws.Cells.Item(17, 1).Value = "可标记位点总数"
    $ws.Cells.Item(19, 1).Value = "【结果 · NNLS 逐杂质（大分子可能病态，以质心法为准）】"
    $ws.Cells.Item(20, 1).Value = "相对含量合计 %"
    $ws.Cells.Item(21, 1).Value = "NNLS 可靠性"
    $ws.Cells.Item(22, 1).Value = "说明"
    $ws.Cells.Item(24, 1).Value = "NNLS 逐杂质相对含量表"
    $ws.Cells.Item(25, 1).Value = "排名"
    $ws.Cells.Item(25, 2).Value = "杂质"
    $ws.Cells.Item(25, 3).Value = "相对含量%"
    $ws.Cells.Item(25, 4).Value = "简并"
    $ws.Cells.Item(25, 5).Value = "主成分"

    # 列宽
    $ws.Columns.Item(1).ColumnWidth = 42
    $ws.Columns.Item(2).ColumnWidth = 22
    $ws.Columns.Item(3).ColumnWidth = 30
    $ws.Columns.Item(4).ColumnWidth = 8
    $ws.Columns.Item(5).ColumnWidth = 10

    # ---- 注入 VBA 模块 ----
    Log "ADDING VBAMODULE"
    $vbcomp = $wb.VBProject.VBComponents.Add(1)
    $vbcomp.Name = "DeconvModule"
    $code = [System.IO.File]::ReadAllText($bas, [System.Text.Encoding]::UTF8)
    $vbcomp.CodeModule.AddFromString($code)
    Log "VBA ADDED"

    $wb.Save()
    Diag ("SAVED target=" + $xlsmPath + " mtime=" + ((Get-Item $xlsmPath).LastWriteTime.ToString("o")))
    Log "SAVED"
    $wb.Close($true)
    Diag "CLOSED"
    Log "DONE"
} catch {
    Log ("ERR: " + $_.Exception.Message)
    try { $wb.Close($false) } catch {}
} finally {
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
