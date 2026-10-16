"""精简实操代码的展示规范与有限回退；从不执行、导入或读取所展示代码。"""
from __future__ import annotations
import ast
import io
import json
import re
import tokenize

_ALLOWED_IMPORTS={'pandas','numpy','matplotlib','statsmodels','sklearn','scipy','math','statistics','seaborn'}
_BLOCKED_CALLS={'exec','eval','compile','__import__','open','input','getattr','setattr','delattr','globals','locals','vars','breakpoint','exit','quit'}
_BLOCKED_ATTRS={'system','popen','spawn','run','call','Popen','connect','request','urlopen','urlretrieve','read_pickle','load','loads','dump','dumps','save','savefig','to_csv','to_excel','to_pickle','to_parquet','to_json','write','write_text','write_bytes','unlink','remove','rmdir','mkdir'}


def normalize_practice_code(text:str)->str:
    """去外层围栏和注释，做展示格式检查；不是可执行安全沙箱。"""
    value=text.strip()
    if value.startswith('```'):
        match=re.fullmatch(r'```(?:python|py)?\s*\n(.*?)\n```',value,re.S|re.I)
        if not match:raise ValueError('代码围栏格式无效')
        value=match.group(1)
    if len(value)>8000:raise ValueError('实操代码过长')
    try:
        tokens=[token for token in tokenize.generate_tokens(io.StringIO(value).readline) if token.type!=tokenize.COMMENT]
        value=tokenize.untokenize(tokens)
        lines=[line.rstrip() for line in value.strip().splitlines()]
        value='\n'.join(line for index,line in enumerate(lines) if line.strip() or (index and lines[index-1].strip()))
        tree=ast.parse(value)
    except (SyntaxError,tokenize.TokenError,IndentationError):raise ValueError('不是有效Python代码') from None
    if not tree.body or len(value.splitlines())>40:raise ValueError('实操代码行数无效')
    for statement in tree.body:
        if not isinstance(statement,(ast.Import,ast.ImportFrom,ast.Assign,ast.AnnAssign,ast.AugAssign,ast.Expr)):raise ValueError('只展示必要的导入、赋值和计算调用')
        if isinstance(statement,ast.Expr) and not isinstance(statement.value,ast.Call):raise ValueError('不是必要代码调用')
        if isinstance(statement,(ast.Import,ast.ImportFrom)):
            modules=[alias.name for alias in statement.names] if isinstance(statement,ast.Import) else [statement.module or '']
            if isinstance(statement,ast.ImportFrom) and statement.level:raise ValueError('不支持相对导入')
            if any(name.split('.')[0] not in _ALLOWED_IMPORTS or '__' in name for name in modules):raise ValueError('只允许数据分析必要库')
            if any(alias.name=='*' for alias in statement.names):raise ValueError('不使用通配导入')
    for node in ast.walk(tree):
        if isinstance(node,(ast.Lambda,ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp,ast.NamedExpr,ast.Await,ast.Yield)):raise ValueError('代码过于复杂')
        if isinstance(node,ast.Name) and (node.id.startswith('__') or node.id in _BLOCKED_CALLS):raise ValueError('不接受动态执行代码')
        if isinstance(node,ast.Attribute) and (node.attr.startswith('__') or node.attr in _BLOCKED_ATTRS):raise ValueError('不接受系统、网络或写文件代码')
        if isinstance(node,ast.Call) and not isinstance(node.func,(ast.Name,ast.Attribute)):raise ValueError('不接受间接代码调用')
        if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.match(r'(?i)^(?:https?://|ftp://|file://|\\\\)',node.value):raise ValueError('不下载外部数据')
    calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call)]
    data_calls=[node for node in calls if not (isinstance(node.func,ast.Name) and node.func.id=='print')]
    meaningful=[node for node in data_calls if (isinstance(node.func,ast.Attribute) and not isinstance(node.func.value,ast.Constant)) or (isinstance(node.func,ast.Name) and node.func.id not in {'str','int','float','bool','list','dict','tuple','set','repr','len','round'})]
    if not meaningful:raise ValueError('不能以常量或文字提示冒充实操代码')
    if any(isinstance(node,ast.Constant) and node.value is Ellipsis for node in ast.walk(tree)):raise ValueError('必要代码不能含省略参数')
    return value.strip()


