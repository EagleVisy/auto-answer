"""全屏杂乱文字中的结构定位，验收范围必须含题干和所有选项。"""
import unittest
from auto_answer.detection import TextBox, locate_question
from auto_answer.models import Rect


def box(x, y, width, text, height=30):
    return TextBox(Rect(x, y, width, height), text, .99)


class LayoutLocatorTests(unittest.TestCase):
    def assert_contains_all(self, selected, rows):
        self.assertIsNotNone(selected, '未定位完整题目')
        for row in rows:
            self.assertTrue(selected.contains(row.rect), f'遗漏题干/选项：{row.text}')

    def test_sidebar_keyword_spam_loses_to_number_type_options(self):
        sidebar = [box(40, 80 + i * 38, 310, '正确学习 阅读解答 题目通知 错误提示') for i in range(17)]
        question = [box(620, 310, 730, '12、（单选题）下列哪一项正确？（ ）'),
                    box(640, 440, 240, 'A. 第一选项'), box(640, 590, 240, 'B. 第二选项'),
                    box(640, 740, 240, 'C. 第三选项'), box(640, 890, 240, 'D. 第四选项')]
        selected = locate_question(sidebar + question, 1920, 1080)
        self.assert_contains_all(selected, question)
        self.assertGreater(selected.x, 500, '误锁左侧菜单')
        self.assertLess(selected.y + selected.height, 1000, '应排除底部导航')

    def test_split_option_letters_and_wide_vertical_spacing(self):
        rows = [box(400, 180, 650, '7.（判断题）下列结论成立吗？（ ）'),
                box(420, 350, 20, 'A'), box(470, 350, 180, '正确'),
                box(420, 530, 20, 'B'), box(470, 530, 180, '错误')]
        selected = locate_question(rows + [box(430, 710, 180, '下一题'), box(1300, 100, 230, '剩余时间：01:57:22')], 1920, 1080)
        self.assert_contains_all(selected, rows)
        self.assertLess(selected.y + selected.height, 710)

    def test_two_column_options_and_wrapped_text(self):
        question = [box(500, 200, 980, '15、以下哪个说法成立（ ）'),
                    box(520, 330, 420, 'A. 这是较长的第一项'), box(560, 370, 400, '第一项换行补充条件'),
                    box(1050, 330, 420, 'B. 第二项'),
                    box(520, 480, 420, 'C. 第三项'), box(1050, 480, 420, 'D. 第四项'),
                    box(1090, 520, 400, '第四项换行完整内容')]
        selected = locate_question(question, 1920, 1080)
        self.assert_contains_all(selected, question)

    def test_plain_numbered_fill_blank_without_question_keyword(self):
        question = [box(500, 300, 900, '3、矩阵A的秩为2，则伴随矩阵的秩为（    ）。')]
        selected = locate_question([box(40, 200, 300, '题目通知：选择正确的学习资料'), *question], 1920, 1080)
        self.assert_contains_all(selected, question)
        self.assertGreater(selected.x, 400)

    def test_numbered_page_menu_is_not_a_question(self):
        rows = [box(50, 100, 350, '1. 新闻通知'), box(50, 160, 350, '2. 系列教材'),
                box(50, 220, 350, '3. 师资培训'), box(50, 280, 350, '4. 大赛真题讲解')]
        self.assertIsNone(locate_question(rows, 1920, 1080))

    def test_decimal_values_and_clock_are_not_question_number(self):
        rows = [box(500, 120, 180, '01:57:22'), box(500, 300, 500, '3.1415926'),
                box(500, 400, 500, '正确显示 错误提示 选择菜单')]
        self.assertIsNone(locate_question(rows, 1920, 1080))

    def test_neighbor_question_not_merged(self):
        first = [box(430, 200, 730, '1、（单选题）选择一个结果（ ）'), box(450, 280, 220, 'A. 结果一'), box(450, 340, 220, 'B. 结果二')]
        second = [box(430, 520, 730, '2、（单选题）选择另一个结果（ ）'), box(450, 600, 220, 'A. 一'), box(450, 660, 220, 'B. 二')]
        selected = locate_question(first + second, 1920, 1080)
        self.assertIsNotNone(selected)
        groups = [group for group in (first, second) if selected.contains(group[0].rect)]
        self.assertEqual(len(groups), 1, '不应把两题拼为一个题')
        self.assert_contains_all(selected, groups[0])

    def test_radio_circle_ocr_prefix_not_lost(self):
        rows = [box(550, 300, 760, '1、（判断题）下列说法成立吗（ ）'),
                box(600, 490, 190, 'OA 正确'), box(600, 630, 190, 'ＯB 错误')]
        self.assert_contains_all(locate_question(rows, 1920, 1080), rows)

    def test_low_last_option_near_screen_bottom_included(self):
        rows=[box(500,100,800,'1、（单选题）下列哪项正确（ ）'),box(520,280,300,'A. 一'),box(520,500,300,'B. 二'),box(520,720,300,'C. 三'),box(520,950,300,'D. 四')]
        self.assert_contains_all(locate_question(rows,1920,1080),rows)

    def test_overlap_duplicates_and_same_row_jitter(self):
        rows=[box(500,180,900,'1、（单选题）选择（ ）'),box(520,350,300,'A. 一'),box(521,351,299,'A. 一'),box(1100,344,300,'B. 二'),box(520,480,300,'C. 三'),box(1100,479,300,'D. 四')]
        self.assert_contains_all(locate_question(rows,1920,1080),rows)

    def test_numbered_inner_condition_not_next_question(self):
        rows=[box(500,160,900,'第8题（单选题）已知满足以下条件，选择结论（ ）'),box(530,220,850,'1）若x为正数，则y大于0。'),box(530,270,850,'2）若x为负数，则y小于0。'),box(520,380,300,'A. 一'),box(520,460,300,'B. 二')]
        self.assert_contains_all(locate_question(rows,1920,1080),rows)

    def test_unnumbered_premise_above_prompt_retained(self):
        rows=[box(500,150,900,'已知a大于0，b小于0，且a+b=1。'),box(500,220,900,'下列哪一项正确？'),box(520,330,300,'A. 一'),box(520,430,300,'B. 二')]
        self.assert_contains_all(locate_question(rows,1920,1080),rows)

    def test_unumbered_question_with_continuous_options(self):
        rows = [box(500, 200, 900, '下列计算结果正确的是？'), box(520, 350, 300, 'A. 1+1=2'),
                box(520, 500, 300, 'B. 1+1=3'), box(520, 650, 300, 'C. 1+1=4')]
        self.assert_contains_all(locate_question(rows, 1920, 1080), rows)


if __name__ == '__main__':
    unittest.main()
