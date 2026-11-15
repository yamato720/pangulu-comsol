# 运行配置

`distribution.json` 是通用模板，首次配置时复制为 `runtime.json`。后者保存本机路径，不纳入 Git；更新 SDK 不覆盖它。

| 字段 | 含义 |
|---|---|
| `comsol_root` | COMSOL 安装目录；留空时自动查找，找到多份时需填写 |
| `cuda_root` | CUDA 运行库目录；客户包为相对路径 `runtime/cuda` |
| `licensed_gpu_index` | 原始 SDK 已授权 GPU 列表中的索引，默认 0 |
| `ordering` | Pangu 重排序：0=L0，1=L1；复用级别为 L2 |
| `gui_global_options` | 在 GUI 动态提供 Pangu 选项，保留当前选择和已有解，不自动保存 |
| `licence_files` | `licence/` 下的原始许可文件名列表；空列表时读取全部 `.key` / `.license` 文件 |

SDK 固定从 `pangu/` 下唯一完整发行目录读取，许可固定从 `licence/` 读取，不需要 profile 或版本登记。`./update.sh 原包.tar.gz` 整体替换 SDK；也可以关闭进程后手动删除旧发行目录，再将新包解压到 `pangu/`。更新 SDK 不清除许可、配置和运行结果。

启动器不创建或改写 COMSOL 的 `.prefs`，也不重定向偏好、临时、恢复、工作区或插件配置目录。默认和用户已有设置由 COMSOL 自己读取。默认不传 `-np` 或改 OpenMP/BLAS 线程数；显式 `--threads N` 只为本次启动指定线程数，并保留亲和性与动态线程选项。Pangu 库和 GUI 模块只用于当前启动，不写入用户偏好。
