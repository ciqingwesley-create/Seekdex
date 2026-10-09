# Windows AI 图片索引性能报告

日期：2026-10-07。继续使用原 Chinese-CLIP ViT-B/16 权重、`rgb224-exif-v1` 预处理、512 维 L2 归一化 float32 向量。没有更换模型、重建已有向量或增加其他用户功能。

## 硬件与环境

- Windows 11 专业版，10.0.26200；约 24 GiB 内存。
- Intel Core i7-8700，6 个物理核心 / 12 个逻辑核心。
- Intel UHD Graphics 630 核显；没有可用 CUDA。
- Python 3.12；PyTorch 2.8.0+cpu；Transformers 4.57.6；OpenVINO 2026.4.1。
- OpenVINO 实际识别 `CPU` 和 `GPU`。GPU 报告 FP32 / FP16 能力；本次实际运行使用 **FP32**，未做 INT8 或 FP16 量化。
- PyTorch 确认可用 MKL / oneDNN，oneDNN 启用。实际算子采样出现 `aten::mkldnn_convolution`、72 次 `aten::addmm`、25 次 `aten::matmul`。MKL 配置版本 2025.2，oneDNN 3.7.1。

## 样本与计时方法

1. 普通 JPEG：16 个测试路径，来自猫、咖啡、宇航员三张公开照片。
2. 大 JPEG：16 个测试路径，样例照片放大为 6000×4000，用于检验解码成本；不是 16 张独立拍摄的高分辨率相机照片。
3. NEF：16 个测试路径，共用之前实际验证过的 `sample-005.nef` 测试副本。
4. 混合：以上 48 个路径，各 16 个 JPEG / 大 JPEG / NEF。
5. 真实 NEF 补充：只读取用户指定目录中的 `sample-005.nef` 到 `sample-006.nef`，16 张不同照片，约 40–47 MB / 张。没有全盘扫描或改写原文件。

测试路径使用受控副本 / 硬链接，重复内容仍逐文件完整推理并保存向量，没有用重复内容跳过计算。每次使用全新的临时 SQLite；流水线核对所有向量成功写入。稳态计时包含读取、解码、预处理、推理、归一化、提交；不包含模型启动、转换、首次调优和基础文件索引刷新。操作系统缓存没有强制清空；对比旧流程时在计时前读取输入一次，以比较热缓存。不能把这些数字当作机械盘冷读取的保证。

原始分阶段结果、线程 / batch 矩阵、兼容性和 GUI 记录汇总在 [JSON 实测数据](ai-performance-results.json)。

## 瓶颈

同一组 48 张混合图片，旧流程（CPU 6 线程，batch 2，串行读图 / 推理 / 写入）热缓存复测：

| 阶段 | 秒 |
| --- | ---: |
| 文件读取 | 0.0328 |
| RAW 预览提取 | 0.0516 |
| Pillow 解码 | 1.4253 |
| 预处理 | 0.4840 |
| Tensor batch 构造 | 0.0011 |
| 模型推理 | **7.7960** |
| 向量归一化 | 0.0030 |
| SQLite | 0.0715 |
| 总时间 | 9.8967 |

CPU image encoder 推理约占 **79%**，解码和预处理约占 19%；SQLite 不到 1%。原实现已经按 batch 推理和提交，并非逐文件 commit。连续两次后台线程检查仍各保持 6 个 Torch 线程，没有发现线程设置丢失。

用户提到的稳定 2 pictures/s **没有在本次受控样本中复现**。旧流程在不同样本约 4.2–5.8 pictures/s；首次读取真实 NEF 的补充测试约 3.91 pictures/s。以上证据能确定此测试的推理瓶颈，不能认定用户每个目录慢到 2 pictures/s 都是同一个原因。

## 优化

