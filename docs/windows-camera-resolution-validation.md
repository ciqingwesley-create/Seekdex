# Windows 拍摄设备与分辨率验证

验证日期：2026-10-07。Windows，Intel i7-8700，Python 3.12；Chinese-CLIP ViT-B/16 与现有 OpenVINO CPU 后端。

## 测试范围

使用现有用户数据库的只读备份和四张真实照片副本，再添加一张无 EXIF 的 PNG。验证数据库、照片副本、缓存和截图都位于 `.verification/camera-windows/`。没有修改用户数据库或原照片，没有扫描整盘、重建 embedding 或修改 AI 预处理。正式数据库在下次启动新版本时自动增加字段，旧记录通过用户选择的元数据补全任务更新。

## 真实文件信息

| 文件 | 原始 Make | 原始 Model | 显示名称 | 校正方向后的尺寸 | 实际 MP |
| --- | --- | --- | --- | --- | ---: |
| sample-001.jpg | SONY | DSC-RX100M6 | Sony DSC-RX100M6 | 5472 × 3648 | 19.961856 |
| sample-003.jpg | NIKON CORPORATION | NIKON D800 | Nikon D800 | 4912 × 7360 | 36.152320 |
| sample-004.nef | NIKON CORPORATION | NIKON D800 | Nikon D800 | 7378 × 4924 | 36.329272 |
| sample-002.nef | NIKON CORPORATION | NIKON D800 | Nikon D800 | 4924 × 7378 | 36.329272 |
| screenshot.png | 空 | 空 | 未知 / 无 EXIF | 1800 × 1200 | 2.160000 |

NEF 的 `Image ImageWidth / ImageLength` 是 160 × 120 缩略图尺寸，不能直接用于分辨率。实际 RAW 尺寸取自 LibRaw 文件头，不解码 RAW 像素；本组 RAW 与相机导出的 JPEG 尺寸不同，程序保留实际文件的结果，不用相机宣传参数替代。

## Windows 窗口与搜索

- 主窗口实际显示，元数据补全按钮处理 5 / 5 条，失败 0。
- `D800 + NEF + 最小 30 MP`：返回 sample-004.nef、sample-002.nef 两张照片。
- 再加入 `竖向 + ≥4K`：仅返回 sample-002.nef，4924 × 7378、36.3 MP，竖图未被排除。
- 相同设备、扩展名、MP、4K 条件加“玻璃杯” AI 查询：两张 NEF 按 cosine 0.41782349、0.41334891 降序返回。
- AI 状态栏反映当前两个有效候选，没有保留上一次竖图条件的一个候选。
- 关闭新增筛选面板后，JPEG 搜索正确返回两张横 / 竖照片；无 EXIF PNG 详情显示“未知 / 无 EXIF”、1800 × 1200、2.2 MP，没有 None。
- 可见结果生成 5 个磁盘缩略图；元数据补全任务自身不生成缩略图。
- 20 ms GUI 心跳记录 557 次，最大间隔约 0.156 秒；没有扫描、读取元数据或 AI 查询造成长时间界面冻结。该小样本结果不代表所有图库和机器的延迟上限。

## 数据与兼容性

验证数据库始终有 **5,760 个已有 embedding**。所有 `(file_uid, model_id, embedding BLOB, indexed_at)` 的整体 SHA256 前后相同：

`72c4a8c89b9e5a3e62f1927deb338076500438822b30507d9fd1f60e3af05db9`

SQLite `integrity_check` 为 `ok`。新增相机 / 宽高元数据更新不触发向量失效；源文件确实改变大小或 mtime 时继续按原机制失效。移动与撤销保留设备、正确宽高及稳定 UID；复制继承已读取元数据并分配新 UID，不误复制旧向量。

## 自动测试与修复

全部 **200 个 pytest 测试通过**，包括原有 141 个。依赖检查通过，没有新增第三方依赖。

新增覆盖 JPEG / NEF / NRW 的设备信息、无 EXIF、全部 EXIF Orientation 方向、宽高和 MP 范围、横 / 竖 4K、8K、厂商 / 型号 / 部分文字 / 大小写搜索、日期与 AI 条件组合、旧数据库迁移、取消续读、源文件变化失效、旧 UID 写入拒绝、已有向量保留和整理联动。

开发验证发现并修复：PNG 获取 EXIF 后再 verify 的生命周期问题；旧缩略图和分辨率没有校正 EXIF 旋转；Windows 早期日期通过 mktime 比较可能失败；AI 状态栏沿用旧筛选候选数量。新增元数据读取不会调用 JPEG / PNG 像素解码，PNG 的 EXIF 由 ExifRead 按块读取 / 跳过图像数据。

原始实机报告：`.verification/camera-windows/report.json`。截图：`d800-nef-30mp.png`、`portrait-4k.png`、`ai-combined.png`、`jpeg.png`、`no-exif.png`。自动测试使用 pytest 临时文件，不依赖这些真实照片。
