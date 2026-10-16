"""外部数据实操题：仅识别依赖与校验函数文本，不读取、下载或执行文件。"""
from __future__ import annotations
import ast
import re
import unicodedata

_FILE=re.compile(r'[^\s"“”<>]+\.(?:xlsx?|csv|sav|dta|json|parquet|txt)(?![A-Za-z0-9_])',re.I)


def needs_external_data(text: str) -> bool:
    value=unicodedata.normalize('NFKC',text)
    reference=bool(_FILE.search(value) or re.search(r'基于.{0,50}数据集|导入.{0,30}数据|原始数据|附件.{0,20}文件',value))
    # 公共文件材料不代表每道题都依赖运行：优先只判断最后一道题的题干。
    starts=list(re.finditer(r'(?:^|\n)\s*\d{1,4}[、.．)）].{0,30}(?:单选|多选|判断|选择|计算|操作)',value))
    question=value[starts[-1].start():] if starts else value
    from .question_layout import option_label
    stem=[]
    for line in question.splitlines():
        if option_label(line):break
        stem.append(line)
    question='\n'.join(stem)
    question=re.split(r'\s+[A-H][.、:：]\s*',question,maxsplit=1)[0]
    operation=bool(re.search(r'拟合|建模|预测|回归|相关系数|聚类|统计量|均值|平均值|中位数|标准差|最大值|最小值|总和|求和|max(?:\s*\(|O|函数)|min(?:\s*\(|O|函数)|sum(?:\s*\(|O|函数)|缺失值|排序|绘图|效果图|运行结果|输出结果|计算|筛选.{0,30}(?:条|行|数)|排序后',question,re.I))
    conceptual=bool(re.search(r'定义是什么|含义是什么|概念是|哪个函数|哪个方法|函数的作用|函数用于|文件扩展名|文件格式|(?:关于|有关).{0,50}(?:说法|描述|叙述|原理|性质)',question,re.I))
    result_dependent=bool(re.search(r'输出结果|运行结果|效果图|大于\s*\d|小于\s*\d|等于\s*\d|计算.{0,20}(?:值|系数|率|R2)|多少|个数|前[三五十\d]+项|均值',question,re.I))
    # 文件说明可能在截图上方被裁掉；题型是单选不妨碍它本质依赖运行。
    model_run=bool(re.search(r'(?:ARMA|ARIMA|SARIMA|回归|聚类|模型).{0,35}(?:拟合|训练|预测|效果图)|(?:拟合|训练).{0,30}(?:模型|温度|数据)',question,re.I))
    data_object=bool(re.search(r'数据表|数据集|数据框|DataFrame|原始数据|导入数据|文件中|表中|温度|销售额|样本数据',question,re.I))
    literal_data=bool(re.search(r'\[[\s\d.,，;；+-]+\]|(?:数据|数列)\s*[:：]\s*\d',question))
    actual_run=bool(re.search(r'输出结果|运行结果|拟合效果图|大于\s*\d|小于\s*\d|等于\s*\d|计算.{0,20}(?:值|系数|率|R2)',question,re.I))
    return operation and (not conceptual or actual_run) and not literal_data and (reference or model_run or (data_object and result_dependent))


def function_lines(text: str) -> str:
    """只接受函数名/调用表达式；不接受赋值、说明、导入或选项字母。"""
    lines=[line.strip().strip('`') for line in text.strip().splitlines() if line.strip()]
    if not 1<=len(lines)<=12:raise ValueError('函数列表数量无效')
    def function_target(node):
        if isinstance(node,ast.Name):return bool(re.fullmatch(r'[A-Za-z_]\w*',node.id)) and node.id not in 'ABCDEFGH' and node.id not in {'exec','eval','compile','__import__','print','input','help'} and not node.id.startswith('__')
        if isinstance(node,ast.Attribute):return function_target(node.value) and not node.attr.startswith('__')
        if isinstance(node,ast.Call):return function_target(node.func)
        return False
    for line in lines:
        if len(line)>350 or re.search(r'[#;]',line):raise ValueError('只允许必要函数')
        try:node=ast.parse(line,mode='eval').body
        except SyntaxError:raise ValueError('函数格式无效') from None
        if not isinstance(node,(ast.Name,ast.Attribute,ast.Call)) or not function_target(node):raise ValueError('不是函数名称或调用')
        for child in ast.walk(node):
            if isinstance(child,(ast.Lambda,ast.ListComp,ast.DictComp,ast.SetComp,ast.GeneratorExp,ast.NamedExpr)):raise ValueError('不是函数列表')
            if isinstance(child,ast.Call) and not function_target(child.func):raise ValueError('包含非必要可执行代码')
            if isinstance(child,ast.Constant) and isinstance(child.value,str) and ('\n' in child.value or ';' in child.value):raise ValueError('不能嵌入完整代码')
    return '\n'.join(lines)
