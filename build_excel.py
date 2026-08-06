# -*- coding: utf-8 -*-
"""
生成 Enhanced ExactMass Calculator (.xlsm)

布局：
  Calculator sheet:
    A1  标题
    输入区（B3:AA3 元素 / B4:AA4 同位素种类 / B5:AA5 原子个数）
    B7  分子式显示（自动）
    B9  电荷数 z
    B10 质量精度（默认 0.001）
    A13 输出表头（峰# | 中性精确质量 | m/z | 相对丰度%）
    A14:A33 输出 20 行
    C40 "计算前20峰" 按钮（VBA 宏 RunTheo）
  IsotopeData sheet（长表）:
    A1:D49 元素/质量数/精确质量/天然丰度
"""
import json
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SRC = r'D:\code test\chem\同位素计算及解卷积\ExactMass_Calculator.xlsx'
OUT = r'D:\code test\chem\同位素计算及解卷积\ExactMass_Calculator_v2.xlsx'
ISO_JSON = r'D:\code test\chem\同位素计算及解卷积\isotope_data.json'

wb = openpyxl.Workbook()

# ---------------------------------------------------------------------------
# 样式常量
# ---------------------------------------------------------------------------
FONT_TITLE = Font(name='微软雅黑', size=14, bold=True, color='1F4E79')
FONT_LABEL = Font(name='微软雅黑', size=11, bold=True, color='1F4E79')
FONT_BODY = Font(name='微软雅黑', size=11)
FONT_HEADER = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
FILL_HEADER = PatternFill('solid', fgColor='2E75B6')
FILL_INPUT = PatternFill('solid', fgColor='FFF2CC')
FILL_OUTPUT = PatternFill('solid', fgColor='E2EFDA')
FILL_ISO = PatternFill('solid', fgColor='DDEBF7')
THIN = Side(style='thin', color='B4B4B4')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center')

# ---------------------------------------------------------------------------
# IsotopeData sheet（长表）
# ---------------------------------------------------------------------------
ws_iso = wb.active
ws_iso.title = 'IsotopeData'
with open(ISO_JSON, encoding='utf-8') as f:
    iso_rows = json.load(f)

headers = ['元素', '质量数', '精确质量 (u)', '天然丰度']
for c, h in enumerate(headers, 1):
    cell = ws_iso.cell(row=1, column=c, value=h)
    cell.font = FONT_HEADER
    cell.fill = FILL_HEADER
    cell.alignment = CENTER
    cell.border = BORDER

for r, row in enumerate(iso_rows, 2):
    ws_iso.cell(row=r, column=1, value=row['element']).font = FONT_BODY
    ws_iso.cell(row=r, column=2, value=row['mass_number']).font = FONT_BODY
    c3 = ws_iso.cell(row=r, column=3, value=row['mass'])
    c3.font = FONT_BODY
    c3.number_format = '0.000000000'
    c4 = ws_iso.cell(row=r, column=4, value=row['abundance'])
    c4.font = FONT_BODY
    c4.number_format = '0.000000'
    for c in range(1, 5):
        ws_iso.cell(row=r, column=c).border = BORDER
        ws_iso.cell(row=r, column=c).fill = FILL_ISO
        ws_iso.cell(row=r, column=c).alignment = CENTER

for col, width in zip('ABCD', [10, 10, 18, 12]):
    ws_iso.column_dimensions[col].width = width

# 元素列表（供 Calculator 下拉数据源使用）
ELEM_LIST = []
for row in iso_rows:
    if row['element'] not in ELEM_LIST:
        ELEM_LIST.append(row['element'])

# ---------------------------------------------------------------------------
# 同位素下拉辅助区（IsotopeData sheet 右侧 F 列起）
# 每个元素占一列：第1行=元素名，其下=natural + 各质量数（字符串）。
# 命名区域 iso_<元素> 指向该列，Calculator 同位素行用 =INDIRECT("iso_"&元素) 动态引用。
# ---------------------------------------------------------------------------
ISO_HELPER_START_COL = 6  # F 列
for idx, elem in enumerate(ELEM_LIST):
    hcol = ISO_HELPER_START_COL + idx
    HL = get_column_letter(hcol)
    # 表头：元素名
    head = ws_iso.cell(row=1, column=hcol, value=elem)
    head.font = FONT_HEADER
    head.fill = FILL_HEADER
    head.alignment = CENTER
    head.border = BORDER
    # 选项：natural + 该元素全部质量数（字符串，避免 12 被显示为数字）
    opts = ['natural'] + [str(r['mass_number']) for r in iso_rows if r['element'] == elem]
    for j, opt in enumerate(opts, start=2):
        ccell = ws_iso.cell(row=j, column=hcol, value=opt)
        ccell.font = FONT_BODY
        ccell.alignment = CENTER
        ccell.border = BORDER
    # 命名区域 iso_<elem>
    last_row = 1 + len(opts)
    wb.defined_names.add(openpyxl.workbook.defined_name.DefinedName(
        f'iso_{elem}',
        attr_text=f"IsotopeData!${HL}$2:${HL}${last_row}",
    ))
