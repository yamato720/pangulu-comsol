# GPU 驻留桥接验收清单

本清单描述验收方法，实际通过状态和性能数据由独立运行记录给出。冻结同一提交、为每个 GPU 使用独立应用目录，并记录源提交、组件哈希、GPU UUID、SDK 原始哈希和线程数。SDK、许可、矩阵/RHS/参考解和数值阈值保持不变。

## 构建和 CPU 检查

- 使用兼容的 CUDA 12 `nvcc` 与主机 C++ 编译器运行正式 `pangu-comsol build`，确认 manifest 的 `device_csr=true`。
- 构建 HOST 回退版本，确认未引入 CUDA 编译器的强制运行依赖。
- 检查 fatbin 含 sm75/sm80 和 compute75 PTX；计算能力低于 7.5 的 GPU 应明确记录 HOST 路径。
- 执行 `python -m unittest discover -s dev/tests`，并编译 `dev/tests/adapter_test.cpp`。
- Python 分发包应包含 `device.cu` 和 `device.h`；预编译运行包仍只需原有组件与 CUDA 运行库。

容量策略可在无 GPU 的 CPU 上故障注入测试，直接调用生产分配函数，检查预留不足才重试精确尺寸、其他错误不重试、20 GiB 边界和退休分配回收：

```bash
g++ -O2 -std=c++17 -ffunction-sections -fdata-sections -Wl,--gc-sections \
  -I/path/to/cuda/include -Isrc/include/cudss -Isrc \
  dev/tests/matrix_capacity_test.cpp -ldl -o /tmp/pangu-matrix-capacity-test
/tmp/pangu-matrix-capacity-test
```

该测试替换 CUDA 分配函数，不加载 CUDA 驱动或执行数值求解；GPU 容量、性能与数值仍须独立验收。

## GPU 正确性和状态

对 direct 和 worker 独立运行，保存原始日志和退出码：

- 实数/复数 general、SPD lower、HPD lower；HOST/DEVICE 稀疏和 dense，两个 RHS，以及原地改 values 后的重分解。现有 adapter_test 覆盖这些组合，误差阈值仍为 `1e-10`。
- 同一 descriptor 和 GPU 地址原地改索引/结构；在相同 n/nnz 下必须重新分析。结构恢复后再次正确求解。
- 32/64 位索引、base-one、独立 ends、managed、allocation-offset 和 HOST 稀疏加 DEVICE dense；descriptor 销毁后重建不能绕过内容检查。
- 无序合法 CSR 保持正确；重复位置、越界索引、下降/越界 rowptr 被拒绝。随后修复原数组可继续分析和求解。
- 比较调用前后的原始 CSR/values/RHS，确认输入未被改写。
- 创建并销毁从未分析矩阵的数据对象，确认提前创建的 native handle 和 worker 正常释放；初始化失败时输出 data 指针为空且子进程退出。
- 变更 n/nnz、实复类型和 RHS 列数，验证设备分配增长、旧 IPC 关闭和析构。批量 RHS 可用对角矩阵 `n=8192, nrhs=512`，在 2/512 列之间切换并交替 RHS 内容。
- 对小契约测试运行 compute-sanitizer memcheck，检查非法 rowptr 在读取 entries 前被拒绝。

失败清理可用 `adapter_test --expect-init-failure`，在独立进程内显式指向不存在的原始许可路径；应保留真实的 `PGL_LICENSE_ERROR`，正常结束测试并输出 `ADAPTER_INIT_FAILURE_CLEANUP_PASS`。不要替换或修改任何现有许可文件。

## 长任务复用

- 同一数据对象固定 n/nnz/pattern，原地更新 values 并求解至少 300 轮。应有一次成功 native 创建、一次 analysis、一次 numeric、299 次 refactor；固定 RHS 布局应只创建两个向量描述符。
- 同一标量/矩阵类型切换真实 n/nnz/pattern 和 lower/upper 后重新分析。native handle 创建应保持一次，`handle_reanalyses` 随实际重分析增加；换标量/矩阵类型仍安全创建新 handle。
- 两个数据对象交替求解不同矩阵，分别验证正确解和各自计数，不能共享因子状态。
- 改变 RHS 列数并回到小列数；GPU/HOST dense 分配只在超过容量时增长，IPC 只在实际更换分配时重开。结束后自有设备 live 字节为零。
- 独立采样 warmup 后 GPU 总内存平台，覆盖厂商不透明因子，不能仅用桥内计数推断总 GPU 内存无增长。
- 真实 COMSOL 59 点保存每 data 的两条 summary；`analysis_requests` 与 `analysis_calls` 分别表示 API 请求和实际 native 分析，避免将 59 次回调误算成 59 次初始化。
- 对真实 nnz 小幅增长核对 12.5% 矩阵预留是否减少 allocation/IPC 次数；`matrix_bytes` 仍为实际长度。结合 `n_min/max`、`nnz_min/max` 和 `confirmed_device_pattern_changes` 核实结构变化，不能用容量复用跳过真实分析。

## 性能边界

在同一 GPU 分配、相同软件线程数下，以原样矩阵/RHS/参考解对比 native Pangu GPU CSC/CSR32、正式 direct、正式 worker 和原生 cuDSS。首次 core 包含 analysis、numeric 和相同的 solve；steady factor 使用相同 L2 原地值更新。每项至少三个独立进程，分别保存每阶段与总 wall time。

调用者构造/上传、组件加载、冷启动和首次准备分别报告。不能把本实现新增的快照、校验、IPC 打开或 dense 复制从 `cudssExecute` 边界剔除。profile 中 GPU 矩阵阶段报告融合的 `validation_and_update_seconds`、四字节状态回读，以及严格结构是否匹配；没有测量的新位置数量不报告。

原生 `pangulu_create` 在 analysis 之前执行；桥接的对应资源由 `cudssDataCreate` 同步建立。对照必须额外测量 Create/DataCreate、API 创建到首次物理解的完整延迟及进程总 wall time，并保留改动前版本的对照。`data_create_timing` 和 `PGL_WORKER_INITIALIZE` 只在 profile 构建中记录真实资源创建耗时。不得把阶段时间的减少直接描述为同幅度的冷启动提速。

百万阶数据至少核对物理 RHS 残差、原 MUMPS 参考解和已知解多 RHS；保留既有验收阈值。实测接近原生的结论只能绑定对应矩阵、GPU、线程配置和提交。

## COMSOL

先运行四类小模型集成测试，再跑 S 模型 20 kHz 和完整 59 点；对 direct/worker 与原生 cuDSS 使用相同 COMSOL 配置和线程数。原 59 点、236 场 CPU 参考比较必须完整通过，保存求解器真实加载库、数值输出与总时间。继续使用原厂授权检查，direct 的私有 launcher 保留既有 libjsig 处理方式。
