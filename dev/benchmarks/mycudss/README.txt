可选 cuDSS 校准工具
==================
本目录由 CLI 的 test 功能调用；源码在 develop 开发分支维护，不进入 main 或 Python 安装包。
源自 study3-pangulu-s/mycudss 的 v1_native_equivalent，原工程保持只读。

三个可手动选择的后端：
  cudss             COMSOL 随附的原生 NVIDIA cuDSS 0.7.1
  mycudss-direct    16 个 ABI 接口原样转发到同一份原生库
  mycudss-worker    CSR暂存、共享内存、socket 与独立worker，再调用同一份原生库

在当前应用目录执行：
  ./pangu-comsol test build
  ./pangu-comsol test status
  ./pangu-comsol test run --backend mycudss-worker /path/to/model.mph
  ./pangu-comsol test benchmark --backend mycudss-direct --project /path/to/study3-pangulu-s --frequencies all

GUI 测试页为 F8，可选后端、启动 GUI 或运行 S 档 Study3 数值验收。
组件独立放 runtime/tests/mycudss/，使用配置中的 COMSOL 与 CUDA 路径。
S 验收使用输入模型中的 CFD/Mapping 已有解，结果写入新的 data/runs/test_* 目录。
原始模型和正式 Pangu 组件不覆盖；每次记录实际库与worker映射、有限性、频点和四场CPU参考比较。
worker 覆盖单 GPU、一般full CSR、FP64实/复数与单RHS；取消在阶段之间生效。

本地 suite.py 按原生/直接/worker/worker/直接/原生运行两轮全59频点，再直接比较原生保存结果。
它是本次内部校准驱动，不作为通用客户工具或自动下载入口。
