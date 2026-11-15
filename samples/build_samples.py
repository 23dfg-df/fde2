#!/usr/bin/env python3
"""Build six original synthetic OCR pages and their explicit reference labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
HEIGHT = 1600
INK = "#203341"
MUTED = "#536775"
TEAL = "#0c7e7d"
PAPER = "#ffffff"
LINE = "#cbd6dc"


def find_font(requested: str | None, bold: bool = False) -> Path:
    candidates = [requested] if requested else []
    candidates += [
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold
        else "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for name in candidates:
        if name and Path(name).is_file():
            return Path(name)
    raise SystemExit("未找到中文字体，请传入 --font 和 --bold-font。")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Page:
    def __init__(self, font_path: Path, bold_path: Path, background: str = PAPER):
        self.image = Image.new("RGB", (WIDTH, HEIGHT), background)
        self.draw = ImageDraw.Draw(self.image)
        self.font_path = font_path
        self.bold_path = bold_path
        self.visible_text: list[str] = []
        self.boxes: list[dict] = []

    def font(self, size: int, bold: bool = False):
        return ImageFont.truetype(str(self.bold_path if bold else self.font_path), size)

    def text(self, x, y, text, size=38, fill=INK, bold=False):
        font = self.font(size, bold)
        bounds = self.draw.textbbox((x, y), text, font=font)
        if bounds[0] < 0 or bounds[1] < 0 or bounds[2] > WIDTH or bounds[3] > HEIGHT:
            raise ValueError(f"文字越界：{text!r} {bounds}")
        self.draw.text((x, y), text, font=font, fill=fill)
        self.visible_text.append(text)
        self.boxes.append({"text": text, "bbox": list(bounds)})

    def wrapped(self, x, y, text, width, size=38, leading=60, fill=INK):
        font = self.font(size)
        current = ""
        for char in text:
            if char == "\n":
                self.text(x, y, current, size, fill)
                current = ""
                y += leading
                continue
            candidate = current + char
            if current and self.draw.textlength(candidate, font=font) > width:
                self.text(x, y, current, size, fill)
                current = char
                y += leading
            else:
                current = candidate
        if current:
            self.text(x, y, current, size, fill)
            y += leading
        return y

    def heading(self, title, subtitle=None):
        self.draw.rectangle((80, 84, 90, 149), fill=TEAL)
        self.text(116, 87, title, 52, bold=True)
        if subtitle:
            self.text(84, 187, subtitle, 29, MUTED)
        self.draw.line((84, 254, 1116, 254), fill=LINE, width=2)

    def reference(self):
        return "\n".join(self.visible_text)


def build(output: Path, font_path: Path, bold_path: Path):
    output.mkdir(parents=True, exist_ok=True)
    records = []

    def save(page: Page, page_id: str, name: str, filename: str, **metadata):
        destination = output / filename
        page.image.save(destination, format="PNG", optimize=True)
        record = {
            "id": page_id,
            "name": name,
            "kind": "synthetic",
            "image": filename,
            "width": WIDTH,
            "height": HEIGHT,
            "reference_text": page.reference(),
            "expected_fields": {},
            "expected_cells": [],
            "sha256": sha256(destination),
            "reference_origin": "original_programmatic_fixture",
            "visible_text_boxes": page.boxes,
            **metadata,
        }
        records.append(record)

    # All visible text is recorded. Text fixtures have no unscored footer.
    page = Page(font_path, bold_path)
    page.heading("实验室 GPU 使用通知", "合成教学样本 / 中文通知")
    y = 316
    for line in [
        "通知编号：LAB-NOTICE-001",
        "日期：2026-11-16",
        "主题：共享计算资源使用约定",
    ]:
        page.text(88, y, line, 38)
        y += 72
    y += 48
    for paragraph in [
        "开始实验前，请确认目标显卡的空闲显存，并核对已有进程的归属。",
        "本次教学任务每次只处理一页文档，保留输入图片、模型输出和运行耗时。",
        "实验结束后，停止自己的模型进程，再检查显存是否释放。",
        "不要将整卡占用直接当作本项目的占用，也不要把接口成功当作答案正确。",
    ]:
        y = page.wrapped(88, y, paragraph, 1024, 39, 64) + 42
    save(page, "sample-01", "中文资源使用通知", "01-notice.png",
         evaluation_mode="text", difficulty="easy",
         description="清晰中文段落与编号；逐字参考覆盖页面所有可见文字。")

    page = Page(font_path, bold_path)
    page.heading("Inference Service Quick Guide", "Synthetic teaching sample / English instructions")
    y = 320
    lines = [
        "Project: Document Parsing Lab",
        "Version: 1.0",
        "",
        "1. Check that the model service is ready.",
        "2. Select one page and submit a request.",
        "3. Save the raw output before editing it.",
        "4. Compare the result with the source image.",
        "",
        "A successful HTTP response does not prove",
        "that every word or number is correct.",
        "",
        "Record latency, token usage, and failures.",
        "Use the same input when comparing settings.",
    ]
    for line in lines:
        if line:
            page.text(88, y, line, 34)
        y += 76
    save(page, "sample-02", "英文推理服务手册", "02-english-guide.png",
         evaluation_mode="text", difficulty="easy",
         description="英语、编号与标点；固定人工定义的逐字参考。")

    page = Page(font_path, bold_path)
    page.heading("文档解析教学资产卡", "合成教学样本 / Synthetic asset record")
    form = [
        ("资产编号 Asset ID", "DOC-LAB-031"),
        ("服务名称 Service", "Document Parser"),
        ("责任组 Team", "实验教学组"),
        ("目标设备 Device", "GPU 1"),
        ("访问地址 Endpoint", "127.0.0.1:18400"),
        ("模型标识 Model", "HunyuanOCR"),
        ("参考页数 Pages", "6"),
        ("状态 Status", "待验收 Pending"),
    ]
    for i, (label, value) in enumerate(form):
        top = 312 + i * 122
        if i % 2 == 0:
            page.draw.rectangle((84, top-8, 1116, top+104), fill="#f0f5f6")
        page.text(106, top+22, label, 32, MUTED)
        page.text(578, top+22, value, 34, INK, bold=True)
    save(page, "sample-03", "中英混合资产表单", "03-asset-form.png",
         evaluation_mode="fields", difficulty="medium",
         expected_fields={
             "asset_id": "DOC-LAB-031", "service": "Document Parser",
             "team": "实验教学组", "device": "GPU 1",
             "endpoint": "127.0.0.1:18400", "model": "HunyuanOCR",
             "pages": "6", "status": "待验收 Pending",
         },
         description="仅检查8个字段值的出现情况；不是表单语义或键值关系的完整判定。")

    page = Page(font_path, bold_path)
    page.heading("共享资源登记表", "合成教学样本 / 4 行 × 4 列，包含表头")
    table_rows = [
        ["设备", "编号", "状态", "用途"],
        ["GPU 0", "DEV-A01", "已分配", "文本推理"],
        ["GPU 1", "DEV-B02", "待分配", "文档解析"],
        ["CPU", "DEV-C03", "共享", "资料检索"],
    ]
    left, top, widths, row_height = 84, 370, [240, 270, 240, 282], 168
    for row_index, row in enumerate(table_rows):
        x = left
        row_y = top + row_index * row_height
        for col_index, value in enumerate(row):
            right = x + widths[col_index]
            page.draw.rectangle((x, row_y, right, row_y+row_height),
                                fill="#e8f1f3" if row_index == 0 else PAPER,
                                outline="#6b7c87", width=2)
            page.text(x+23, row_y+61, value, 34, INK, bold=row_index == 0)
            x = right
    save(page, "sample-04", "四行四列资源表格", "04-resource-table.png",
         evaluation_mode="table", difficulty="medium",
         expected_cells=[cell for row in table_rows for cell in row],
         expected_table={"rows": 4, "columns": 4, "cells": table_rows},
         description="16个单元格正文的覆盖与HTML表格存在性；不把词面覆盖当结构正确率。")

    page = Page(font_path, bold_path)
    page.heading("双栏学习卡：申请与验收", "合成教学样本 / 阅读顺序：先左栏，再右栏")
    page.draw.line((600, 305, 600, 1450), fill=LINE, width=2)
    columns = [
        (88, "左栏：申请流程", [
            "第一步，记录实验目标。",
            "只选择一个需要验证的问题。",
            "第二步，检查计算资源。",
            "记录目标卡、空闲显存和时间。",
            "第三步，保存初始配置。",
            "保留模型名称与输入文件。",
            "最后，提交一次短请求。",
        ]),
        (644, "右栏：验收记录", [
            "先检查请求是否成功。",
            "再比较原图与生成文字。",
            "编号、金额和日期要逐项核对。",
            "失败的页面也要保存。",
            "不要只挑选正确的案例。",
            "改变参数前写下预期。",
            "复做后解释差异的原因。",
        ]),
    ]
    for x, heading, paragraphs in columns:
        page.text(x, 324, heading, 36, TEAL, bold=True)
        y = 420
        for paragraph in paragraphs:
            y = page.wrapped(x, y, paragraph, 465, 31, 55) + 42
    save(page, "sample-05", "双栏阅读顺序挑战", "05-two-column.png",
         evaluation_mode="text", difficulty="hard",
         reading_order="title, subtitle, complete left column, complete right column",
         description="CER同时受到识别与阅读顺序影响；应另行人工检查左右栏是否串行。")

    page = Page(font_path, bold_path, background="#fbfaf6")
    page.heading("小字与低对比度核对单", "合成教学样本 / 字符辨识压力测试")
    y = 340
    paragraphs = [
        "记录编号：QC-2026-061。以下内容用于检验小字号和近似字符，不代表真实业务记录。",
        "样本 A 的设备码是 OCR-O0-011，样本 B 的设备码是 OCR-00-101。字母 O 与数字 0 必须分别识别。",
        "校验序列：Il1、B8、S5、Z2。请保留大小写、数字和标点，不要根据常识替换原文。",
        "记录值：12.50 ms、0.75 GiB、99.0%。小数点的位置变化会改变含义，不能只检查数字是否出现。",
        "时间分别为 08:05、18:50 和 00:15；日期分别为 2026-11-06、2026-11-16 和 2026-12-01。",
        "批次编号分别为 A-1007、A-1070 和 A-1700。解析时应保持原始顺序，并保留三个不同编号。",
        "结论：模型输出需要逐项核对。低对比度页面出现漏字、错字或重复时，记录原始输出和失败原因。",
    ]
    for paragraph in paragraphs:
        y = page.wrapped(88, y, paragraph, 1024, 28, 48, "#87939d") + 48
    save(page, "sample-06", "小字与近似字符挑战", "06-low-contrast.png",
         evaluation_mode="text", difficulty="hard",
         description="28px低对比度文字、近似字符和小数；只用于受控压力测试，不代表扫描件分布。")

    manifest = {
        "dataset_id": "fde-doc-synthetic-v1",
        "version": 1,
        "created_on": "2026-11-16",
        "description": "6张原创程序生成的教学文档；不是训练数据或真实业务验收集。",
        "font": {"regular": font_path.name, "bold": bold_path.name,
                 "regular_sha256": sha256(font_path), "bold_sha256": sha256(bold_path)},
        "text_reference_policy": "all_visible_text_in_explicit_reading_order",
        "pages": records,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--font", help="可读中文的TTF或TTC字体路径")
    parser.add_argument("--bold-font", help="可选粗体字体路径")
    args = parser.parse_args()
    result = build(args.output, find_font(args.font), find_font(args.bold_font, bold=True))
    print(json.dumps({"output": str(args.output), "pages": len(result["pages"]),
                      "characters": [len(p["reference_text"]) for p in result["pages"]]}, ensure_ascii=False))
