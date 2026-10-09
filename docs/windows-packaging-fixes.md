# Windows 打包修复与版本划分

验证日期：2026-10-08。产品版本由 app_info.py 统一生成。

## 变更

- 在 PyInstaller / pythonw 第三方导入前补齐缺失 stdout / stderr，禁用控制台下载进度条。
- 普通 HTTP 流式模型下载；禁用原生 Xet 下载。元数据请求失败时使用同一官方固定快照直接 GET，保留 HTTPS 与 SHA256。
- 标准版不含模型；预装版仅附带 Chinese-CLIP 固定快照的 4 个文件、默认 OCR 的 3 个模型，合计 784,968,419 字节，加清单和许可证说明。不附带 OpenVINO IR、编译缓存、调优结果、用户图片、数据库或识别结果。
- 预装模型首次启动在后台校验并原子导入用户缓存；有效缓存复用；取消与损坏文件不覆盖原有模型。AI model_id / preprocessing / 512 维语义空间保持不变。
- Windows PowerShell 5 以 UTF-8 读取中文验证报告。

## 测试与实际运行

自动测试：**315 passed，16.70 秒，退出码 0**，保留原 304 项，新增 11 项覆盖输出流、模型下载回退、网络错误、取消、校验、预装导入、重复启动、危险路径与错误模型空间。真实 pythonw 从没有 stdout / stderr 的环境下载官方配置成功。
打包 EXE 在隔离目录和原生 Windows Qt 平台执行验证，清理 PATH 中的 Python 环境引用；这仍然是开发机，不是无 Python 干净机验证。

### 最终标准版 EXE

直接运行最终 dist EXE，使用空的隔离数据 / 模型缓存，实际下载 Chinese-CLIP 与 OCR、创建 OpenVINO IR，并完整运行向导、普通搜索、缩略图、拍摄时间、设备 / 分辨率过滤、AI、相似图、OCR、组合搜索、整理预览、move / undo、配置恢复和数据库完整性检查。共 21 个工作流检查通过，约 270.17 秒，最大 GUI 心跳间隔约 **0.609 秒**。

第一轮已修复输出流的构建曾出现约 125.515 秒的心跳暂停；禁用原生 Xet、使用 HTTP 流式下载后，在同样的首次下载工作流中未再出现长暂停。这是本机功能验证，不能当成所有网络条件下的性能保证。

### 最终预装 ZIP

完整解压最终 ZIP，校验其中 EXE 与最终 dist 的 SHA256 相同。使用空缓存、`HF_HUB_OFFLINE=1`，后台自动导入随包模型；不提供旧 OpenVINO IR 或本机调优文件。随后现场转换并通过上述完整流程；验证器禁止网络连接。约 **45.84 秒**，最大 GUI 心跳间隔约 **0.25 秒**。该次隔离配置使用 OpenVINO CPU、手动 batch 4；标准版验证保留自动设置。

AI / OCR 均使用 OpenVINO CPU。两个版本对同样三张测试图的 AI cosine 分数完全一致；保持原模型 ID、预处理和 512 维向量。真实原数据库仍有 38,936 条文件、5,760 条 embedding、1,272 条 OCR；抽查 100 条向量身份和字节指纹与修复前完全一致，没有重建或删除用户索引。

### 产物完整性

最终统一脚本退出码 0，两个 Inno Setup 安装包均编译成功。两个 ZIP 完整 CRC 检查通过；标准 ZIP 含 0 个模型权重，预装 ZIP 含 1 个 safetensors 和 3 个 ONNX 模型，没有用户数据库、临时下载文件、OpenVINO IR 或调优缓存。四个最终文件的大小、SHA256 和版本划分见 release/release-manifest.json 与 SHA256SUMS.txt。

本机直接 EXE 验证通过，但没有在无 Python 的全新 Windows 环境验证。已有正式旧版安装，为避免覆盖其卸载注册表，没有实际安装 / 升级 / 卸载这两个新安装包；不把编译成功称为安装验收通过。

## 发布边界

四种产物为安装包 Standard / Preinstalled 与 Portable ZIP Standard / Preinstalled。
仍未代码签名，其他启用 Smart App Control 的机器可能拦截。未改动本机安全策略。
本机已有正式旧版安装，因此不使用相同 AppId 的隔离安装覆盖其卸载注册表；安装升级验收尚待独立环境。
预装包只做本地交付，没有自动上传模型或发布 GitHub Release；模型与代码许可证分别见 THIRD_PARTY_LICENSES.md。
