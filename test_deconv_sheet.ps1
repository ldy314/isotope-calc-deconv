# -*- coding: utf-8 -*-
# test_deconv_sheet.ps1 - 端到端验证解卷积表
$ErrorActionPreference = "Stop"
$dir = "D:\code test\chem\同位素计算及解卷积"
$src = Join-Path $dir "ExactMass_Impurities_Deconv.xlsm"
$test = Join-Path $dir "ExactMass_Impurities_DECONV_TEST.xlsm"
$log = Join-Path $dir "_deconv_test_log.txt"
function Log($m) { Add-Content -Path $log -Value $m -Encoding UTF8 }
Set-Content -Path $log -Value "TEST START" -Encoding UTF8

# 1) 复制
Copy-Item $src $test -Force
Log "COPIED"

# 2) 注入表+宏+按钮
& (Join-Path $dir "add_deconv_sheet.ps1") -xlsmPath $test
Log "INJECT DONE (see _deconv_inject_log.txt)"

# 3) 重新打开，填 SPH20291 输入，运行宏
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$excel.AutomationSecurity = 1   # msoAutomationSecurityLow：允许通过自动化运行宏
try {
    $wb = $excel.Workbooks.Open($test)
    $calc = $wb.Sheets("Calculator")
    $ws = $wb.Sheets("Deconvolution")

    # 诊断：确认 DeconvModule / RunDeconv 已注入
    $found = $false
    foreach ($comp in $wb.VBProject.VBComponents) {
        if ($comp.Name -eq "DeconvModule") { $found = $true }
    }
    Log ("VBComponent DeconvModule present: " + $found)

    # 清空并填 SPH20291: C natural 128, C 13 6, H natural 198, N natural 26, N 15 2, O natural 35, S natural 2 ; z=2
    $excel.Range("Calculator!B3:Z5").ClearContents() | Out-Null
    $calc.Cells.Item(3,2).Value = "C"; $calc.Cells.Item(4,2).Value = "natural"; $calc.Cells.Item(5,2).Value = 128
    $calc.Cells.Item(3,3).Value = "C"; $calc.Cells.Item(4,3).Value = "13";      $calc.Cells.Item(5,3).Value = 6
    $calc.Cells.Item(3,4).Value = "H"; $calc.Cells.Item(4,4).Value = "natural"; $calc.Cells.Item(5,4).Value = 198
    $calc.Cells.Item(3,5).Value = "N"; $calc.Cells.Item(4,5).Value = "natural"; $calc.Cells.Item(5,5).Value = 26
    $calc.Cells.Item(3,6).Value = "N"; $calc.Cells.Item(4,6).Value = "15";      $calc.Cells.Item(5,6).Value = 2
    $calc.Cells.Item(3,7).Value = "O"; $calc.Cells.Item(4,7).Value = "natural"; $calc.Cells.Item(5,7).Value = 35
    $calc.Cells.Item(3,8).Value = "S"; $calc.Cells.Item(4,8).Value = "natural"; $calc.Cells.Item(5,8).Value = 2
    $calc.Cells.Item(9,2).Value = 2

    # 谱路径用相对路径（验证相对解析）
    $ws.Cells.Item(5,2).Value = "解卷积\SPH20291_MS+_deconv\spec_msplus_from_jdx.csv"
    $ws.Cells.Item(8,2).Value = "TRUE"

    Log "INPUTS SET; running RunDeconv..."
    $excel.Run("RunDeconv")
    Log "RunDeconv returned"

    # 读结果
    Log ("状态        : " + $ws.Cells.Item(11,2).Value)
    Log ("实测质心    : " + $ws.Cells.Item(12,2).Value)
    Log ("天然质心    : " + $ws.Cells.Item(13,2).Value)
    Log ("全标记质心  : " + $ws.Cells.Item(14,2).Value)
    Log ("平均标记数  : " + $ws.Cells.Item(15,2).Value)
    Log ("平均富集度% : " + $ws.Cells.Item(16,2).Value)
    Log ("可标记位点  : " + $ws.Cells.Item(17,2).Value)
    Log ("NNLS合计%   : " + $ws.Cells.Item(20,2).Value)
    Log ("NNLS可靠性  : " + $ws.Cells.Item(21,2).Value)
    Log ("说明        : " + $ws.Cells.Item(22,2).Value)

    Log "--- NNLS 表 ---"
    for ($r = 26; $r -le 60; $r++) {
        $nm = $ws.Cells.Item($r,2).Value
        if ($nm -eq $null -or $nm -eq "") { break }
        Log ($ws.Cells.Item($r,1).Value.ToString() + " | " + $nm + " | " + $ws.Cells.Item($r,3).Value.ToString())
    }

    $wb.Save()
    $wb.Close($true)
    Log "TEST DONE"
} catch {
    Log ("ERR: " + $_.Exception.Message)
    try { $wb.Close($false) } catch {}
} finally {
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
