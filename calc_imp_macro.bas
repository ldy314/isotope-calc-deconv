Option Explicit

' 内核 Sleep（毫秒级等待，64 位 Office 需 PtrSafe）
#If VBA7 Then
    Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal ms As Long)
#Else
    Private Declare Sub Sleep Lib "kernel32" (ByVal ms As Long)
#End If

' ============================================================
' RunCalcImp - 枚举同位素取代杂质 + 计算每个杂质的理论同位素峰，输出 CSV
' 架构：与 RunImp 一致：Shell() 启动 Python（无重定向），
'       calc_imp.py 用 --out 写 CSV、--imp-out 写杂质协议文件。
' 参数（Calculator 输入区）：
'   B3:AA5 元素表（元素/同位素种类/原子个数）
'   B9     电荷数 z（默认 1）
'   B10    质量精度（峰合并容差 Da，默认 0.001）
'   B11    丰度阈值 %（默认 0.05）
' 输出：
'   1) Excel 输出区（21 行起，每杂质 4 行块，同 RunImp）
'   2) CSV 文件：<xlsm同目录>\impurity_peaks_<时间戳>.csv（每行=杂质-峰对）
' 状态写入 F2
' ============================================================
Sub RunCalcImp()
    Dim ws As Worksheet
    Dim col As Integer
    Dim elem As String, iso As String, cnt As String
    Dim args As String
    Dim pyPath As String, pyExe As String
    Dim dirPath As String
    Dim outPath As String, impPath As String, errPath As String
    Dim cmd As String
    Dim i As Long
    Dim fso As Object, file As Object
    Dim errText As String
    Dim pid As Long
    Dim waited As Long
    Dim allText As String
    Dim lines() As String
    Dim ln As String
    Dim r As Long
    Dim name As String
    Dim parts() As String
    Dim j As Long
    Dim curRow As Long
    Dim nImp As Long
    Dim blockPos As Integer
    Dim z As Long, tol As Double, minAb As Double
    Dim stream As Object
    Dim csvPath As String
    Dim stamp As String

    On Error GoTo ErrHandler

    Set ws = ThisWorkbook.Sheets("Calculator")
    dirPath = "D:\code test\chem\同位素计算及解卷积"
    pyPath = dirPath & "\calc_imp.py"
    impPath = Environ("TEMP") & "\calc_imp_result.txt"
    errPath = Environ("TEMP") & "\calc_imp_err.txt"

    ws.Cells(2, 6).Value = "运行中..."

    ' --- 收集输入（B3:AA5，同 RunImp） ---
    args = ""
    For col = 2 To 27
        elem = Trim(CStr(ws.Cells(3, col).Value))
        iso = Trim(CStr(ws.Cells(4, col).Value))
        cnt = Trim(CStr(ws.Cells(5, col).Value))
        If elem <> "" And cnt <> "" Then
            If iso = "" Then iso = "natural"
            If IsNumeric(cnt) Then
                args = args & " " & elem & " " & iso & " " & CStr(CLng(cnt))
            End If
        End If
    Next col

    If args = "" Then
        ws.Cells(2, 6).Value = "错误：输入区为空"
        Exit Sub
    End If

    ' --- 参数：z / tol / min-ab ---
    If IsNumeric(ws.Cells(9, 2).Value) Then z = CLng(ws.Cells(9, 2).Value) Else z = 1
    If IsNumeric(ws.Cells(10, 2).Value) Then tol = CDbl(ws.Cells(10, 2).Value) Else tol = 0.001
    If IsNumeric(ws.Cells(11, 2).Value) Then
        minAb = CDbl(ws.Cells(11, 2).Value)
    Else
        minAb = 0.05
    End If
    If minAb < 0 Then minAb = 0

    pyExe = GetPythonExe()
    If pyExe = "" Then
        ws.Cells(2, 6).Value = "错误：未找到 Python，请安装并执行 pip install molmass"
        Exit Sub
    End If

    ' CSV 输出路径：xlsm 同目录，带时间戳
    stamp = Format(Now, "yyyymmdd_hhmmss")
    csvPath = dirPath & "\impurity_peaks_" & stamp & ".csv"

    ' 删除旧结果/错误文件
    Set fso = CreateObject("Scripting.FileSystemObject")
    If fso.FileExists(impPath) Then fso.DeleteFile impPath
    If fso.FileExists(errPath) Then fso.DeleteFile errPath

    cmd = """" & pyExe & """ """ & pyPath & """ --elements " & Trim(args) & _
          " --z " & CStr(z) & _
          " --tol " & Replace(CStr(tol), ",", ".") & _
          " --min-ab " & Replace(CStr(minAb), ",", ".") & _
          " --out """ & csvPath & """ --imp-out """ & impPath & """"

    pid = Shell(cmd, 0)
    If pid = 0 Then
        ws.Cells(2, 6).Value = "错误：无法启动 Python"
        Exit Sub
    End If

    ' 轮询最多 60 秒
    waited = 0
    Do While waited < 300
        Sleep 200
        waited = waited + 1
        If fso.FileExists(impPath) Then
            Sleep 500
            Exit Do
        End If
    Loop

    If Not fso.FileExists(impPath) Then
        If fso.FileExists(errPath) Then
            Set file = fso.OpenTextFile(errPath, 1, False, 0)
            errText = file.ReadAll
            file.Close
            ws.Cells(2, 6).Value = "错误：" & Left(errText, 150)
        Else
            ws.Cells(2, 6).Value = "错误：计算超时或 Python 执行失败"
        End If
        Exit Sub
    End If

    ' --- 读取杂质协议（UTF-8） ---
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2
    stream.Charset = "utf-8"
    stream.Open
    stream.LoadFromFile impPath
    allText = stream.ReadText
    stream.Close
    Set stream = Nothing

    lines = Split(allText, vbLf)

    ' --- 清空旧输出区 ---
    For r = 24 To 623
        ws.Cells(r, 1).Value = ""
        For j = 2 To 12
            ws.Cells(r, j).Value = ""
        Next j
    Next r

    ' --- 回填杂质块（状态机，同 RunImp） ---
    curRow = 24
    nImp = 0
    blockPos = 0
    For i = 0 To UBound(lines)
        ln = Trim(lines(i))
        If ln = "" Then GoTo nextLine

        If Left(ln, 1) = "#" Then
            name = Trim(Mid(ln, 2))
            If InStr(name, "imp v1") > 0 Then GoTo nextLine
            If InStr(name, "none") > 0 Then GoTo nextLine
            nImp = nImp + 1
            ws.Cells(curRow, 1).Value = "[" & nImp & "] " & name
            ws.Cells(curRow + 1, 1).Value = "元素"
            ws.Cells(curRow + 2, 1).Value = "同位素种类"
            ws.Cells(curRow + 3, 1).Value = "原子个数"
            blockPos = 1
        ElseIf blockPos >= 1 And blockPos <= 3 Then
            parts = Split(ln, "|")
            For j = 0 To UBound(parts)
                ws.Cells(curRow + blockPos, 2 + j).Value = Trim(parts(j))
            Next j
            If blockPos = 3 Then
                curRow = curRow + 4
                blockPos = 0
            Else
                blockPos = blockPos + 1
            End If
        End If
nextLine:
    Next i

    If nImp = 0 Then
        ws.Cells(2, 6).Value = "完成：无修饰同位素，无杂质可枚举"
    Else
        ws.Cells(2, 6).Value = "完成：共 " & CStr(nImp) & " 个杂质，CSV → " & csvPath
    End If
    Exit Sub

ErrHandler:
    ws.Cells(2, 6).Value = "错误：" & Err.Number & " " & Err.Description
End Sub

' 查找可用的 Python 解释器（带 molmass 的优先）
Function GetPythonExe() As String
    Dim cands As Variant
    Dim i As Long
    Dim testCmd As String, outFile As String
    Dim fso As Object
    Dim waited As Long
    Dim f As Object
    Dim t As String

    cands = Array( _
        "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe", _
        "python" _
    )
    GetPythonExe = ""
    For i = LBound(cands) To UBound(cands)
        If Len(Dir(cands(i))) > 0 Or cands(i) = "python" Then
            outFile = Environ("TEMP") & "\imp_pycheck.txt"
            Set fso = CreateObject("Scripting.FileSystemObject")
            If fso.FileExists(outFile) Then fso.DeleteFile outFile
            testCmd = """" & cands(i) & """ -c ""import molmass; open(r'" & outFile & "', 'w').write('OK')"""
            Call Shell(testCmd, 0)
            waited = 0
            Do While waited < 50
                Sleep 200
                waited = waited + 1
                If fso.FileExists(outFile) Then Exit Do
            Loop
            If fso.FileExists(outFile) Then
                Set f = fso.OpenTextFile(outFile, 1, False, 0)
                t = f.ReadAll
                f.Close
                If InStr(t, "OK") > 0 Then
                    GetPythonExe = cands(i)
                    Exit For
                End If
            End If
        End If
    Next i
End Function
