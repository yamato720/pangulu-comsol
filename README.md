# Pangu–COMSOL

通过 cuDSS 接口，将 COMSOL 的稀疏直接线性求解接到 Pangu。COMSOL 继续负责物理场、网格、组装、外层迭代和后处理。

本仓库只保留正式模组源码、启动器、通用配置和客户使用说明。开发测试、基准、内部报告、运行数据和打包工具不纳入版本管理。厂商 SDK、客户许可及配套运行库另行提供。

## 内容

```text
start.sh             GUI 启动入口
update.sh            从原始发行包整体更新 SDK
pc.py                启动、配置、许可检查及正式组件构建
src/                 正式 C/C++、Java 源码和 cuDSS 接口声明
config/              通用配置模板及字段说明
licence/README.txt   许可文件放置说明
docs/                使用说明与接入架构
```

运行时使用 `pangu/` 中的一份原始 SDK、`licence/` 中的客户原始许可，以及 `runtime/` 中的接入组件与 CUDA 运行库。这些本机文件不进入 Git。

## 使用

首次配置时，将 `config/distribution.json` 复制为 `config/runtime.json`，填写本机 COMSOL 和 CUDA 位置。`comsol_root` 留空时会自动搜索安装；找到多份时需指定路径。

```bash
cp config/distribution.json config/runtime.json
./update.sh /path/to/pangulu-release.tar.gz
python3 -B pc.py license /path/to/customer.key
./start.sh
./start.sh /path/to/model.mph
```

配套组件已预编译时可直接运行；需要从本仓库源码构建时，准备 C/C++ 编译器、CUDA 12.9 开发头文件与库、COMSOL 6.4，然后执行 `python3 -B pc.py build`。具体步骤见 [使用说明](docs/usage.md)。

GUI 在兼容求解器树中提供 **Pangu** 选项，用户选择后使用。注册选项保留当前求解器选择和已有解，不自动计算或保存。命令行指定模型时先打开工作副本；GUI 内直接打开原文件后保存仍会写入原文件，需要保留原件时使用 Save As。

启动器沿用 COMSOL 默认或用户已有的偏好、临时、恢复、工作区及配置位置，不创建或重写 `.prefs`。线程数仅在显式 `--threads` 时为本次启动指定。

## 更新 SDK

关闭本模块启动的 COMSOL 和 worker 后执行：

```bash
./update.sh /path/to/new-pangulu-release.tar.gz
```

更新器校验原始 `SHA256SUMS` 和 ABI，再整体替换 `pangu/`，许可、配置和运行结果保留。也可手动删除旧 SDK 解压目录，再将新原包解压到 `pangu/`，无需版本登记或 activate。

[客户使用说明](docs/customer-README.txt) · [批处理与 Java API](docs/usage.md) · [接入架构](docs/architecture.md) · [配置字段](config/README.md)