ws_iso.column_dimensions['F'].width = 10
ws_iso.column_dimensions['G'].width = 10

# ---------------------------------------------------------------------------
# Calculator sheet
# ---------------------------------------------------------------------------
ws = wb.create_sheet('Calculator')

# 标题
ws.merge_cells('A1:J1')
c = ws['A1']
c.value = '同位素精确质量 / m/z 计算器 v2 (前20同位素峰, Python引擎)'
c.font = FONT_TITLE
c.alignment = Alignment(horizontal='left', vertical='center')

# --- 输入区 ---
ws['A3'] = '元素'
ws['A3'].font = FONT_LABEL
ws['A4'] = '同位素种类'
ws['A4'].font = FONT_LABEL
ws['A5'] = '原子个数'
ws['A5'].font = FONT_LABEL

# 默认示例：自然分布 C134 H198 N28 O35 S1
defaults = [
    ('C', 'natural', 134),
    ('H', 'natural', 198),
    ('N', 'natural', 28),
    ('O', 'natural', 35),
    ('S', 'natural', 1),
]
N_COLS = 26  # B..AA

for i in range(N_COLS):
    col = i + 2  # column index (B=2)
    L = get_column_letter(col)
    # 元素行：数据校验下拉（从 IsotopeData 元素列表）
    cell3 = ws[f'{L}3']
    if i < len(defaults):
        cell3.value = defaults[i][0]
    cell3.fill = FILL_INPUT
    cell3.font = FONT_BODY
    cell3.alignment = CENTER
    cell3.border = BORDER
    dv3 = openpyxl.worksheet.datavalidation.DataValidation(
        type='list', formula1=f'="{",".join(ELEM_LIST)}"', allow_blank=True,
        showDropDown=False, showErrorMessage=True)
    dv3.error = '请从下拉列表选择元素'
    dv3.errorTitle = '无效元素'
    ws.add_data_validation(dv3)
    dv3.add(cell3)

    # 同位素种类行：级联下拉（=INDIRECT("iso_"&元素)），随元素动态更新
    cell4 = ws[f'{L}4']
    if i < len(defaults):
        cell4.value = defaults[i][1]
    cell4.fill = FILL_INPUT
    cell4.font = FONT_BODY
    cell4.alignment = CENTER
    cell4.border = BORDER
    # 动态引用同位素辅助区的命名区域 iso_<元素>，改元素后下拉自动更新。
    # 元素为空时返回空列表避免 #REF! 错误
    dv4 = openpyxl.worksheet.datavalidation.DataValidation(
        type='list',
        formula1=f'=IF({L}3="","",INDIRECT("iso_"&{L}3))',
        allow_blank=True,
        showDropDown=False, showErrorMessage=True)
    dv4.error = 'natural 或质量数（如 13）'
    dv4.errorTitle = '无效同位素'
    ws.add_data_validation(dv4)
    dv4.add(cell4)

    # 原子个数行
    cell5 = ws[f'{L}5']
    if i < len(defaults):
        cell5.value = defaults[i][2]
    cell5.fill = FILL_INPUT
    cell5.font = FONT_BODY
    cell5.alignment = CENTER
    cell5.border = BORDER
    cell5.number_format = '0'

for col in range(2, 2 + N_COLS):
    ws.column_dimensions[get_column_letter(col)].width = 9

# --- 分子式显示（B7 自动生成，含下标/上标） ---
# 方案：第 6 行为辅助行（每列一个短公式生成分子式片段），B7 用 CONCATENATE 拼接。
# 避免单个 B7 公式超 Excel 8192 字符限制。
SUB_MAP_D = {'0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
             '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉'}
SUB_MAP_U = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
             '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'}


def _sub_formula(ref: str, mapping: dict) -> str:
    """生成把 ref 中 0-9 替换为下标/上标的公式"""
    f = ref
    for d, rep in mapping.items():
        f = f'SUBSTITUTE({f},"{d}","{rep}")'
    return f


# 第 6 行：每列生成分子式片段（纯同位素 → 上标质量数&元素&下标个数）
for i in range(N_COLS):
    col = i + 2
    L = get_column_letter(col)
    cnt_fmt = _sub_formula(f'TEXT({L}5,"0")', SUB_MAP_D)
    iso_fmt = _sub_formula(f'TEXT({L}4,"0")', SUB_MAP_U)
    cell6 = ws[f'{L}6']
    cell6.value = (
        f'=IF({L}5="","",'
        f'IF(OR({L}4="",{L}4="natural"),'
        f'{L}3&{cnt_fmt},'
        f'{iso_fmt}&{L}3&{cnt_fmt}))'
    )
    cell6.font = Font(name='微软雅黑', size=8, color='999999')

# B7 = CONCATENATE(B6:AA6)（忽略空）+ 电荷上标（z≠0 时）
ws['A7'] = '分子式（自动）'
ws['A7'].font = FONT_LABEL
ws.merge_cells('B7:J7')
ws['B7'].font = FONT_BODY