def fallback_practice_code(text:str)->str:
    """仅明确文件和聚合操作时给代码，不据选项编数值或补未知模型参数。"""
    from .question_layout import option_label
    rows=[]
    for line in text.splitlines():
        if option_label(line):break
        rows.append(line)
    stem=re.split(r'\s+[A-H][.、:：]\s*','\n'.join(rows),maxsplit=1)[0]
    starts=list(re.finditer(r'(?:^|\n)\s*\d{1,4}[、.．)）].{0,30}(?:单选|多选|判断|选择|计算|操作)',stem))
    question=stem[starts[-1].start():] if starts else stem
    question=re.sub(r'(?:不使用|不要|无需|不进行|不需要)[^，,。;；\n]{0,30}','',question)
    # 不把复杂模型题削减成一次聚合，也不猜order/训练特征/阈值。
    if re.search(r'拟合|建模|回归|聚类|预测|排序|筛选|分组|各组|相关|绘图|曲线|方差|总体标准差|样本标准差|加权|众数|分位|百分位|偏度|峰度',question):return ''
    def filenames(value):
        pattern=r'["“\']([^"”\'\n]+\.(?:xlsx?|csv))["”\']'
        quoted=re.findall(pattern,value,re.I)
        rest=re.sub(pattern,' ',value,flags=re.I)
        rest=re.sub(r'(?:基于|读取|导入|分析|使用|通过|文件|数据集|Excel表格|表格)\s*',' ',rest,flags=re.I)
        # 未加引号的中文文件边界含混，不能把“销售2026.xlsx”偷偷截成2026.xlsx。
        if re.search(r'[\u4e00-\u9fff][A-Za-z0-9_.-]*\s*\.\s*(?:xlsx?|csv)',rest,re.I):return ['<ambiguous-file>','<unknown-file>']
        found=quoted+re.findall(r'(?<![\w.-])([A-Za-z0-9_-][A-Za-z0-9_.-]*\s*\.\s*(?:xlsx?|csv))(?![A-Za-z0-9_])',rest,re.I)
        return list(dict.fromkeys(re.sub(r'\s*\.\s*','.',name) for name in found))
    files=filenames(question) or filenames(stem)
    if len(files)!=1:return ''
    filename=files[0]
    columns=list(re.finditer(r'["“\']([^"”\'\n]+)["”\']\s*(?:列|字段)',question))
    if len(columns)>1:return ''
    col=columns[0] if columns else None
    if col and re.search(r'["“\'][^"”\'\n]+["”\']\s*(?:和|与|、|及)',question):return ''
    if not col and re.search(r'列|字段',question) and not re.search(r'(?:所有|全部|各)(?:数值|数字|数值型)?列',question):return ''
    variable=re.search(r'命名为\s*([A-Za-z_][A-Za-z0-9_]*)',stem)
    name=variable.group(1) if variable and not variable.group(1).startswith('__') else 'df'
    operations=[]
    for pattern,method in [(r'均值|平均值|mean\s*\(','mean'),(r'最大值|(?<![A-Za-z_])max\s*(?:\(|O|函数)','max'),(r'最小值|(?<![A-Za-z_])min\s*(?:\(|O|函数)','min'),(r'求和|总和|(?<![A-Za-z_])sum\s*(?:\(|O|函数)','sum'),(r'中位数','median'),(r'标准差','std')]:
        if re.search(pattern,question,re.I):operations.append(method)
    if not operations:return ''
    read='read_csv' if filename.lower().endswith('.csv') else 'read_excel'
    lines=['import pandas as pd','',f'{name} = pd.{read}({json.dumps(filename,ensure_ascii=False)})']
    target=f'{name}[{json.dumps(col.group(1),ensure_ascii=False)}]' if col else name
    for method in dict.fromkeys(operations):lines.append(f'print({target}.{method}({"" if col else "numeric_only=True"}))')
    return normalize_practice_code('\n'.join(lines))
