Pangu–COMSOL 使用说明
====================

入口
----
pangu-comsol 是统一 CLI。start.sh 保留为一键 GUI 入口，内部调用 CLI 的 run 命令。
需要 Python 3.12+、COMSOL 6.4、适配的 NVIDIA 驱动和原始许可。
Linux x86_64 预编译组件的系统库要求以配套组件说明为准。

首次使用
--------
  ./pangu-comsol init
  ./pangu-comsol package install /path/to/pangulu-release.tar.gz
  ./pangu-comsol license add /path/to/customer.key
  ./pangu-comsol doctor
  ./start.sh

init 初始化本模块 config/runtime.json，已有配置保持原样。
首次使用会查找 COMSOL，找到多份或没有找到时可显式设置：
  ./pangu-comsol init --comsol /path/to/multiphysics --cuda /path/to/cuda

配套组件已预编译时无需编译器。只有源码时，准备 CUDA 开发头文件和库、gcc/g++，
执行 ./pangu-comsol build。模组不包含 COMSOL 安装程序。

运行
----
  ./start.sh /path/to/model.mph
  ./pangu-comsol run /path/to/model.mph
  ./pangu-comsol run --backend cudss /path/to/model.mph

在 Fully Coupled 等兼容节点的 Linear solver 中选择 Pangu。
选项注册保留当前选择和已有解，不自动计算或保存。
带模型路径启动时打开 data/runs/ 内的工作副本；GUI 内直接打开原文件后保存仍会写回原文件。

SDK 包管理
----------
发行包由用户自行下载或从供货方取得，CLI 不内置下载网址，只接收本地原始文件。
  ./pangu-comsol package status
  ./pangu-comsol package update /path/to/new-pangulu-release.tar.gz

更新前关闭本模块启动的 COMSOL 和 worker。先检查原始 SHA256SUMS 与 ABI，
再整体替换 pangu/。失败恢复旧 SDK；licence/、配置和运行结果保持原样。
也可手动删除 pangu/ 内旧发行目录，再解压新原包；保持其中恰好一份完整 SDK。

许可管理
--------
  ./pangu-comsol license add /path/to/customer.key
  ./pangu-comsol license list
  ./pangu-comsol license use customer.key
  ./pangu-comsol license auto
  ./pangu-comsol license add /path/to/renewed/customer.key --replace

CLI 只管理原始签名文件，保持其字节不变；授权由厂商 SDK 校验。
license use 指定已导入文件的文件名，多个名称用空格分隔。
license auto 使用 licence/ 中全部 .key / .license 文件；传给 SDK 时按厂商要求使用逗号分隔。
默认不覆盖不同内容的同名文件，续期替换须显式使用 --replace。

配置与检查
----------
  ./pangu-comsol config show
  ./pangu-comsol config set comsol_root /path/to/multiphysics
  ./pangu-comsol config set gui_global_options false
  ./pangu-comsol status
  ./pangu-comsol doctor

这些命令只管理本模块配置，不写 COMSOL 偏好文件，不重定向已有目录设置。
线程数默认继承原设置，仅 run --threads N 时指定本次线程数。
COMSOL 自身正常保存界面状态和用户操作的行为保持原样。
