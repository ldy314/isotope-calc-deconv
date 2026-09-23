# -*- coding: utf-8 -*-
"""用 Excel COM 构建自包含的「同位素杂质计算.xlsm」：
- 把计算引擎(embedded_engine.py)内嵌进隐藏表 EngineSrc；
- 建 方法说明 / 输入 / 结果 各表；
- 注入 VBA 模块 ModRun（RunX：提取引擎→运行→读 CSV 回填→保存）；
- 在 输入 表放「计算」按钮。
结果是一个单文件：用户只改 输入 表、点按钮即可。
"""
import os
import win32com.client as wc

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_SRC = os.path.join(HERE, "embedded_engine.py")
OUT = os.path.join(HERE, "同位素杂质计算.xlsm")
PY_DEFAULT = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

# ---------------------------------------------------------------------------
# 内嵌 VBA 代码（RunX + 辅助）
# ---------------------------------------------------------------------------
VBA = r'''Option Explicit

Sub RunX()
    Dim wb As Workbook
    Set wb = ThisWorkbook
    Dim wsIn As Worksheet
    Set wsIn = wb.Sheets("输入")

    ' 1) Python 解释器
    Dim py As String
    py = Trim(CStr(wsIn.Range("B7").Value))
    If py = "" Then py = "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

    ' 2) 提取内嵌引擎到临时文件
    Dim engSrc As String
    engSrc = ExtractEngine()
    If engSrc = "" Then
        MsgBox "未找到内嵌引擎（EngineSrc 表为空）。", vbCritical
        Exit Sub
    End If
    Dim fso As Object
    Set fso = CreateObject("Scripting.FileSystemObject")
    Dim tmpPath As String
    tmpPath = Environ("TEMP") & Application.PathSeparator & "iso_engine.py"
    WriteUtf8 tmpPath, engSrc

    ' 3) 先保存工作簿，使输入可被子进程读取
    On Error Resume Next
    wb.Save
    On Error GoTo 0

    ' 4) 运行引擎
    Dim wsh As Object
    Set wsh = CreateObject("WScript.Shell")
    Dim cmd As String
    cmd = Chr(34) & py & Chr(34) & " " & Chr(34) & tmpPath & Chr(34) & " --xlsm " & Chr(34) & wb.FullName & Chr(34)
    Dim exec As Object
    Dim errText As String
    errText = ""
    On Error GoTo Fail
    Set exec = wsh.Exec(cmd)
    Do While exec.Status = 0
        DoEvents
    Loop
    Dim outText As String
    outText = exec.StdOut.ReadAll
    errText = exec.StdErr.ReadAll
    If InStr(errText, "Error") > 0 Or InStr(errText, "Traceback") > 0 Or errText <> "" Then
        wsIn.Range("D3").Value = "引擎报错（详见临时文件 stderr）"
        If Environ("ISO_NO_MSGBOX") <> "1" Then MsgBox "引擎报错：" & vbCrLf & Left(errText, 3000), vbCritical
        Exit Sub
    End If

    ' 5) 回填结果表（中间 CSV 在 %TEMP%）
    Dim folder As String, base As String
    folder = Environ("TEMP")
    base = fso.GetBaseName(wb.Name)

    FillFromCsv folder & Application.PathSeparator & base & "_overview.csv", "四法总览", 1
    FillFromCsv folder & Application.PathSeparator & base & "_z3_tiers.csv", "z3_档汇总", 1
    ' z3_档汇总 档划分说明块（读 tiernote.csv）
    Dim notePath As String
    notePath = folder & Application.PathSeparator & base & "_tiernote.csv"
    If fso.FileExists(notePath) Then
        Dim ntxt As String
        ntxt = ReadUtf8(notePath)
        ntxt = Replace(ntxt, vbCr, "")
        Dim nlines() As String
        nlines = Split(ntxt, vbLf)
        Dim nr As Long, ni As Long, nl As String
        nr = 12
        ThisWorkbook.Sheets("z3_档汇总").Range("A11").Value = "档的划分说明："
        ThisWorkbook.Sheets("z3_档汇总").Range("A11").Font.Bold = True
        For ni = 1 To UBound(nlines)
            nl = Trim(nlines(ni))
            If nl <> "" Then
                ThisWorkbook.Sheets("z3_档汇总").Cells(nr, 1).Value = nl
                ThisWorkbook.Sheets("z3_档汇总").Range(ThisWorkbook.Sheets("z3_档汇总").Cells(nr, 1), ThisWorkbook.Sheets("z3_档汇总").Cells(nr, 7)).Merge
                ThisWorkbook.Sheets("z3_档汇总").Cells(nr, 1).HorizontalAlignment = 1
                nr = nr + 1
            End If
        Next ni
    End If
    FillFromCsv folder & Application.PathSeparator & base & "_z3_imp.csv", "z3_杂质明细", 1
    FillFromCsv folder & Application.PathSeparator & base & "_z4_tiers.csv", "z4_档汇总", 1
    FillFromCsv folder & Application.PathSeparator & base & "_z4_imp.csv", "z4_杂质明细", 1
    FillFromCsv folder & Application.PathSeparator & base & "_calib.csv", "校准与实测", 1

    wsIn.Range("D3").Value = "计算完成"
    On Error Resume Next
    wb.Save
    On Error GoTo 0
    If Environ("ISO_NO_MSGBOX") <> "1" Then MsgBox "计算完成，结果已写入各工作表。", vbInformation
    Exit Sub
Fail:
    wsIn.Range("D3").Value = "运行失败：" & Err.Description
    If Environ("ISO_NO_MSGBOX") <> "1" Then MsgBox "运行失败：" & Err.Description, vbCritical
End Sub

Private Function ExtractEngine() As String
    Dim wb As Workbook
    Set wb = ThisWorkbook
    Dim ws As Worksheet
    Set ws = wb.Sheets("EngineSrc")
    Dim s As String
    s = ""
    Dim r As Long
    r = 1
    Do While Len(CStr(ws.Cells(r, 1).Value)) > 0
        s = s & CStr(ws.Cells(r, 1).Value)
        r = r + 1
    Loop
    ExtractEngine = s
End Function

Private Sub WriteUtf8(path As String, text As String)
    Dim s As Object
    Set s = CreateObject("ADODB.Stream")
    s.Type = 2
    s.Charset = "utf-8"
    s.Open
    s.WriteText text
    s.SaveToFile path, 2
    s.Close
End Sub

Private Function ReadUtf8(path As String) As String
    Dim s As Object
    Set s = CreateObject("ADODB.Stream")
    s.Type = 2
    s.Charset = "utf-8"
    s.Open
    s.LoadFromFile path
    ReadUtf8 = s.ReadText
    s.Close
End Function

Private Sub FillFromCsv(csvPath As String, sheetName As String, headerRow As Long)
    Dim fso As Object
    Set fso = CreateObject("Scripting.FileSystemObject")
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Sheets(sheetName)
    Dim lastRow As Long
    lastRow = ws.Cells(ws.Rows.Count, 1).End(-4162).Row
    If lastRow > headerRow Then
        ws.Range(ws.Cells(headerRow + 1, 1), ws.Cells(lastRow, 20)).ClearContents
    End If
    If Not fso.FileExists(csvPath) Then Exit Sub
    Dim txt As String
    txt = ReadUtf8(csvPath)
    txt = Replace(txt, vbCr, "")
    Dim lines() As String
    lines = Split(txt, vbLf)
    Dim r As Long, c As Long
    r = headerRow + 1
    Dim i As Long
    For i = 1 To UBound(lines)
        Dim line As String
        line = Trim(lines(i))
        If line = "" Then GoTo NextLine
        Dim parts() As String
        parts = Split(line, ",")
        For c = 0 To UBound(parts)
            ws.Cells(r, c + 1).Value = Trim(parts(c))
        Next c
        r = r + 1
NextLine:
    Next i
End Sub
'''

