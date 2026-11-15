Pangu–COMSOL 使用说明
==========================

目录
----
start.sh          启动 COMSOL GUI
update.sh         从原始发行包整体更新 Pangu SDK
pc.py             启动与配置入口
pangu/            一份原始 Pangu 发行包的完整解压目录
licence/          统一存放本机购买的原始许可文件
config/           COMSOL 安装位置及运行设置
runtime/module/   已编译适配库、worker、Java 扩展和原生环境检查
runtime/cuda/     随包提供的 NVIDIA CUDA 12.9 数学运行库及原始许可说明

首次使用
--------
1. 本机已有 COMSOL 6.4、NVIDIA 驱动、Python 3.12 或更高版本。
   支持 Linux x86_64；预编译组件的具体系统库要求见配套说明。
   不包含 COMSOL 安装程序。
2. 将原始签名 .key 文件放进 licence/。通用包不带其他机器的授权。
3. ./start.sh
   启动器自动查找常见位置和 PATH 中的 COMSOL 安装。
   找不到或找到多个安装时，在 config/runtime.json 中填写 comsol_root。
   可用 python3 -B pc.py discover 查看发现的安装目录。
4. 打开模型，在 Fully Coupled 等兼容节点的 Linear solver 中选择 Pangu。
   启动和计算前使用原始厂商 SDK 检查许可及 GPU；错误记录在本次会话目录。

已提供预编译组件，正常启动及同 ABI 的 SDK 更新无需编译器、开发源码或测试脚本。

整体更新 Pangu
--------------
先关闭本模块启动的 COMSOL 窗口及求解进程，然后执行：

  ./update.sh /path/to/pangulu-new-release.tar.gz

更新器先在临时目录解压并检查原始 SHA256SUMS 和 Pangu ABI，成功后替换 pangu/。
更新失败保留原 SDK；licence/、config/ 和已有运行结果不随 SDK 替换。
不需要 activate、登记版本或修改库路径。成功后重新启动 COMSOL。

也可手动删除 pangu/ 内旧的完整解压目录，再把新发行包解压到 pangu/。
该目录内保持恰好一份 SDK；启动器直接识别其中 include/pangulu.h 和 lib/libpangulu.so。
每次启动重新核对原始 SDK 校验和，不依赖安装登记文件。

本模块支持 Pangu ABI 2，随包 CUDA runtime 为 12.9。新 ABI 或其他 CUDA/硬件版本需配套模块。
原始 SDK 自带的文档、第三方声明和样例保持厂商发行包原样。

其他命令
--------
  python3 -B pc.py doctor
  ./start.sh /path/to/model.mph
  ./start.sh --backend cudss /path/to/model.mph
  python3 -B pc.py inspect /path/to/model.mph
  python3 -B pc.py configure /path/to/model.mph --target sol1/s1/fc1 --output /path/to/model-pangu.mph

带模型路径启动时，先复制到 data/runs/comsol_<时间戳>/models/ 后打开工作副本。
全局选项注册保留当前选择、启用状态和已有解，不自动计算或保存。
GUI 内直接打开原文件后按 Ctrl+S 仍会保存原文件，需要保留原件时使用 Save As。
模型使用标准 Direct 节点，普通 COMSOL 能读取；普通会话中该节点按原生 cuDSS 配置运行。

运行输出在首次使用时创建于 data/runs/；每次会话的 session.json 记录 SDK、worker、库和许可哈希。


设置继承
--------
启动器不创建或改写 COMSOL 偏好文件，偏好、临时、恢复、工作区和配置目录均按默认或已有设置使用。
线程数不默认覆盖，仅显式 --threads N 指定本次线程数；已有亲和性选项保留。
Pangu 库和 GUI 模块仅在当前进程加载，不写入 COMSOL 安装或用户偏好。
COMSOL 自身正常保存界面状态和用户操作的行为保持原样。
