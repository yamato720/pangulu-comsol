# 安装与使用

客户版结构和操作见 [客户说明](customer-README.txt)。本模块目前验证 Linux x86_64、COMSOL 6.4、Pangu ABI 2 和 NVIDIA CUDA 12.9。

## 终端应用与安装

在交互终端中执行 `pangu-comsol` 或 `pangu-comsol menu` 进入菜单，保留所有子命令用于自动化。`pipx install .` 或虚拟环境中的 `python -m pip install .` 可安装应用，安装后无需停留在源码目录。

安装版本的 SDK、许可及配置默认位于 `~/.local/share/pangu-comsol/`（或 `$XDG_DATA_HOME/pangu-comsol/`），可用 `--home /path/to/data` 或 `PANGU_COMSOL_HOME` 指定。源码入口 `./pangu-comsol` 默认仍使用本模组目录。Python 应用升级只替换程序代码，不清除这些可变文件。

菜单包含概览、配置、运行、SDK 包和授权页面。F1–F5 切换页面，F6 刷新，F7 检查，Ctrl+C 取消当前操作，Ctrl+Q 退出。SDK 更新和组件构建期间要等待操作完成或取消收尾；COMSOL GUI 独立运行，退出菜单不会关闭已提交的 GUI。

F8 打开可选测试页，手动选择原生 cuDSS、cuDSS 直接封装或 cuDSS worker 封装，可启动测试 GUI 或运行单独提供的 S 数值验收工具。`test status` 查看可用性，`test build [--source 目录]` 构建校准组件；组件位于 `runtime/tests/mycudss/`。测试源码、模型和验收资料单独提供，不随正式应用安装。

测试页也可选择 Pangu，再选择 worker/direct 执行方式。Pangu 组件通过普通 `build` 构建；`test build` 仅构建可选 NVIDIA 校准组件。

```bash
./pangu-comsol test run --backend mycudss-direct /path/to/model.mph
./pangu-comsol test benchmark --backend mycudss-worker \
  --project /path/to/study3-pangulu-s --frequencies all --threads 8 --repeat 2
```

测试 GUI 的模型路径打开工作副本；在模型中使用 Direct/cuDSS。S 档数值验收求解 Study 3，复用已有 CFD/Mapping，核对完成标记、实际库映射、全部频点、有限性、物理 DOF 与四字段 CPU 参考，结果保存到 `data/runs/test_<后端>_<时间戳>/`。封装使用配置所选的 CUDA 目录，并调用同一份原生 NVIDIA cuDSS，独立于 Pangu SDK 与许可。

直接封装转发已观察的 16 个原生接口；worker 封装目前覆盖单 GPU、一般 full CSR、FP64 实数/复数、单 RHS，取消在阶段之间生效。这是已验证的测试契约，其他矩阵类型与高级模式需单独验证。

↑↓ 和 Tab / Shift+Tab 在控件间移动，蓝框及按钮上的 `▶` 标出当前焦点；Enter 执行按钮或打开下拉框，空格切换开关或勾选许可。Esc 回到页签，←→ 切换页面；绿色表示当前页面。输入框的 ←→ 保留文本编辑，下拉框打开后 ↑↓ 选择、Enter 确认、Esc 取消。许可列表内 ↑↓ 移动，到首尾后继续移动可离开列表；行焦点不等于勾选，勾选后仍需“保存选择”。

## 客户使用

原始 SDK 的完整解压目录放在 `pangu/` 中，购买的原始签名许可放在 `licence/` 中。客户包已带预编译的适配库、worker 和 Java 扩展，无需开发编译工具或 `dev/`。

```bash
./start.sh
./start.sh /path/to/model.mph
./pangu-comsol doctor
./pangu-comsol discover
```

Pangu 默认通过独立 worker 执行；可用以下开关选择同进程方式：

```bash
./start.sh --execution direct /path/to/model.mph
./pangu-comsol configure /path/to/model.mph --target sol1/s1/fc1 \
  --output /path/to/model-direct.mph --execution direct --run
./pangu-comsol test benchmark --backend pangu --execution direct \
  --project /path/to/study3-pangulu-s --frequencies all
```

执行方式作用于本次进程，模型中的 Pangu 节点保持标准 Direct 结构。两种方式使用同一份 `libpangu_backend.so`，保持相同矩阵处理及 SDK 数值策略；direct 使用进程内暂存区，向量暂存及输出有限性检查仍保留。

直接模式在 `runtime/launchers/` 生成当前 COMSOL 启动脚本的私有副本，仅省略 `libjsig` 预加载，并用配置中的安装根目录启动。原始 SDK 在存在该预加载时会报告完整性检查失败；此模式不修改或跳过 SDK 授权。它改变了本次 JVM 信号预加载条件，不能据此保证任意插件或故障处理语义等价。默认 worker 使用原始 COMSOL 启动器。会话 `session.json` 记录执行方式、原脚本/私有脚本哈希及实际后端库路径。

`config/runtime.json` 的 `comsol_root` 留空时自动搜索本机安装，路径未找到或有多份时填写具体安装目录。启动器不写 COMSOL 偏好文件，不传偏好、临时、恢复、工作区或配置目录的重定向参数；已有设置由 COMSOL 正常读取。默认继承线程配置及亲和性，只有显式 `--threads N` 才指定本次线程数。`licence_files=[]` 时自动读取 `licence/` 内全部 `.key` / `.license` 原件，以 SDK 要求的逗号分隔格式传入；授权始终由厂商库判断。

