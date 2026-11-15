# 上游来源

- 生成模型：腾讯 `tencent/HunyuanOCR`，本实验固定提交 `47644ecc4fc854efa4f505155158831f36773ee4`，模型标为 HunyuanOCR-1.5。
- 模型与官方任务提示来源：[Tencent-Hunyuan/HunyuanOCR](https://github.com/Tencent-Hunyuan/HunyuanOCR)。`doc_parse`、`structured_parse` 与 `table` 提示按官方 `inference/utils/tasks.py` 的固定定义使用，文件 blob 为 `9e385c1f49082ffa2be67323c9a7a6b4e4c38b91`。
- 模型许可：[模型仓库 LICENSE](https://huggingface.co/tencent/HunyuanOCR/blob/47644ecc4fc854efa4f505155158831f36773ee4/LICENSE)。权重保存在服务器外置目录，不包含在本项目源码交付包中。
- 推理服务使用 vLLM 0.18.1；安装方案依据[官方 CUDA 12 / AR 部署指南](https://github.com/Tencent-Hunyuan/HunyuanOCR/blob/main/docs/inference/archive/vLLM.md)。本项目不照抄其中按进程名批量停止服务的命令，停止时只操作本项目的服务单元。
- 应用使用 FastAPI、Pydantic、Uvicorn、HTTPX、Pillow、PyMuPDF 与 SQLite。依赖各自适用其上游许可；准确安装版本见运行时的 freeze 文件。
- `samples/` 为本项目原创合成教学样本；参考文本和字段用于可控评测，不代表真实业务文档的质量分布。

模型、推理框架和样本分别标记来源；不将上游模型或框架宣称为本项目原创。

原创应用源码许可证尚未选择；本次未默认添加 MIT 或其他源码许可证。模型、框架、PyMuPDF 等依赖适用其上游许可，使用或再发布时应分别核对。
