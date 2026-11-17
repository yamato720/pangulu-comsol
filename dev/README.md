# 开发与验收工作区（develop）

`develop` 跟踪本目录的开发测试源码、环境准备脚本及小型结果报告。`main` 保持交付源码；Python 安装包仍由 `MANIFEST.in` 排除本目录，生产构建 `pangu-comsol build` 只编译 `src/`。

## 分支与文件

正式功能修改和开发资料分开提交；向 `main` 更新时只移入正式功能提交，保留开发资料在 `develop`。

SDK 原包、设备许可、COMSOL/CUDA 安装、模型、矩阵和原始大日志保持在本地 `pangu/`、`licence/`、`runtime/`、`data/` 中，不进入公开 Git 历史。现有实验压缩包单独转交；本机包在 `data/exports/`。结果摘要位于 [results/](results/README.md)。

历史实验驱动按现状保留，有些包含原机器的路径和日期目录。目标端 Codex 可按实际安装和数据位置调整；这里不预设另一台机器的布局。

## Python 与本地依赖

从仓库根目录开始；Python 3.12+，应用依赖由 `pyproject.toml` 定义，数据处理依赖见 `requirements.txt`。纯 Python 测试使用标准库 unittest。

```bash
python3 -m venv runtime/dev-env
. runtime/dev-env/bin/activate
python -m pip install -e . -r dev/requirements.txt
python -B -m unittest discover -s dev/tests -v
```

原生及 COMSOL 测试另需 gcc/g++、COMSOL 6.4、CUDA 12.9、原始 PanguLU SDK 与该 GPU 的许可。版本基线和原厂库哈希记录在 [environment.json](environment.json)。

GPU CSR 构建需要 CUDA 12 的 `nvcc`；可用 `PC_CUDA_COMPILER=/path/to/nvcc` 和 `NVCC_CCBIN=/path/to/supported/g++` 指定工具链。构建清单 `device_csr` 必须为 `true`，启动日志应包含 `GPU CSR exact validation and CUDA IPC enabled`。仅有运行库/头文件时会构建明确标识的 HOST 回退版。手工 profile 构建需要将 `device.cu` 编译为 PIC 对象并链接到 `libcudss`，同时为 `bridge.cpp` 定义 `PGL_DEVICE=1`；该对象只依赖 cudart，数值后端不需要重复链接 CUDA 核心。

`device.cu` 使用完整偏移边界校验、索引范围/顺序校验和每个索引/数值位的比较；没有结构哈希。非法偏移在访问索引前拒绝；无序和重复索引走 HOST 的严格规范化/重复检查。IPC 句柄只导出桥接层独立分配的内存，因此调用者的子分配、managed 内存和原地更新不影响进程隔离。设备矩阵和多 RHS 缓冲区分别保留，旧分配在后端完成切换后释放。

```bash
# 已有兼容 CUDA 安装可直接使用；需要原始 wheel 依赖时才运行此脚本。
python3 -B dev/tools/install_cuda_dependencies.py
./pangu-comsol init --comsol /path/to/multiphysics --cuda /path/to/cuda-12.9
./pangu-comsol package install /path/to/original-pangulu-sdk.tar.gz
./pangu-comsol license add /path/to/device.license.key
./pangu-comsol doctor
./pangu-comsol build
./pangu-comsol test build
./pangu-comsol test status
```

最后两条直接使用本分支的 `dev/benchmarks/mycudss/`，构建原生 cuDSS 直接转发和 worker 两种校准组件。cuDSS 使用配置中 COMSOL 随附的原始库，运行组件仍生成在 `runtime/tests/`。

## 工具入口

| 路径 | 用途 |
|---|---|
| `manage.py` | 单独编译、运行适配层和 COMSOL 小模型集成测试 |
| `tests/` | SDK 整体更新、失败恢复、许可保留、ABI 和 Java 模型行为测试 |
| `benchmarks/s/` | S 模型 20 kHz / 59 频点、四物理场 CPU 参考比较与资源采样 |
| `benchmarks/compatibility/` | COMSOL 兼容接口审计 |
| `benchmarks/mycudss/` | 原生 cuDSS / 直接封装 / worker 封装校准及 S 档对比 |
| `benchmarks/pangu_execution*.py` | PanguLU worker/direct 完整流程及固定矩阵对比 |
| `benchmarks/matrix_duel/` | 同矩阵 PanguLU/cuDSS 原生 GPU 阶段计时、CUPTI 与数值核对 |
| `benchmarks/pinched_cylinder/` | 圆柱薄壳模型、矩阵捕获、独立重放及失败记录方法 |
| `benchmarks/profile_study3.py` | Study 3 CPU/GPU 瓶颈采样 |
| `tools/package-release.py` | 按白名单生成客户运行包 |
| `tools/package-module.py` | 生成维护者使用的开发源码包 |
| `tools/check-s-result.py` | 只读检查 GUI 保存的 S 结果 |
| `tools/install_cuda_dependencies.py` | 工厂安装并核对 NVIDIA 原始 CUDA 依赖 |
| `tools/run-s.sh` | S 验收入口 |

```bash
python3 -B -m unittest discover -s dev/tests -v
python3 -B dev/manage.py integration --out data/runs/integration
dev/tools/run-s.sh --frequencies 20000
python3 -B dev/tools/check-s-result.py data/models/S_pangu_26.46_gui-t1.mph
python3 -B dev/tools/package-release.py
```

S 测试需要另行提供 `models/S_after_mapping.mph` 和 `models/S_full59_solved.mph`。纯矩阵测试的数据也单独提供，记录和准备方法见各实验 README。`pinched_cylinder/replay_package/` 是已有独立复跑包的入口源码，不依赖更改客户 CLI。

通用包默认不含许可；按订单配发可显式使用 `--licence /path/to/customer.key --output data/releases/customer-name`，仅指定的原始文件会进入 licence/，不会自动带入本机其他授权。

许可统一放根目录 licence/，SDK 更新使用 pangu-comsol package update。所有入口直接使用系统 CUDA/NVML 驱动，无环境选择开关或专用重执行入口。

此前版本化 SDK 管理、Slurm 模板和远端性能脚本已作为历史开发材料保留在本次交付验证目录的 legacy-developer-tools.tar.gz。它们不进入客户运行包，也不再挂在主启动 CLI 上。
