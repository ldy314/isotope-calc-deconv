Option Explicit

' 内核 Sleep（毫秒级等待，64 位 Office 需 PtrSafe）
#If VBA7 Then
    Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal ms As Long)
#Else
    Private Declare Sub Sleep Lib "kernel32" (ByVal ms As Long)
#End If

' ============================================================
' RunTheo - 调用 Python theo.py 计算前20同位素峰并回填
' 架构：VBA 内置 Shell() 启动 Python（隐藏窗口），轮询输出文件等待完成。
' 关键设计：
'   - stderr 重定向到独立错误文件（theo_err.txt），避免 2>&1 混入 JSON
'   - 结果文件读取用 Tristate 0 (ASCII)，与 theo.py 纯 ASCII JSON 输出匹配
'   - 状态写入 F2 单元格，含每一步的进度/错误
' 依赖：theo.py 与 .xlsm 同目录；本机 Python + molmass
' ============================================================
Sub RunTheo()
    Dim ws As Worksheet
    Dim col As Integer
    Dim elem As String, iso As String, cnt As String
    Dim args As String
    Dim theoPath As String, pyExe As String
    Dim dirPath As String
    Dim jsonPath As String, errPath As String
    Dim cmd As String
    Dim i As Long
    Dim z As Long, tol As Double
    Dim fso As Object, file As Object
    Dim json As String, errText As String
    Dim m As Double, mz As Double, ab As Double
    Dim r As Long
    Dim pid As Long
    Dim waited As Long
    Dim lines() As String
    Dim ln As String
    Dim rank As Long
    Dim reading As Boolean

    On Error GoTo ErrHandler

    Set ws = ThisWorkbook.Sheets("Calculator")
    dirPath = "D:\code test\chem\同位素计算及解卷积"
    theoPath = dirPath & "\theo.py"
    jsonPath = Environ("TEMP") & "\theo_result.json"
    errPath = Environ("TEMP") & "\theo_err.txt"

    ws.Cells(2, 6).Value = "运行中..."

    ' --- 收集输入 ---
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

    z = CLng(ws.Cells(9, 2).Value)
    tol = CDbl(ws.Cells(10, 2).Value)

    pyExe = GetPythonExe()
    If pyExe = "" Then
        ws.Cells(2, 6).Value = "错误：未找到 Python，请安装并执行 pip install molmass"
        Exit Sub
    End If

    ' 删除旧结果/错误文件
    Set fso = CreateObject("Scripting.FileSystemObject")
    If fso.FileExists(jsonPath) Then fso.DeleteFile jsonPath
    If fso.FileExists(errPath) Then fso.DeleteFile errPath

    ' stderr 单独重定向到 errPath，stdout 到 jsonPath
    cmd = """" & pyExe & """ """ & theoPath & """ --elements " & Trim(args) & _
          " --z " & CStr(z) & " --tol " & Replace(CStr(tol), ",", ".") & _
          " --json > """ & jsonPath & """ 2> """ & errPath & """"

    ' --- 用 Shell 启动（隐藏窗口），随后轮询输出文件 ---
    pid = Shell(cmd, 0)
    If pid = 0 Then
        ws.Cells(2, 6).Value = "错误：无法启动 Python"
        Exit Sub
    End If

    ' 轮询最多 60 秒，等待结果文件出现（每 200ms 检查一次）
    waited = 0
    Do While waited < 300
        Sleep 200
        waited = waited + 1
        If fso.FileExists(jsonPath) Then
            ' 额外等待 0.5 秒确保写入完成
            Sleep 500
            Exit Do
        End If
    Loop

    ' 若超时，先看错误文件
    If Not fso.FileExists(jsonPath) Then
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

    ' --- 读取结果（ASCII 模式读取纯 ASCII JSON） ---
    Set file = fso.OpenTextFile(jsonPath, 1, False, 0)
    json = file.ReadAll
    file.Close

    ' --- 解析 JSON ---
    lines = Split(json, vbLf)
    reading = False
    r = 14
    rank = 0
    For i = 0 To UBound(lines)
        ln = Trim(lines(i))
        If InStr(ln, """peaks""") > 0 Then reading = True
        If reading And InStr(ln, """rank""") > 0 Then
            m = ExtractNum(ln, "mass")
            mz = ExtractNum(ln, "mz")
            ab = ExtractNum(ln, "abundance")
            rank = rank + 1
            ws.Cells(r, 1).Value = rank
            ws.Cells(r, 2).Value = m
            ws.Cells(r, 3).Value = mz
            ws.Cells(r, 4).Value = ab
            r = r + 1
        End If
    Next i

    For i = r To 33
        ws.Cells(i, 1).Value = ""
        ws.Cells(i, 2).Value = ""
        ws.Cells(i, 3).Value = ""
        ws.Cells(i, 4).Value = ""
    Next i

    ws.Cells(2, 6).Value = "完成：共 " & (r - 14) & " 个同位素峰"
    Exit Sub

ErrHandler:
    ws.Cells(2, 6).Value = "错误：" & Err.Number & " " & Err.Description
End Sub

Function ExtractNum(line As String, key As String) As Double
    Dim pos As Long, start As Long, stopAt As Long
    Dim snippet As String
    pos = InStr(line, """" & key & """")
    If pos = 0 Then
        ExtractNum = 0
        Exit Function
    End If
    start = InStr(pos, line, ":") + 1
    stopAt = InStr(start, line, ",")
    If stopAt = 0 Then stopAt = Len(line) + 1
    snippet = Mid(line, start, stopAt - start)
    snippet = Replace(snippet, "}", "")
    snippet = Replace(snippet, " ", "")
    ExtractNum = Val(snippet)
End Function

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
            ' 验证该 Python 可用且能 import molmass
            outFile = Environ("TEMP") & "\theo_pycheck.txt"
            Set fso = CreateObject("Scripting.FileSystemObject")
            If fso.FileExists(outFile) Then fso.DeleteFile outFile
            testCmd = """" & cands(i) & """ -c ""import molmass; print('OK')"" > """ & outFile & """ 2>&1"
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
