from pathlib import Path
import json,unittest
from PIL import Image
from auto_answer.detection import TextBox
from auto_answer.models import Rect
from auto_answer.auto_locator import propose_location
from auto_answer.practice import needs_external_data
from auto_answer.function_guidance import fallback_functions

class Practical141Tests(unittest.TestCase):
    def test_material_contiguous_intro_and_previous_question_boundary(self):
        image=Image.new('RGB',(1400,1000),'white')
        def box(y,text,x=300,w=800):return TextBox(Rect(x,y,w,30),text,.99)
        rows=[box(200,'以下每道题均独立操作，不继承其他题目的修改。'),box(240,'导入后对象命名为demo，后续均使用该对象。'),box(280,'demo.xlsx'),box(380,'1、（单选题）通过max()分析demo.xlsx输出最大值（）')]
        rows += [box(480+i*100,f'{letter}. 数值') for i,letter in enumerate('ABCD')]
        p=propose_location(rows,image);self.assertTrue(p.functions_only);self.assertLess(p.rect.y,200)
        previous=[box(100,'1、（判断题）以下结论（）'),box(150,'old.csv'),box(210,'A. 正确'),box(270,'B. 错误')]
        current=[box(400,'2、（单选题）最大值属于哪种统计量（）')]
        current += [box(520+i*100,f'{letter}. 术语') for i,letter in enumerate('ABCD')]
        p=propose_location(previous+current,image)
        self.assertFalse(p.functions_only);self.assertGreater(p.rect.y,350)

    def test_extrema_file_dependency_vs_concept(self):
        self.assertTrue(needs_external_data('1、（单选题）请通过max()分析demo.xlsx，列中的最大值是（）'))
        self.assertTrue(needs_external_data('1、（单选题）请通过maxO函数分析demo.xlsx，列中的最大值是（）'))
        self.assertIn('pd.Series.max',fallback_functions('请通过maxO函数分析demo.xlsx，该列输出是（）'))
        self.assertIn('pd.Series.max',fallback_functions('请通过max()分析demo.xlsx，该列输出是（）'))
        self.assertFalse(needs_external_data('下列关于max函数的作用描述正确的是？'))
        self.assertFalse(needs_external_data('已知数据[1,4,8]，最大值是多少？'))

if __name__=='__main__':unittest.main()
