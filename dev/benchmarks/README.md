# 模型验收与接口审计（不发放）

| 路径 | 用途 |
|---|---|
| `s/run.py` | 启动 S 档验证，记录完成标记、频点、库映射、资源和原始输入哈希 |
| `s/SStudy3.java` | 使用生产 PanguModule 配置并求解，核对 CFD/Mapping、物理 DOF 与四字段参考结果 |
| `s/SavedSResultCheck.java` | 只读检查已保存的 59 频点结果 |
| `compatibility/audit_comsol.py` | COMSOL / 海光所需兼容接口核对 |

S 验收入口为 `dev/tools/run-s.sh`。Pangu 最近状态放 data/runs/latest-s/，原生 cuDSS 对照放 latest-cudss/。新运行目录统一为 s_<时间戳>/。

本目录的 Python / Java 驱动不是客户运行依赖，客户包不包含本目录。旧远端性能脚本及 Slurm 模板已归档为历史开发材料，本次不提交远端作业。