# ---------------------------------------------------------------------------
# 方法说明文本（写入 方法说明 表）
# ---------------------------------------------------------------------------
METHOD_LINES = [
    ("A1", "同位素杂质含量计算 — 方法说明"),
    ("A3", "一、问题：给定全标记稳定同位素内标（分析物本体），其在天然同位素背景下的"),
    ("A4", "   『被天然同位素替换回去』的杂质含量如何定量。"),
    ("A5", "二、物理模型："),
    ("A6", "   • 9 档体系：tier t = 8 个标记原子中有 t 个被天然同位素替换（t=0 本体，t=8 全天然）。"),
    ("A7", "   • 每档用理论同位素包络（molmass 多项式卷积）算出离散峰列表。"),
    ("A8", "   • 标记化合物 M+1 拆为相邻双峰（¹²C→¹³C 与 ¹⁴N→¹⁵N），5 ppm 下不可分，"),
    ("A9", "     故用『落在某锚点窗口内的全部峰概率之和』作为系数，而非按序号索引。"),
    ("A10", "   • 5 ppm 下 基峰(t) 与 M0(t-1) 位置重合 → M0 法与基峰法量级一致、可互校验（链端存在固有偏差），以 M0·积分 为准。"),
    ("A11", "三、计算流程："),
    ("A12", "   1) 读 mzML（质心化谱按 m/z 分箱累加为轮廓谱），自动检测 RT 出峰窗口；"),
    ("A13", "   2) 逐锚点宽窗找真实峰心做质量校准（修正局部质量偏差）；"),
    ("A14", "   3) 在 ±0.04 Da 窗提强度（积分 与 峰顶 两种方式）；"),
    ("A15", "   4) 用理论『窗口重叠概率』作固定系数，三角逐级扣除（轻→重 / 重→轻）；"),
    ("A16", "   5) 归一化到总量 100%；同档内不可分辨的 ¹³C/¹⁵N 替换组合合并为 1 行（列出全部分子式与各自真实 m/z），杂质明细共 9 行（每档 1 行）。"),
    ("A17", "四、输入（见『输入』表）：mzML 路径、电荷态(3/4/两者)、分辨率 ppm、元素表。"),
    ("A18", "五、输出：四法总览、各电荷态档汇总与杂质明细（含 理论M0 m/z / 实测M0 m/z）、校准与实测。"),
    ("A21", "六、已扣除其它杂质影响：逐级三角扣除时，计算某档含量扣掉相邻更轻档(t+1)基峰在 M0(t) 处的串入；"),
    ("A22", "   非相邻档相距 ~0.33/z Da（≈353 ppm），远在 ±0.04 Da 提取窗外，不串入。杂质明细的 实测M0 m/z 即校准后真实峰心。"),
    ("A23", "七、已扣除背景：本流程用原始 mzML 自积分（分箱+窗口求和），按约定从峰附近平缓基线处扣本底——"),
    ("A24", "   基线取测量窗外侧环（相邻档间距之内）的中位强度，再自积分值减『基线×箱数』、自峰顶减『基线』。"),
    ("A25", "   质心化谱窗外无连续本底，实测本底≈0，故结果与未扣一致；若改用 .lcd（LabSolutions 已积分并扣本底）则无需此步。"),
    ("A27", "八、四种估计量（M0·积分 / M0·峰顶 / 基峰·积分 / 基峰·峰顶）的区别与可信度："),
    ("A28", "   • 四个列是四种不同估计量，对紧邻本体的 1档 天然会发散；它们量的是不同锚点位置、用不同量峰方式。"),
    ("A29", "   • M0·积分 = 在『该档自己的 M0 m/z』窗口(±0.04 Da)内所有点强度之和 − 本底×箱数（峰面积法，忠实于真实丰度）。"),
    ("A30", "   • M0·峰顶 = 同位置窗口内最高单点强度 − 本底（峰高法，对峰形/重叠更敏感）。"),
    ("A31", "   • 基峰·积分 / 基峰·峰顶 = 在『该档基峰 m/z』(≡ 上一档 M0，仅差 0.001 Da)处测量；须从本体巨峰中『大数减大数』抠残差，"),
    ("A32", "     对 1档 最不可靠、系统性偏高（sp_003 z3 给 3.77% / 4.42%）。"),
    ("A33", "   • 为何 M0·积分 最小且最可信：1档 的 M0(944.48)是孤立干净小峰，直接测得 6713；本体在 0.33 Da 外不干扰。"),
    ("A34", "     而本体峰被 1档 基峰叠加而变宽，使『峰顶法』分母被压低、基峰法残差失真，故另三列偏大（M0·峰顶 3.66%）。"),
    ("A35", "   • 报告值取 M0·积分（sp_003 z3 本体 98.331% / tier1 1.669%）。2–8 档含量≈0，四法差异看不出；唯紧邻本体的 1档 暴露分歧。"),
    ("A19", "（引擎与运行说明）可执行引擎内嵌于隐藏表 EngineSrc，由『输入』表『计算』按钮触发（VBA→Python→CSV→回填）。"),
    ("A20", "注：本工具用理论强度作固定系数定量，非实测谱拟合（deconvolution）。"),
]

