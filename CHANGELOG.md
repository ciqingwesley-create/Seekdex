# 发布说明

版本由 `src/seekdex/app_info.py` 统一提供，构建时自动生成版本化发布说明。

## 2026-10-09 — Source license change

- 项目自有源代码从 MIT 改为 GNU GPL v3.0，SPDX：**GPL-3.0-only**（仅第 3 版）。
- 旧 MIT 版本的既有授权不受追溯影响；历史正文保留于 `docs/licenses/LEGACY-MIT.txt`。此记录不是给当前新版本增加 MIT 双许可。
- README、包元数据、About、安装器和新 RC 包统一许可证声明，保留作者版权与第三方通知；不重新授权模型权重。
- 对应应用源码快照包含所有受版本控制的构建脚本 / 工作流 / 文档，并记录生成包的 Git commit；Windows 标准包不带模型权重。

## 0.5.0-rc1 — Seekdex · 索星仪

- 首个正式采用 Seekdex 品牌的 Unsigned Release Candidate；按发布计划使用 RC 版本，不是此前开发构建 0.5.1 的稳定升级承诺。
- Python 包改为 `seekdex`，产品 EXE 改为 `Seekdex.exe`。
- 保留 LocalImageSearch 安装器 GUID；迁移旧数据、INI / QSettings、模型 / 编译 / 调优缓存，保留文件 UID、AI embedding 和 OCR；原目录保留。
- 新旧独立数据库 / 不同模型发生冲突时停止提示，不自动覆盖。
- 清理当前文档中的私人路径与 UID，截图重新使用合成图片生成。
- 增加 Windows CI、协作 / 安全文件、本地全历史审计和 RC 发布说明。
- 创建不包含旧提交的独立公开候选历史；原私有仓库与备份保留。模型权重再分发授权、代码签名及公开发布仍需人工处理。

## Features

- 标准 / 预装两种安装包，以及标准 / 预装两种 Portable ZIP。
- 预装版只附带固定版本识图和 OCR 模型；后台校验、导入、离线使用，不修改现有向量空间。
- Windows 安装包与 Portable；无需另外安装 Python。
- 规范的用户数据、配置、模型、日志目录；保留旧索引的安全迁移。
- 首次启动向导、设置中心、多目录索引管理、网格 / 列表、详情侧栏。
- 保留属性搜索、Chinese-CLIP、OpenVINO、OCR、整理预览与移动撤销。

## Fixes

- 无控制台 EXE / pythonw 下载进度条不再访问空 stdout / stderr。
- 模型下载使用普通 HTTP 流式路径，避免原生 Xet 下载引起的长时间 GUI 信号暂停。
- 构建脚本按 UTF-8 读取中文 GUI 验证报告，兼容 Windows PowerShell 5。
- Hub 元数据下载失败时回退同一官方快照的直接下载，保留 HTTPS 和 SHA256 检查；连接失败提示可读原因。
- AI / OCR 异常日志保留完整异常链；旧进程混用更新后模块时提示重启。
- OCR 索引时可使用普通搜索和图片内容搜索，取消独立。
- 拍摄日期筛选补读元数据时释放 SQLite 查询快照，避免并发写入锁错误。

## Known issues / 发布检查

- 产物未做代码签名；先前版本曾被 Windows Smart App Control 拦截（Code Integrity 3077）。其他启用此策略的机器仍可能拦截，需要可信代码签名。各版直接运行与模型安装验证见本次验证报告。
- CPU wheel 不提供 CUDA 执行；无 CUDA 时自动 CPU 回退，OpenVINO GPU 是否可用取决于 Intel 驱动。
- 模型语义与 OCR 准确率受图片内容和 RAW 内嵌预览质量限制。
- 干净 Windows / 无 Python 机器验证结果见最终验证报告，不把开发机隔离测试称为干净机验证。

## 发布说明模板

更新 Features / Fixes / Known issues；附上构建生成的 SHA256SUMS.txt。
本构建不会自动推送或创建 GitHub Release。