- 有界读取 / 单图预处理队列 → 真正 batch 推理 → 有界写入队列；准备下一批与当前推理重叠。输入队列最多 2 个初始 batch，输出队列最多 2 个 batch。
- 图片转为模型输入后尽快释放 PIL 图像；只保留有限数量的 3×224×224 数组。
- 独立写入线程持有自己的 SQLite 连接，批量归一化，每 batch 一次事务。无效单条向量不拖累其余有效条目；数据库异常回滚当前 batch，其他批次继续。
- 确认并保留 JPEG `draft` 减采样、448 像素上限和 RAW 内嵌预览；没有改变旧预处理的像素输出。AI 从不执行 RAW demosaic。
- 实测 intra-op 1 / 2 / 4 / 6 和 batch 1 / 4 / 8 / 16 / 32，inter-op 尝试设为 1；按模型 / 机器 / 运行库缓存结果，保留手动 batch。6 个线程不是直接照搬逻辑核心数。
- OpenVINO 只转换 vision encoder + projection，使用原来的 processor。文本编码仍用 PyTorch。转换、编译、兼容性缓存独立保存，转换互斥并用临时文件提交。
- OpenVINO GPU 编译、运行或向量兼容性检查失败，先退到 OpenVINO CPU，再退到 PyTorch CPU。缺少 OpenVINO 时普通搜索和原 AI 后端仍可用。
- 状态显示真实后端、batch、完成数量和约 8 秒滑动平均速度。取消停止新排队，当前单图 / batch 可以结束，已提交数据可续用。

LibRaw 原生 I/O 与解析不可完全拆开：`file_read` 包含 RAW 打开 / 文件头解析，`preview_extract` 包含原生预览提取及其读取。真实 NEF 的 OpenVINO CPU 测试中，16 张预览提取约 0.050 秒，JPEG 解码约 0.891 秒；两者不能混称为完整 RAW 解码。`raw_unpack` 为 0。

## Batch 与线程

32 张混合输入的串行稳态推理 pictures/s（**实际测试 batch 32**，而不是把 24 张尾批标成 32）：

| 后端 / 线程 | batch 1 | batch 4 | batch 8 | batch 16 | batch 32 |
| --- | ---: | ---: | ---: | ---: | ---: |
| PyTorch CPU / 4 | 6.58 | 6.84 | 6.87 | 7.03 | 5.87 |
| OpenVINO CPU / 6 | 11.79 | **12.19** | 11.08 | 10.72 | 9.74 |
| OpenVINO GPU | 4.83 | 3.90 | 5.47 | 6.02 | **7.27** |
| OpenVINO AUTO | 5.03 | 3.12 | 4.50 | 5.19 | **6.55** |

另完成 CPU 1 / 2 / 4 / 6 线程的 batch 矩阵。PyTorch 单线程混合整体约 2.25–2.56 pictures/s，2 线程约 3.70–4.22，4 / 6 线程约 5–5.7；自动调优不采用“batch 或线程越多越快”的假设。

完整流水线再次比较 PyTorch 4 / 6 线程的五种 batch：本次混合样本最大整体速度是 4 线程、batch 1 的 6.46 pictures/s。OpenVINO CPU 6 线程的 batch 1 为 9.56，batch 4 为 9.77。接近的测量会受温度、后台负载影响；本机缓存采用完整索引测得的 **OpenVINO CPU / 6 线程 / batch 4**。

## 同组完整索引结果

下表是 48 张混合图片的稳态完整计算，单位均为 pictures/s：

| 后端 | batch | 线程 | 读取＋解码＋预处理 | 推理 | 整体 | 相对旧流程 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 旧 PyTorch CPU 串行 | 2 | 6 | 24.08 | 6.16 | **4.85** | 1.00× |
| PyTorch CPU 流水线（实测最优） | 1 | 4 | 25.11 | 6.49 | **6.46** | 1.33× |
| OpenVINO CPU 流水线（实测最优） | 4 | 6 | 21.01 | 9.88 | **9.77** | **2.02×** |
| OpenVINO GPU FP32 流水线 | 32 | — | 25.78 | 7.18 | **6.38** | 1.32× |
| OpenVINO AUTO 流水线 | 32 | — | 26.63 | 6.42 | **5.80** | 1.20× |

