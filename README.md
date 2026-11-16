# Pangu–COMSOL

Pangu–COMSOL 是带交互式终端界面的 CLI 应用，用于配置 COMSOL 接入、管理本地 SDK 包与原始许可、检查环境和启动求解。COMSOL 继续负责物理场、网格、组装、外层迭代和后处理。

本仓库仅保留正式源码、安装定义、通用配置及客户说明；测试、内部报告、运行数据和打包工具不纳入 Git。厂商 SDK、许可和 CUDA 运行库另行提供。

## 交互式终端应用

在源码目录直接启动：

```bash
./pangu-comsol
```

界面提供 **概览、配置、运行、SDK 包、授权、测试** 页面，支持鼠标和键盘。F1–F5 切换管理页面，F6 刷新，F7 检查，F8 打开测试；Ctrl+C 取消当前操作，Ctrl+Q 退出。操作失败会保留界面，可查看详情后重试。

↑↓ 或 Tab / Shift+Tab 移动焦点，Enter 执行按钮或打开下拉框，空格切换开关、勾选许可。Esc 回到页签后可用 ←→ 切换页面。蓝框和按钮上的 `▶` 表示当前焦点；绿色页签表示当前页面，勾选许可后需“保存选择”。下拉框内 ↑↓ 选择、Enter 确认、Esc 取消；许可列表内 ↑↓ 移动，到首尾后继续移动可离开列表。

耗时操作在后台执行。菜单提交 COMSOL 启动后，GUI 独立运行，可以继续使用菜单或退出终端应用。SDK 包仍由用户自行下载，界面只接收本地原始发行包，不内置下载网址。许可管理只导入和选择原始签名文件，实际授权由厂商 SDK 校验。

## 安装应用

需要 Python 3.12+。取得本仓库后，可通过 pipx 安装：

```bash
pipx install .
pangu-comsol
```

也可在自己的 Python 虚拟环境中执行 `python -m pip install .`。源码方式需要先安装应用依赖；本工作区若已创建 `runtime/app-env/`，根目录入口会使用该私有环境。

安装后的应用将可变数据放在 `~/.local/share/pangu-comsol/`，或 `$XDG_DATA_HOME/pangu-comsol/`。程序代码安装在 Python 环境中，SDK、许可和本机配置与程序分开，CLI 升级不会替换它们。

使用已有模组目录：

```bash
pangu-comsol --home /path/to/pangulu-comsol
```

`PANGU_COMSOL_HOME` 也可指定应用目录。源码根目录入口默认继续使用本目录。`start.sh` 保留为一键 GUI 入口，等价于 `./pangu-comsol run`。

## 子命令

原有子命令继续可用，适合自动化：

```bash
pangu-comsol init --comsol /path/to/multiphysics --cuda /path/to/cuda
pangu-comsol package install /path/to/pangulu-release.tar.gz
pangu-comsol license add /path/to/customer.key
pangu-comsol doctor
pangu-comsol run /path/to/model.mph
```

| 命令 | 用途 |
|---|---|
| `menu` | 显式打开终端界面；无参数且连接交互终端时也进入界面 |
| `init` | 初始化本模块配置，保留已有选择 |
| `config show` / `config set KEY VALUE` | 查看或修改本模块配置 |
| `run [模型.mph]` | 打开 COMSOL；`--detach` 提交启动后立即返回 |
| `package status/install/update` | 查看、导入或整体更新本地 SDK 包 |
| `license add/list/use/auto` | 导入、列出、选择原始许可，或自动使用全部许可 |
| `status` / `doctor` | 查看状态或执行原生授权与 GPU 检查 |
| `build` | 从应用自带的正式源码构建接入组件 |
| `test status/build/run/benchmark` | 查看、构建可选 cuDSS 校准组件，选择后端启动 GUI 或运行单独提供的 S 验收工具 |
| `inspect/configure/restore` | 查看、配置或恢复模型的求解器引用 |

## 运行文件

应用目录中的 `pangu/` 保存一份完整原始 SDK，`licence/` 保存客户许可，`config/` 保存本机配置，`runtime/` 保存接入组件及 CUDA 运行库，`data/runs/` 保存模组运行记录。这些内容不随 Python 应用代码升级而删除。

配套接入组件已预编译时可直接运行。仅有源码时，需要 gcc/g++、CUDA 12.9 开发头文件与库、COMSOL 6.4，再执行 `pangu-comsol build`。安装后的应用也带有必要的正式构建源码，不依赖开发工作区。

GUI 中的 Pangu 选项由用户自行选择，不自动计算或保存。命令行指定模型时打开工作副本；GUI 内直接打开原文件后保存仍会写回原文件，需要保留原件时使用 Save As。

应用沿用 COMSOL 默认或用户已有的偏好、临时、恢复、工作区及配置位置，不创建或重写 `.prefs`。线程数仅在显式指定时覆盖本次运行。

## 可选后端测试

测试页可手动选择原生 `cudss`、`mycudss-direct` 直接封装、`mycudss-worker` 独立进程封装。三者的数值核心均为 COMSOL 随附的 NVIDIA cuDSS；后两者用来校准接口及桥接开销。

```bash
./pangu-comsol test status
./pangu-comsol test build
./pangu-comsol test run --backend mycudss-worker /path/to/model.mph
./pangu-comsol test benchmark --backend mycudss-direct \
  --project /path/to/study3-pangulu-s --frequencies all
```

校准源码和 S 验收工具单独提供，本机开发目录为 `dev/benchmarks/mycudss/`，不纳入正式源码仓库或应用安装包。也可用 `test build --source /path/to/calibration-sources` 指定源码。测试组件独立构建到 `runtime/tests/mycudss/`，运行记录及模型工作副本写入 `data/runs/`，每次会话记录实际原生库、封装库及 worker 路径与哈希。

测试 GUI 内选择 Direct/cuDSS 后计算。数值测试默认先跑 20 kHz，可选 `--frequencies all` 跑完整 59 频点，`--repeat N` 重复运行；需要单独提供对应 S 输入与 CPU 参考模型。没有校准工具时，原生 cuDSS 测试启动仍可使用。

[使用说明](docs/usage.md) · [客户操作](docs/customer-README.txt) · [接入架构](docs/architecture.md) · [配置字段](config/README.md)