# 输入表默认值
INPUT_DEFAULTS = {
    "B3": r"D:\code test\chem\同位素计算及解卷积\解卷积\解卷积\再处理2\sp_003\sp_003.mzML",
    "B4": "sp_003",
    "B5": "3,4",
    "B6": 5,
    "B7": PY_DEFAULT,
}
INPUT_LABELS = [
    ("A3", "mzML 文件路径："),
    ("A4", "样品名称："),
    ("A5", "计算电荷态 (3 / 4 / 两者)："),
    ("A6", "分辨率 (ppm)："),
    ("A7", "Python 解释器："),
    ("D2", "状态："),
    ("A9", "标记化合物元素表（分析物本体，全标记）："),
    ("A10", "元素"), ("B10", "同位素"), ("C10", "个数"),
]
# 元素表 7 行（SPH20291-Isotope1）：元素/同位素/个数
ELEMENT_TABLE = [
    ("A11", "C", "natural", 128), ("B11", None, None, None),
    ("A12", "C", "13", 6),
    ("A13", "H", "natural", 198),
    ("A14", "N", "natural", 26),
    ("A15", "N", "15", 2),
    ("A16", "O", "natural", 35),
    ("A17", "S", "natural", 2),
]

# 结果表表头
HEADERS = {
    "四法总览": ["电荷态", "方法", "本体%", "tier1%", "tier2-8%"],
    "z3_档汇总": ["tier", "名称", "本体(M0积分)%", "M0峰顶%", "基峰积分%", "基峰峰顶%", "档的划分说明"],
    "z3_杂质明细": ["tier", "类型", "名称", "理论M0 m/z", "实测M0 m/z", "含量%", "该档成员数"],
    "z4_档汇总": ["tier", "名称", "本体(M0积分)%", "M0峰顶%", "基峰积分%", "基峰峰顶%", "档的划分说明"],
    "z4_杂质明细": ["tier", "类型", "名称", "理论M0 m/z", "实测M0 m/z", "含量%", "该档成员数"],
    "校准与实测": ["电荷态", "档", "锚点", "理论m/z", "实测m/z", "偏移Da", "偏移ppm", "本底/每箱"],
}


