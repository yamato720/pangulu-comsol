# Pangu–COMSOL

通过 cuDSS 接口，将 COMSOL 的稀疏直接线性求解接到 Pangu。COMSOL 继续负责物理场、网格、组装、外层迭代和后处理。

本仓库只保留正式模组源码、CLI、通用配置和客户说明。开发测试、基准、内部报告、运行数据与打包工具不纳入版本管理。厂商 SDK、原始许可和配套运行库另行提供。

## 快速开始

```bash
./pangu-comsol init
./pangu-comsol package install /path/to/pangulu-release.tar.gz
./pangu-comsol license add /path/to/customer.key
./pangu-comsol doctor
./start.sh
```

`start.sh` 保留为一键打开 GUI 的入口，等价于 `./pangu-comsol run`。也可指定模型：

```bash
./start.sh /path/to/model.mph
./pangu-comsol run /path/to/model.mph
```

初始化会查找本机 COMSOL，只在首次创建配置时保存唯一发现的安装；已有配置不会被重置。也可显式指定：

```bash
./pangu-comsol init --comsol /path/to/multiphysics --cuda /path/to/cuda
```

配套组件已预编译时可直接运行。仅有源码时，准备 gcc/g++、CUDA 12.9 开发头文件与库、COMSOL 6.4，然后执行 `./pangu-comsol build`。

## CLI

| 命令 | 用途 |
|---|---|
| `init` | 初始化本模块配置，保留已有选择 |
| `config show` / `config set KEY VALUE` | 查看或修改本模块配置 |
| `run [模型.mph]` | 启动 COMSOL GUI |
| `package status` | 查看当前 SDK 和模组组件 |
| `package install 本地包.tar.gz` | 校验并导入原始 SDK 包 |
| `package update 本地包.tar.gz` | 校验后整体替换已有 SDK，保留许可与配置 |
| `license add 文件.key` | 导入原始签名许可；续期替换同名文件可显式加 `--replace` |
| `license list` / `license use 文件名...` / `license auto` | 列出、选择许可，或自动使用目录内全部许可 |
| `status` / `doctor` | 查看安装状态或执行原生授权/GPU 检查 |
| `inspect` / `configure` / `restore` | 查看、配置或恢复模型的求解器引用 |

所有 SDK 包均由用户自行取得，CLI 只接收本地文件，不假设下载网址。也可关闭进程后手动删除 `pangu/` 内旧发行目录，再解压新原包，无需登记版本或 activate。

## 布局

```text
pangu-comsol          统一 CLI 入口
start.sh              一键启动 GUI
src/python/pangulu_comsol/
  cli.py              命令、配置初始化和本地文件管理
  runtime.py          COMSOL 启动、SDK 校验及组件构建
src/                  C/C++、Java 正式源码和接口声明
config/               通用模板与本机配置
pangu/                一份完整的原始 SDK 解压目录
licence/              客户原始签名许可
runtime/              编译组件和 CUDA 运行库
```

本机配置、SDK、许可和运行产物不进入 Git。启动器保留 COMSOL 默认或用户已有的偏好、临时、恢复、工作区及配置位置，不创建或改写 `.prefs`，线程数只在显式 `--threads` 时指定。

GUI 在兼容求解器树中提供 Pangu 选项，用户自行选择；不自动计算或保存。命令行指定模型时打开工作副本，GUI 内直接打开原文件后保存仍会写入原文件，需要保留原件时使用 Save As。

[使用说明](docs/usage.md) · [客户操作](docs/customer-README.txt) · [接入架构](docs/architecture.md) · [配置字段](config/README.md)