流水线阶段时间重叠，整体速度不能通过阶段时间简单相加得出。GPU / AUTO 的线程数只涉及参考 PyTorch / CPU 配置，不是给核显指定 6 个 GPU 线程。

OpenVINO CPU 在分组测试中的整体速度：普通 JPEG **11.42**、6000×4000 JPEG **9.34**、NEF 副本 **8.33**、混合 **9.35**、16 张不同真实 NEF **8.21**。PyTorch CPU 同组 batch 8 的补充测量分别为 6.18、5.75、5.14、5.32、5.39；与最终选出的 batch 1 不同，完整原始值保留在 JSON。

相对用户给出的 2 pictures/s，9.77 数值上约 **4.89×**；严格同组对比提升为 **2.02×**。OpenVINO AUTO 插件在这块 UHD 630 上偏向 GPU，效果弱于 CPU；程序界面的“自动”依据实际测速选 OpenVINO CPU，**不是强制使用 OpenVINO AUTO 插件**。

## 向量兼容与已有索引

从优化前的测试数据库读取 6 张真实照片的既有向量，包括 JPEG、PNG 和 NEF。分别用新 PyTorch、OpenVINO CPU、GPU、AUTO 生成向量：

- 全部 512 维，L2 norm 一致。
- 各后端与旧向量最低 cosine **0.9999998808**。
- 五组中文查询的完整排序一致；最大分数差小于 **2.1×10⁻⁷**。
- 切换后端再次建立索引，全部记为已有，完成 0；旧 BLOB 和生成时间未改写。
- 保持原 `model_id`、UID 和数据库结构，无需迁移或重建 embeddings。

每种实际设备 / 驱动 / OpenVINO 版本首次运行还有数值门槛检查，最低 cosine 必须 ≥0.9999，否则回退。小样本验证不等于证明所有图库 Top-K 永远完全一致，但没有观察到本次搜索排序变化。

## 自动测试与 Windows GUI

- 原 106 个测试保留；新增 24 个，**130 / 130 全部通过**。
- 覆盖真实 batch 接口、有界取消与续建、读取与推理重叠、批次事务 / 回滚、写线程失败退出、无效向量隔离、OpenVINO 缺失和 CPU / GPU 回退、兼容性门槛、转换缓存、旧向量复用、模型空间保护、手动配置及测速选择。
- `pip check` 通过。
- Windows 原生主窗口实际显示；受控目录普通搜索得到 48 个文件。
- 自动使用 OpenVINO CPU / batch 4；取消后保留 8 个向量，续建完成剩余 40 个，失败 0。两次带 GUI 的显示速度分别约 **8.1 / 9.7 pictures/s**，与独立 benchmark 的运行条件不同。最终检查也确认可见缩略图正常加载。
- 中文搜索成功；切换 PyTorch CPU 后，已有 48、本次完成 0，向量字节不变。SQLite 完整性为 `ok`。
- 最终检查中，20 ms GUI 定时器记录 630 次，最大间隔 **0.156 秒**；前次为 673 次 / 0.188 秒。索引期间未发现长时间冻结。初始化与本机测试不代表任何硬件的响应上限。

推荐当前机器保留界面“自动”与批次“自动”，其缓存结果为 **OpenVINO CPU，batch 4，CPU 线程 6**。品牌迁移后转换缓存和 `tuning-*.json` 位于 `%LOCALAPPDATA%\Seekdex\models\chinese-clip-vit-b16-f4a64596bbcf\`。删除调优 JSON 后下次缺失向量索引重新测量；不要删除已有 SQLite 或 embedding。

参考：[OpenVINO PyTorch 转换](https://docs.openvino.ai/2025/openvino-workflow/model-preparation/convert-model-pytorch.html)、[CPU 执行配置](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/cpu-device.html)、[AUTO 设备选择](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/auto-device-selection.html)。实测结论来自本机报告，不来自这些文档的性能承诺。
