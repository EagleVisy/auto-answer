"""实操函数提示容错与有限本地函数目录；内容只展示，从不执行。"""
from __future__ import annotations
import re
from .practice import function_lines


def normalize_functions(text: str) -> str:
    cleaned=[]
    for raw in text.splitlines():
        line=raw.strip()
        if line.startswith('```') or not line:continue
        line=re.sub(r'^\s*(?:[-*•]|\d+[.)、])\s*','',line).strip().strip('`')
        cleaned.append(line)
    return function_lines('\n'.join(cleaned))


def fallback_functions(text: str) -> str:
    """只对明确操作给必要API；不根据数据猜拟合曲线或数值。"""
    # 干扰选项不是必须操作，目录仅依据当前题干与文件材料。
    from .question_layout import option_label
    stem=[]
    for line in text.splitlines():
        if option_label(line):break
        stem.append(line)
    value='\n'.join(stem).casefold()
    value=re.split(r'\s+[a-h][.、:：]\s*',value,maxsplit=1)[0]
    # 否定的操作不用于本地目录选择，含混复杂指令宁可不兜底。
    value=re.sub(r'(?:不使用|不要|无需|不进行|不需要)[^，,。;；\n]{0,30}', '', value)
    functions=[]
    if re.search(r'\.xlsx?|电子表格|excel',value):functions.append('pd.read_excel')
    elif '.csv' in value:functions.append('pd.read_csv')
    if re.search(r'arma|arima|sarima',value):
        functions += ['statsmodels.tsa.arima.model.ARIMA','ARIMA.fit','ARIMAResults.predict']
    elif re.search(r'逻辑回归|logisticregression',value):
        functions += ['sklearn.linear_model.LogisticRegression','LogisticRegression.fit','LogisticRegression.predict']
    elif re.search(r'线性回归|linearregression',value):
        functions += ['sklearn.linear_model.LinearRegression','LinearRegression.fit','LinearRegression.predict']
    elif re.search(r'聚类|kmeans',value):functions += ['sklearn.cluster.KMeans','KMeans.fit_predict']
    if re.search(r'相关系数|相关矩阵',value):functions.append('pd.DataFrame.corr')
    if re.search(r'中位数',value):functions.append('pd.DataFrame.median')
    if re.search(r'标准差',value):functions.append('pd.DataFrame.std')
    if re.search(r'最大值|(?<![A-Za-z_])max\s*(?:\(|o|函数)',value):functions.append('pd.Series.max')
    if re.search(r'最小值|(?<![A-Za-z_])min\s*(?:\(|o|函数)',value):functions.append('pd.Series.min')
    if re.search(r'求和|总和|(?<![A-Za-z_])sum\s*(?:\(|o|函数)',value):functions.append('pd.Series.sum')
    if re.search(r'均值|平均值',value):
        if re.search(r'分组|各组|groupby',value):functions.append('pd.DataFrame.groupby')
        functions.append('pd.DataFrame.mean')
    if re.search(r'排序',value):functions.append('pd.DataFrame.sort_values')
    if re.search(r'筛选',value):functions.append('pd.DataFrame.query')
    if re.search(r'均方误差|mse',value):functions.append('sklearn.metrics.mean_squared_error')
    if re.search(r'R2|r2|决定系数',value):functions.append('sklearn.metrics.r2_score')
    if re.search(r'缺失值',value):functions += ['pd.DataFrame.isna','pd.DataFrame.sum']
    if re.search(r'拟合效果图|绘图|画图|可视化|曲线|plot',value):functions.append('matplotlib.pyplot.plot')
    # 单凭文件名不猜下一步操作。
    if not functions or all(name in ('pd.read_excel','pd.read_csv') for name in functions):return ''
    return function_lines('\n'.join(dict.fromkeys(functions)))
