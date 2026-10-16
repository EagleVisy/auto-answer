from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def validate(self) -> None:
        if not all(type(v) is int for v in (self.x, self.y, self.width, self.height)):
            raise ValueError("区域坐标必须为整数")
        if not 24 <= self.width <= 16384 or not 24 <= self.height <= 16384:
            raise ValueError("区域宽高应在 24–16384 像素之间")

    def intersection(self, other: Rect) -> Rect | None:
        x, y = max(self.x, other.x), max(self.y, other.y)
        r, b = min(self.x + self.width, other.x + other.width), min(self.y + self.height, other.y + other.height)
        return Rect(x, y, r - x, b - y) if r > x and b > y else None

    def contains(self, other: Rect) -> bool:
        return self.x <= other.x and self.y <= other.y and self.x + self.width >= other.x + other.width and self.y + self.height >= other.y + other.height


@dataclass
class Settings:
    mode: str = "manual"
    monitor_index: int = 0
    capture_rect: Rect | None = None
    answer_rect: Rect = field(default_factory=lambda: Rect(70, 100, 460, 230))
    model: str = "deepseek-flash"
    effort: str = "low"
    max_tokens: int = 4096
    timeout_seconds: int = 35
    scan_interval_ms: int = 700
    stable_ms: int = 1000
    auto_refresh_ms: int = 250
    auto_stable_ms: int = 650
    auto_lock_timeout_ms: int = 5000
    reasoning_words: int = 120
    font_size: int = 22
    font_family: str = "Microsoft YaHei UI"
    text_color: str = "#ECFDF5"
    background_color: str = "#142A32"
    opacity: int = 88
    show_capture: bool = True
    show_answer: bool = True
    history_limit: int = 200
    privacy_accepted: bool = False
    web_search: bool = False
    search_privacy_accepted: bool = False

    def validate(self) -> None:
        if self.mode not in {"manual", "auto"}:
            raise ValueError("识题模式无效")
        if self.effort not in {"none", "low", "high", "max"}:
            raise ValueError("思考强度无效")
        if self.model != "deepseek-flash":
            raise ValueError("当前只支持具备图像能力的官方 deepseek-flash")
        ranges = {"monitor_index": (0, 32), "max_tokens": (512, 32768), "timeout_seconds": (5, 180),
                  "scan_interval_ms": (250, 10000), "stable_ms": (300, 10000), "auto_refresh_ms": (100, 2000), "auto_stable_ms": (200, 5000), "auto_lock_timeout_ms": (1000, 30000), "reasoning_words": (20, 2000),
                  "font_size": (6, 72), "opacity": (0, 100), "history_limit": (10, 2000)}
        for name, (low, high) in ranges.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} 应在 {low}–{high} 之间")
        for name in ("text_color", "background_color"):
            if not isinstance(getattr(self, name), str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", getattr(self, name)):
                raise ValueError("颜色需使用 #RRGGBB 格式")
        if not isinstance(self.font_family, str) or not self.font_family.strip() or len(self.font_family) > 100:
            raise ValueError("字体名称无效")
        for name in ("show_capture", "show_answer", "privacy_accepted", "web_search", "search_privacy_accepted"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("开关设置无效")
        self.answer_rect.validate()
        if self.capture_rect:
            self.capture_rect.validate()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        if not isinstance(data, dict):
            raise ValueError("设置格式无效")
        fields = cls.__dataclass_fields__
        clean = {k: v for k, v in data.items() if k in fields}
        for key in ("capture_rect", "answer_rect"):
            if key in clean and clean[key] is not None:
                clean[key] = Rect(**clean[key])
        result = cls(**clean)
        result.validate()
        return result


@dataclass(frozen=True)
class AnswerResult:
    question: str
    answer: str
    certain: bool
    is_question: bool
    sources: tuple = ()
    timings: dict = field(default_factory=dict)
    answer_kind: str = 'answer'

    @classmethod
    def from_dict(cls, value: dict) -> AnswerResult:
        if not isinstance(value, dict):
            raise ValueError("回答不是 JSON 对象")
        for name in ("question", "answer"):
            if not isinstance(value.get(name), str):
                raise ValueError("回答缺少文字字段")
        for name in ("certain", "is_question"):
            if type(value.get(name)) is not bool:
                raise ValueError("回答缺少有效判断字段")
        if len(value["answer"]) > 60000 or len(value["question"]) > 8000:
            raise ValueError("回答长度异常")
        if value["is_question"] and value["certain"] and not value["answer"].strip():
            raise ValueError("回答内容为空")
        kind=value.get('answer_kind','answer')
        if kind not in ('answer','functions','practice_code'):raise ValueError('回答类型无效')
        answer=value['answer'].strip()
        if kind=='functions':
            if value['certain'] or not value['is_question']:raise ValueError('函数提示不可冒充确定答案')
            from .practice import function_lines
            answer=function_lines(answer)
        if kind=='practice_code':
            if value['certain'] or not value['is_question']:raise ValueError('未运行实操代码不可标确定答案')
            from .practice_code import normalize_practice_code
            answer=normalize_practice_code(answer)
        return cls(value['question'].strip(), answer, value['certain'], value['is_question'],answer_kind=kind)

    @property
    def display_answer(self) -> str:
        if not self.is_question:
            return "等待题目"
        if self.answer_kind=='functions':
            from .practice import function_lines
            return function_lines(self.answer)
        if self.answer_kind=='practice_code':
            from .practice_code import normalize_practice_code
            return normalize_practice_code(self.answer)
        return self.answer if self.certain else "无法确定，请检查题目是否完整"
