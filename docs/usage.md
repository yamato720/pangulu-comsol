# 安装与使用

客户版结构和操作见 [客户说明](customer-README.txt)。本模块目前验证 Linux x86_64、COMSOL 6.4、Pangu ABI 2 和 NVIDIA CUDA 12.9。

## 客户使用

原始 SDK 的完整解压目录放在 `pangu/` 中，购买的原始签名许可放在 `licence/` 中。客户包已带预编译的适配库、worker 和 Java 扩展，无需开发编译工具或 `dev/`。

```bash
./start.sh
./start.sh /path/to/model.mph
python3 -B pc.py doctor
python3 -B pc.py discover
```

`config/runtime.json` 的 `comsol_root` 留空时自动搜索本机安装，路径未找到或有多份时填写具体安装目录。启动器不写 COMSOL 偏好文件，不传偏好、临时、恢复、工作区或配置目录的重定向参数；已有设置由 COMSOL 正常读取。默认继承线程配置及亲和性，只有显式 `--threads N` 才指定本次线程数。`licence_files=[]` 时自动读取 `licence/` 内全部 `.key` / `.license` 原件，以 SDK 要求的逗号分隔格式传入；授权始终由厂商库判断。

## 整体更新 SDK

关闭本模块启动的 COMSOL 和 worker 后执行：

```bash
./update.sh /path/to/pangulu-new-release.tar.gz
```

先解压到临时目录，检查厂商 `SHA256SUMS` 和 ABI，再替换 `pangu/`。失败恢复旧 SDK，`licence/` 和配置保持原样。也可直接删除 `pangu/` 内旧发行目录，再将新原包解压到 `pangu/`；无需 activate 或修改库路径。每次启动识别实际 SDK 并核对校验和，不依赖安装登记。新 ABI 需要配套适配模块；其他 CUDA/设备版本需单独验证。

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
python3 -B pc.py inspect /path/to/model.mph
python3 -B pc.py configure /path/to/model.mph \
  --target sol1/s1/fc1 --output /path/to/model-pangu.mph --run
python3 -B pc.py restore /path/to/model-pangu.mph \
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

1. 复制 `config/distribution.json` 为 `config/runtime.json`。
2. 设置 `comsol_root` 和 `cuda_root`；构建时 CUDA 目录应包含 `include/` 和 `lib64/`。
3. 将原始 SDK 解压到 `pangu/`，或使用 `./update.sh 原始发行包.tar.gz`。
4. 执行：

```bash
python3 -B pc.py build
```

构建只使用 `src/`，产物写入 `runtime/module/`。目录中的组件清单用于校验版本、ABI 和文件哈希。运行所需的系统库版本取决于所用构建工具链，应以对应组件的要求为准。

## 适配范围

单 GPU，实数/复数 FP64，CSR 输入转换为 CSC，支持 GENERAL/SYMMETRIC/HERMITIAN/SPD/HPD 及对应存储视图。内部维度与非零元数量限制在 int32 范围；多个 RHS 要求连续列主序。使用 strict 结构检查、L0/L2、hybrid memory never、hybrid compute off。

转置/伴随、FP32、多 GPU、非连续多 RHS 等未实现路径会拒绝。cuDSS 的重排序、主元和诊断策略不与 Pangu 一一对应，限制见 [接口核对](compatibility/comsol-dcu.txt)。其他数值模式需按对应模型和功能验证。
