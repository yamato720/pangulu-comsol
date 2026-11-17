GPU驻留纯矩阵对照（内部测试，不随正式应用发放）
================================================
主对照：PanguLU原始SDK的GPU CSC接口，与已校准mycudss-direct调用的NVIDIA cuDSS GPU CSR。
PanguLU配置L0/L2；cuDSS自动排序/分解、hybrid关闭，pivot阈值1、epsilon1e-13。
不启动COMSOL、worker或PanguLU cuDSS适配器。准备/转换/上传/参考RHS均在计时外。
只在计时结束后下载结果并独立验证原系统残差和已知解相对误差。
重分解前使用预先上传的1.00/1.01/0.99倍系数变体，零/非零模式不变。

本次结果目录见 data/runs/latest-matrix-duel.json。
report.txt 为可读报告，report.json 为全部统计与来源，comparison.csv 可直接制表。
每个子目录保存真实库清单、CPU/阶段时钟、GPU活动轨迹和数值检查。
CUPTI使用单调时钟，按GPU区间并集计时，不把重叠内核时间简单相加。

复用：
  python3 -B dev/benchmarks/matrix_duel/run.py run --case s_complex --backend pangu --label your-unique-label
  python3 -B dev/benchmarks/matrix_duel/run.py run --case s_complex --backend mycudss --label another-unique-label
  加 --trace 开启独立GPU活动记录；PanguLU可加 --layout csr 做原生GPU CSR控制。
  analyze.py 接收运行子目录路径，生成分阶段metrics.json。

数据：S捕获复数一般矩阵；48^3 Poisson正定实矩阵；48^3 非对称输运实矩阵。
原始输入、SDK、许可、COMSOL库和正式模组保持，内部源码及数据不纳入Git。
此实验不是当前CLI适配器的整体性能，也不代表任意矩阵/硬件的通用排名。
