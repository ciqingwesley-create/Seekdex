# 第三方组件与模型许可证

Seekdex 自有应用代码为 **GPL-3.0-only**。此许可不替代第三方代码、原生库或模型权重许可；它们的原始通知保持不变。旧 MIT 版本的既有许可不受追溯影响。
构建脚本会收集已安装依赖中的 LICENSE / COPYING / NOTICE 和元数据到安装包 `seekdex/resources/licenses/`。
应用源码快照也随程序提供，见 `application-source.zip`。标准版不附带模型；本地预装版按用户要求包含下列固定快照的识图 / OCR 权重和必要配置，文件来源及 SHA256 记录在 `preinstalled-models/manifest.json`。预装版未自动对外发布。

| 组件 | 代码许可证 | 权重 / 原生库说明 | 上游 |
| --- | --- | --- | --- |
| PySide6 / Shiboken6 / 使用的 Qt Core、Gui、Widgets、Network、SVG | LGPL-3.0 / GPL-3.0 / 商业许可选项；保持上游 LGPL / GPL 多许可声明，本构建继续按 LGPL 动态库路径提供通知与可替换方式 | 保留 LGPLv3、GPLv3 文本、Qt 版权和第三方说明。用户可替换独立 Qt DLL；不禁止为修改库而调试。未使用 GPL-only Qt Charts 等模块。 | https://doc.qt.io/qtforpython-6/licenses.html |
| Pillow | MIT-CMU（当前 wheel 的 License-Expression；PIL 许可） | JPEG、PNG、TIFF 等编解码器各有许可，保留 wheel 中说明 | https://github.com/python-pillow/Pillow/blob/main/LICENSE |
| NumPy | BSD-3-Clause | BLAS / LAPACK 等见其完整第三方说明 | https://github.com/numpy/numpy/blob/main/LICENSE.txt |
| ExifRead | BSD-3-Clause | 仅提取 EXIF，不改图片 | https://github.com/ianare/exif-py/blob/master/LICENSE.txt |
| rawpy | MIT | 随 wheel 的 LibRaw 是 LGPL-2.1 / CDDL 双许可组件，不能把 LibRaw 称为 MIT | https://github.com/letmaik/rawpy/blob/main/LICENSE |
| PyTorch | BSD-3-Clause | 保留其原生依赖许可；当前 Windows 构建使用 CPU wheel | https://github.com/pytorch/pytorch/blob/main/LICENSE |
| OpenVINO | Apache-2.0 | CPU / GPU 插件及第三方依赖保留独立说明 | https://github.com/openvinotoolkit/openvino/blob/master/LICENSE |
| Transformers / Hugging Face Hub / Safetensors | Apache-2.0 | 可选模型与库不是同一种许可 | https://github.com/huggingface/transformers/blob/main/LICENSE |
| Chinese-CLIP | MIT（官方代码项目） | 沿用官方 ViT-B/16 固定快照。官方模型页引用代码项目，未见独立权重许可声明；不能仅凭代码 MIT 推断全部权重使用权。预装包用于本地交付，对外再分发前应核实上游权重授权。 | https://huggingface.co/OFA-Sys/chinese-clip-vit-base-patch16 |
| RapidOCR | Apache-2.0 | OCR 代码与模型来源分开记录 | https://github.com/RapidAI/RapidOCR/blob/main/LICENSE |
| PP-OCRv6 small / PP-OCRv4 方向模型 | PaddleOCR 上游 Apache-2.0 | ONNX 文件来自 RapidAI 固定发布镜像；每个文件校验 SHA256，见 `ocr/model_cache.py` | https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE |
| ONNX Runtime | MIT | 其第三方组件见完整 THIRD_PARTY_NOTICES | https://github.com/microsoft/onnxruntime/blob/main/LICENSE |
| OpenCV | Apache-2.0（当前 4.x / 5.x），包含其他第三方许可 | 不包含其 Qt GUI；使用本程序的 PySide6 | https://github.com/opencv/opencv/blob/4.x/LICENSE |
| Python | PSF-2.0 及随发行版许可 | SQLite 为 public domain；SSL 等另有许可 | https://docs.python.org/3/license.html |
| PyInstaller bootloader | GPL-2.0-or-later，带允许分发打包程序的特殊例外 | 构建工具许可不把应用自动变为 GPL | https://pyinstaller.org/en/stable/license.html |
| Inno Setup 6.7.3 | Inno Setup License（自有宽松许可，非 MIT） | 本次使用未修改官方安装工具；保留版权和站点说明；商业用途要求另见官方说明 | https://github.com/jrsoftware/issrc/blob/main/license.txt |

## 本次核实与未解决项

核实日期 2026-10-08。实际 wheel 许可 / 版本由 `scripts/build_resources.py` 导出，完整文本随包保留。官方 [RapidOCR v3.9.2 README](https://github.com/RapidAI/RapidOCR/blob/v3.9.2/README.md) 与 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE) 分别用于代码和模型来源核实；ONNX 转换镜像不改变必须保留的上游版权通知。

Chinese-CLIP 使用固定 `f4a64596bbcf9a2a94591b74b9dc39b2e4e77e3e` 快照，SHA256 与语义空间不变。代码 MIT 已核实；固定快照模型卡没有独立、明确的权重许可证字段，因此权重再分发仍为待确认项。不能把代码 MIT 等同于全部权重 MIT。未确认前只准备本地预装包，不公开上传预装权重。

项目内测试图片在运行时由 Pillow 生成。公开截图由 `scripts/generate_public_screenshots.py` 生成合成媒体与虚构路径；没有分发私人 RAW / JPEG 或来源不明照片。公开仓库采用独立清理后的历史，旧私人历史只保存在私有归档与本地备份中。

## Qt / LibRaw 可修改与重链接

本程序使用 onedir 动态分发，不静态链接 Qt / LibRaw，不限制更换 LGPL 动态库或为此调试。
在兼容 ABI / 架构下可替换 `_internal/PySide6/` 的 Qt DLL；rawpy 原生库可能需按其官方 wheel 构建流程重新构建扩展。
对应未修改的上游源码可从 Qt 官方 `https://download.qt.io/official_releases/QtForPython/`、
Qt `https://download.qt.io/archive/qt/`、rawpy `https://github.com/letmaik/rawpy` 与 LibRaw `https://www.libraw.org/download` 获取。
实际依赖版本和许可文件由构建脚本记录在 `licenses/dependencies.json`，应用源码在同目录上一级的 `application-source.zip`。

使用或再分发 Qt 与模型前应阅读附带的完整许可。安装包保留通知，不以本应用 GPLv3 替代它们。兼容性核对和未确认项见 `docs/licensing-audit.md`。
