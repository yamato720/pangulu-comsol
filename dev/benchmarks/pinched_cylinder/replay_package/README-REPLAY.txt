圆柱薄壳受压 / Pinched cylinder：可迁移复跑包
============================================

本次原实验在本机 WSL Linux x86_64 中运行：
  GPU：NVIDIA GeForce RTX 4080 Laptop GPU，12 GiB，驱动 621.14。
  原工作目录：/home/lwf/comsol-projects/pangulu-comsol
  原结果目录：data/runs/pinched-cylinder_20261116T184505
  CPU线程数：8。原始Pangu26.46 ABI2、CUDA12.9、NVIDIAcuDSS0.7.1。

两个包解压到同一个目录，会合并成 pinched-cylinder-replay/：
  pinched-cylinder-code.tar.gz：独立入口、C++源码、原模型和上游源码、计时方法。
  pinched-cylinder-data.tar.gz：605阶小矩阵、1017005阶完整矩阵、原始点载荷、
                              CPU MUMPS参考解、原始结果/日志/追踪和失败记录。
无需复制原来的pangulu-comsol仓库，也不需要COMSOL或MOOSE才能重放已有矩阵。

1. 准备目标机器的依赖
-------------------
  Linux x86_64（原生Linux或WSL），NVIDIA GPU及正常的CUDA驱动。
  Python3.10以上、g++（支持C++17）。矩阵复跑只用Python标准库。
  原厂Pangu SDK：解压后有 include/pangulu.h、lib/libpangulu.so，ABI必须为2。
  CUDA12.9开发环境：include/及lib64/（或lib/）中有runtime、cuBLAS。
  NVIDIA cuDSS0.7.1：原始共享库及同版本官方头文件。
  目标设备对应的原始Pangu许可证；新GPU通常需要它自己的许可证。
  可选：CUDA12.9 CUPTI开发组件，用于GPU kernel活动追踪。

SDK、CUDA/cuDSS原厂文件、设备许可证和本机编译产物不放进这两个包。
这些依赖由目标机器提供；原实验版本/库哈希在 evidence/original-run/ 中。
若目标机器有COMSOL6.4，可使用它安装目录ext/cudss/glnxa64下的0.7.1共享库；
头文件仍需来自匹配的cuDSS开发包，或你已有的合法安装。

本实验是单GPU测试。Pangu使用“授权GPU列表索引0”，原生cuDSS使用CUDA逻辑设备0。
请使两者指向同一张卡；运行结果记录实际CUDA设备UUID，不同UUID会使比较失败。
集群分配了CUDA_VISIBLE_DEVICES时保留它；程序不会修改GPU身份、驱动或系统设置。

2. 解压、配置、编译
-----------------
在两个压缩包所在目录执行：

  sha256sum -c SHA256SUMS
  tar -xzf pinched-cylinder-code.tar.gz
  tar -xzf pinched-cylinder-data.tar.gz
  cd pinched-cylinder-replay
  python3 replay.py verify

以下/path/to/替换为目标机器的实际路径（不要照抄原机器的路径）：

  python3 replay.py configure \
    --sdk /path/to/extracted-pangulu-sdk \
    --cuda /path/to/cuda-12.9 \
    --cudss-library /path/to/libcudss.so.0.7.1 \
    --cudss-include /path/to/cudss/include \
    --licence /path/to/device.license.key \
    --threads 8

若需要GPU追踪，在configure命令中再加：
    --cupti /path/to/cupti
该目录内需有include/cupti.h及lib/libcupti.so.12（或lib64/）。
可用 --library-path /path/to/extra/lib 补充正常的第三方依赖搜索路径。

  python3 replay.py build

configure只写本包config.local.json；build只写本包build/。
不会下载/安装软件，不会改COMSOL设置、用户shell配置或原厂SDK/许可证。
换机器后重新configure和build；库或配置改变后也重新配置和编译。
不要把原机器的config.local.json、build/直接搬过去代替编译。

3. 先小规模验证，再跑完整百万阶
---------------------------

  python3 replay.py run --case small --repeat 1 --label small-check
  python3 replay.py run --case million --repeat 2 --label million-compare

默认依次比较：
  pangu-L0：原始Pangu SDK，GPU CSC，L0排序/L2复用。
  mycudss：已有校准的直接转发封装，最终调用你配置的原始NVIDIA cuDSS。

原案例的L1配置可单独选择：
  python3 replay.py run --case million --backends pangu-L1 --repeat 2 --label original-L1
  pangu-L1使用L1排序/L2复用，与公开案例的选择一致。

可额外核对完全直接链接的原生cuDSS：
  python3 replay.py run --case million --backends cudss mycudss --repeat 2 --label native-vs-forward

--label必须是新名字，已有输出目录不会覆盖。顺序在相邻重复间反转。
全部操作串行，避免两个求解器争用GPU。
无追踪单次上限默认180秒，可通过--timeout调整。

