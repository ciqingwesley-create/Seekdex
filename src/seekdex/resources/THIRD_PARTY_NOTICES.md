# 第三方 AI 与 OCR 组件

本项目通过独立后端调用第三方模型，没有把模型权重放入仓库。

## Chinese-CLIP

- 上游：<https://github.com/OFA-Sys/Chinese-CLIP>
- 模型：<https://huggingface.co/OFA-Sys/chinese-clip-vit-base-patch16>
- 权重快照：`f4a64596bbcf9a2a94591b74b9dc39b2e4e77e3e`
- 权重 SHA256：`29cc0b2bcf6ff777f2e15742be92b110e4acbdb2068356e862c4637a4b15fe4f`
- 上游代码许可证：[MIT-LICENSE.txt](https://github.com/OFA-Sys/Chinese-CLIP/blob/master/MIT-LICENSE.txt)；固定模型快照的独立权重再分发授权待核实，不能由代码 MIT 自动推断。
- Copyright (c) 2022-2023 OFA-Sys Team，以及上游 MIT 文件列出的 OpenCLIP 贡献者。

使用和分发上游模型时请保留其版权与许可证文件。本项目只在用户同意后从官方仓库下载 safetensors 权重，不加载 pickle 权重或远程自定义代码。

## 运行依赖

PyTorch（BSD-3-Clause）、Transformers（Apache-2.0）、Hugging Face Hub（Apache-2.0）、Safetensors（Apache-2.0）、NumPy（BSD-3-Clause）和 OpenVINO（Apache-2.0）各自保留原许可证；随依赖安装的许可文件是完整条款的来源。AI 与 OpenVINO 依赖为可选安装。OpenVINO 模型转换使用 telemetry stub，不发送转换遥测；运行与转换均使用已安装的本地权重。

## RapidOCR / PP-OCRv6

- [RapidOCR](https://github.com/RapidAI/RapidOCR) 3.9.2：[Apache-2.0 许可证](https://github.com/RapidAI/RapidOCR/blob/main/LICENSE)。
- PP-OCRv6 small / medium 来自 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)：[Apache-2.0 许可证](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)。ONNX 文件通过 RapidAI 固定 v3.9.2 模型镜像下载，模型缓存代码记录每个文件 SHA256。文字方向分类使用该镜像的 PP-OCRv4 模型。
- [ONNX Runtime](https://github.com/microsoft/onnxruntime/blob/main/LICENSE)：MIT。
- OCR 可选依赖还包含 OpenCV、Shapely、pyclipper、OmegaConf 等，上游随安装包提供其完整许可证。分发模型 / 依赖时请保留相应许可证与版权声明，不能仅以本项目许可证代替。
- 模型不纳入 Git；OCR 推理不下载字典或字体，不上传图片 / 文本。仅显式模型安装访问 RapidAI 的模型镜像。