def main():
    excel = wc.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        wb = excel.Workbooks.Add()
        # 删除默认多余 sheet
        want = ["方法说明", "输入", "四法总览", "z3_档汇总", "z3_杂质明细",
                "z4_档汇总", "z4_杂质明细", "校准与实测", "EngineSrc"]
        # 先确保都存在
        existing = {}
        for s in wb.Sheets:
            existing[s.Name] = s
        for name in want:
            if name not in existing:
                ws = wb.Sheets.Add()
                ws.Name = name
                existing[name] = ws
        # 删除非目标 sheet
        for s in list(wb.Sheets):
            if s.Name not in want:
                try:
                    s.Delete()
                except Exception:
                    pass

        # 方法说明
        ws = wb.Sheets("方法说明")
        ws.Activate()
        for cell, text in METHOD_LINES:
            ws.Range(cell).Value = text

        # 输入表
        ws = wb.Sheets("输入")
        for cell, text in INPUT_LABELS:
            ws.Range(cell).Value = text
        for cell, val in INPUT_DEFAULTS.items():
            ws.Range(cell).Value = val
        # 元素表（逐行写 A/B/C）
        row = 11
        tbl = [
            ("C", "natural", 128), ("C", "13", 6), ("H", "natural", 198),
            ("N", "natural", 26), ("N", "15", 2), ("O", "natural", 35),
            ("S", "natural", 2),
        ]
        for i, (e, iso, cnt) in enumerate(tbl):
            r = row + i
            ws.Range(f"A{r}").Value = e
            ws.Range(f"B{r}").Value = iso
            ws.Range(f"C{r}").Value = cnt

        # 结果表表头
        for name, hdr in HEADERS.items():
            ws = wb.Sheets(name)
            for c, h in enumerate(hdr, start=1):
                ws.Cells(1, c).Value = h

        # EngineSrc：写入引擎源码分块
        ws = wb.Sheets("EngineSrc")
        with open(ENGINE_SRC, "r", encoding="utf-8") as f:
            src = f.read()
        chunks = [src[i:i + 30000] for i in range(0, len(src), 30000)]
        for i, ch in enumerate(chunks):
            ws.Cells(i + 1, 1).Value = ch
        ws.Visible = 2  # xlSheetVeryHidden

        # 注入 VBA 模块
        vbcomp = wb.VBProject.VBComponents.Add(1)  # vbext_ct_StdModule
        vbcomp.Name = "ModRun"
        vbcomp.CodeModule.AddFromString(VBA)

        # 输入表放按钮
        ws = wb.Sheets("输入")
        btn = ws.Buttons().Add(420, 30, 130, 38)
        btn.OnAction = "RunX"
        btn.Caption = "计算"
        btn.Name = "btnRun"

        # 保存为 xlsm
        wb.SaveAs(OUT, FileFormat=52)  # 52 = xlOpenXMLWorkbookMacroEnabled
        print("SAVED:", OUT)
        print("engine chunks:", len(chunks), "chars:", len(src))
    finally:
        try:
            wb.Close(False)
        except Exception:
            pass
        excel.Quit()


if __name__ == "__main__":
    main()
