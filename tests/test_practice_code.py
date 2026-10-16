import io,json,tempfile,threading,unittest,tokenize
from pathlib import Path
from unittest.mock import patch
import httpx
from PIL import Image
from auto_answer.practice_code import normalize_practice_code,fallback_practice_code
from auto_answer.models import AnswerResult,Settings
from auto_answer.pipeline import AnswerPipeline
from auto_answer.api import DeepSeekClient,prompt
from auto_answer.storage import HistoryStore

CODE='import pandas as pd\n\ndf = pd.read_excel("demo.xlsx")\nprint(df.mean(numeric_only=True))'
class PracticeCodeTests(unittest.TestCase):
    def test_code_comments_removed_and_hash_inside_column_preserved(self):
        raw='```python\nimport pandas as pd  # 导入\ndf = pd.read_excel("demo.xlsx")\nprint(df["列#1"].mean()) # 计算均值\n```'
        code=normalize_practice_code(raw)
        self.assertIn('import pandas as pd',code);self.assertIn('print(df["列#1"].mean())',code)
        self.assertFalse(any(t.type==tokenize.COMMENT for t in tokenize.generate_tokens(io.StringIO(code).readline)))
    def test_only_display_no_execution_or_file_read(self):
        with patch('builtins.open',side_effect=AssertionError('不得读文件')):
            result=AnswerResult.from_dict({'question':'实操','answer':CODE,'certain':False,'is_question':True,'answer_kind':'practice_code'})
            self.assertEqual(result.display_answer,CODE)
        for bad in ['A','pd.read_excel','import os\nos.system("cmd")','eval("1+1")','exec("x=1")','print(__import__("os"))','pd.read_excel("https://example.com/data.xlsx")','df.to_csv("x.csv")','代码如下\nprint(1)']:
            with self.assertRaises(ValueError,msg=bad):normalize_practice_code(bad)
        with self.assertRaises(ValueError):AnswerResult.from_dict({'question':'实操','answer':CODE,'certain':True,'is_question':True,'answer_kind':'practice_code'})
    def test_fallback_uses_real_filename_column_and_no_fake_model_parameters(self):
        code=fallback_practice_code('基于“sample.xlsx”数据集回答\n141、（单选题）请通过maxO函数分析sample.xlsx，示例“数值列”列的最大值是（）\nA. 101.1\nB. 101.2')
        self.assertIn('pd.read_excel("sample.xlsx")',code);self.assertIn('print(df["数值列"].max())',code);self.assertNotIn('101',code)
        mean=fallback_practice_code('读取data.xlsx数据集，计算所有数值列平均值')
        self.assertIn('print(df.mean(numeric_only=True))',mean)
        self.assertEqual(fallback_practice_code('基于data.csv对温度做ARMA模型拟合，选择效果图'),'')
        self.assertEqual(fallback_practice_code('基于data.csv计算某列均值'),'')
    def test_fallback_does_not_guess_ambiguous_columns_or_filename(self):
        code=fallback_practice_code('基于report.2026.xlsx数据集，计算所有数值列平均值')
        self.assertIn('"report.2026.xlsx"',code)
        for question in ['读取data.xlsx，计算“收入”列均值与“成本”列最大值','读取data.xlsx，计算收入列均值','读取data.xlsx，计算“收入”和“成本”列均值','读取销售2026.xlsx，计算均值','读取"a.xlsx"和b.xlsx，分别计算均值','读取data.xlsx求平均值和方差','读取data.xlsx求总体标准差','读取"data.xlsx"计算均值和众数']:
            self.assertEqual(fallback_practice_code(question),'',question)
        code=fallback_practice_code('材料a.xlsx与b.xlsx\n1、（单选题）读取a.xlsx计算均值')
        self.assertIn('"a.xlsx"',code);self.assertNotIn('"b.xlsx"',code)
        for bad in ['print("请先读取数据再计算均值")','print(str("请先读取数据再计算均值"))','print("请先读取数据再计算均值".format())','import pandas as pd','print(1)']:
            with self.assertRaises(ValueError):normalize_practice_code(bad)

    def test_sse_missing_kind_and_comments_becomes_code_and_never_searches(self):
        seen=[]
        def handler(request):
            body=json.loads(request.content);seen.append(body)
            value={'question':'模拟','answer':CODE+' # 注释','certain':False,'is_question':True}
            blocks=[{'choices':[{'delta':{'content':json.dumps(value,ensure_ascii=False)}}]},{'choices':[{'delta':{},'finish_reason':'stop'}]}]
            return httpx.Response(200,text='\n\n'.join('data: '+json.dumps(b,ensure_ascii=False) for b in blocks)+'\n\ndata: [DONE]\n\n')
        def factory(settings,key):return DeepSeekClient(settings,key,httpx.MockTransport(handler))
        class Search:
            def __init__(self):raise AssertionError('实操不应搜索私有数据')
        result=AnswerPipeline(Settings(web_search=True),'mock',factory,Search).answer(b'x',threading.Event(),'基于demo.xlsx计算均值')
        self.assertEqual(result.answer_kind,'practice_code');self.assertEqual(result.display_answer,CODE);self.assertFalse(result.certain)
        self.assertIn('不写注释',seen[0]['messages'][0]['content'])
    def test_history_preserves_new_code_and_old_function_records(self):
        with tempfile.TemporaryDirectory() as folder:
            history=HistoryStore(Path(folder));output=io.BytesIO();Image.new('RGB',(40,40),'white').save(output,format='PNG')
            try:
                history.add(output.getvalue(),AnswerResult('代码',CODE,False,True,answer_kind='practice_code'),.1,50)
                history.add(output.getvalue(),AnswerResult('旧函数','pd.read_excel(...)',False,True,answer_kind='functions'),.1,50)
                rows=history.list();self.assertEqual({r['answer_kind'] for r in rows},{'practice_code','functions'})
                self.assertEqual(next(r for r in rows if r['answer_kind']=='practice_code')['answer'],CODE)
            finally:history.close()

if __name__=='__main__':unittest.main()