# 电荷上标：把 B9 的数值转成上标字符（²⁺/³⁻/⁺/⁻），z=0 或空时不显示
# 例：z=2 → "²⁺"，z=-1 → "⁻"（化学惯例单电荷省略数字 1）
CHARGE_SUP = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
}
abs_z = _sub_formula('TEXT(ABS(B9),"0")', CHARGE_SUP)
# 单电荷（|z|=1）省略数字：⁺ / ⁻；多电荷显示 ²⁺ / ³⁻ 等
charge_abs = (
    f'IF(ABS(B9)=1,'
    f'IF(B9>0,"⁺","⁻"),'
    f'{abs_z}&IF(B9>0,"⁺","⁻"))'
)
charge_suffix = f'IF(OR(B9="",B9=0),"",{charge_abs})'
ws['B7'].value = (
    '=CONCATENATE(B6, C6, D6, E6, F6, G6, H6, I6, J6, K6, L6, M6, N6, O6, P6, Q6, R6, S6, T6, U6, V6, W6, X6, Y6, Z6, AA6)'
    ' & ' + charge_suffix
)

# --- 参数区 ---
ws['A9'] = '电荷数 z'
ws['A9'].font = FONT_LABEL
ws['B9'] = 1
ws['B9'].font = FONT_BODY
ws['B9'].alignment = CENTER
ws['B9'].border = BORDER
ws['B9'].fill = FILL_INPUT

ws['A10'] = '质量精度 (Da)'
ws['A10'].font = FONT_LABEL
ws['B10'] = 0.001
ws['B10'].font = FONT_BODY
ws['B10'].alignment = CENTER
ws['B10'].border = BORDER
ws['B10'].fill = FILL_INPUT
ws['B10'].number_format = '0.000'

ws['A11'] = '丰度阈值 (%)'
ws['A11'].font = FONT_LABEL
ws['B11'] = 0.05
ws['B11'].font = FONT_BODY
ws['B11'].alignment = CENTER
ws['B11'].border = BORDER
ws['B11'].fill = FILL_INPUT
ws['B11'].number_format = '0.00'

# --- 输出表 ---
ws['A12'] = '输出：丰度前20同位素峰（按质量升序）'
ws['A12'].font = FONT_LABEL

out_headers = ['#', '中性精确质量 (u)', 'm/z', '相对丰度 (%)']
for c, h in enumerate(out_headers, 1):
    cell = ws.cell(row=13, column=c, value=h)
    cell.font = FONT_HEADER
    cell.fill = FILL_HEADER
    cell.alignment = CENTER
    cell.border = BORDER

for r in range(14, 34):  # 20 行
    for c in range(1, 5):
        cell = ws.cell(row=r, column=c)
        cell.font = FONT_BODY
        cell.border = BORDER
        cell.fill = FILL_OUTPUT
        cell.alignment = CENTER
    ws.cell(row=r, column=2).number_format = '0.000000'
    ws.cell(row=r, column=3).number_format = '0.000000'
    ws.cell(row=r, column=4).number_format = '0.0000'

ws.column_dimensions['A'].width = 16
ws.column_dimensions['B'].width = 22
ws.column_dimensions['C'].width = 18
ws.column_dimensions['D'].width = 14

# --- 按钮（VBA 宏位置说明） ---
ws['A36'] = '使用说明：'
ws['A36'].font = FONT_LABEL
notes = [
    '1. 第3行选元素，第4行选同位素种类（natural=自然分布，或输入质量数如 13），第5行输入原子个数',
    '2. 选 natural 时原子个数=该元素总原子数（按天然丰度算分布包络）；选纯同位素（如 13）时=该同位素原子数（固定质量）',
    '3. 同元素可占多列（如 natural C×128 + ¹³C×6）表示部分标记多肽',
    '4. 第9行电荷数 z（可正可负，0=中性分子不显示电荷），第10行质量精度=峰合并容差（默认0.001 Da）',
    '5. 第11行丰度阈值=相对丰度低于此值的峰不显示（默认0.05%，最强峰=100%）',
    '6. 点击"计算前20峰"按钮，调用 Python (theo.py) 计算并回填输出表',
    '7. 需要本机安装 Python 3.11+ 与 molmass 库：pip install molmass',
]
for i, note in enumerate(notes):
    cell = ws[f'A{37 + i}']
    cell.value = note
    cell.font = Font(name='微软雅黑', size=10, color='595959')

# 按钮说明（实际按钮由 inject_vba.ps1 通过 COM 创建，位置在 E40 附近）
ws['A44'] = '▶ 按钮"计算前20峰"由构建脚本自动放置（E40 区域）'
ws['A44'].font = Font(name='微软雅黑', size=10, bold=True, color='C00000')

wb.save(OUT)
print(f'已保存: {OUT}（此为 .xlsx 中间格式，随后由 inject_vba.ps1 注入宏并另存为 .xlsm）')
