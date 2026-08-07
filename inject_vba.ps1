# -*- coding: utf-8 -*-
"""
通过 Excel COM：打开 .xlsx → 注入 VBA 宏 + 按钮 → 另存为 .xlsm
"""
$ErrorActionPreference = "Stop"
$xlsx = "D:\code test\chem\同位素计算及解卷积\ExactMass_Calculator_v2.xlsx"
$xlsm = "D:\code test\chem\同位素计算及解卷积\ExactMass_Calculator_v2.xlsm"
$bas  = "D:\code test\chem\同位素计算及解卷积\theo_macro.bas"
$log  = "D:\code test\chem\同位素计算及解卷积\_vba_log.txt"

Set-Content -Path $log -Value "START" -Encoding UTF8

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false

try {
    Set-Content -Path $log -Value ("OPENING " + $xlsx) -Encoding UTF8
    $wb = $excel.Workbooks.Open($xlsx)
    Set-Content -Path $log -Value "OPENED" -Encoding UTF8

    Set-Content -Path $log -Value "ACCESSING VBPROJECT" -Encoding UTF8
    $vbcomp = $wb.VBProject.VBComponents.Add(1)
    $vbcomp.Name = "TheoModule"
    $code = [System.IO.File]::ReadAllText($bas, [System.Text.Encoding]::UTF8)
    $vbcomp.CodeModule.AddFromString($code)
    Set-Content -Path $log -Value "VBA ADDED" -Encoding UTF8

    $ws = $wb.Sheets("Calculator")
    # 按钮：left, top, width, height (points)。放 E40 附近
    $btn = $ws.Buttons().Add(650, 48, 150, 40)
    $btn.OnAction = "RunTheo"
    $btn.Caption = "计算前50峰"
    $btn.Font.Size = 12
    $btn.Font.Bold = $true
    Set-Content -Path $log -Value "BUTTON ADDED" -Encoding UTF8

    # 另存为 xlsm (FileFormat 52 = xlOpenXMLWorkbookMacroEnabled)
    $wb.SaveAs($xlsm, 52)
    Set-Content -Path $log -Value "SAVED AS XLSM" -Encoding UTF8
    $wb.Close($true)
    Set-Content -Path $log -Value "DONE" -Encoding UTF8
} catch {
    Set-Content -Path $log -Value ("ERR: " + $_.Exception.Message) -Encoding UTF8
    try { $wb.Close($false) } catch {}
} finally {
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
