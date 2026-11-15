# 接入结构

统一入口为 `pangu-comsol`。`start.sh` 是 GUI 快捷入口，转交 `pangu-comsol run`；批处理、配置、SDK 包和许可管理使用同一 CLI。

## 源码阅读顺序

核心分为三层。CLI 与 Python 运行层准备本次运行；Java 代码把模型的线性求解器连接到 cuDSS 槽位；C/C++ 代码实现这个槽位并调用 Pangu。

| 层 | 从哪个文件看起 | 关键入口 |
|---|---|---|
| 命令与管理 | `src/python/pangulu_comsol/cli.py` | `init`、`config`、`package`、`license` |
| 运行与构建 | `src/python/pangulu_comsol/runtime.py` | `launch`、`comsol_command`、`clean_env`、`build` |
| GUI 选项 | `src/java/PanguDesktop.java` | `start`、空闲时的 `tick` |
| 模型配置 | `src/java/PanguModule.java` | `targets`、`registerAll`、`apply`、`restore` |
| cuDSS ABI | `src/bridge.cpp` | `cudssExecute`、矩阵暂存、`start_worker` / `rpc` |
| Pangu 调用 | `src/worker.c` | `main`、`load_matrix`、`initialize_analysis`、`factor`、`phase` |

`pangu/` 保存原始厂商 SDK，`licence/` 保存独立许可，`runtime/` 保存编译组件和运行依赖。运行时文件与正式源码分开放置。

## 一次求解的调用路径

1. Python 运行层读取 `config/runtime.json`，解析活动 SDK，在 `data/runs/` 建立自身记录与许可快照；COMSOL 偏好、临时和恢复路径继承默认或用户已有设置。
2. COMSOL 的 `-cudssroot` 指向本模块 `runtime/module/`，从那里加载 `libcudss.so.0.7.1`。
3. GUI 的 `PanguDesktop` 在空闲时用 `PanguModule.registerAll` 为所有兼容求解器操作添加标准 Direct 选项，保留原来的选择和已有解。用户在 Linear solver 中选择 Pangu 后使用 `cudss` 槽位。批处理则用 `PanguModule.apply` 显式选择指定节点。
4. COMSOL 组装稀疏矩阵后调用 cuDSS 接口。`src/bridge.cpp` 接收这些调用，检查 CSR、标量类型、结构和求解阶段。
5. bridge 通过 Unix socket 发送命令，通过共享内存传递矩阵、RHS 和解；独立 `pangulu_worker` 由适配库启动。
6. `src/worker.c` 把矩阵转换为 Pangu 使用的布局，通过厂商公共 C API 调用分析、分解、重分解和回代。
7. 解向量写回共享内存，再由 bridge 返回 COMSOL。COMSOL 继续外层迭代、时间步和物理场恢复。

```mermaid
flowchart TD
    L[pangu-comsol：命令与运行] --> C[原始 COMSOL]
    L --> D[src/java/PanguDesktop.java：注册 GUI 选项]
    D --> J[src/java/PanguModule.java：标准节点配置]
    J --> C
    C --> B[src/bridge.cpp：cuDSS ABI]
    B -->|socket 命令 / 共享内存数据| W[src/worker.c]
    W --> S[原始 Pangu SDK]
    S --> G[GPU]
    G --> W
    W --> B
    B --> C
```

## 文件分工

| 文件 | 作用 |
|---|---|
| `pangu-comsol`、`src/python/pangulu_comsol/` | 初始化配置、本地 SDK 包管理、原始许可文件管理、构建和启动 |
| `src/java/PanguModule.java` | 模型检查、消费者选择、启用/恢复求解器 |
| `src/java/PanguDesktop.java` | 本会话的全局选项注册、GUI 空闲检测和状态记录 |
| `src/java/PanguBatch.java` | COMSOL batch 入口 |
| `src/bridge.cpp` | cuDSS ABI、矩阵检查、进程间传输 |
| `src/worker.c` | 原始 Pangu API 调用、矩阵转换与求解状态 |
| `src/protocol.h` | 命令和共享内存布局 |
| `src/pattern_cache.h` | 稀疏结构转换和缓存检查 |
| `src/native_license.c` | 原生 SDK / GPU / 许可检查 |

## 边界

COMSOL 的物理场、网格、组装、外层非线性迭代、时间步和后处理继续由 COMSOL 完成。替换发生在线性直接求解后端。

适配库覆盖经过验证的 cuDSS 调用子集。参数被接收不代表所有 cuDSS 数值策略都已映射；真实配置及诊断限制见 [使用说明](usage.md) 的适配范围。

SDK 完整原件放在 `pangu/` 下唯一发行目录，许可放在根目录 `licence/`，生产组件在 `runtime/module/`。更新直接替换整套 SDK，启动器按实际文件和 ABI 识别，不使用版本登记或 current 链接。原始 SDK、配套库及签名许可保持字节不变。正式构建仅编译 src/，不依赖内部测试工具。

桌面扩展在当前进程的原始插件启动列表后追加 Pangu，不写 COMSOL 安装或用户偏好，不改工作区和插件配置目录。后续普通启动使用原始列表，附加初始模块由 Equinox 启动机制移除；缺少会话标记时 Pangu 扩展也不会注册选项。命令行指定的 GUI 输入先复制为会话工作模型；桌面注册不保存模型。GUI 内直接打开原文件后手动保存，仍会把新增标准节点写入该文件。普通 COMSOL 能读取这些节点，CPU 求解器引用保留；cuDSS 在同一 Pangu 会话内整体映射到适配库，要使用 NVIDIA 原生 cuDSS 应另开原生会话。

## 运行记录

模组自身的记录放在 `data/runs/comsol_<时间戳>/`，`session.json` 保存后端、实际库路径、SDK 与许可哈希以及启动参数。模型工作副本和批处理输出按命令指定的位置保存。COMSOL 自身的偏好、缓存、临时与恢复文件仍按默认或用户已有设置管理。