## 初始化与管理

```bash
./pangu-comsol init
./pangu-comsol config show
./pangu-comsol license add /path/to/customer.key
./pangu-comsol license list
./pangu-comsol license use customer.key
```

init 不重置现有配置。可用 `--comsol`、`--cuda` 显式设置本次初始化的对应字段。`start.sh` 作为 GUI 快捷入口保留，内部转交 `pangu-comsol run`。

## 整体更新 SDK

关闭本模块启动的 COMSOL 和 worker 后执行：

```bash
./pangu-comsol package update /path/to/pangulu-new-release.tar.gz
```

SDK 包由用户自行下载，CLI 只接收本地文件，不内置下载地址。首次导入使用 `package install`，更新使用 `package update`。先解压到临时目录，检查厂商 `SHA256SUMS` 和 ABI，再替换 `pangu/`。失败恢复旧 SDK，`licence/` 和配置保持原样。也可直接删除 `pangu/` 内旧发行目录，再将新原包解压到 `pangu/`；无需 activate 或修改库路径。每次启动识别实际 SDK 并核对校验和，不依赖安装登记。新 ABI 需要配套适配模块；其他 CUDA/设备版本需单独验证。

## 模型与 GUI

Pangu GUI 在空闲时为各 Study 已生成的兼容求解器树添加标准 Pangu Direct 选项，也识别后来打开的模型和新生成的求解器序列。尚未生成求解器树的 Study 需先生成配置。在 Fully Coupled、Segregated Step 等节点的 Linear solver 中选择 Pangu，Direct 内的后端枚举仍使用 cuDSS 槽位。

注册选项保留原选择、启用状态和已有解，不自动计算、清解或保存。显式 `apply` / `restore` 会切换指定消费者并清除对应 solver 的旧解。

命令行带模型路径启动时，先复制为会话中的工作副本。GUI 内直接打开原文件后按 Ctrl+S 仍会保存新增节点，保留原文件时使用 Save As。保存的是标准 Direct 节点，普通 COMSOL 能读取；普通会话中该节点使用原生 NVIDIA cuDSS，CPU 计算可选择保留的 MUMPS/PARDISO 等节点。

所有 cuDSS 节点在同一 Pangu 进程中都进入适配库；原生 NVIDIA 对照使用独立会话：

```bash
./start.sh --backend cudss /path/to/model.mph
```

会话放在 `data/runs/comsol_<时间戳>/`，`session.json` 保存实际 SDK、worker、库路径和许可哈希；`native-license.log` 保存原生授权与设备检查，`pangu-options.json` / `.log` 保存选项注册状态。判断求解成功还要核对完成标记、有限性与实际后端证据，不能仅看退出码或 MPH 文件。

## 批处理与 Java API

```bash
./pangu-comsol inspect /path/to/model.mph
./pangu-comsol configure /path/to/model.mph \
  --target sol1/s1/fc1 --output /path/to/model-pangu.mph --run
./pangu-comsol restore /path/to/model-pangu.mph \
  --target sol1/s1/fc1 --output /path/to/model-restored.mph
```

目标使用 inspect 输出中 SELECTABLE 标记的实际节点；多个候选需明确指定。输出要求新路径，拒绝覆盖源文件。

Java Shell 或模型方法可导入 `runtime/module/PanguModule.jar` 并使用：

```java
org.pangulu.comsol.PanguModule.inspect(model);
org.pangulu.comsol.PanguModule.targets(model);
org.pangulu.comsol.PanguModule.registerAll(model);
org.pangulu.comsol.PanguModule.apply(model, "sol1/s1/fc1");
org.pangulu.comsol.PanguModule.restore(model, "sol1/s1/fc1");
```

## 从源码构建（可选）

配套运行组件已提供时可直接启动。需要自行构建时，准备 gcc/g++、完整的 CUDA 12.9 开发头文件与库，以及 COMSOL 6.4 安装。Java 编译使用 COMSOL 自带工具。

1. 执行 `./pangu-comsol init` 初始化本机配置；已有配置保留。
2. 设置 `comsol_root` 和 `cuda_root`；构建时 CUDA 目录应包含 `include/` 和 `lib64/`。
3. 将原始 SDK 解压到 `pangu/`，或使用 `./pangu-comsol package install 原始发行包.tar.gz`。
4. 执行：

```bash
./pangu-comsol build
```

源码运行时构建使用 `src/`；安装后的应用使用随包的同一套正式 C/C++、Java 源码。产物写入应用数据目录的 `runtime/module/`。目录中的组件清单用于校验版本、ABI 和文件哈希。运行所需的系统库版本取决于所用构建工具链，应以对应组件的要求为准。

## 适配范围

单 GPU，实数/复数 FP64，CSR 输入转换为 CSC，支持 GENERAL/SYMMETRIC/HERMITIAN/SPD/HPD 及对应存储视图。内部维度与非零元数量限制在 int32 范围；多个 RHS 要求连续列主序。使用 strict 结构检查、L0/L2、hybrid memory never、hybrid compute off。

转置/伴随、FP32、多 GPU、非连续多 RHS 等未实现路径会拒绝。cuDSS 的重排序、主元和诊断策略不保证与 Pangu 一一对应。其他数值模式需按对应模型和功能验证。
