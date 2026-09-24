$ErrorActionPreference = "Stop"
$path = "D:\code test\chem\同位素计算及解卷积\ExactMass_Impurities_DECONV_TEST.xlsm"
$log = "D:\code test\chem\同位素计算及解卷积\_diag.log"
Set-Content -Path $log -Value "DIAG START" -Encoding UTF8
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$excel.AutomationSecurity = 1
try {
    $wb = $excel.Workbooks.Open($path)
    Add-Content -Path $log -Value ("AutomationSecurity=" + $excel.AutomationSecurity)
    try { $excel.Run("RunImp"); Add-Content -Path $log -Value "RunImp: OK" }
    catch { Add-Content -Path $log -Value ("RunImp ERR: " + $_.Exception.Message) }
    try { $excel.Run("RunDeconv"); Add-Content -Path $log -Value "RunDeconv: OK" }
    catch { Add-Content -Path $log -Value ("RunDeconv ERR: " + $_.Exception.Message) }
    $wb.Close($false)
} catch {
    Add-Content -Path $log -Value ("OPEN ERR: " + $_.Exception.Message)
} finally {
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
