# 运行配置

`distribution.json` 是通用模板，`pangu-comsol init` 据此创建本机 `runtime.json`。已有配置不会被重置，仅显式选项修改对应字段；本机配置不纳入 Git。

```bash
./pangu-comsol init --comsol /path/to/multiphysics --cuda /path/to/cuda
./pangu-comsol config show
./pangu-comsol config set gui_global_options false
```

| 字段 | 含义 |
|---|---|
| `comsol_root` | COMSOL 安装目录；留空时自动查找，多份安装时需指定 |
| `cuda_root` | CUDA 运行库目录；配套目录一般为 `runtime/cuda` |
| `licensed_gpu_index` | 原始 SDK 已授权 GPU 列表中的索引，默认 0 |
| `ordering` | Pangu 重排序：0=L0，1=L1；复用级别为 L2 |
| `gui_global_options` | 在 GUI 动态提供 Pangu 选项，不自动选择、计算或保存 |
| `licence_files` | `licence/` 下的原始许可文件名列表；空列表时读取全部 `.key` / `.license` |

`config set` 的数值、布尔和列表值使用 JSON 写法，如 `0`、`false`、`["customer.key"]`。许可选择也可直接使用 `license use customer.key` 或 `license auto`。

SDK 位于 `pangu/` 下唯一完整发行目录，许可独立放在 `licence/`。CLI 的包管理只接收用户提供的本地发行包；SDK 更新不清除许可、配置和运行结果，手动解压也可直接识别。

本模块配置与 COMSOL 自身偏好分开。启动器不创建或改写 `.prefs`，不重定向偏好、临时、恢复、工作区或配置目录。线程数只在显式 `--threads` 时指定；已有亲和性与动态线程选项保留。
