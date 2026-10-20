# CEST Studio

CEST / APT MRI 数据处理与可视化的 **桌面工具**。把一条完整的 CEST 处理链收进一个图形界面：
**DICOM 重建 → 掩模与器官 ROI → Z 谱后处理 → 逐体素 LD 拟合 → PRF 提取与随机森林分析**，
参数全部摆在界面上，不用再改脚本、不依赖 MATLAB。

- 平台：**Windows 10 / 11（64 位）**
- 形态：**免安装、免联网**（便携版自带 Python 与全部依赖）
- 算法口径：与参考 MATLAB 实现逐位对拍（见各模块实测记录）

---

## 一、下载即用（推荐 —— 不需要装 Python）

1. 打开本仓库右侧的 **Releases** 页；
2. 下载 **`CEST-Studio-v1.0.0-win64.zip`**（约 470 MB，内含 `runtime\` 便携 Python 3.10 + 全部依赖库）；
3. 解压到任意本地目录（路径带中文也可以；**建议不要放在网盘同步目录里**）；
4. 双击解压出来的 **`启动 CEST Studio.vbs`** —— 程序启动，**无黑色窗口**。

> 解压后请**保持目录结构不变**：`runtime\`、`CEST_Studio\`、`03_Python_全链路\` 必须和
> `启动 CEST Studio.vbs` 在同一层。整个文件夹一起拷/移即可。

首次启动会让你指定一个 **输出总目录（PROJ）**，之后所有结果都写在它下面。

排错入口：双击 **`启动 CEST Studio（调试）.bat`**（带控制台，能看到全部输出），
或查看 `CEST_Studio\logs\gui_<日期>.log`。

---

## 二、功能模块

界面左侧按处理顺序排列，①→⑧ 串成一条链：

| 序 | 模块 | 作用 | 主要产物（写在 PROJ 下） |
|:--:|---|---|---|
| ⓪ | 导入重建 | 已有 s0 / Z 谱 / w 的 `.mat`，直接组装成 ① 的结构，不用碰 DICOM | `01_重建\<病例>\` |
| ① | DICOM 重建 | 序列自动匹配、3D/4D 判定、频率表确认 → 出 S0 / Z 谱 / w | `01_重建\<病例>\` |
| ② | 多层图谱展示 | raw / ST / APTW / LD 拼图，静态出图 + 交互预览（单层 / 逐层 / 特定频偏） | `02_展示\<病例>\` |
| ③ | CEST 后处理 | Z 谱插值、B0 校正（不校正 / B0 图谱 / 自校正）、逐层落盘；可批量全病例；顺带出 ROI 定量曲线 | `03_CEST后处理\<病例>\` |
| ④ | 逐体素 LD | 洛伦兹拟合，拟合参数全在界面（fast / exact 两种） | `04_逐体素LD\<病例>\` |
| ⑤ | PRF 提取 | 训练集提取：组内·按组 / 组内·单个 / 组间对比，左右脑分开 | `05_PRF提取\` |
| ⑥ | PRF 分析 | 随机森林特征重要性（组内 / 单个对比 / 组间），出图 + Excel | `06_PRF分析\` |
| ⑦ | 诊断自检 | 数据链自检 | — |
| ⑧ | 去噪掩模 + 器官 ROI | 交互画布手画掩模与 ROI（矢量多边形 + 画笔） | `00_掩模\<病例>\` |

> **设计原则**：**缺掩模 / 缺数据时直接拦下并红字提示去哪补**，
> 不自动生成掩模、不自动估 σ、不自动切色图 —— 这些必须人工决定。

---

## 三、从源码运行（开发者）

前提：**Python 3.10（64 位）**。

```bat
git clone https://github.com/<你的账号>/CEST-Studio.git
cd CEST-Studio
pip install -r requirements.txt
```

然后：

- 双击 **`CEST_Studio\启动.vbs`**（会自动在常见位置寻找本机 Python；或用下面的命令行）；
- 命令行（调试 / 自动化）：

```bat
python CEST_Studio\app\main.py --open <输入目录> --proj <输出目录> --nav 1
```

> 源码仓库里 **不含 `runtime\`**（见 `.gitignore`）。想验证"免安装"形态，请用 Releases 里的完整包。

自检脚本（每模块一份，跑完 `EXIT 0` 即通过）：

```bat
python CEST_Studio\tools\validate_mask.py
python CEST_Studio\tools\validate_roi.py
python CEST_Studio\tools\validate_display.py
...
```

---

## 四、目录结构

```
cest-gui/
├─ 启动 CEST Studio.vbs          ← 终端用户双击这里（无黑窗口，用包内 runtime）
├─ 启动 CEST Studio（调试）.bat   ← 排错用（带控制台）
├─ runtime\                      ← 便携 Python 3.10 + 全部依赖（仅免安装包内含，不入 Git）
├─ 03_Python_全链路\函数库\        ← 核心算法层（cest_io / cest_recon / cest_post /
│                                    cest_ld / cest_display / cest_roi / cest_group /
│                                    prf_extract / prf_analysis / dicom_order / run_log）
├─ CEST_Studio\                  ← 界面外壳（PySide6）
│   ├─ app\                      ← 主窗口 / 页面 / 模块适配 / 对话框 / 控件
│   ├─ detect\                   ← 序列匹配、数据链探测、手动重建
│   ├─ jobs\                     ← 任务线程
│   ├─ config\                   ← 设置读写（settings.json 不入库）
│   ├─ tools\                    ← 自检脚本 validate_*.py
│   └─ 启动.vbs                   ← 开发者入口（自动寻找本机 Python）
├─ requirements.txt              ← 依赖清单（已 pin 版本）
├─ 使用说明.txt / 开发说明.txt
└─ README.md
```

---

## 五、数据与目录约定

`CEST_Studio` 只负责界面与编排；**算法都在 `03_Python_全链路\函数库\`**（同一份真源，不复制）。

输出总目录（PROJ）由界面指定，各模块自动建子目录：

```
PROJ\
├─ 00_掩模\<病例>\        Mask / Thmask / ROI_total / ROI<k> / mask_check.png
├─ 01_重建\<病例>\        s0 / normZ / ssat / w_offset ...
├─ 02_展示\<病例>\
├─ 03_CEST后处理\<病例>\   {full|pos|neg}_interp_<后缀>\sliceN\...
├─ 04_逐体素LD\<病例>\
├─ 05_PRF提取\            组内_按组\ | 组内_单个\ | 组间\训练集最终版\
├─ 06_PRF分析\
└─ 日志\                  <模块>_<时间戳>.log
```

同名重跑会先自动备份为 `<名>.bak_<时间戳>`，不覆盖旧产物。

---

## 六、依赖版本（实测可跑）

| 包 | 版本 | 用途 |
|---|---|---|
| PySide6 | 6.11.2 | 界面 |
| numpy | 2.2.6 | 数值 |
| scipy | 1.15.3 | 插值 / 拟合 / .mat 读写 |
| matplotlib | 3.10.9 | 出图 |
| pydicom | 3.0.2 | DICOM |
| h5py | 3.16.0 | v7.3 .mat |
| pandas | 2.3.3 | 表格 |
| openpyxl | 3.1.5 | 导出 .xlsx |
| scikit-learn | 1.7.2 | ⑥ 随机森林 |

Python **3.10.10**（64 位）。

---

## 七、常见问题

**双击没反应 / 窗口一闪而过**
用 `启动 CEST Studio（调试）.bat` 启动看输出，或查 `CEST_Studio\logs\gui_<日期>.log`。

**提示"找不到核心层（函数库）"**
`03_Python_全链路\` 和 `CEST_Studio\` 必须在同一层目录下（整包一起拷）。

**4D 数据跑不了**
每层帧数 > 1 的序列，必须先在 ① 页的「序列与频率」里生成并**确认频率表**，否则不许重建。

**跑得慢**
重活主要花在磁盘 I/O（`*_interp.mat` 单个数百 MB），不一定是算得慢。
④ 逐体素 LD 默认 `fast`（中心/线宽网格 + 幅度/基线闭式解）≈1.5 s/层，属正常。

---

## 八、说明

- 本项目面向本地科研数据处理，算法口径以参考 MATLAB 实现为准并逐位对拍；
- **仓库内不含任何病例数据**；示例中出现的病例名（`c1` / `ctrl_3H_小鼠1` 等）仅为命名示例。
- 详细内部文档见 `CEST_Studio\README.md` 与 `使用说明.txt`。


## 九、更新（不用每次重下 470 MB）

装了之后不用重新下载整个免安装包。我们会发一个**代码更新包**
（`CEST-Studio-更新-vX.Y.Z` **文件夹**，约 0.9 MB）：

1. 把收到的「更新工具」文件夹解压进你自己的 `cest-gui`（和 `启动 CEST Studio.vbs` 同一层最省事，放哪一层都行）；
2. 双击里面的 **`应用更新.bat`** —— 它自己找目录、自己用自带的更新包，**不用选、不用按**；
3. 装完**关掉 CEST Studio 窗口**，重新双击「启动 CEST Studio.vbs」才生效。

也可以把更新包（文件夹或老的 zip）直接拖到 `应用更新.bat` 上放开。

被替换的旧文件会自动备份到 `_bak_更新_<时间戳>\`（不要了直接删）；
校验不通过或中途出错会自动整体回滚，不会把程序装坏。
`runtime\`、你的 `settings.json` 和你的数据都不会被碰；装更新**不会在桌面留下任何文件**。

> 只有「随包运行环境/依赖」变化时，才需要重新下载完整免安装包（很少见，会另外说明）。
