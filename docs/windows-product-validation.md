# Windows 产品化验证

日期：2026-10-07。版本从 `src/seekdex/app_info.py` 读取。

## 环境与边界

- Windows 11 Pro 10.0.26200；Intel Core i7-8700，6 核 / 12 线程。
- Python 3.12.14、PySide6 6.11.2、PyTorch 2.8.0+cpu、OpenVINO 2026.4.1、ONNX Runtime 1.30.0、RapidOCR 3.9.2。
- 验证使用隔离配置和临时 JPEG / 中文英文截图 / 普通文件，没有全盘扫描或重建用户 AI / OCR 索引。
- 实际窗口在 Qt `windows` 平台显示，通过 Qt 事件循环驱动真实控件和后台任务，不是 offscreen 验证，也不是人工逐项鼠标操作。

## 测试

最终 `pytest`：**302 passed，14.91 s，进程退出码 0**。保留原有 268 项，新增 34 项覆盖配置、路径、迁移、只读缓存、首启、目录覆盖 / 移除、结果上限、视图 / 排序、设置恢复、模型清理与隐私日志等。

一次并行构建中的旧安全测试遇到 WinError 5 临时文件占用；独立复测和后续全部测试通过。另发现只读 OpenVINO 缓存导致迁移临时文件删除失败，以及启动日志提前创建配置可能阻止旧配置导入；均已修复并增加回归测试。迁移时导入缺失配置键，保留已有目标配置。

## 数据迁移

通过 SQLite backup 从旧数据目录迁移，目标已有数据库时不覆盖；复制模型 / 缓存，仅填补缺失项；原目录保留。实际迁移后：

| 表 | 迁移前 | 迁移后 |
| --- | ---: | ---: |
| files | 38,936 | 38,936 |
| image_embeddings | 5,760 | 5,760 |
| ocr_results | 1,272 | 1,272 |
| file_operations | 0 | 0 |

抽查 100 条旧 embedding，UID、模型 ID 与向量字节完全一致；`integrity_check=ok`。模型不重复下载，不改变 Chinese-CLIP / OCR 模型与预处理 ID。SQLite 对当前 38,936 条记录的一次文件名＋扩展名筛选，计数 5,108，热缓存查询约 4.1 ms；不包含 UI 传输、首次模型加载或磁盘扫描，不能当作完整搜索端到端基准。

## 源码 GUI

最终完整工作流验证成功（约 21 s，事件循环心跳最大间隔约 0.42 s）：七步向导、重叠多目录去重、普通搜索、缩略图 / EXIF、设备与分辨率 / 日期筛选、列表排序、网格卡片、详情侧栏、请求默认程序打开、索引管理、离线 AI / OCR / 相似图 / 组合搜索、整理预览、移动撤销、设置保存、重开恢复、数据库完整性。

AI 实际为 OpenVINO CPU；OCR 实际为 OpenVINO CPU。模型、向量和 OCR 均在本地运行。只为临时三张图片创建测试向量；不改变用户已有向量。

另用源码 GUI 的全新隔离缓存实际下载 Chinese-CLIP 权重与 OCR 模型，校验成功，并现场生成 OpenVINO IR；随后完整搜索、整理与恢复流程通过。该次 AI 为 OpenVINO CPU，OCR 为 ONNXRuntime CPU。这是源码验证，不代表最终 EXE 的模型安装验证已经完成。

![主界面](screenshots/windows-main.png)

![索引管理](screenshots/windows-index-manager.png)

## 打包 GUI 与最终产物限制

中间 PyInstaller onedir EXE 已直接启动并完成上述完整工作流，约 20.69 s，心跳最大间隔约 0.344 s。运行时清空 PYTHONPATH / PYTHONHOME / VIRTUAL_ENV，PATH 仅保留 Windows，AI、OpenVINO CPU、OCR 正常。此验证仍在开发机上，机器上存在 Python 和源码，**不能称为干净机 / 无 Python 机器验证**。

打包发现 Qt 的 ICU DLL 被开发工具 Poppler 的同名 DLL污染，导致 QtWidgets 导入失败；已修复构建 PATH 并排除错误 ICU。最终构建排除未使用的 Qt Virtual Keyboard / QML / PDF 插件，仅保留实际所需运行库与常用图片格式插件。

**最终 EXE 启动被 Windows Smart App Control 拦截**，不是应用测试通过。Code Integrity 事件 3077 / 3033 明确指出未满足签名等级要求，事件 3118 为 Smart App Control Block。没有改变安全策略，没有导入自签名根证书或绕过拦截。

安装程序也已实际尝试启动，但被相同系统策略拦截，未能安装。因此最终包从空缓存下载模型、现场 OpenVINO 转换、最终 EXE 人工双击、安装后完整使用、升级及卸载保留数据等仍待可信签名后复验。中间版本成功的结果不能替代最终版本验收。最终 ZIP / Setup 的生成与 SHA256 是产物证据，不是最终运行验证通过的证据。

当前没有可用的 Windows Sandbox / VM / 全新用户验证环境，**未完成干净机验证**。当前 CPU PyTorch 构建不提供 CUDA；模型语义和 OCR 准确率、RAW 预览质量等原有限制保持不变。

## 交付与后续验收

产物在 `release/`，校验值在 `SHA256SUMS.txt`。统一脚本默认运行测试并执行 EXE 验证，失败停止；本机生成最终包时显式使用 `-SkipRuntimeVerification`，产物标为未验证。未自动推送或发布 GitHub Release。

下一步是使用可信代码签名证书签名并复验最终 EXE，在干净 Windows 机器测试模型安装、离线使用、安装升级和卸载保留数据。本阶段未加入其他 AI 模型、人脸识别、视频搜索或 Ubuntu 适配。
