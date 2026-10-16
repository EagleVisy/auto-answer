import json,threading,unittest
import httpx
from auto_answer.models import AnswerResult,Settings
from auto_answer.practice import needs_external_data,function_lines
from auto_answer.pipeline import AnswerPipeline
from auto_answer.api import DeepSeekClient,payload,ApiError


class PracticeFunctionTests(unittest.TestCase):
    def test_file_dependency_not_conceptual_or_plain_choice(self):
        self.assertTrue(needs_external_data('基于demo.xlsx数据集，进行回归拟合并选择效果图'))
        self.assertFalse(needs_external_data('下列关于电子表格功能正确的是'))
        self.assertFalse(needs_external_data('读取demo.xlsx的函数是哪个函数'))
        self.assertFalse(needs_external_data('ARMA模型的定义是什么'))
        material='基于demo.xlsx数据集回答，数据修改操作不继承到其他题。\n'
        self.assertFalse(needs_external_data(material+'1、（单选题）ARMA模型的定义是什么？'))
        self.assertTrue(needs_external_data(material+'1、（单选题）对温度进行回归并计算R2，哪个函数的输出结果大于0.8？'))

    def test_only_functions_are_displayed_and_uncertain(self):
        result=AnswerResult.from_dict({'question':'示例实操','answer':'pd.read_csv(...)\nmodel.fit()\nplt.plot(...)','certain':False,'is_question':True,'answer_kind':'functions'})
        self.assertEqual(result.display_answer,'pd.read_csv(...)\nmodel.fit()\nplt.plot(...)')
        self.assertFalse(result.certain)
        self.assertEqual(function_lines('model.fit(data["温度"])'),'model.fit(data["温度"])')
        for bad in ('A','pd.read_csv(): 读取文件','import pandas as pd','x = func()','print(1) # note','用途：函数',"foo(exec('x=1'))", "foo(eval('1+1'))", "foo({x:x for x in []})"):
            with self.assertRaises(ValueError):function_lines(bad)
        with self.assertRaises(ValueError):AnswerResult.from_dict({'question':'示例','answer':'model.fit()','certain':True,'is_question':True,'answer_kind':'functions'})

    def test_pipeline_function_mode_never_searches_private_data_or_accepts_choice(self):
        class Search:
            def __init__(self):raise AssertionError('缺文件不应搜索替代私有数据')
        class Client:
            def __init__(self,*args):self.functions_only=False
            def answer(self,image,cancel):
                self.assert_functions=self.functions_only
                assert self.functions_only
                return AnswerResult('模拟实操','A',True,True)
        result=AnswerPipeline(Settings(web_search=True),'mock',Client,Search).answer(b'image',threading.Event(),'基于demo.xlsx数据进行拟合并选择效果图')
        self.assertFalse(result.certain);self.assertNotEqual(result.display_answer,'A')

    def test_cropped_practical_choice_without_filename(self):
        self.assertTrue(needs_external_data('146、（单选题）对温度进行ARMA模型拟合，选择拟合效果图'))
        self.assertTrue(needs_external_data('80、（单选题）对数据表分组汇总，计算各组平均值，输出结果为多少？'))
        self.assertFalse(needs_external_data('1、（单选题）计算数据[1,2,3]的均值'))
        class Client:
            def __init__(self,*args):self.functions_only=False
            def answer(self,*args):return AnswerResult('示例实操','import pandas as pd\ndf = pd.read_excel("demo.xlsx")\nprint(df.mean(numeric_only=True))',False,True)
        result=AnswerPipeline(Settings(),'mock',Client).answer(b'x',threading.Event(),'146、（单选题）对温度进行ARMA模型拟合，选择拟合效果图')
        self.assertEqual(result.answer_kind,'practice_code');self.assertIn('print(df.mean(numeric_only=True))',result.display_answer)

    def test_function_fallback_uses_stem_not_distractor_options(self):
        from auto_answer.function_guidance import fallback_functions
        text='使用data.csv销售额做线性回归拟合并预测，输出结果是？\nA. ARMA模型输出\nB. 线性回归\nC. 聚类'
        functions=fallback_functions(text)
        self.assertIn('LinearRegression',functions);self.assertNotIn('ARIMA',functions)
        self.assertNotIn('ARIMA',fallback_functions(text.replace('\n',' ')))
        self.assertFalse(needs_external_data('1、（单选题）下列关于DataFrame均值函数的描述，正确的是？\nA. mean计算均值\nB. corr计算均值'))
        self.assertFalse(needs_external_data('材料说明:data.csv\n1、（单选题）已知数据[10,20,30]，计算均值是多少？'))
        self.assertTrue(needs_external_data('数据表中温度均值是多少？'))

    def test_empty_model_practical_uses_only_known_function_names(self):
        class Client:
            def __init__(self,*args):pass
            def answer(self,*args):return AnswerResult('示例实操','',False,True)
        result=AnswerPipeline(Settings(),'mock',Client).answer(b'x',threading.Event(),'基于demo.xlsx对温度进行ARMA模型拟合，选择拟合效果图')
        self.assertFalse(result.certain)
        self.assertEqual(result.answer_kind,'answer','未知order参数不能凭目录猜模型代码')
        self.assertNotIn('ARIMA(',result.display_answer)

    def test_actual_sse_function_contract_no_description(self):
        seen=[]
        def handler(request):
            seen.append(json.loads(request.content))
            value={'is_question':True,'question':'模拟缺文件题','certain':False,'answer_kind':'practice_code','answer':'import pandas as pd\ndf = pd.read_csv("demo.csv")\nprint(df.mean(numeric_only=True))'}
            chunks=[{'choices':[{'delta':{'content':json.dumps(value,ensure_ascii=False)}}]},{'choices':[{'delta':{},'finish_reason':'stop'}]}]
            return httpx.Response(200,text='\n\n'.join('data: '+json.dumps(c,ensure_ascii=False) for c in chunks)+'\n\ndata: [DONE]\n\n')
        def factory(settings,key):return DeepSeekClient(settings,key,httpx.MockTransport(handler))
        result=AnswerPipeline(Settings(),'mock',factory).answer(b'image',threading.Event(),'基于demo.csv数据集进行回归建模')
        self.assertEqual(result.answer_kind,'practice_code');self.assertFalse(result.certain)
        self.assertIn('不写注释',seen[0]['messages'][0]['content'])
        self.assertIn('print(df.mean(numeric_only=True))',result.display_answer)


if __name__=='__main__':unittest.main()
