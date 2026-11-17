圆柱薄壳受压 / Pinched cylinder：内部复测
======================================
来源：
  https://github.com/solved-tech-dev/case-studies/tree/main/MOOSE-with-Solved-Tech
固定案例仓库提交：acce983316ed368f7dd260d36b2a2329907ea208
原始输入、网格、细分程序、PETSc接入程序按Git blob SHA及SHA256校验。
所有测试依赖、模型、矩阵和日志位于 data/runs/pinched-cylinder_20261116T184505。
本目录和数据均被Git忽略，不属于正式客户应用；不改用户COMSOL设置或原SDK/许可。

方法：
1. 使用官方预编译 MOOSE 2026.11.13，私有环境位于测试目录 moose-env。
   未安装到系统，未运行shell init，未修改用户conda配置。
   注意其MOOSE提交08315af82e、PETSc3.26.0与原案例锁定提交/文档版本不同。
2. 原始QUAD4网格按原脚本 factor=45 细分，不做额外曲面投影。
   203401节点、202500壳单元、五个变量，1017005阶、45630025存储项。
3. 完整应用以CPU MUMPS和原始PanguLU PETSc插件分别求解，检查五个完整物理场。
   附加标准PETSc二进制viewer导出A/b/x，原始输入文件保持不变。
4. 读取第一组完整A/b/x，保留完整图及显式零项，并独立检查MUMPS解的残差。
   convert.py生成GPU测试的标准CSC以及原始点载荷RHS。
5. 主测试直接使用原PanguLU SDK GPU CSC L1/L2，比较mycudss-direct转发的真实
   NVIDIA cuDSS0.7.1 GPU CSR，双方均为FP64实数通用LU。没有COMSOL/worker/IPC。
   数据转换、三组系数(1.00/1.01/0.99)、真解/RHS生成和上传不计入阶段时间。
   双方共用同一策略：只保留一份GPU系数缓冲区；最终测试采用原始单RHS负载。
   每次先分析/分解并求解原始物理载荷，再做一个已知解RHS检查；不做数值重分解。
   物理载荷求解在首分解之后首先执行，保留其首次调用成本。
6. 按原CSC和RHS在CPU独立重算残差；已知解相对L2<=1e-6、残差<=1e-8。
   原始载荷结果额外与MUMPS线性解比较，相对L2<=1e-6。
   真实已映射库、完成标记、数值检查、GPU追踪零丢失共同判定成功。
7. PanguLU L0、L1及cuDSS各两次无追踪；L0/cuDSS各完成一次CUPTI追踪。
   L1的8RHS诊断追踪在约200秒中止，原始单RHS追踪也未在300秒内完成，均保留失败记录。
   不把异常追踪的等待时间混入正常性能。GPU时间取区间并集，不重复累计重叠。
   排序/分析包含厂商CPU工作，分别列出；原生阶段墙钟与GPU kernel时间分别列出。

复跑已准备的矩阵（label必须是新的名字）：
  python3 -B dev/benchmarks/pinched_cylinder/bench.py run --case million --backend pangu --reorder L0 --label your-pangu-run
  python3 -B dev/benchmarks/pinched_cylinder/bench.py run --case million --backend mycudss --label your-cudss-run
加 --trace 得到分阶段GPU活动；L1追踪在本机显存接近满载时未完成，限制300秒并记录失败。
加 --reorder L1 核对原案例的匹配/缩放配置。默认不重分解；--refactors 可调整。
无追踪上限180秒。数据、SDK和原始许可沿当前模组配置读取。
不需要安装MOOSE即可重放已有矩阵，使用当前模组原始SDK/CUDA/许可和已校准mycudss。

文件：
  bootstrap.py / remote-files.json：固定来源和下载校验。
  run_application.py：调用未修改的上游运行器，附加PETSc矩阵/RHS/解导出。
  convert.py：PETSc32/64位整数二进制读取，保留显式零的CSR->CSC。
  bench.py：编译原生对照并运行单个测试。
  suite.py：串行交替测试和进度记录。
  finish_limited.py：首次调查时限制异常追踪并完成独立控制的记录；正常复跑不需要此脚本。
  original_host.cpp：直接调用未修改的上游HOST CSC适配器，排除MOOSE应用环境。
  report.py：原系统/已知解/CPU物理解对照、统计和最终报告。
  data/runs/latest-pinched-cylinder.json：本次结果指针。
  data/runs/pinched-cylinder_20261116T184505/report.txt：结果说明。
  同目录report.json / comparison.csv：完整统计及表格。

条形图中的原始硬件、cuDSS配置和原始矩阵未在公开仓库提供；本次是当前本机环境
对公开模型重新组装后复测，不能宣称严格复现图中的1.11秒/11.44秒。
上游源码许可保留在 sources/MOOSE-with-Solved-Tech/LICENSE 和 LICENSE-NOTICE.md。
