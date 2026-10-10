# Windows 发行一致性检查

只有已提交且干净的源码可构建；构建环境中安装的 seekdex 包元数据必须与源码一致。先安装 `pip install --no-deps -e .` 刷新包版本。

运行 `scripts/build_windows.ps1 -Edition Standard`。检查不能通过 `-SkipRuntimeVerification` 跳过：该选项只跳过功能测试，实际 EXE 身份检查始终执行。

检查内容：

1. pyproject 的 GPL-3.0-only、项目主页与 authoritative app_info 一致。
2. application-source.zip 的每一项与 Git HEAD 的 blob 内容一致，包含全部受控源码；source-provenance.json 记录 commit、显示 / Python / Windows 版本、许可证、主页和源码哈希。
3. 实际执行打包 EXE 的隔离 `--verify-identity`，检查编译后的元数据、Python dist-info、完整 GPL / README / 通知、PE 数值 / 显示版本、主页及 SourceCommit。
4. build-verification.json 绑定 EXE 及全部运行文件哈希。替换 EXE / 资源或混入模型权重会失败。
5. Setup 的实际 PE 包含相同版本、GPL 声明、主页、source commit；编译输入与输出哈希绑定同一已验证 Portable 树。此检查不等价于干净环境安装测试。
6. 最终校验 ZIP 内全部文件与已验证树一致，Setup 哈希 / 元数据 / 编译凭据一致。SHA256SUMS 使用明确的两项标准文件白名单。

构建后再次执行 `python scripts/release_checks.py`，成功才可使用 release-consistency.json / release-manifest.json。便携单独构建必须显式使用 `--portable-only`，不会冒充完整二件套发行。

`planned_tag` 只是待创建 Tag 名称。本地验证不会创建或更新远程 Tag。维护者创建并获取 Tag 后，必须执行 `python scripts/release_checks.py --tag v0.5.0-rc2`，Tag 版本或目标 commit 不匹配会失败。人工发布时还需确认 CI 通过、附件 SHA256 匹配，并完成尚未完成的 Windows 安装与许可证复核。
