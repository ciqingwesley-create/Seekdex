# Windows v0.5.0 正式发布前质量验证 — 2026-10-10

## 结论：当前 NO-GO

当前不建议发布 v0.5.0。功能样本运行通过，但已公开 RC 的二进制/内嵌源码采用 MIT，与 GPL-3.0-only 标签和发布说明错配；干净 Windows 和安装生命周期验证尚未完成。

本轮没有修改许可证、添加用户功能、开启 Ubuntu 适配、关闭 Windows 安全保护、发布版本、替换公开附件、修改标签或公开仓库历史。未发现需要改动当前 main 核心逻辑的功能错误。发布材料错误只能在后续经确认的新候选发行中修复；当前 main 的本地 GPL 构建已正确，但不能据此宣称公开下载包已修复。

## 环境与证据范围

- Windows 11 Pro 10.0.26200 x64，Intel Core i7-8700（6核12线程），约23.8 GiB可用物理内存总量。
- Sandbox、Hyper-V、VirtualMachinePlatform未启用；HypervisorPresent=false，无可用干净虚拟机。没有启用组件或重启/修改BIOS。
- 实际运行下载的 `Seekdex.exe`，工作目录是其解压目录；清除开发Python环境及PATH，用隔离应用数据和缓存。外部测试驱动用Python做进程计时/文件校验，**应用运行本身不是python -m**。本机仍安装Python与开发工具，不能称为无Python干净系统。
- GUI驱动操作真实Qt窗口、后台线程、真实SQLite、真实Chinese-CLIP/OCR模型；只使用合成照片/截图/旧用户配置。目录选择注入测试路径，不代表原生目录选择器已人工验证。
- 已公开包完整运行21项、本地GPL候选完整离线运行22项、实际迁移8项检查通过。向量均512维、float32/2048字节，L2范数接近1。
- 完整源码pytest：**327 passed，0 failed，0 skipped，26.45秒**；AI/OCR单元测试使用fake/mock，不将这些结果当作真实模型GUI验证。
- 本轮安装/升级/卸载受自动审批保护：已有相同AppId的管理员安装；用户明确选择BLOCKED、待干净VM测试。没有执行本轮Setup。
- 两个下载文件都未带浏览器Zone.Identifier；因此未验证浏览器下载后的SmartScreen/MOTW路径。检测到两者NotSigned，不改变系统保护或绕过拦截。

## 发布一致性

