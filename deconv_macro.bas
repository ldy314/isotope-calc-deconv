Option Explicit

' 解卷积 / 富集度分析 按钮宏
' 读取『杂质枚举器』Calculator 的分子式(第3-5行)与电荷(B9) + 本表输入的实测谱CSV，
' 调用同目录 deconv_excel.py，回填质心法富集度与 NNLS 逐杂质表。
Sub RunDeconv()
    Dim ws As Worksheet, calc As Worksheet
    Set ws = ThisWorkbook.Sheets("Deconvolution")
    Set calc = ThisWorkbook.Sheets("Calculator")

    Dim wbPath As String
    wbPath = ThisWorkbook.Path

    ' ---- 读分子式（来自 Calculator 第3-5行，B列起） ----
    Dim elemArgs As String
    elemArgs = ""
    Dim col As Integer
    For col = 2 To 26
        Dim el As String, iso As String, cnt As String
        el = CStr(calc.Cells(3, col).Value)
        If el = "" Or el = "0" Then Exit For
        iso = CStr(calc.Cells(4, col).Value)
        cnt = CStr(calc.Cells(5, col).Value)
        If cnt = "" Or cnt = "0" Then cnt = "1"
        elemArgs = elemArgs & " " & Q(el) & " " & Q(iso) & " " & Q(cnt)
    Next col

    If elemArgs = "" Then
        MsgBox "未在 Calculator 第3-5行找到分子式输入。", vbExclamation
        Exit Sub
    End If

    Dim z As Integer
    z = CInt(calc.Cells(9, 2).Value)

    Dim spectrum As String, py As String
    spectrum = CStr(ws.Cells(5, 2).Value)
    py = CStr(ws.Cells(9, 2).Value)
    If spectrum = "" Or spectrum = "0" Then
        MsgBox "请先填写『实测谱 CSV 路径』(B5)。", vbExclamation
        Exit Sub
    End If
    ' 相对路径（不含盘符/UNC）按工作簿目录解析，方便填 "解卷积\...\spec.csv"
    If InStr(spectrum, ":") = 0 And Left(spectrum, 2) <> "\\" Then
        spectrum = wbPath & "\" & spectrum
    End If
    If py = "" Or py = "0" Then py = "python"

    Dim sLo As String, sHi As String
    sLo = CStr(ws.Cells(6, 2).Value)
    sHi = CStr(ws.Cells(7, 2).Value)
    Dim doNNLS As Boolean
    doNNLS = (UCase(CStr(ws.Cells(8, 2).Value)) = "TRUE")

    ' ---- 拼命令 ----
    Dim script As String, outJson As String
    script = wbPath & "\deconv_excel.py"
    outJson = wbPath & "\deconv_result.json"

    Dim cmd As String
    cmd = Q(py) & " " & Q(script) & " --elements" & elemArgs _
        & " --z " & z & " --spectrum " & Q(spectrum) & " --out " & Q(outJson)
    If sLo <> "" And sLo <> "0" Then cmd = cmd & " --lo " & sLo
    If sHi <> "" And sHi <> "0" Then cmd = cmd & " --hi " & sHi
    cmd = cmd & " --nnls " & IIf(doNNLS, "true", "false")

    ' ---- 运行（WScript.Shell，等待结束，最长5分钟） ----
    ws.Cells(11, 2).Value = "运行中..."
    Dim wsh As Object, exec As Object
    Set wsh = CreateObject("WScript.Shell")
    On Error Resume Next
    Set exec = wsh.Exec(cmd)
    On Error GoTo 0
    If exec Is Nothing Then
        ws.Cells(11, 2).Value = "启动 python 失败：请检查 B9 的 Python 路径"
        Exit Sub
    End If
    Dim deadline As Date
    deadline = Now + TimeSerial(0, 5, 0)
    Do While exec.Status = 0
        DoEvents
        If Now > deadline Then
            exec.Terminate
            ws.Cells(11, 2).Value = "超时（>5分钟）"
            Exit Sub
        End If
    Loop

    ' ---- 读结果（UTF-8 读取，避免中文/同位素上下标乱码） ----
    Dim scalars As String, tableCsv As String
    scalars = wbPath & "\deconv_result_scalars.txt"
    tableCsv = wbPath & "\deconv_result_table.csv"
    If Dir(scalars) = "" Then
        Dim errFile As String
        errFile = wbPath & "\deconv_err.txt"
        If Dir(errFile) <> "" Then
            ws.Cells(11, 2).Value = "引擎报错，详见 deconv_err.txt"
        Else
            ws.Cells(11, 2).Value = "未生成结果文件"
        End If
        Exit Sub
    End If

    Dim dict As Object
    Set dict = CreateObject("Scripting.Dictionary")
    Dim raw As String, lns() As String, li As Long, line As String, pos As Integer
    raw = ReadUtf8(scalars)
    raw = Replace(raw, vbCr, "")
    lns = Split(raw, vbLf)
    For li = 0 To UBound(lns)
        line = lns(li)
        pos = InStr(line, "=")
        If pos > 0 Then dict(CStr(Left(line, pos - 1))) = Mid(line, pos + 1)
    Next li

    ws.Cells(12, 2).Value = ToNum(dict("m_meas"))
    ws.Cells(13, 2).Value = ToNum(dict("m_nat"))
    ws.Cells(14, 2).Value = ToNum(dict("m_lab"))
    ws.Cells(15, 2).Value = ToNum(dict("avg_labels"))
    ws.Cells(16, 2).Value = ToNum(dict("enrichment_pct"))
    ws.Cells(17, 2).Value = ToNum(dict("total_mod_atoms"))
    ws.Cells(20, 2).Value = ToNum(dict("nnls_sum"))

    Dim rel As String
    rel = dict("nnls_reliable")
    If rel = "True" Then
        ws.Cells(21, 2).Value = "可靠"
    ElseIf rel = "False" Then
        ws.Cells(21, 2).Value = "不可靠"
    Else
        ws.Cells(21, 2).Value = "-"
    End If
    ws.Cells(22, 2).Value = dict("nnls_reason")
    ws.Cells(11, 2).Value = "完成"

    ' ---- NNLS 表：先清空旧表（行26-200），再写入（UTF-8 读取） ----
    Dim r As Long
    For r = 26 To 200
        ws.Cells(r, 1).Value = ""
        ws.Cells(r, 2).Value = ""
        ws.Cells(r, 3).Value = ""
        ws.Cells(r, 4).Value = ""
        ws.Cells(r, 5).Value = ""
    Next r
    If Dir(tableCsv) <> "" Then
        raw = ReadUtf8(tableCsv)
        raw = Replace(raw, vbCr, "")
        lns = Split(raw, vbLf)
        Dim startIdx As Long
        startIdx = 0
        If UBound(lns) >= 0 Then
            If InStr(lns(0), "rank,name") > 0 Then startIdx = 1
        End If
        r = 26
        Dim parts() As String
        For li = startIdx To UBound(lns)
            line = Trim(lns(li))
            If line <> "" Then
                parts = Split(line, ",")
                If UBound(parts) >= 4 Then
                    ws.Cells(r, 1).Value = CInt(parts(0))
                    ws.Cells(r, 2).Value = parts(1)
                    ws.Cells(r, 3).Value = CDbl(parts(2))
                    ws.Cells(r, 4).Value = IIf(parts(3) = "1", "是", "")
                    ws.Cells(r, 5).Value = IIf(parts(4) = "1", "主成分", "")
                    r = r + 1
                End If
            End If
        Next li
    End If

    ' 自动保存，确保结果落盘（不依赖调用方 Save）
    On Error Resume Next
    ThisWorkbook.Save
    On Error GoTo 0
End Sub

' UTF-8 文本读取（VBA 原生 Open 会按系统 ANSI 读取，导致中文/上下标乱码）
Private Function ReadUtf8(path As String) As String
    Dim stream As Object
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 2 ' adTypeText
    stream.Charset = "utf-8"
    stream.Open
    stream.LoadFromFile path
    ReadUtf8 = stream.ReadText
    stream.Close
End Function

Private Function Q(s As String) As String
    Q = Chr(34) & s & Chr(34)
End Function

Private Function ToNum(s As String) As Variant
    If s = "NA" Or s = "" Then
        ToNum = "-"
    Else
        ToNum = CDbl(s)
    End If
End Function
