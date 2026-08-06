Option Explicit

' 内核 Sleep（毫秒级等待，64 位 Office 需 PtrSafe）
#If VBA7 Then
    Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal ms As Long)
#Else
    Private Declare Sub Sleep Lib "kernel32" (ByVal ms As Long)
#End If

' ============================================================
' RunImp - 调用 Python imp.py 枚举同位素取代杂质并回填
' 架构：与 RunTheo 一致：Shell() 启动 Python（无重定向），
'       imp.py 用 --out 自行写结果文件（成功→文本协议，失败→imp_err.txt）。
' 结果文件协议（每杂质 4 行）：
'   # <名称(Unicode 分子式，含电荷)>   ← 名称行
'   <元素以|分隔>                      ← 数据行1
'   <同位素以|分隔>                    ← 数据行2
'   <个数以|分隔>                      ← 数据行3
' 读取：ADODB.Stream + Charset=utf-8（名称含 ²⁺/₁₃ 等 Unicode 字符）
' 回填：从 Calculator 第 16 行开始，每个杂质 4 行块：
'   [N] 分子式  | (A列)     (第 2 列起填元素)
'   元素        | (A列)     (第 2 列起填同位素)
'   同位素种类  | (A列)     (第 2 列起填个数)
'   原子个数    | (A列)
' 状态写入 F2 单元格
' ============================================================
Sub RunImp()
    Dim ws As Worksheet
    Dim col As Integer
    Dim elem As String, iso As String, cnt As String
    Dim args As String
    Dim impPath As String, pyExe As String
    Dim dirPath As String
    Dim outPath As String, errPath As String
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
    Dim blockPos As Integer   ' 1=元素, 2=同位素, 3=个数
    Dim z As Long
    Dim stream As Object

    On Error GoTo ErrHandler

    Set ws = ThisWorkbook.Sheets("Calculator")
    dirPath = "D:\code test\chem\同位素计算及解卷积"
    impPath = dirPath & "\imp.py"
    outPath = Environ("TEMP") & "\imp_result.txt"
    errPath = Environ("TEMP") & "\imp_err.txt"

    ws.Cells(2, 6).Value = "运行中..."

    ' --- 收集输入（与 RunTheo 相同：B3:AA5） ---
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

    ' --- 电荷 z（B9） ---
    If IsNumeric(ws.Cells(9, 2).Value) Then
        z = CLng(ws.Cells(9, 2).Value)
    Else
        z = 1
    End If

    pyExe = GetPythonExe()
    If pyExe = "" Then
        ws.Cells(2, 6).Value = "错误：未找到 Python，请安装并执行 pip install molmass"
        Exit Sub
    End If

    ' 删除旧结果/错误文件
    Set fso = CreateObject("Scripting.FileSystemObject")
    If fso.FileExists(outPath) Then fso.DeleteFile outPath
    If fso.FileExists(errPath) Then fso.DeleteFile errPath

    ' 直接调用 imp.py --out 模式
    cmd = """" & pyExe & """ """ & impPath & """ --elements " & Trim(args) & _
          " --z " & CStr(z) & " --out """ & outPath & """"

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
        If fso.FileExists(outPath) Then
            Sleep 500
            Exit Do
        End If
    Loop

    If Not fso.FileExists(outPath) Then
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

    ' --- 读取结果（UTF-8，用 ADODB.Stream） ---
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2          ' adTypeText
    stream.Charset = "utf-8"
    stream.Open
    stream.LoadFromFile outPath
    allText = stream.ReadText
    stream.Close
    Set stream = Nothing

    lines = Split(allText, vbLf)

    ' --- 先清空旧输出区（21 行起，600 行 = 150 杂质 × 4 行块） ---
    For r = 21 To 620
        ws.Cells(r, 1).Value = ""
        For j = 2 To 12
            ws.Cells(r, j).Value = ""
        Next j
    Next r

    ' --- 回填杂质块（状态机） ---
    curRow = 21
    nImp = 0
    blockPos = 0
    For i = 0 To UBound(lines)
        ln = Trim(lines(i))
        If ln = "" Then GoTo nextLine

        If Left(ln, 1) = "#" Then
            ' 名称行；跳过头部 "# imp v1 | ..." 与 "# none"
            name = Trim(Mid(ln, 2))
            If InStr(name, "imp v1") > 0 Then GoTo nextLine
            If InStr(name, "none") > 0 Then GoTo nextLine
            nImp = nImp + 1
            ' 行1：分子式（A 列）
            ws.Cells(curRow, 1).Value = "[" & nImp & "] " & name
            ' 行2~4：标签写在 A 列
            ws.Cells(curRow + 1, 1).Value = "元素"
            ws.Cells(curRow + 2, 1).Value = "同位素种类"
            ws.Cells(curRow + 3, 1).Value = "原子个数"
            blockPos = 1
        ElseIf blockPos >= 1 And blockPos <= 3 Then
            ' 数据行：以 | 分隔，写入第 2 列起
            parts = Split(ln, "|")
            For j = 0 To UBound(parts)
                ws.Cells(curRow + blockPos, 2 + j).Value = Trim(parts(j))
            Next j
            If blockPos = 3 Then
                curRow = curRow + 4   ' 下一个杂质块
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
        ws.Cells(2, 6).Value = "完成：共 " & CStr(nImp) & " 个杂质"
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
