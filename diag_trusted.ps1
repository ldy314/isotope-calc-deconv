$ErrorActionPreference = "Stop"

# 先用一个临时 Excel 实例读取真实版本号
$tmp = New-Object -ComObject Excel.Application
$tmp.Visible = $false; $tmp.DisplayAlerts = $false
$ver = $tmp.Version
$tmp.Quit()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($tmp) | Out-Null
Write-Output ("Excel version: " + $ver)

$tlRoot = "HKCU:\Software\Microsoft\Office\$ver\Excel\Security\Trusted Locations"
if (-not (Test-Path $tlRoot)) { New-Item -Path $tlRoot -Force | Out-Null }
$i = 0
while (Test-Path "$tlRoot\Location$i") { $i++ }
$locPath = "$tlRoot\Location$i"
New-Item -Path $locPath -Force | Out-Null
New-ItemProperty -Path $locPath -Name "Path" -Value "D:\code test\chem\同位素计算及解卷积" -PropertyType String | Out-Null
New-ItemProperty -Path $locPath -Name "AllowSubfolders" -Value 1 -PropertyType DWord | Out-Null
Write-Output ("Added trusted location under v$ver : " + $locPath)

try {
    & "D:\code test\chem\同位素计算及解卷积\test_deconv_sheet.ps1"
} finally {
    Remove-Item -Path $locPath -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output ("Removed trusted location: " + $locPath)
}
