"""规则分数只说明文本/字段覆盖，不能替代语义审阅。"""

import re
import unicodedata
from collections import Counter
from html.parser import HTMLParser

from rapidfuzz.distance import Levenshtein


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))


class Cells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells, self.parts, self.inside = [], [], False
        self.has_table = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.has_table = True
        if tag in ("td", "th"):
            self.inside, self.parts = True, []

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.inside:
            self.cells.append(normalize("".join(self.parts)))
            self.parts, self.inside = [], False


def evaluate(page, text, task="structured_parse"):
    mode = page.get("evaluation_mode", "none")
    result = {
        "available": False, "mode": mode, "cer": None, "edit_distance": None,
        "reference_characters": None, "field_coverage": None,
        "matched_fields": [], "missing_fields": [], "table_cell_coverage": None,
        "expected_cell_count": None, "matched_cell_count": None,
        "human_verified": False, "semantic_accuracy": None,
        "note": "无参考答案；请人工核对原图。",
    }
    actual = normalize(text)
    if mode == "text" and page.get("reference_text"):
        if task != "structured_parse":
            result["note"] = "参考答案包含全部可见文字，当前任务与参考口径不一致，未计算CER。请选择全文文字提取（structured_parse）进行文本评测。"
            return result
        expected = normalize(page["reference_text"])
        if not expected:
            result["note"] = "参考答案归一化后为空，未计算CER；请人工复核。"
            return result
        if len(expected) > 20000 or len(actual) > 40000:
            result["note"] = "文本超过自动评测长度预算，请人工复核。"
            return result
        distance = Levenshtein.distance(expected, actual)
        result.update(available=True, cer=round(distance / len(expected), 6),
                      edit_distance=distance, reference_characters=len(expected),
                      note="CER=编辑距离/参考字符数，NFKC后删除空白，保留标点和Markdown符号。越低越好，可能大于1；不等于语义准确率。")
    elif mode == "fields" and page.get("expected_fields"):
        fields = page["expected_fields"]
        matched = [k for k, value in fields.items() if normalize(value) and normalize(value) in actual]
        result.update(available=True, field_coverage=round(len(matched) / len(fields), 6),
                      matched_fields=matched, missing_fields=[k for k in fields if k not in matched],
                      note="字段覆盖按预期值是否出现在归一化输出中计算，不验证字段与值的关联，也不代表整体语义正确。")
    elif mode == "table" and page.get("expected_cells"):
        parser = Cells()
        parser.feed(text)
        expected = Counter(normalize(v) for v in page["expected_cells"])
        actual_cells = Counter(parser.cells)
        matched = sum(min(count, actual_cells[value]) for value, count in expected.items())
        total = sum(expected.values())
        result.update(available=True, table_cell_coverage=round(matched / total, 6),
                      expected_cell_count=total, matched_cell_count=matched,
                      table_html_present=parser.has_table,
                      note="按HTML td/th单元格文本多重集合匹配，保留重复次数；不验证行列位置或合并结构，仍需看图复核。")
    return result
