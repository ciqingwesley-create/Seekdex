# Seekdex · 索星仪：公开前审计

品牌与迁移审计：2026-10-08；独立公开历史准备：2026-10-09。不增加搜索功能。

## 基线与品牌分类

当前候选源码为 Seekdex 0.5.0-rc1。基于已清理的最新源码新建独立 Git 仓库，首个提交为 `Initial public release of Seekdex`，没有父提交、旧 refs 或旧 Git 对象。原私有仓库与完整本地备份保留，没有改远程 visibility / 权限、push、tag 或 release。本报告只适用于这份独立候选仓库，不能据此直接公开原私有历史。

初始旧品牌搜索约 295 处、42 个文件，按类型处理：

| 类型 | 处理 |
| --- | --- |
| UI / About / 首启 / 设置 / 日志 | Seekdex 或 Seekdex · 索星仪 |
| package / imports / entry points / PyInstaller | `src/seekdex`，`python -m seekdex`，GUI entry point `seekdex` |
| EXE / Setup / Portable / metadata | Seekdex；display 0.5.0-rc1；PE numeric 0.5.0.1；PEP 440 0.5.0rc1 |
| Installer GUID | 保留 `{B2853172-9740-4A21-B7A7-BF42BC3370F8}` |
| 数据 / 日志 / 缩略图 / 模型 | `%LOCALAPPDATA%\Seekdex`；旧目录名仅迁移兼容 |
| QSettings / config key | INI 键不改；旧 namespace 只用于一次性导入 |
| DB / cache / model identity | 不改 file_uid、固定模型快照、512 维空间、OCR identity 和缩略图 key |
| 文档 / tests / CI / build | 新品牌；旧名只在迁移 / 升级兼容用例、CHANGELOG 保留 |
| GitHub URLs | 暂时保留有效私有仓库链接，注释人工 slug 更名后更新，不擅自改 remote |

没有持久化 Python pickle 对旧应用 module path 的依赖。权重继续为 safetensors / ONNX，OpenVINO IR / model metadata 身份不变。

## Migration

旧正式 `%LOCALAPPDATA%\LocalImageSearch`、早期 `%LOCALAPPDATA%\local-image-search` → `%LOCALAPPDATA%\Seekdex`。显式 SEEKDEX_HOME 隔离测试不会导入真实资料。

- SQLite backup、integrity_check、原子不覆盖发布；UID / AI BLOB / OCR / 整理记录保留。
- 模型、OpenVINO IR / compiled cache / tuning、缩略图复制缺失项；原目录保留，不重新下载或计算。
- INI / 历史 QSettings 缺失键迁移，新配置优先；旧内部缓存路径更新，外部自定义路径保留。
- 旧日志归档 `logs/legacy`。持久 receipt 支持数据库已备份但缓存未完成时重试；完成 flag 防止重新恢复主动清理的缓存。
- 两份独立数据库 / 不同模型内容或旧程序仍运行时停止提示，不擅自合并覆盖。
- 修复旧缓存迁移 POSIX rename fallback 可能覆盖竞态目标的问题；使用 exclusive link，失败保留源；阻止目标链接逃逸。

## Git audit

本地完整备份包含原 `.git` 的全部对象 / refs / reflogs / config / index、全部 refs 的 Git bundle 和版本控制中的源码快照。逐文件 SHA256、bundle verify、独立恢复与 `git fsck --full` 均已校验。私有备份不随公开源码分发；环境、模型、用户数据库和发行包继续留在原位置，不加入 Git。

新候选仓库由源码快照重新 `git init`，只有一个 main 初始提交，没有导入旧 objects / refs / reflogs / remote；不通过 orphan 分支或隐藏旧 refs 来假装清理。

复查范围包括全新历史及全部本地 Git objects：

- 官方校验来源的 gitleaks 8.30.1 本地 git 和目录扫描；不上传到第三方扫描网站。
- 逐一比对旧历史中的 24 个真实 file_uid 和 24 条私人照片路径；另查真实用户名、本机项目目录、照片目录与 JSON 中的 UID 实值。
- `file_uid` / `expected_file_uids` 作为程序字段、空 CSV 表头和空测试清单继续保留；不等于分发真实个人记录。
- 检查 >10 / >50 / >100 MiB blobs、被跟踪的数据库 / 模型 / 私人照片 / 凭据文件。
- 当前 PNG 均人工查看，是合成图形与虚构路径；没有复制旧截图，PNG 不含私人文字 metadata。
- 用户明确提供的项目作者姓名、邮箱和 GitHub 项目地址按原开源身份保留。

复查未检出私人路径、已知真实 UID 或 secret，三个大文件阈值均为 0。扫描结果不是绝对安全保证；本地完整机器报告在私有验证目录保留，不提交其中的实际文件系统路径。

**这份独立候选源码通过本轮历史隐私检查。原 GitHub 私有仓库仍有旧历史，不能直接改 Public。** 推荐人工新建空仓库后上传候选 main；若一定保留原远程仓库身份，需要另行确认历史替换与残留对象清理。本轮没有 push 或改 remote。

## Open-source files / licenses

MIT LICENSE 完整保留。README、CONTRIBUTING、SECURITY、CHANGELOG、issue / PR templates、Windows CI 已准备。CI 用合成图片 / fake backend，不下载模型，不声明 Linux 官方支持。

THIRD_PARTY_LICENSES 分别记录 PySide6 / Qt、Pillow、NumPy、ExifRead、rawpy / LibRaw、PyTorch、OpenVINO、Transformers / Hub / Safetensors、Chinese-CLIP code / weights、RapidOCR / OCR weights、ONNX Runtime、PyInstaller、Inno Setup，包内收集实际 wheel 完整通知。

Chinese-CLIP 代码 MIT 已核实，但固定快照独立权重再分发许可仍不明确；标准包不含模型，预装包只在本地准备，未上传。公开预装权重前需要核实。不要把产品 MIT 自动应用到模型。

## Tests / RC verification

**327 passed / 0 failed / 0 skipped**，18.17s；原 315 项完整保留，新增 12 项迁移 / 身份 / 冲突 / 中断 / package / installer identity 测试。`pip check` 通过。editable package 为 seekdex，旧开发入口已卸载。

最终 Portable / Setup 从包含本报告的源码提交构建。实际 EXE、隔离迁移、安装升级结果另存本地 `release/SEEKDEX-RC-VERIFICATION.md`，避免再次把实际验证环境路径写入公开文档。当前 RC unsigned；不会以未通过的验证作为成功，不改变 Windows 安全策略。

## Manual actions

1. 私有备份另存妥当；公开时使用此独立候选仓库，不上传旧 `.git` / bundle / 私人验证目录。
2. 当前无已确认需 rotate 的 secret；如后续发现先 revoke / rotate。
3. 保持 Private 时人工 repo rename `seekdex`，更新 URLs / origin。
4. 人工确认发布目标与隐私检查后公开独立候选源码；预装模型分发还需独立许可确认。
5. 人工创建 `v0.5.0-rc1` tag。
6. 人工 GitHub pre-release，优先标准包；预装权重授权待确认。
7. 申请可信代码签名，补充干净机与不同升级权限模式验证。