配置了CUPTI后，可单独测GPU kernel时间：
  python3 replay.py run --case million --backends pangu-L0 mycudss \
    --repeat 1 --trace --label gpu-activity

原机器上L1的GPU追踪在显存接近满载时超过300秒未完成；无追踪L1曾成功完成。
因此追踪默认不启用，也不把追踪等待当成正常求解性能。--trace-timeout可调整上限。
GPU追踪有测量开销；不要把追踪墙钟与无追踪墙钟混着平均。

迁移包复验又记录到一次百万阶无追踪L1超过180秒未完成，显存接近耗尽；
同环境的原实验二进制也未在30秒诊断上限内完成。L1有实际成功记录，也有资源敏感的
未完成记录，不能把其成功时间当作稳定保证。默认先比较L0/mycudss，L1显式选择。
这些新记录保存在evidence/portability-validation中，未删掉失败结果或放宽误差阈值。

4. 原始HOST封装对照
------------------

  python3 replay.py host-control --case million

此命令直接调用原公开项目中未修改的adapter/pangulu_solver.hpp，使用HOST CSC矩阵，
不启动MOOSE，核对原始点载荷和MUMPS参考线性解。结果也在runs/下。
这个原始适配器保留了Pangu26.46版本检查；主矩阵入口则按ABI2编译。
若要从物理模型重新组装矩阵，则需要MOOSE/PETSc/MPI/libMesh等额外依赖：
  参见upstream/MOOSE-with-Solved-Tech/docs/BUILD-AND-RUN.zh-CN.md，
  以及upstream/MOOSE-with-Solved-Tech/cases/pinched-cylinder/。
原脚本run_case.py可以使用目标机器已安装的合适MOOSE程序运行该案例。

5. 数据格式和计时口径
-------------------
  data/matrices/million：n=1017005，存储项45630025，实数FP64通用LU。
  ptr.u64：CSC列指针，little-endian uint64，n+1项，零起始。
  idx.i32：CSC行索引，little-endian int32，nnz项，零起始且逐列有序。
  values.bin：little-endian float64，nnz项，保留全部显式零。
  physical_rhs.bin：原始点载荷，little-endian float64，n项。
  application_linear_solution.bin：原始CPU MUMPS参考线性解，float64，n项。

两边使用同一矩阵、同一原始载荷；Pangu用CSC、cuDSS用等价CSR。
GPU上传、格式转换、制造已知解RHS、结果下载和独立校验均在阶段计时之外。
分析阶段包含厂商自己的CPU排序/预处理；GPU kernel时间单独报告。
核心合计=分析+首次数值分解+首次原始物理载荷单RHS求解。
之后再解一个已知解RHS作数值校验，不计入核心合计；最终实验不做重分解。

每次要求：完成标记、正确库映射、输出有限、原系统相对残差<=1e-8、
已知解相对L2误差<=1e-6、原始载荷解相对MUMPS参考L2误差<=1e-6。
GPU追踪还要求完整结束且丢失0条。退出码0或产生文件本身不代表成功。
新入口还核对实际CUDA UUID，避免不同后端用了不同GPU。

6. 输出及原始记录
---------------
  runs/<label>/summary.txt / summary.json / comparison.csv：目标机器的新对比。
  每次子目录：stdout.log、stderr.log、command.json、result.json、metrics.json，
              physical_solution.bin；追踪时另有activities-*.jsonl。
  runs/<label>/provenance.json：本次实际SDK/许可/库/编译产物哈希和路径。
  evidence/original-run/report.txt：原机器的最终结果和限制。
  evidence/original-run/：原始试跑成功/失败记录、完整物理场、版本/哈希等证据。
  manifests/data-selection.json：打包取舍；仅省去安装缓存、完整环境、检查点、
                                 本机二进制及一份已核对相同的重复原始矩阵。
  provenance/original-scripts/：原机器使用的调查脚本，仅供溯源，保留历史路径。
  replay.py及src/：真正的可迁移入口，不依赖这些历史路径。

原MOOSE完整应用曾有一次80.7秒的分解观察；同一未修改HOST封装独立重放约1.92秒。
应用环境或显存/上下文/负载影响尚未完全隔离；该观察没有混入正常纯矩阵均值。
本机MOOSE/PETSc和Pangu SDK原包哈希与案例文档不同；不宣称严格复现原图数值。
性能不设预期速度门槛；换GPU、SDK版本、驱动后，记录实测结果即可。

原案例来源：
  https://github.com/solved-tech-dev/case-studies/tree/main/MOOSE-with-Solved-Tech
固定案例仓库提交：acce983316ed368f7dd260d36b2a2329907ea208
上游AGPL/LGPL许可及版权说明保留在upstream/MOOSE-with-Solved-Tech中。
