# Seekdex · 索星仪 v0.5.0-rc2

Unsigned Release Candidate，发布文案草稿。此版本替代 rc1；不是正式 v0.5.0。

## 发行修正

- 解决 rc1 公开附件与 GPLv3 源码 / Tag 不一致的问题。rc2 标准 Portable 和 Setup 从同一个干净提交构建。
- Python 版本 `0.5.0rc2`、GUI / EXE / 安装器显示版本 `0.5.0-rc2`、Windows 数值版本 `0.5.0.2`。
- 主项目许可证 **GPL-3.0-only**；包内包含完整 GPLv3、README、版权及第三方通知、对应应用源码。
- 修正旧二进制的 GitHub 主页地址，统一 https://github.com/ciqingwesley-create/Seekdex 。
- 记录源 commit 与源码 SHA256；构建检查实际 EXE、安装器元数据和全部 Portable 文件哈希。版本 / 许可证 / 主页 / commit 不一致时拒绝发行验证。
- `SHA256SUMS.txt` 只列标准 Portable ZIP 与标准 Setup EXE。公开包不带 Chinese-CLIP 或 OCR 模型权重，使用时按需安装模型。
- 不改变 AI embedding / OCR 身份，不要求已有索引重建。

## 许可证历史

当前自有源码为 GPL-3.0-only。此前 MIT 版本已经授予的许可不受追溯影响。rc1 发布页错误附带的旧 MIT 构建不会仅因为 GPL Tag 而变成 GPL 构建。第三方代码、原生库及模型仍采用各自条款，见 THIRD_PARTY_LICENSES.md 与 docs/licensing-audit.md。

## 限制与发布前要求

- Windows binaries currently unsigned. SmartScreen / Smart App Control may warn or block unsigned builds. 不关闭安全保护绕过拦截。
- 干净 Windows 虚拟机中的安装、升级、卸载及无开发环境验证仍未完成。
- Chinese-CLIP 权重再分发授权未确认，因此不公开预装权重。
- 第三方原生组件对应源码 / 重链接等义务仍需发行方复核；附带通知不等于已完成全部法律要求。
- 本文是发布准备，尚未创建 rc2 Tag 或 Release。人工发布时必须让 `v0.5.0-rc2` 指向 release-manifest.json 中的完整 commit，并附两项标准文件及其 SHA256SUMS.txt；不改写 rc1 Tag / 附件。