- GitHub main与 `v0.5.0-rc1` 解引用目标：`5fe20e25b93bf52f4c1df6b41cfa42ce41442d43`，GPL-3.0-only。
- [公开RC](https://github.com/ciqingwesley-create/Seekdex/releases/tag/v0.5.0-rc1)：2026-10-09 22:07:53（UTC+8）已发布，prerelease=true，非稳定正式版。
- 公开ZIP内GPL声明缺失，实际为1070字节MIT正文；内嵌 `app_info.LICENSE=MIT`，旧主页slug `local-image-search`；内嵌源码116文件，缺文档/工作流及源码commit凭据。
- Setup版本同为0.5.0-rc1，其完整下载hash与既有MIT构建一致；没有安装/解包查看其许可页，不能把检测hash当成新的安装许可页验证。
- 本地GPL候选的官方GPL正文、152文件源码快照和commit记录已经通过校验，但它们不是当前公开RC附件。

### 真实下载SHA256

| 文件 | SHA256 | 与清单/GitHub一致 |
| --- | --- | --- |
| Seekdex-0.5.0-rc1-Windows-x64-Portable.zip | `1cc5a37fc9ae81f4f23f1a291dde0909c3bf197f27fe6782bccaf0a9182128cc` | PASS |
| Seekdex-0.5.0-rc1-Windows-x64-Setup.exe | `4ab07b43cf818a4d2e1ffb80da9ac36b7cd353a9420e9f1f6d8c0c0a9eeffdd2` | PASS |
| SHA256SUMS.txt | `df2d854a3408dc2e01a3eb5cb57b90826dc693d6f31a23dd3512fda0a69f1cb9` | PASS |

清单还包含两个Release中不存在的预装版文件。下次发行应只生成本次实际发布版本的清单；本轮未替换已发布清单或附件。

## 测试矩阵

状态含义：PASS=本轮指定范围内实际检查通过；FAIL=已复现问题；BLOCKED=环境/保护/用户选择阻止执行；NOT TESTED=本轮没有该范围的实际验证。`自动测试`列单独标记源码测试证据，不把它转换为冻结包运行PASS。

| 类别 | 项目 | 本轮实际状态 | 源码自动测试 | 证据/限制 |
| --- | --- | --- | --- | --- |
| 发布 | GitHub main GPL-3.0-only | PASS | — | 远程 main=5fe20e25b93bf52f4c1df6b41cfa42ce41442d43；API读取LICENSE及pyproject。 |
| 发布 | RC 已发布、保持 prerelease | PASS | — | Release 407974509：draft=false, prerelease=true；2026-10-09 22:07:53 UTC+8发布。 |
| 发布 | RC 标签指向 GPL 提交 | PASS | — | v0.5.0-rc1 的解引用目标为 5fe20e2。 |
| 发布 | 公开 EXE/Setup 版本一致 | PASS | — | 两者 FileVersion/ProductVersion 均为 0.5.0-rc1。 |
| 发布 | 公开二进制与 GPL 标签/说明一致 | FAIL | — | 下载 ZIP 的完整许可证为 MIT（1070 字节）；内嵌 app_info.LICENSE=MIT。Setup校验值也与既有MIT构建一致，未实际安装确认其许可页。 |
| 发布 | 公开包对应源码可追溯到标签 | FAIL | — | 内嵌源码仅116文件，LICENSE=MIT；没有source-provenance，缺docs/workflows；GPL提交的本地包为152文件。 |
| 发布 | 公开包主页为当前 Seekdex 地址 | FAIL | — | 内嵌 HOMEPAGE 仍为旧 local-image-search slug，不是当前 Seekdex 项目地址；未据此推断HTTP访问结果。 |
| 发布 | 下载完整性和 SHA256SUMS | PASS | — | 真实下载两个完整文件后计算SHA256，与GitHub digest及已下载的SHA256SUMS条目一致。 |
| 发布 | 校验清单只列实际发布文件 | FAIL | — | SHA256SUMS还列Portable-Preinstalled/Setup-Preinstalled，但Release只有标准版两个包及清单。 |
| 发布 | 签名状态识别 | PASS | — | 下载的 EXE、Setup 均为 NotSigned；没有修改安全策略。 |
| 环境 | Windows Sandbox / 干净虚拟机 | BLOCKED | — | Sandbox/Hyper-V/VirtualMachinePlatform为Disabled，HypervisorPresent=false，无Sandbox.exe或虚拟机管理工具；未启用或重启系统。 |
| 环境 | 无外部 Python 的干净 Windows Portable | BLOCKED | — | 宿主 EXE 实测清除PYTHONPATH/PYTHONHOME/VIRTUAL_ENV及开发PATH，不能替代无Python的新系统。 |
| 环境 | 干净 Windows Setup | BLOCKED | — | 缺少干净环境。 |
| 环境 | 真实浏览器下载的 SmartScreen / SAC 流程 | NOT TESTED | — | 命令行下载的两个文件没有Zone.Identifier；未检查带浏览器MOTW的用户流程，不等同于安全保护通过。 |
| 功能 | 首次启动向导 | PASS | PASS | 直接运行下载的冻结EXE，实际Qt七步向导和合成目录索引流程完成。 |
| 功能 | 选择测试目录并建立基础索引 | PASS | PASS | 注入合成目录路径，经实际向导任务建立5条文件索引；包含重叠目录去重。 |
| 功能 | 原生系统文件夹选择对话框 | NOT TESTED | — | 运行驱动以合成路径替代原生选择器；仍需人工交互测试。 |
| 功能 | 文件名筛选（打包GUI） | NOT TESTED | PASS | 完整327测试中的搜索层用例通过，本次冻结GUI驱动未单独执行文件名查询断言。 |
| 功能 | 扩展名筛选（打包GUI） | NOT TESTED | PASS | 搜索/索引自动测试通过；本次冻结GUI驱动未单独执行扩展名查询。 |
| 功能 | 修改时间筛选（打包GUI） | NOT TESTED | PASS | 修改时间边界自动测试通过；本次GUI驱动验证的是拍摄时间。 |
| 功能 | EXIF / 拍摄时间筛选 | PASS | PASS | 合成JPEG的NIKON D800与2026-05-06时间读取、日期筛选正确。 |
| 功能 | 拍摄设备筛选 | PASS | PASS | 实际打包GUI查询D800只返回camera.jpg。 |
| 功能 | 分辨率筛选 | PASS | PASS | 实际打包GUI验证800×600下限筛选。 |
| 功能 | 缩略图与详情 | PASS | PASS | 可见缩略图/尺寸加载、详情面板、列表/网格运行通过。 |
| 功能 | 调用系统默认程序打开 | PASS | PASS | 实际QDesktopServices.openUrl返回成功；不是模拟调用。外部查看器最终显示另记未测试。 |
| 功能 | 外部默认查看器最终显示文件 | NOT TESTED | — | 未独立观察外部查看器显示的文件内容。 |
| 功能 | Chinese-CLIP 首次下载/校验/加载 | PASS | PASS | 下载的EXE从空的隔离模型缓存安装固定快照，753106020字节权重通过校验，真实OpenVINO CPU加载成功。 |
| 功能 | AI 语义搜索 | PASS | PASS | 真实模型对合成图库查询电脑屏幕，返回3项；不是语义准确率评测。 |
| 功能 | 以图搜图 | PASS | PASS | 真实图片查询返回2项，直接运行冻结EXE。 |
| 功能 | OCR 首次模型安装 | PASS | PASS | 实际安装检测/识别/方向模型，真实OpenVINO CPU处理3张合成图片。 |
| 功能 | OCR 文字搜索 | PASS | PASS | Windows Update只返回screen.png。 |
| 功能 | AI + OCR 组合搜索 | PASS | PASS | 冻结GUI组合查询只返回screen.png。 |
| 功能 | 模型安装后离线查询 | PASS | PASS | 另一次本地GPL完整运行及迁移运行禁止socket网络连接，AI/OCR/以图搜图通过。 |
| 功能 | 按时间整理预览 | PASS | PASS | 实际OrganizeTask预览，未修改源文件。 |
| 功能 | 移动与撤销 | PASS | PASS | 实际后台整理任务移动合成照片并撤销恢复，数据库状态正常。 |
| 功能 | 复制整理（打包GUI） | NOT TESTED | PASS | 源代码自动测试确实复制临时文件并验证安全删除，但本次冻结GUI驱动没有复制分支。 |
| 功能 | 设置持久化 | PASS | PASS | 窗口重新创建保留网格/搜索目录等；真实EXE进程重开配置未丢失。 |
| 功能 | 独立新进程重启的数据保留 | PASS | PASS | 无verify参数重新启动同一EXE，正常关闭；5文件/3AI/3OCR行逐字节一致，integrity=ok。 |
| 功能 | NEF/NRW真实样本（打包GUI） | NOT TESTED | PASS | 本轮测试媒体只有合成JPEG/PNG；未读取用户私人RAW。 |
| 安装 | 本轮Setup实际安装 | BLOCKED | — | 自动审批因已有同AppId管理员安装拒绝；用户明确选择留待干净VM。本轮没有执行安装器。 |
| 安装 | 本轮安装升级 | BLOCKED | — | 同上；不将2026-10-09历史安装验证计成本轮已发布Setup的验证。 |
| 安装 | 本轮卸载保留数据库及模型 | BLOCKED | — | 同上；无法给出本轮实际卸载通过结论。 |
| 安装 | Portable旧用户数据迁移 | PASS | PASS | 旧版合成配置迁移：5文件/3AI/3OCR行一致，389模型/缓存文件hash一致，8项实际GUI检查通过。 |
| 性能 | 启动与内存测量 | PASS | — | 公开Portable首次大窗口1.813秒、峰值2740.5MiB；本地GPL大窗口1.093秒、峰值2706.7MiB；不含干净机结论。 |
| 性能 | GUI心跳与短时响应 | PASS | — | 两个完整GUI流程最大心跳间隔约0.328秒；迁移0.078秒。外部阶段采样0.25秒，不报告精确逐操作毫秒值。 |
| 性能 | CPU完整索引流水线 | PASS | — | 当前GPL源码实测32张唯一合成JPEG，batch=4/thread=4；真实解码、512维推理、SQLite写入完成。非打包EXE吞吐量。 |
| 性能 | 冻结EXE大型图库吞吐量 | NOT TESTED | — | 冻结包实测图库仅3图片，不能用其微型任务声称大型图库速度。 |
| 稳定性 | 启动日志/未处理异常/依赖加载 | PASS | PASS | 本轮实际应用日志无ERROR/CRITICAL/Traceback；Qt插件、torch/OpenVINO/ONNXRuntime/RapidOCR/rawpy加载成功。 |
| 稳定性 | 长时间/大图库/断网下载压力测试 | NOT TESTED | — | 本轮不是长时压力测试；没有真实机械盘全盘扫描或跨卷真实移动。 |
| 候选 | 本地GPL包完整离线运行 | PASS | — | 尚未发布的GPL EXE通过22项实际GUI检查，官方GPL正文和metadata断言通过，3个512维float32单位向量正常。 |

状态条目统计：PASS=30, FAIL=4, BLOCKED=6, NOT TESTED=10。这不是pytest用例数。

## 启动、内存与响应

| 运行对象 | 首次大原生窗口 | 进程峰值工作集 | 整个驱动流程 | 最大GUI心跳间隔 |
| --- | --- | --- | --- | --- |
| 实际下载的公开RC：含首次模型安装 | 1.813s | 2740.5 MiB | 68.453s | 0.328s |
| 本地GPL候选：已安装模型、离线 | 1.093s | 2706.7 MiB | 42.468s | 0.328s |

独立新EXE进程重启主窗口约0.718秒，5文件/3AI/3OCR记录逐字节保持一致，数据库integrity=ok。启动时刻是首次大原生窗口出现，不含模型准备完成；峰值不是空闲内存。GUI心跳是短时流程测量，不能据此声称大图库或长期无卡顿。

## CPU索引实测

相同32张唯一合成JPEG：24张1920×1080，8张6000×4000；同一Chinese-CLIP固定模型，batch 4、4线程。当前GPL源码后端执行完整流水线，确实生成向量并写入临时SQLite。模型已经加载/预热，计时不含下载、模型加载、转换；文件由本轮生成，OS缓存可能较热，不是HDD测试。没有更改默认性能设置。

| 后端 | batch / threads | decode pictures/s | inference pictures/s | overall pictures/s | 32张索引耗时 |
| --- | --- | --- | --- | --- | --- |
| cpu | 4 / 4 | 64.04 | 7.16 | 7.06 | 4.531s |
| OpenVINO CPU | 4 / 4 | 48.09 | 9.01 | 8.82 | 3.627s |

这些是**源码后端的短基准**，不是干净Windows或打包EXE的大库吞吐量。冻结EXE本轮真实AI/OCR图库只有3张图片，不能据此推算生产环境速度。阶段累计耗时与墙钟可因流水线重叠不同。

## 日志与稳定性

已运行的公开Portable、本地GPLPortable和迁移应用日志没有ERROR/CRITICAL/Traceback，验证进程正常退出。Qt插件、PyTorch、OpenVINO、ONNXRuntime、RapidOCR、rawpy均实际加载；已运行功能未发现打包依赖缺失。未验证Setup在干净系统的DLL依赖或安全拦截，未进行长时/大型图库压力、真实跨盘移动、不同网络代理/断网恢复或真实RAW覆盖。

## 距离 v0.5.0 正式发布的事项

1. 处理已公开RC的发行材料错配。不得覆盖本轮既有Tag/附件；由维护者决定说明旧RC问题并准备新的RC版本，使Tag、源码commit、程序/安装器版本、GPL声明、完整源码和SHA256清单一致。当前main的GPL打包修正可复用，但必须绑定新的候选版本并再验证。
2. 在真正无Python/源码/开发DLL的Windows Sandbox或新VM运行Portable与Setup；记录OS版本和安全设置，保持保护开启。
3. 在该环境完成安装→旧版升级→卸载保留真实数据库/模型→重装，以及旧用户数据迁移；宿主Portable迁移通过不能替代安装升级验证。
4. 补足原生目录选择器、文件名/扩展名/修改时间筛选、复制整理和外部默认查看器显示的打包GUI/人工测试。源码自动测试通过不代表这部分已实机通过。
5. 用带真实浏览器下载来源标记的包验证SmartScreen/SAC。RC目前未签名；决定正式发行的可信代码签名和警告说明方案，不以关闭保护解决。
6. 对更大且可控的图库测量冻结包CPU吞吐量、内存、取消/恢复及长时间稳定性，补真实NEF/NRW与跨卷安全场景；不应全盘扫机械盘来做测试。
7. 延续既有第三方/模型授权及原生依赖对应源码未确认项的检查；本轮没有修改许可证、重新授权模型或作法律合规结论。

本轮只新增质量报告，不创建Release/Tag、不推送历史或替换公开文件。
