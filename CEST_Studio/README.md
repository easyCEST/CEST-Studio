# CEST Studio（骨架 v0.6）

CEST 处理链的桌面外壳。**核心层不复制、不搬移**（`03_Python_全链路\函数库`），
本目录只放「壳」：探测器 + 序列/频率判定 + 掩模画布 + 任务线程 + 界面 + 模块适配。

当前已接：**① DICOM 重建**（含序列匹配 / 3D·4D 判定 / 频率表）、
**⑧ 去噪掩模 + 器官 ROI**（交互画布，Mask 与 ROI_total 都出）、
**③ CEST 后处理**（Z 谱插值 / B0 三模式自校正 / 逐层落盘，含 ROI 定量曲线；支持「全部病例（自动）」批量）、
**④ 逐体素LD**（洛伦兹拟合，拟合参数全在 GUI / 带预设）、
**⑤ PRF 提取**（训练集：不同个体·同一部位 / 同一个体·不同部位，单个 / 按组，走 `prf_batch` 全开关）、
**⑥ PRF 分析**（随机森林特征重要性：组间·同一位置对比 / 组内·左右对比 → 图 + Excel）、
**② 多层图谱展示**（raw/ST/APTW/LD 静态拼图 + 交互式预览）、**⑦ 诊断自检**。

**输入目录按模块独立**（2026-10-02）：① 吃原始 DICOM；⑧④② 吃项目目录 PROJ（不设默认 = 输出总目录），
页顶自动解析出 病例 / 数据 / 掩模（详见「三之六」）——缺数据时【开始】直接置灰。

后续：② 还想加：多频偏并排 / 批量出图。

---

## 一、怎么跑

> **要做汇报 PPT？** 看 `频率重要性通用\汇报\PPT生成说明.md`：本项目 PPT/图/预览/生成脚本
> 统一放 `频率重要性通用\汇报\`，一键命令是
> `powershell -NoProfile -ExecutionPolicy Bypass -File "…\汇报\buildPPT_gui.ps1"`。

**双击 `启动.vbs`**（推荐，**没有黑窗口**：用 `pythonw.exe` + 隐藏窗口）：

```
CEST_Studio\
├─ 启动.vbs                  ← 以后双击这个（无黑窗口）
├─ 启动.bat                  ← 双击也行，它会立刻转给 启动.vbs（最多闪一下）
└─ 启动_调试（带控制台）.bat  ← 排错用：带控制台，能看到所有输出
```

解释器（不新建环境）：`C:\Users\Administrator\Desktop\PycharmProjects\机器学习\venv\Scripts\pythonw.exe`

* **无控制台时的日志**：用 `pythonw` 启动时所有输出（含报错）落到 `logs\gui_<日期>.log`；
  每次运行的详细日志另外落在 `PROJ\日志\<模块>_<时间戳>.log`。
* 改解释器：`启动.vbs` 里的 `py = "…"` 与调试 bat 里的 `PY=`；
  改核心层位置：`app\core_path.py` 的 `CORE_LIB`。

命令行（调试 / 自动化用）：

```
python app\main.py --open <输入目录> --proj <输出目录> --nav 1 --shot out.png
```

---

## 二、用法

1. **输入根按模块独立**（2026-10-02 起）：① DICOM 重建 吃**原始 DICOM**；⑧④② 等数据处理模块
   吃**项目目录 PROJ**（不设就**默认 = 输出总目录**）。把目录拖进左上角 = 设置**当前模块**的输入根；
   勾上旁边的「应用到所有数据处理模块（⑧④②…）」可一次改完下游。
   详见「**三之六、输入根按模块独立 + 数据链**」。
2. 选**输出总目录**（PROJ）→ 各模块自动在下面建
   `00_掩模 / 01_重建 / 02_展示 / 03_后处理 / 04_逐体素LD / 05_PRF提取 / 06_PRF分析 / 日志`；
   ⑧④② 的输入根会自动解析出**病例 + 数据目录 + 掩模**（每页**右栏顶部**的「数据链」三行实时显示）。
3. 左侧点模块 → 看参数 → **开始**。
4. 若是 **4D 数据**（每层帧数 > 1），必须先在 ① 页的「序列与频率」面板点 **频率表…** 生成并确认，
   否则**不许重建**。
5. **⑧ 去噪掩模 + 器官 ROI**：点【打开掩模画布…】画一张掩模 → 确定 → 【开始】存到 `PROJ\00_掩模\<病例>\`；
   之后 ② 出图 / ④ LD 会自动优先取这张掩模（详见第三节之二）。
   同一个画布还能画**器官 ROI**（ROI1…ROIn，底图自动切成「Z@ppm 层间平均 × 掩模」）→
   存成 ③ 后处理要用的 `ROI_total.mat`（详见第三节之三）。

导航标记：`√` 输出目录已存在｜`●` 当前识别下可用｜`○` 暂不可用。

---

## 二之二、⓪ 导入重建（从已有 .mat 组装，不用碰 DICOM）

> 用途：手里已有重建好的 **s0 / Z 谱 / w**（历史数据、别人给的数据），想直接跑 ③④② ——
> 用本页组装成 ① 的产物结构即可，**不用再从原始 DICOM 重建**。

**输入（两种形态都认）**

| 形态 | 例子 | 说明 |
|---|---|---|
| 分离式 | `...\数据1\`：`S0.mat` + `zspect_dataavecorrect.mat` + `w_offset.mat` | 一个目录 = 一个病例 |
| 整合式 | `...\001\fasting\0p7P_interp.mat`（含 `S0` + `img_interp`） | 也支持直接给单个 `.mat` |

**参数（就 5 项）**

`输入（目录或 .mat）` / `病例名` / `输出根（留空 = 当前 PROJ）` / `Z 谱文件（留空 = 自动）` /
`频偏（留空 = 按点数推断）`

**自动识别**：s0 ← `s0/S0`；Z 谱 ← `img_interp / zspect_* / norm_z / ssat / z …`；
w ← `w_offset / w / freq`；整合式文件里若自带 S0，**优先用同一文件里的 S0**（比目录里别的文件更同源）。

**频偏**：有 `w_offset.mat` 就用它；没有 → 按频率点数推断（`201 → −10:0.1:10`、`241 → −12:0.1:12`）；
也可手填 `-10:0.1:10`。

**输出（`<输出根>\01_重建\<病例>\`，与 ① 完全同名同变量）**

```
s0.mat        变量 s0        (X,Y,层)
normZ.mat     变量 norm_z    (X,Y,层,频)     ← 归一化 Z 谱（主产物）
ssat.mat      变量 ssat      (X,Y,层,频) + axis_order='layer_freq'
w_offset.mat  变量 w_offset  (1,频数) + freq_seq (频数,1)
b0.mat         有就写，没有跳过
import_meta.json  来源文件 / 变量 / 形状 / 频偏来源（留痕）
```

同名重跑先 `.bak_<时间戳>`。

**★ 不会多除一次 S0**：这批数据本身已是 Z 谱（实测非零中位 0.84 / 0.47 / 0.29，`is_already_norm` 判 True）；
`cest_post.normalize_spect` 的「已归一」判据 2026-10-05 已由 **min/max 改成 **分位**（1%/99%，与 `cest_ld` 统一）**，
含少量越界点（<1%，如 max 13.4）不会再被误判重标定 —— 实测 `normalize_spect` 原样返回、`max|diff| = 0`。

**实测（2026-10-05）**

| 源 | 导入结果 |
|---|---|
| `2026\数据路径\数据1` | s0 (96,96,1)、normZ (96,96,1,74)、w 74 点 −10..10（来自 w_offset.mat） |
| `sub001-005\001\fasting\`（0p7P_interp.mat） | s0 (80,80,41)、normZ (80,80,41,241)、w 241 点 −12..12（按点数推断） |

导入后 ③/④ 的校验**只报「缺掩模」**（去 ⑧ 画一下即可），说明 s0/normZ/ssat/w 结构全部被接受。

---

## 三、① 的目录预览 / 序列匹配 / 3D·4D 判定 / 频率表（重要规则）

### 3.0 目录预览（手动挑序列）
① 页「序列与频率」面板点 **浏览 / 选择序列…** 打开：
1. 打开 = 输入总目录的**第一级文件夹**（= 病例），带「← 上一级」「面包屑」；
2. **双击目录进入**（如 `c1`）→ 列出里面所有含 .dcm 的文件夹：`名称 / 帧数 / 角色 / 编号 / 维度`；
3. **勾选**一个或多个；**「角色（可手动改）」列是下拉框**：名字规范的会自动填好
   `S0/Ssat/B0Ma`，**命名不规范就在这里手动指定**（完全不依赖名字）。
   名字可解析时，点 **补齐同组** 自动把「同前缀 + 编号相邻」的三个勾上/指定好，
   并读 DICOM 标签把**维度**填上；结果区写明「完整 / 缺哪些 / 还有哪些候选」。
4. **确定** → 回填给 ①（只重建你勾选的；若缺件会再问一次「只重建已有的吗」）。

* 标签认**别名**：`CEST→Ssat`、`B0Map/B0→B0Ma`、`S0m→S0`（见 `detect/series.py` 的 `TAG_ALIAS`），
  所以旧那套 `cest_tra_fast_10slice_{S0_702,Ssat_703,B0Map_704}` 也能被补齐。
* **标签是「其它」的也能勾选**（旧那套 `CEST_701`、`B1Map_AFI_1`、`Fact3d_*` 等）：勾了以后按
  **【单序列】重建**——只出一个 **3D** `(X, Y, 层)`，落到 `<病例>\<文件夹名>.mat`（`zdata`），
  不需要频率表；**层数按 DICOM 实测**（不写死 20）。若原始每层 >1 帧，主输出取层内平均，
  另存一份 `_4d.mat` 备查（不丢数据）。
* **只有一个病例**也支持：把病例目录本身（里面直接是序列文件夹）拖进来即可，
  会自动识别成 1 个病例；病例名取目录名（可在参数里改）。
* 手动挑的文件夹走 `detect/manual_recon.recon_with_dirs`（用核心层公开构件拼装），
  **不动核心层一行**；自动匹配的病例仍走 `cest_recon.run_recon`。

### 3.1 序列匹配
在病例目录里按「**同前缀 + 编号相邻**」匹配 `S0 / Ssat / B0Ma`（与核心层 `find_cest_dirs` 同口径），
并**报出**：缺哪个、还有哪些同样相邻的候选组、以及有多少文件夹不参与匹配。

### 3.2 3D / 4D 判定（读 DICOM 标签，有缓存）
用 `dicom_order.read_series_meta + detect_nesting` 拿到 **帧数 / 层数 / 每层帧数 / 采集序**：

| 每层帧数 | 维度 | 输出形状 | 例子 |
|---|---|---|---|
| = 1 | **3D** | `(X, Y, 层)` | `S0_1201`(20/20)、`B0Ma_1203`(20/20) |
| > 1 | **4D** | `(X, Y, 层, 频)` | `Ssat_1202`(620/20=31) |

`S0 / B0Ma` 天然 3D，不需要频率。

### 3.3 频率表（4D 必需）
**硬规则：4D 序列没有"已确认的频率表" → 不能跑。** 长度还必须**等于每层帧数**
（核心层 `make_ssat` 也校验），否则一律拦下。
自动来源优先级：

**不自动检索任何目录。** 打开频率表对话框时只带出「本病例上次确认过的表」；
其余一律由你手动给，四种方式：

| 方式 | 说明 |
|---|---|
| **选择频率文档…** | 手动指定一份文件，**当场解析**：`*.prot`（UIH 协议 XML）/ `*.mat` / `*.txt` / `*.csv`；解析不出来就提示你手动输入 |
| **从旧结果继承** | 读病例目录 / `PROJ\01_重建\<病例>\` 里的 `freq.mat`（`freq_seq` 优先）/ `w_offset.mat` |
| **常用预设** | 只提供通用的「起止:步长」模板（P/N 241、APT 201、7T 101），**不含任何写死的本批数值** |
| **手动输入** | 一行/逗号/空格分隔，也认 `-12:0.1:12` |

关于 `*.prot`（实测可行）：它是 UIH 的协议 XML（`UProtocol`），里面有
`FreqOffsetValue`（唯一频偏，升序）、`SsatScanNum`（每层采集帧数）、`TargetFreq`（目标频偏 3.5）、
`TargetOffsetAverage`（重复次数）。GUI **运行时解析**它并还原成采集顺序表：
在 `TargetFreq` 首次出现处按 `SsatScanNum − 唯一个数` 补插几遍。
（实测本批 9 个 `.prot` 解析出的 31 个值与 `CEST_result\c1\freq.mat` 的 `freq_seq` **逐位一致**。）

> 填的是**采集顺序表（含重复）**，去重平均后得到的 `w_offset` 才是写进 mat 的那份；界面两份都显示。
> 长度必须等于每层帧数（界面会实时校验，不符就不能确定）。

### 3.4 落盘与追溯
```
PROJ\01_重建\_频率\<病例>.json       确认过的表（含来源、时间、去重后的 w_offset）
PROJ\01_重建\<病例>\freq_list.txt    人可读：采集顺序表 + 去重后 + 来源
```

---

## 三之二、⑧ 去噪掩模（Mask = 组织 ROI × 噪声 SNR 掩模）

> **手动使用说明（含「数据是怎么选的」）见同目录 `手动使用说明_⑧去噪掩模.md`。**

口径与 MATLAB 完全一致（`denoise_Mask.m` / `ThreMask_SNR.m` / `GenerateThmask.m` /
`GenerateliverMask.m` + `runCESTpost.m` 的 4.1 节）：

```
底图        = mean(S0, 层)                         ← 与 MATLAB 的 img_mask_base 逐位相同
snrMask     = 底图 > snr × std(底图[噪声ROI])        snr 默认 15
liverMask   = 手画的组织/肝脏多边形（roipoly）
Mask        = liverMask × snrMask                  ← 最终「去噪掩模」，②④ 用它压背景
```

### 3.5 画布怎么用

**底图**：默认 **`S0 层间平均`**（= 左边层览第 1 项 `★ 全层平均`，画共用掩模就用它）/
`S0 逐层`；若输入里有 `img_interp + w`，还可切 `Z @ ppm（逐层／层间平均）`。

**色图**：`灰度` / `彩色 viridis（≈parula）` / `jet（首行黑）` / `jet`，
**只影响观感、不影响任何计算**。切「掩模对象」时会自动切到 MATLAB 对应那张：

| 画什么 | MATLAB 脚本 | 色图 |
|---|---|---|
| 噪声 ROI | `ThreMask_SNR.m` / `denoise_Mask.m`（写了 `colormap(gray)`） | 灰度 |
| 组织/肝脏 | `GenerateliverMask.m`（**没设 colormap** → MATLAB 默认 parula） | 彩色 viridis（parula 的近似） |
| SNR 掩模 / 器官 ROI / 出图 | `ROIplotSong_0708.m`（`colormap([0 0 0; jet(255)])`） | jet（首行黑） |

**左边层览**：第 1 项 **`★ 全层平均`**（S0 的 20 层平均图，画共用掩模就在这张上画），
下面 `层 1 … 层 20`，点某层 → 画布切到该层。

**改形状**：多边形是矢量的 —— 工具排「选择/拖动」下，拖顶点改单点、拖内部整体挪、右键顶点删点；画笔/橡皮是叠在多边形上的另一层「笔迹」。

> **20 层都要画吗？不用。** 一张 2D 共用掩模铺满 20 层（MATLAB 当年也是对平均图画一个
> `roipoly`，存出来是 2D）。只有某层组织位置跟平均值差很多时才：**点那一层 → 勾
> 「本层单独保存」→ 修**，该层用自己那张、其余层用共用张；导出时自动合成 3D `(X,Y,层)`。
> （「本层单独保存」是覆盖式：那层不跟共用张求并集。）

**掩模对象**（3 个，互相独立叠加显示）：

| 对象 | 颜色 | 作用 |
|---|---|---|
| `组织/肝脏 liverMask` | 绿 | **手画**；去噪掩模的主体 |
| `噪声区 noiseROI` | 橙 | **手画**；只用来算噪声 σ（不参与最终掩模） |
| `SNR 自动掩模 snrMask` | 青 | 由【算 SNR 掩模】按 `底图 > snr×σ` 生成，**只读** |

**工具**：多边形（左键加点 → **双击确定**，自动封闭；右键 / Backspace 撤掉刚点的那个点）、矩形（按住拖，生成四点可拖角）、画笔、橡皮、
撤销、清空本目标；滚轮缩放、中键拖拽平移；不透明度滑杆。

**SNR 面板**：`SNR 系数`（默认 15）；`噪声 σ` 留 0 = 用「噪声区」里画的像素算 σ，
**也可以直接手输 σ**（要复现 MATLAB 旧结果就填它算出的那个 σ，见第四节实测值）。
【算 SNR 掩模】→【SNR 掩模 → 复制成 liverMask 初值】= MATLAB「先 SNR 自动出初值，再手工修」。

**粒度**：默认一张 **2D 全层共用**；勾「本层单独保存」后，本层的涂改只作用于这一层，
导出时自动合成 **3D (X,Y,层)**（该层用单独的，其余层用共用张）。

### 3.6 产物（`PROJ\00_掩模\<病例>\`）

| 文件 | 变量 | 维度 | 谁用 |
|---|---|---|---|
| `Mask.mat` | `Mask` | **3D (X,Y,层)** | ② 展示（压背景）/ ④ 逐体素LD |
| `Thmask.mat` | `Thmask` | 3D | ③ 后处理（与 Python `run_post` 同名口径） |
| `liverMask.mat` | `liverMask` | 画布原样（2D 或 3D） | ③ 后处理 |
| `snrMask.mat` | `snrMask` | 画布原样 | 备查 |
| `roi_meta.json` | — | — | 底图/层数/σ/阈值/体素数/是否逐层相同/时间 |
| `mask_check.png` | — | — | 三格确认图（底图轮廓 / SNR 轮廓 / 最终掩模叠加） |

* 覆盖前自动备份：`<名>.bak_<时间戳>.mat`。
* **为什么 `Mask.mat` 存 3D**：② 的 `apply_mask` 是 MATLAB 口径 `img .* Mask - (~Mask)`，
  MATLAB 有隐式扩展（2D 掩模自动按第 3 维展开）、numpy 没有 → 2D 会广播报错。
  ④ 读 3D 时取第 1 层，与 2D 完全等价。参考的 MATLAB `mask.mat` 是 2D → 对拍时取任一层。

### 3.7 载入现成掩模

【载入已有掩模…】选一个 `.mat`（含 `Mask` / `Thmask` / `liverMask` / `snrMask` 任一），
或【从输入目录找】自动在输入目录 / `PROJ\00_掩模\<病例>` 里找 `Mask.mat / liverMask.mat / Thmask.mat`。
按 MATLAB 口径：`Thmask` 单独出现时当作 **SNR 掩模**处理（`GenerateThmask.m` 存的正是它）。

### 3.8 与 MATLAB 已有结果对照（实测，2026-10-01）

参考数据：`2026\09\CEST_post\<病例>\full_interp_0803\`（MATLAB 手工跑的 c1 / h1）。
一键复跑：`python CEST_Studio\tools\validate_mask.py --reproduce`
（体检已有结果：`--check --proj <PROJ>`）

| 项目 | c1 | h1 |
|---|---|---|
| 底图 `mean(S0,3)` vs 参考 `img_mask_base` | max\|diff\| = 0 | max\|diff\| = 0 |
| `liverMask` vs 参考 `liverMask.mat`（8847 / 10051 体素） | 差异 0 体素，Dice 1.000000 | 差异 0 体素，Dice 1.000000 |
| `snrMask` vs 参考 `Thmask.mat`（26823 / 27959） | max\|diff\| = 0 | max\|diff\| = 0 |
| 最终 `Mask` vs 参考 `mask.mat` 的 `Thmask` | max\|diff\| = 0 | max\|diff\| = 0 |
| 参考反推的噪声 σ（阈值 = 15σ） | 25.7933（阈值 386.899） | 16.3333（阈值 245.0） |

另外：把参考 `liverMask.mat` 的 0.5 等值线当「手画多边形」喂进画布，
结果与参考 `liverMask` **逐位相同（差异 0 体素）** → 画布的多边形→掩模口径
（`cest_post.points_to_mask`）与 MATLAB `roipoly` 在这个数据上等价。

### 3.9 一处口径差异（务必知道）

MATLAB `GenerateThmask.m` 先把 SNR 掩模写进 `Thmask.mat`，之后**不再覆盖**它
（所以 MATLAB 的 `Thmask.mat` = SNR 掩模）；而 Python 的 `cest_post.run_post` 会把
`Thmask = liverMask × snrMask` 后的结果**覆盖写回 `Thmask.mat`**。
本 ⑧ 模块按 **Python 口径**写（`Thmask.mat` = 组合后的最终掩模），
并另存一份 `snrMask.mat` = SNR 掩模，两边的信息都不丢。

---

## 三之三、⑧ 画器官 ROI（ROI1…ROIn → ROI_total.mat）

> 对应 MATLAB `runCESTpost.m` 第 32~39 行 + `ROI_name.m` + `ROIplotSong_0708.m`，逐行复刻。

**参数（⑧ 页「参数」里）**

| 参数 | 默认 | 说明 |
|---|---|---|
| `ROI 数量（器官 ROI）` | 1 | = MATLAB `nROI`；画布会生成 ROI1…ROIn（名称与 MATLAB 一致） |
| `ROI 目标 ppm` | 3.5 | = MATLAB `target_freq`；ROI 底图取这个 ppm 的 Z 谱 |
| `ROI 显示` | `Z 原始（APT）` | `Z 原始（APT）` = MATLAB `roi_display_mode 1`；`1−Z（ST）` = `mode 2`。画布上也能随时切 |
| `ROI 色标下限 / 上限` | 留空 | 留空 = 0 / 有效值 95% 分位（画布上随时可改） |

**ROI 底图（固定这一种，不给 S0 底图）**

```
归一化 Z 谱 @ 目标ppm 的 20 层平均 × 去噪掩模
```

- 读 `PROJ\01_重建\<病例>\normZ.mat` → 对每层每体素 `normalize_spect(tol=0.02, p2/p98)`
  （与 ③ 后处理里的 `Mz` 同口径）→ 取 `roi_ppm` 最近频点 → 对 20 层求平均 → `.* Mask`
- 两种**显示模式**（画布「底图」组里的 `ROI 显示`，对应 MATLAB `roi_display_mode`）：
  - `Z 原始（APT）` = `roi_display_mode 1`（默认）
  - `1−Z（ST）` = `roi_display_mode 2`（**先 1−Z、再乘掩模**，与 MATLAB 一致）
- **明确不做 mode 0（S0 底图）**；显示模式只影响观感，不改 ROI 几何与索引值
- 「**★ 全层平均**」行 = 该底图的 20 层平均 → 画出来就是 **2D 全层共用**（同 MATLAB `roipoly` 口径）；
  左栏点**某一层** = 该层的 Z 图 → 勾「**本层单独保存**」就能**逐层单独画**（器官 ROI 现在也支持，见下「逐层 ROI」）

**前置门禁**：画 ROI 必须先有去噪掩模。没有 → 拦下提示。
打开画布时会自动把 `00_掩模\<病例>\` 里现成的 `liverMask.mat` / `snrMask.mat` 当底稿带进来
（否则「只画 ROI」会让 liverMask 变空，保存时反而把原 Mask 覆盖掉）。

**怎么画**

1. 设好数量：⑧ 页「参数 → `ROI 数量（器官 ROI）`」（默认 1；就在参数区前几行）
   → 点【打开掩模画布…】（或【重新画 ROI…】）；
   画布里「掩模对象」组也有 `ROI 数量` +【应用】，改完立刻增删 ROI1…ROIn（不用关画布）；
2. 右边「掩模对象」列表里会多出 `ROI1…ROIn`（颜色循环）；
3. 目标切到 ROI1 → 底图自动切「ROI 底图：Z × 掩模」、色图自动 jet（首行黑）；
   还想看 ST 就把「底图」组里的 `ROI 显示` 切到 `1−Z（ST）`（色标会自动按新图重算）；
   停「★ 全层平均」→ 底图是 20 层平均；点某一层 → 底图切成**该层的 Z 图**；
4. 交互与画掩模完全一样：多边形（左键加点、**双击确定**、右键/Backspace 撤点）、矩形、画笔、橡皮；
   「选择/拖动」下拖顶点 / 整体挪 / 右键顶点删点；撤销 30 步；
5. **色标边画边调**：上限/下限两个框 + 【自动(95%分位)】按钮，改一下实时刷新（只影响观感）；
6. 逐个画（MATLAB：`Please select ROI1 now>>>` → ROI2 → …）→ 【确定】→ ⑧ 页【开始】落盘；
   要**逐层**画：先停「★ 全层平均」画一张（= 2D 共用），再点某一层、勾「本层单独保存」微调；
7. 画完重开画布还能接着拖（多边形/笔迹都回传，ROI 也一样）。

**复用 vs 重画**

- **默认沿用**：`00_掩模\<病例>\ROI_total.mat` 已存在 → 不重画（照 MATLAB 的 `try/load`）；
- 要覆盖重画：点 **【重新画 ROI…】**（不用手删文件）→ 画布上改完确定 → 下次【开始】覆盖重画。

**产物（同一个目录 `PROJ\00_掩模\<病例>\`）**

| 文件 | 变量 | 内容 |
|---|---|---|
| `ROI<k>.mat` | `ROI` | 该 ROI 的 (X,Y) 0/1（与 MATLAB 同名同变量） |
| `ROI_total.mat` | `ROI_total` | `cell(nROI,1)`；元素 = **1-based 列优先**线性索引，**升序**（= MATLAB `find`）。2D ROI = 该张 ∩ 掩模；逐层 ROI = **各层并集** ∩ 掩模（老工具/不传 layer 的兜底） |
| `ROI_total.mat` | `ROI_total_layers` | **只有存在逐层 ROI 时才写**：`cell(nROI,1)`，元素 = `cell(1,层数)`；`{k}{z}` = 第 k 个 ROI 在第 z 层的 1-based 索引列向量。**2D 的 ROI → 空 `[]`（读回时回退 `ROI_total`）；该层确实没画 → 单个 `-1` 哨兵** |
| `roi_meta.json` | — | 并入掩模 meta：新增 `roi` 段（nROI/模式/体素数/首值/最大值/ppm/显示模式/色标/时间） |
| `mask_check.png` | — | 有 ROI 时多一格「④ 器官 ROI」（各 ROI 边界不同颜色） |

- 与去噪掩模**求交**：`idx = ROI ∩ Thmask`（= MATLAB `find(ROI .* Thmask)`）；交集为空 → 报错请重画；
- 覆盖前自动备份 `<名>.bak_<时间戳>.mat`；
- 打包用核心层 `cest_post._cell_of_arrays`，复用 `points_to_mask`（画多边形→掩模）、
  `roi_plot_song`（「有 ROI_total 就复用」那条分支）、`savemat`。核心层只动了 **3 个文件**：
  `cest_roi.load_roi_total(path, layer=None)` + 新增 `load_roi_layers(path)`、
  `cest_roi.run_curves(..., layer=None)`、`prf_extract.load_features(..., layer=None)`
  （+ 内部 `_layer_of_dir` / `_roi_at_layer`）—— 都是「加可选参数、默认行为不变」；
  另 `cest_post.roi_plot_song` 复用外部 `ROI_total.mat` 时**顺手把 `ROI_total_layers` 一起拷过去**
  （原来只拷 `ROI_total`，会把逐层表丢掉）。

**验收（实测 2026-10-02）**

一键：`python CEST_Studio\tools\validate_roi.py --reproduce --via-core`

| 项目 | c1 | h1 |
|---|---|---|
| 参考 `ROI1.mat`（勾画体素） | 8537 | 9291 |
| 参考 `Mask`（`mask.mat` 的 Thmask） | 8847 | 10051 |
| 参考 `ROI_total.mat`（∩ 掩模后） | **8524**（首值 5470 / 最大 28315） | **9289**（首值 5086 / 最大 29470） |
| 本次保存路径生成的 `ROI_total` | **逐位一致（差异 0 体素，Dice 1.000000）** | **逐位一致** |
| 结构 | `cell(1,1)`、元素列向量、1-based 列优先、升序 | 同 |
| ROI ⊂ Mask | √ | √ |
| 核心层 `roi_plot_song`「有就复用」分支 | √ 一致 | √ 一致 |

口径要点：MATLAB `find` 返回的是**列优先升序**，所以本模块把索引按升序排，
这样存出来的 `ROI_total.mat` 与 MATLAB **逐位一致**（不是只「集合一致」）。

**逐层 ROI（3D，可选；不动 2D 时代的任何口径）**

不同层器官形状不一样（小肠 / 肾 / 肝）时，一张 2D ROI 套不下，就逐层画。

- **怎么出 2D / 3D**
  - 停「★ 全层平均」上画 → 全层共用一张 → 产物**仍是 2D**（与 MATLAB `roipoly` 逐位一致，8583/9291 那套验收不变）；
  - 停**某一层** + 勾「**本层单独保存**」→ 只改这一层 → `ROI<k>.mat` 出 **3D `(X,Y,层)`**，
    同时写出 `ROI_total_layers`；没勾的层 = 回退到 2D 共用那张。
  - 该勾选框对器官 ROI 现在**可用**（旧版对 ROI 灰掉、强制 2D）。
- **下游按层取**
  - ③：`cest_roi.load_roi_total(path, layer=n)`；`roi_curve_dialog` 逐层调 `run_curves(..., layer=n)`。
    该层没画到某个 ROI → 该 ROI 本层**跳过**（段日志里 `[跳过]`）；本层一个都没有 → 直接拦下报错。
  - ⑤：`prf_extract.load_features(..., layer=None)` 默认**按每个 src_dir 名里的 `sliceN` 自动认层**；
    ROI 文件带 `ROI_total_layers` 就取该层，缺层回退 2D。
  - **不传 `layer` / 文件是纯 2D → 与改动前完全一致**（回归：`validate_roi --reproduce` 仍逐位一致）。
- **`ROI_total_layers` 读法**（`cest_roi.load_roi_layers`）：空数组 = 该 ROI 是 2D（回退 `ROI_total[k]`）；
  `[-1]` = 该层确实没有；否则就是该层索引（已转 0-based）。
- 自检：`python CEST_Studio\tools\validate_roi_layers.py`（36 项：落盘→读回、③ 取层、⑤ 认层、画布 3D 往返）。

---

## 三之四、③ CEST 后处理（Z谱插值 · B0 校正 · 逐层落盘）

> 对应 MATLAB `runCESTpost.m`，算法走核心层 `cest_post`（**一行未改**）；
> 逐层循环放在壳里，为了**逐层进度 + 可取消 + 每层日志**。

**依赖（门禁，缺了就红字拦下）**

| | |
|---|---|
| **必须** | `PROJ\01_重建\<病例>\`（`normZ.mat` / `w_offset.mat` / `s0.mat`；B0 图谱校正另需 `b0.mat`） |
| **必须** | `PROJ\00_掩模\<病例>\Mask.mat`（来自 ⑧ 去噪掩模） |
| 可选 | `00_掩模\<病例>\liverMask.mat`（`V_*_mask` 用它）、`ROI_total.mat`（拷一份到输出，供 ROI 曲线用） |
| **不做** | 交互勾画掩模、自动生成掩模、4D 汇总、配准（配准单独一期） |

**B0 校正三模式**（主表单下拉，对应 MATLAB `B0correct`）

| 界面 | 值 | 输出子目录 |
|---|---|---|
| 不校正 | 0 | `No_corr` |
| B0 图谱校正 | 1 | `B0map_corr`（需 `b0_scale`/`b0_offset`，见高级参数） |
| 自校正 | 2（默认） | `Self_corr` |

**参数**

- 主表单：`输入 / 病例名 / B0 校正模式 / 频偏（全·正·负）/ 目标 ppm / 输出版本后缀 / 掩模文件 / 层 / 写 V_as 全量 / 另存未校正版`
- 【**高级参数…**】：**归一化**(`tol`/低分位/高分位)、**插值与B0**(`x_lw`、QC、旋转、`b0_scale`、`b0_offset`)、**层与输出**(总层数留空=自动、单精度)、**ROI与掩模**(掩模取第几层、SNR、nROI、ROI 底图)
- 预设：`自校正（默认）` / `不校正` / `B0 图谱校正` / `细插值 0.025` / `粗插值 0.1`

**产物（`PROJ\03_CEST后处理\<病例>\{full|pos|neg}_interp_<后缀>\`）**

```
sliceN\                     S0 / S0m / S0_temp / Thmask / w_offset / x_as .mat
sliceN\<tag>\               V_exp / V_as / V_exp_mask / V_as_mask / sft_map .mat
病例目录\                   w_offset.mat / x_as.mat / ROI_total.mat / post_meta.json
```

同名重跑先备份 `.bak_<时间戳>`；`post_meta.json` 记录本次全部参数 + 每层耗时。

**验收（实测 2026-10-05，c1 slice1 自校正，与 MATLAB `CEST_post\c1\full_interp_0803` 对拍）**

| 文件 | max\|diff\| | 逐位一致（<1e-6） |
|---|---|---|
| `V_exp_mask.mat` | 0.86 | **99.9941%**（2/33792 体素为搜谷模糊点，与历史记录一致） |
| `V_as_mask.mat` | 0.86 | **99.9941%** |
| `w_offset.mat` | 0 | **100%** |
| `x_as.mat` | 3.6e-15 | **100%** |
| `V_exp.mat` | 18（相对 ~2e-5） | 47%（未 mask 的原始信号，尺度差，待查，不影响下游） |
| `sft_map.mat` | 13.4 | 46.8%（QC 附属产物，不影响主链路） |

**顺带修的**：`cest_post.aggregate_4d` 只有调用没有定义（死分支）→ 界面不暴露 `save_4d_aggregates`，源码已加注释。

---

**批量：处理范围 = 全部病例（自动）**

主表单第一项「处理范围」默认「当前病例」；选「全部病例（自动）」后，③ 会把 `PROJ\01_重建` 下
**所有病例自动跑一遍** —— **缺前置条件的病例会先在日志里逐个提醒并跳过**，不用一个个点。
批量模式下数据链门禁放宽为「只要 PROJ 里有病例就放行」（缺谁运行时再说）。

实测：2 个病例、只有 1 个有掩模 → 日志提示 `· case2：缺 掩模 00_掩模\case2\Mask.mat`，只跑 case1。

**顺带出 ROI 定量曲线（像 MATLAB 那样一步出）**

③ 的高级参数「顺带出 ROI 定量曲线（Zspc/LD）」默认开：每层把 ⑧ 的 `ROI_total.mat` 拿来做
ROI 定量，落到 `sliceN\<tag>\ROI_<时间戳>\` —— 与 MATLAB `ROIprocessSXL1` 同名产物
（`Zspc_<n>ROI.mat` / `Zspc_as_<n>ROI.mat` / `LD.mat` / `Lorentzian_fit.mat` / `ROI曲线.png|xlsx`）。

关掉它，就还是老路子：只在 ③ 页【ROI 定量曲线…】按钮里按需单独出（或走 ⑤）。
**两条路都留着**，ROI 曲线走的是同一个核心函数 `cest_roi.run_curves`（所见即所得）。

---

## 三之六、ROI 定量曲线（③ CEST 后处理里的【ROI 定量曲线…】按钮，**不单独占导航**）

> 对应 MATLAB `ROIprocessSXL1.m` + `LD_spectra_plus_rex_0p7uT_filter.m`；
> 算法走核心层 **`cest_roi.py`（新增，未改任何现有函数）**。

**依赖（门禁）**

| | |
|---|---|
| **必须** | `PROJ\00_掩模\<病例>\ROI_total.mat`（⑧ 里画的器官 ROI） |
| **必须** | `PROJ\03_CEST后处理\<病例>\*_interp_*\sliceN\<tag>\V_exp_mask.mat`（先跑 ③） |
| 可选 | 同目录的 `V_as_mask.mat`（出插值版 `_as` 曲线） |

**参数**：主表单 `输入根 / 病例名 / 用哪个校正目录 / 层 / 要算哪些 ROI`；
【**高级参数…**】：**裁剪**(开关、频偏方向)、**拟合区间**(l3~h3)、**拟合界与初值**(lb/ub/par0)、**输出**(dpi、.mat/.xlsx/.png)；5 个预设。

**产物（`PROJ\03_CEST后处理\<病例>\ROI曲线\<时间戳>_<tag>\sliceN\`）**

```
Zspc_{N}ROI.mat / _Raw / _0to1     裁剪前全长（= MATLAB 的 Zspc_* 口径）
Zspc_filter.mat / w_offset_filter.mat   裁剪后（cell）
LD.mat / Lorentzian_fit.mat             裁剪后（cell，等长）
Zspc_as_{N}ROI.mat / _Raw / _0to1       插值版（有 V_as_mask 时）
ROI曲线.png  上：Z 谱+拟合；下：LD 残差（带 0 基线）
ROI曲线.xlsx   sheet「LD_fit」+「Zspc_as」
roi_meta.json  参数 + 每 ROI 的拟合参数/裁剪范围
```

**口径（与 MATLAB 逐位校准过，见 `_dev\参考资料_ROI定量曲线_样例解析.md`）**

| 项 | 做法 | 实测 |
|---|---|---|
| Raw | ROI 内逐频偏平均（V_exp_mask / V_as_mask） | 与 MATLAB `mSigE_Raw` **max\|diff\|=0**（~1e-15） |
| ÷列最大 / 0–1 | `Raw/max(axis=0)` / 每列 min-max | 与 MATLAB `mSigE` / `mSigE_0to1` **max\|diff\|=0** |
| 边缘裁剪 | 「连续 3 点递增」剔除（左 cur+4 / 右 cur-3） | 与 MATLAB `w_offset_trimmed` **逐位一致** |
| **LD** | `Lorentzian_fit − min-max(Z)`（残差） | 定义验证 **max\|diff\|=0** |
| 拟合 | `cest_ld.fit_lorentz / lorentz_n`（Python 口径） | 与 MATLAB 的 LM 局部解可能不同（已约定不追） |

**一键验收**：`runtime\python.exe -X utf8 CEST_Studio\tools\validate_roi_curve.py`
（建临时 PROJ → 跑 ③ 一层 → 直接调 `cest_roi.run_curves` → 自检：LD 定义 + 三口径关系，实测全过）

**交互**：③ 页右栏【**ROI 定量曲线…**】→ 左边勾「层」（**默认已把 ③ 跑完的所有层全勾上，一次全出**）
和「ROI」→ 实时看图 → 【导出选中层…】（逐层各出一套 png/mat/xlsx）。
预览与导出走的是**同一个** `cest_roi.run_curves`，所见即所得。

---

## 三之七、⑤ PRF 提取（训练集，2026-10-05 复刻原代码）

界面只有**两个模式**，每个**点一次全出**；分组不写死名字，走核心层 `cest_group`（按病例名前缀自动识别几级）。

| 模式 | 依据 | 一次产出 |
|---|---|---|
| **组内 · 按组** | 每个「组」（ctrl_3H） | 老鼠 **6 份**（每组一份） |
| **组内 · 单个** | 每个病例 | 老鼠 **36 份** |
| **组间** | 按「配对级」（老鼠=时间 3H/1W/5W） | **3 份**，每份 左/右 两套 |

**每份都出「左 / 右」**（ROI 奇=左、偶=右；**奇对奇、偶对偶，绝不跨侧**），文件名/变量名对齐原代码：

三个模式**各占一个子文件夹**（互不干扰；同名重跑自动备份 `<名>.bak_<时间戳>`）：
```
05_PRF提取\组内_按组\<组名>\            trainData.mat trainLabel.mat w_offset.mat
                                        trainDataLeft_ROI.mat(var totalTrainDataLeft)
                                        trainDataRight_ROI.mat(var totalTrainDataRight)
                                        → 本文件夹 groupPaths.mat
05_PRF提取\组内_单个\<病例名>\          …同上，但 trainDataLeft_ROI1.mat(var trainDataLeft)
                                        trainDataRight_ROI2.mat(var trainDataRight)
                                        → 本文件夹 resultPaths.mat
05_PRF提取\组间\训练集最终版\<配对级>\
                                        trainDataLeft_ROI.mat(var dataLeft) trainLabelLeft.mat(var labelLeft)
                                        trainDataRight_ROI.mat(var dataRight) trainLabelRight.mat(var labelRight)
                                        w_offset.mat → 本文件夹 newGroupPaths.mat
```

**分组识别（核心层 `cest_group`）**：组名 = 去掉病例名末尾个体编号；级 = 组名按分隔符（默认 `_`）切段中
「取值 ≥2 种」的段。猴子 `c1..hp3` → 1 级 3 组；老鼠 `ctrl_3H_小鼠1` → 2 级 6 组。扫描后会把识别结果
打进日志（几级、每级取值、每组几人）。

**参数**：数据根 / 输出根 / 目录结构(cestpost·hier·flat) / 分析模式 / 粒度 / 分隔符 / 部位划分（主表单）；
信号归一·eps·柱状图·hier 的 level1/level2·结果目录前缀等（→【高级参数…】）。

**实测（2026-10-05，`tools\validate_prf_extract.py` → EXIT 0）**
- 组内·按组 → 4 份（合成 2 级×2 组）；组内·单个 → 8 份；组间 → 2 个配对级目录（3H/1W）
- 真实 "测试集"：组内·按组 **6 份**、单个 **36 份**、组间 **3 份**（训练集最终版\{1W,3H,5W}）
- ⑥ `run_left_right` 能直接读 ⑤ 组间产物（出 importance_left/right.png）
- `read_path_info` 同时认 `pathInfo / groupPaths / resultPaths / newGroupPaths`（旧 `结果` 也能读）

**⚠ 归一化口径**：GUI 的 `V_exp` 已经除过 S0（≈ MATLAB 的 `V_exp_mask`）；所以「信号归一」保持
**不除 S0**，再除一次就失真。（原始 MATLAB `V_exp` 才是未归一。）

## 三之九、⑥ PRF 分析（2026-10-06 三模式改版）

> 复刻原 python（`机器学习\prf_*.py`）出图风格（`结果0417` 示意）：**一张图里把 1W/3H/5W 并排、异色区分、图例带样本数量**，不再每个时间点/每只鼠单独出一堆图。算法走核心层 **`prf_analysis.py`（未改一行）**。

**三个模式**（界面「分析模式」切换），各吃 ⑤ 的一类产物、各写一个子文件夹：

| 模式 | 吃 ⑤ 哪套 | 出图 |
|---|---|---|
| **组内** | `05_PRF提取\组内_按组\groupPaths.mat` | 每个「组别」一张图，图内 = 1W/3H/5W **三根并排柱** |
| **单个对比** | `05_PRF提取\组内_单个\resultPaths.mat` | 每个「组别」一张图，图内 = 该组**每只鼠一根柱**（不拆左右、无组间） |
| **组间** | `05_PRF提取\组间\newGroupPaths.mat` | **左脑一张 + 右脑一张**，图内 = 1W/3H/5W 三根并排柱 |

**两种出图样式**（「出图样式」下拉切）
- `仅均值`：1 个随机森林 + `n_permute` 次置换取均值（无误差棒），走核心 `plot_multi`。
- `均值±SD`：重训 `n_repeats` 次（`random_state=42+run`）取 Mean±SD（**带误差棒**）。

**频偏范围** = **去水峰**（挖空 0ppm 附近）+ **边缘裁剪**，**左右分开填**（默认水峰 ±0.5、边缘 −3.7 / 3.6）。
「频偏范围」下拉：`clinical(±1.2~4.5)` / `rat7t(±0.5~3.0)` / **`自定义（下面 4 个边界）`**。
掩码在 ⑥ 里按 `w_offset` 自建（核心 `load_dataset`/`run_compare` 的预设写死，故只调核心 `rf_importance` / `merge_peaks` / `plot_multi` / `RANGE_PRESETS`）。

**参数**：输入地址清单 / 分析模式 / 出图样式 / 重复次数 / 输出根 / 图分辨率（主表单）；
频偏范围·去水峰左右·边缘左右·合并 5 峰·合并方式 / 置换次数·树数·深度·叶最小样本·随机种子 / 图格式
（进【高级参数…】，带预设）。

**产物**（默认 `PROJ\06_PRF分析\`，三模式各一子文件夹、同名重跑 `.bak_<时间戳>`）
```
06_PRF分析\组内\      ctrl_频率重要性.png/.xlsx   model_频率重要性.png/.xlsx   prf_analysis_meta.json
06_PRF分析\单个对比\  ctrl_1W_频率重要性.png/.xlsx …（每个组一张，图内每只鼠一柱）
06_PRF分析\组间\      左脑_频率重要性.png/.xlsx   右脑_频率重要性.png/.xlsx    prf_analysis_meta.json
06_PRF分析\prf_analysis_meta.json   顶层汇总（供数据链自检）
```
xlsx：一个 sheet 一个柱系列。`fmt` 高级参数可选 `png`/`svg`。

**实测（2026-10-06，`tools\validate_prf_analysis.py` → EXIT 0）**
- 组内·仅均值 → `组内\ctrl_频率重要性.png`（1W/3H/5W 三柱，图例 `n=240`）+ `model_…`；组内·均值±SD → 同款带误差棒
- 单个对比 → `ctrl_3H/ctrl_1W/model_3H/model_1W_频率重要性.png`（每组 3 只鼠三柱）
- 组间 → `组间\左脑_频率重要性.png` + `右脑_频率重要性.png`（各 3 柱）
- 导航顺序不变；⑥ 在注册表、`chain.resolve('m6_prf_analysis')` 就绪；`settings.json` MD5 前后不变

**坑**
- 组内按「第 1 级（ctrl/model）」拆图、把「最后一级（时间）」当柱子；单个对比按「组」拆、把「个体」当柱子（都走核心 `cest_group`）。
- 组间每个配对级目录必须有 `w_offset.mat`（⑤ 已写）；缺 `dataLeft/labelLeft` 的目录逐个跳过，不硬跑。

---

## 三之五、② 多层图谱展示（2026-10-06 展示类型四选一）

> 复刻 MATLAB 的多层拼图。核心 `cest_display.py` 只修了一个**广播 bug**，新出图能力全靠参数打通。

**「展示类型」下拉**（主表单）
| 选项 | 固定住 | 铺成子图 |
|---|---|---|
| **自动**（默认） | — | **单层 → 全频率**；多层 → 固定频偏逐层 |
| 固定频偏（逐层） | 目标频偏 `w` | 所有层 |
| 固定层（全频率） | 层号 | 所有频点（逐子图标题 = 该 ppm） |
| **特定频率** | — | 你指定的频率（`频率1` ~ `频率5`，留空 = 跳过） |

- **特定频率**：单层 → **一张图**（N 个频率并排）；多层 → **每个频率一张图**（该频率 × 所有层）。
  落在单独子文件夹 `02_展示\<病例>\特定频率\`；子图标题用**实际匹配帧的 ppm**（无精确帧取最近邻），日志打印「请求 → 实际」。
- 频率维度的子图标题 = 各频点 ppm（走核心 `gen_mosaic` 的 `title_values / title_fmt`）。
- `APTW` 在「固定层(全频率) / 特定频率」下**拦下**（± 差值无逐频点概念）→ 用 ST / raw / LD。
- 命名：层维度 `<MODE> <w>ppm_combined`；全频率 `<MODE> slice<层>_allfreq_combined`；特定频率 `<MODE> 特定频率_combined`（单层）/ `<MODE> <w>ppm_combined`（多层各一张）。
- 交互式图谱预览暂只支持「固定频偏（逐层）」；其余展示类型请直接点【开始】出图。

**核心修复**：`cest_display.apply_mask` 的广播 bug —— 单层 `img3d=(X,Y,1)` × 2D 掩模 `(X,Y)` 会被 numpy 广播成 `(X,Y,X)`（层轴炸成一整维），**这正是单层数据"多层展示没法用"的根因**。已把 2D 掩模补成 `(X,Y,1)` 按层广播。

**自检**：`tools\validate_display.py` → EXIT 0（自动 / 固定频偏 / 固定层 / 特定频率 × 单层&多层 + APTW 拦截 + 注册导航）。

---

## 三之八、输入根按模块独立 + 数据链（PROJ 自动解析）

> **数据链自检（页面那排 ①重建 → ⑧掩模 → ③后处理 → ④逐体素LD → ⑤PRF提取 → ⑥PRF分析 → ②展示）已改为「做过的显示绿色 ✓」**：没绑定病例的跨病例页（⑤⑥）按**项目级**判断（有没有做过），不再一律「待跑」；①⑧③④② 选了病例就按该病例逐个查。⑤⑥ 的本级还会**按当前「分析模式 / 任务」**判断该模式对应的产物在不在（例：⑥ 选「组间」但 ⑤ 只有「组内_按组」→ ⑤PRF提取 显示待跑）。

### 6.1 一句话
每页有**自己的输入根**（存在 `settings.json` 的 `inputs = {模块id: 目录}`）：

| 模块 | 输入根 | 说明 |
|---|---|---|
| ① DICOM 重建 | 原始 DICOM 目录 | 输出仍落 `PROJ\01_重建\<病例>\` |
| ⑧ 去噪掩模 / ④ 逐体素LD / ② 多层图谱（③ 后处理…） | **项目目录 PROJ** | 不设就**默认 = 输出总目录** |

切页各自载入、互不覆盖；顶部拖拽区改的是**当前模块**的输入根，
旁边的「应用到所有数据处理模块」（`main_window.chk_all`）勾上一次改完 ⑧④②（① 不受影响）。

### 6.2 「数据链」三行（每页**右栏顶部**）
```
输入根：C:\...\2026\10\结果目录（默认 = 输出总目录 PROJ）· 识别：CEST 项目目录（PROJ）
病例：[c1 ▼]   病例 c1 ｜ 数据 01_重建\c1 ✓ ｜ 掩模 00_掩模\c1\Mask.mat ✓
数据链：① 重建 ✓ → ⑧ 掩模 ✓ → ④ 逐体素LD ✓ → ② 展示 待跑
```
* **病例下拉** = `PROJ` 里枚举出的病例（走 `R.iter_cases()`，`_频率` 这类 `_` 开头的元数据目录不算病例）。
  换病例 → `src` / `mask_file` 自动重解析；PROJ 模式下原来的「病例名」文本框会自动隐藏。
* **数据链自检** 每个都是按钮，缺哪一步点一下直接跳到对应模块（`MainWindow.goto(mid)`）。
* **缺数据** → 红字写清「缺哪一步、去哪补」，并把**【开始】置灰**（与 ④ 原有的「缺掩模直接拦下」同风格）。
  不自动生成掩模、不自动估 σ、不自动切色图。

### 6.3 解析器：`detect/chain.py`（壳内，核心层不动）
```
NEEDS = { 'm1_recon': (),                       # 只吃原始 DICOM
          'm_roi':      ('01_recon',),
          'm4_ld':      ('01_recon', '00_roi'),
          'm2_display': ('01_recon', '00_roi'),
          'm3_post':    ('00_roi',), 'm0_check': () }
resolve(proj, module, case=None) -> Resolved(case, cases, src_dir, mask_file,
                                              missing, ready, hint)
list_cases(proj) / pick_case(proj, case) / stage_status(proj, case) / relpath(...)
```
* 数据目录 `src_dir` = `PROJ\01_重建\<病例>`（要 `s0`/`ssat`/`w_offset`）；
  ② 允许用 `04_逐体素LD\<病例>\0.7ut_img_interp.mat` 顶替。
* 掩模 = `PROJ\00_掩模\<病例>\Mask.mat`（退 `mask`/`liverMask`/`Thmask`）。
* 病例优先级：病例下拉里选的 → `04_逐体素LD` 里已有的 → `01_重建` 里第一个。

### 6.4 每页的三种输入根模式（决定【开始】能不能点）
| 模式 | 触发 | 行为 |
|---|---|---|
| `proj` | 输入根 = 项目目录 PROJ（或还没设，默认走 PROJ） | 用 `chain.resolve` 严格门禁 |
| `single` | 输入根 = 某个病例 / 某份结果目录（`recon_mat`/`recon_root`/`img_mat`/`roi_mat`/`post_mat`） | 「单病例模式」：不列病例，走 `suggest()` + `validate()` 把关 |
| `bad` | 既不是 PROJ 也不是病例目录（如 `01_重建\_频率`） | 红字 + 【开始】灰 |

### 6.5 模块侧怎么接
给模块类加两个属性即可（`app/modules/base.py`）：
```python
chain_proj = True                              # 输入根默认 = PROJ；页顶显示「数据链」三行
chain_fill = ('case', 'src', 'mask_file')      # 输入是 PROJ 时自动填的字段
```
⑧ 的 `chain_fill = ('case', 'src')` —— **故意不填 `mask_file`**：
⑧ 的「载入已有掩模」被自动填了就会跳过画布，掩模必须由用户画/载入。

> **踩过的坑**：离屏测试脚本会把 `config\settings.json` 当成自己的存储 → 会覆盖用户真实配置。
> 写脚本时先 `S.FILE = Path('settings_test.json')`；跑 `app\main.py` 的脚本先备份、跑完还原。

---

## 四、目录结构

```
CEST_Studio\
├─ 启动.bat                    双击启动（GBK + CRLF，内含中文路径）
├─ app\
│   ├─ main.py                 入口（--open / --proj / --nav / --shot）
│   ├─ main_window.py          拖拽区 / 识别 / 输出目录 / 导航 / 全局日志
│   ├─ core_path.py            ★ 接入核心层（改 CORE_LIB 只改这里）
│   ├─ theme.py                统一外观（Fusion + QSS；换配色只改这里）
│   ├─ pages\base_page.py      通用模块页：由 schema 自动生成界面
│   ├─ widgets\                drop_zone / log_panel / recon_plan（① 的序列·频率面板）
│   │                          mask_canvas（⑧ 画布控件 SliceCanvas / MaskTarget / MaskCanvasPanel）
│   │                          autosize（fit_label：自动换行状态标签的高度自适应）
│                          flow_layout（FlowLayout / FlowWidget：窄了自动折行的容器，数据链按钮用）
│   ├─ dialogs\freq_dialog.py  频率表对话框（检索/继承/载入/预设/手动 + 校验 + 确认）
│   ├─ dialogs\browse_dialog.py 目录预览（双击进入 / 勾选序列 / 补齐同组 / 分析维度）
│   ├─ dialogs\mask_canvas_dialog.py  ★ ⑧ 去噪掩模 + 器官 ROI 画布（层览/勾画/SNR/ROI底图/色标/导出）
│   ├─ dialogs\mosaic_view_dialog.py  ★ ② 多层图谱交互预览（层览/缩放平移/实时色标/导出）
│   ├─ dialogs\params_dialog.py       ★ 通用「高级参数…」对话框（按 group 分组 + 预设 + 校验）
│   ├─ dialogs\clean_dialog.py        ★ ④「清空旧结果…」（.bak 备份 / 整个病例子目录）
│   └─ modules\                ★ 模块适配层
│       ├─ base.py             Module / Field 协议（Field.kinds/modes 控制显隐）
│       ├─ registry.py         注册表（加模块只改这里）
│       ├─ m1_recon.py         ① 重建 + 序列匹配 + 3D/4D + 频率门禁
│       ├─ m_roi.py            ⑧ 去噪掩模 + 器官 ROI（画布 + 落盘 Mask/Thmask/liverMask/snrMask/ROI_total + 确认图）
│       ├─ m4_ld.py            ④ 逐体素LD（洛伦兹拟合；门禁 + 参数全在 GUI + 逐层进度）
│       ├─ m2_display.py       ② 多层图谱展示（掩模门禁 + _cache 复用 + 交互预览）
│       └─ m0_check.py         ⑦ 诊断/自检
├─ jobs\    ctx.py（log/progress/cancel）+ worker.py（QThread + print 重定向）
├─ detect\
│   ├─ rules.py                目录指纹
│   ├─ scanner.py              识别类型 → 列可用模块
│   ├─ chain.py                ★ 数据链解析器（NEEDS / resolve / list_cases / stage_status）
│   ├─ series.py               ★ 序列扫描 + 3D/4D 判定 + 组合匹配（含标签别名）
│   ├─ manual_recon.py         ★ 用「显式指定的文件夹」重建（手动挑序列时用）
│   └─ freq_doc.py             ★ 频率来源：文档检索 / 继承 / 解析 / 校验 / 存档
├─ tools\validate_mask.py      ★ ⑧ 与 MATLAB 已有掩模的对照脚本（--reproduce / --check）
├─ tools\validate_roi.py       ★ ⑧ 器官 ROI 与 MATLAB ROI_total 的对照脚本（--reproduce / --check / --via-core）
├─ tools\validate_ld.py        ★ ④ 逐体素LD 的验证脚本（--parity 对核心层 / --check 对参考 / --run）
└─ config\settings.py          记住上次的目录与参数
```

---

## 五、怎么改（扩展点）

| 想干什么 | 改哪里 |
|---|---|
| 加参数 | 该模块的 `schema`（`Field(...)`），`kinds=` 限定识别类型、`modes=` 限定图类型 |
| 换算法/调用 | 该模块的 `run()` |
| 自动填参 / 校验 | `suggest()` / `validate()` |
| 改数据链（某模块要哪些上游产物 / 病例枚举） | `detect\chain.py` 的 `NEEDS` / `resolve` / `list_cases`；模块侧加 `chain_proj` / `chain_fill` |
| 改每页「输入根 → 解析结果」显示 / 病例下拉 / 灰按钮 | `app\pages\base_page.py` 的 `refresh_chain()` / `_on_case_changed()` / `_update_stage()` / `_apply_ready()` |
| 改输入根按模块存 / 拖拽区语义 | `app\main_window.py` 的 `_inputs` / `input_root_for()` / `_det_for()` / `set_input()` |
| 加一个模块 | 新建 `modules\mX_*.py` → 在 `registry.py` 注册 |
| 改序列匹配规则 | `detect\series.py` 的 `match_group` / `TAG_ALIAS` |
| 改「手动指定文件夹」的重建 | `detect\manual_recon.py` |
| 改频率来源/解析 | `detect\freq_doc.py` |
| 改识别规则 | `detect\rules.py` / `detect\scanner.py` |
| 改 ⑧ 画布交互/工具 | `app\widgets\mask_canvas.py`（画布）+ `app\dialogs\mask_canvas_dialog.py`（面板/动线） |
| 改 ⑧ 的 ROI 底图 / 色标 / ROI 产物 | `app\dialogs\mask_canvas_dialog.py`（ROI 对象 / 底图 / 色标）+ `app\modules\m_roi.py` 的 `_load_roi_z` / `build_roi_total` / `run()` 的 4.5 段 |
| 改 ⑧ 的掩模算法与产物口径 | `app\modules\m_roi.py` 的 `run()`（算法仍调 `cest_post` 的原函数） |
| 改 ② 多层图谱（门禁/缓存/命名） | `app\modules\m2_display.py`（算法仍调 `cest_display`） |
| 改 ② 交互预览 | `app\dialogs\mosaic_view_dialog.py`（画布复用 `app\widgets\mask_canvas.py` 的 SliceCanvas） |
| 改 ④ 逐体素LD（门禁/参数/产物） | `app\modules\m4_ld.py`（算法仍调 `cest_ld`）+ `Field(advanced=True, group=…)` + `app\dialogs\params_dialog.py` |
| 换配色/圆角/字号 | `app\theme.py` |

---

## 六、已知约定 / 注意

- **编号对应**：方案文档的 Ⓞ（诊断）在界面显示为 **⑦**、画布（方案里的 Ⓡ）显示为 **⑧**
  （圈字母 `Ⓞ`/`Ⓡ` 中文字体没有，会变方块）；业务主线 ①~⑥ 不变。
  导航顺序：**① 重建 → ⑧ 去噪掩模 → ④ 逐体素LD → ② 多层图谱展示 → ⑦ 诊断自检**。
- **轴序铁律**：核心层数组一律「层优先」`[X, Y, 层, 频]`，mat 带 `axis_order='layer_freq'`。
- **⑧ 画布右侧栏宽度按内容自适应**（约 470 px 起、上限 560），并关掉横向滚动条 ——
  不会出现「必须左右滑才能看全」的情况（ROI 下拉框、色标上下限、SNR 按钮都完整可见）。
- **两类 ROI 不要混**：② 用**去噪/背景掩模** `00_掩模\<病例>\Mask.mat`（去背景）；
  ③ 后处理用**器官 ROI** `00_掩模\<病例>\ROI_total.mat`（⑧ 已做：同目录的 `ROI_total.mat` / `ROI<k>.mat`）。
- **分隔条不能拖到没有**：每页「左栏|右栏」与「导航|页面」两处 splitter 都设了 `setChildrenCollapsible(False)` + `setCollapsible(0/1, False)`，两侧都不再能被折成 0 宽（最小 左 196 / 右 320）；数据链按钮改折行布局（`app\widgets\flow_layout.py`），窄窗口也排得下。
- **竖向分隔条（上下可拖，2026-10-20）**：只给「值得的」页装，其余页维持原样堆叠。
  左栏「参数区↔输出与运行」：③④⑤⑥②⑧（`m3_post/m4_ld/m5_prf/m6_prf_analysis/m2_display/m_roi`）；
  右栏「结果↔预览」：②④⑥⑧（`m2_display/m4_ld/m6_prf_analysis/m_roi`，这些页 `preview_image()` 才真出图）；
  ⓪①③⑤⑦ 预览区恒为空占位、①⓪⑦ 参数少，都不装。主窗口「导航|页面↔全局日志」也改成竖分隔条。
  实现：`base_page.py` 顶部 `VS_LEFT`/`VS_RIGHT` 两个 id 元组 + `_build()` 里条件套 `QSplitter(Vertical)`（`vsplitLeft`/`vsplitRight`；初值 左栏 `[620,230]`、右栏 `[280,420]`，日志 `[620,130]`），日志原来那句 `log_panel.setMaximumHeight(130)` 已删；`theme.py` 补了 `QSplitter::handle:vertical{height:8px}` 与 `:hover` 着色（否则竖向把手看不见）。所有竖条同样 `childrenCollapsible=False` + 两侧不可折叠。
- **② 的 LD 图**：先把 ④ 逐体素LD 跑出来（`PROJ\04_逐体素LD\<病例>\voxel_LD.mat`），② 选 LD 就能出。
- **④ 的拟合参数全在 GUI**：`Field(advanced=True, group='…')` 的参数不占主表单，统一进【高级参数…】
  （`app\dialogs\params_dialog.py`，编辑的是页面同一批控件 → `settings.json` / 运行天然一致）。
  归一化的分位 1/99、`do_rescale=False` 与 fast 的细化步长 (0.02→0.005) 是**核心层写死的**，已写在提示里。
- **② 的产物命名不要动**：`{MODE} {w}ppm_combined.png/.mat/.json`（小数点→下划线）由
  `combined_name()` 一处生成；同名重跑先备份 `.bak_<时间戳>`；中间文件只放 `02_展示\<病例>\_cache\`。
- **「数据链」块在右栏顶部**（2026-10-02 调整）：原放在页顶整行会吃掉左栏高度，
  把 ④/②/⑧ 面板的状态行压到低于最小高度 → 被下边框裁掉一行。挪到右栏后，
  三个面板拿到的都是自己的最小高度（实测 box/panel 高度 == 最小高度），不再被裁。
  状态标签统一用 `app\widgets\autosize.py` 的 `fit_label()`（按固定参考宽度定死高度，
  高度不随宽度变，QScrollArea 的估算才不会来回打架）。
- **「将生成…」会实时跟着参数变**（2026-10-02 修）：`base_page._build` 给所有 schema 控件接了
  `_on_any_param → refresh_preview`（纯拼字符串）；重活 `refresh_extra` 仍只挂在会改计划的
  6 个 key（src/cases/case/seq_base/slice_max/m0_tpi）上。之前 ② 把图类型改成 ST，
  下面仍写 APTW —— 那只是**显示旧值**，真跑（`run()` 现读参数）落盘的本来就是 ST。
  ★ 加新参数控件后不用额外接线（自动接上）。
- **② 的自适应色标口径**：在 `img .* Mask`（背景=0）上取非 0 体素的 5~95%
  （对齐 MATLAB `plotLayerQuantileCurve_new`），不是在背景 −1 的图上取。
- ② 的输入若是「重建结果」，会先自动跑轻量 `make_img_interp`（不做 LD 拟合）再出图。
- **② 的 `apply_mask` 不接受 2D 掩模**（MATLAB 有隐式扩展、numpy 没有）：
  ⑧ 的 `Mask.mat` / `Thmask.mat` 统一存 3D (X,Y,层)；外部若把 2D 掩模给 ② 会报 broadcast 错。
- **⑧ 的 `Thmask.mat` 与 MATLAB 口径不同**：MATLAB 里它是 SNR 掩模（不会被覆盖），
  Python `run_post` / ⑧ 里它是「组织 × SNR」的组合掩模；⑧ 另存 `snrMask.mat` 保全信息。
- 采集序（`layer_freq`/`freq_layer`）由 DICOM 标签自动判定；本批 UIH 数据实测为 **freq_layer**，
  与旧的 `CEST_result`（按 `layer_freq` 分组）**分组不同**——强制 `layer_freq` 重跑可逐位复现旧结果
  （`max|diff|=0`），属既定行为，不是 bug。
- 出图走 matplotlib 的 Agg；日志同时落盘 `PROJ\日志\<模块>_<时间戳>.log`。
- **报告结果一律以核心层为准；本壳不改任何算法。**
- **① 的“多余帧”不再猜（2026-10-05 改）**：每层帧数 > 频偏数时，多出的帧由
  `cest_recon.resolve_frame_offsets` 按 **z 谱 ±k 镜像对称**（远共振权重更高）枚举判定为
  「额外帧」并**丢弃**（不再按 .prot 的 TargetFreq 猜“重复的 3.5”）；若某多余帧与相邻帧
  几乎相同(corr≥0.995) 则判为重复采集、并入平均。日志会写“判定第 N 帧为额外帧 → 丢弃
  （谱线不对称度 x%）”，>10% 会 ⚠ 报警。频率表可以只给**唯一频偏**（少于帧数）——UIH 允许、
  Philips 仍要求频偏数恰好 = 帧数−M0。旧的 31 项表（含重复 3.5）仍能用，结果一致。
- **④/② 的 LD 图异常先查两处（2026-10-04）**：
  1. `cest_ld.voxel_ld_filter_fast` 曾把洛伦兹符号写反（`best[bi,2] = -a` → 应为 `+a`），
     使近水处冒出假峰、LD 整体不可信；已修（与 `voxel_ld_filter`/MATLAB 对齐，max diff 1.20→0.013）。
     改过核心层后必须**重跑 ④**（② 才跟着更新）。
  2. 本协议的 Ssat 是 **30 个频偏 + 1 帧额外（水参考）**，但 `detect/freq_doc.py` 按
     `TargetOffsetAverage=2` 补了一个「重复 3.5」，把最后那帧标成了 **+10ppm**，
     而 +10 是 LD 基线拟合区间之一 → 基线被拉低 → LD@3.5≈0 → ② 全黑。
     实测 8/9 例末帧都塌到 ~8%，且原始帧 1..30 的 ±k 谱线近乎完美对称（第 31 帧像 +0.5ppm 水峰侧）。
- **双击「启动 CEST Studio.vbs」没反应 = 窗口被 SW_HIDE 吞了（2026-10-04 修）**：vbs 原写
  `sh.Run cmd, 0`（`0`=SW_HIDE），会把它塞进子进程 STARTUPINFO，覆盖 Qt 主窗**第一次**
  `ShowWindow` → 进程在跑（CPU 停、Responding=True）、窗口就是不出现、日志只有一行「启动」。
  已改成 `sh.Run cmd, 1`；`app\main.py` 在 `win.show()` 后另加了
  `ShowWindow(hwnd, SW_SHOW) + SetForegroundWindow` 兜底（任何 SW_HIDE 入口都能显示）。
  再遇到「任务管理器里有 pythonw、界面没有」优先查这条，别去查依赖/数据。

> 目录名说明：PROJ 下的固定子目录用**中文名**（00_掩模 / 01_重建 / 02_展示 / 03_后处理 / 04_逐体素LD / 05_PRF提取 / 06_PRF分析 / 日志）；已跑过的老项目里的英文名（00_roi / 01_recon / …）**仍然能识别**，不用改名。

- **④ 的拟合进度（2026-10-02）**：进度条按整条流水线折算 0~100（读数据 → 归一化 → 掩模 →
  逐层 LD 拟合 → 保存 → 质检图）；下面一行文字每层刷新「第 k/n 层 · 本层 x.xs · 均 x.xs · 剩 xx s」，
  并由 `base_page` 的每秒心跳（QTimer）追加「⏱ 已用 xx s」—— **运行中界面永远在动**。
- **绝对不要在日志链里 print**：运行期 `jobs/worker.py` 会把**全进程** `sys.stdout/stderr`
  换成 `_StreamToLog`（→ 发日志信号）。日志处理链里任何一次 `print`/warning 都会
  write → emit → print → write 无限递归 → **栈溢出、进程无声死亡**
  （表现：日志只剩一行、进度永远 0%）。已加重入保护，但新增日志槽时仍不要再 print。
- **输入根按模块独立（2026-10-02）**：`settings.json` 的 `inputs = {模块id: 目录}`；
  ① 吃原始 DICOM，⑧④②（③…）不设就**默认 = 输出总目录 PROJ**；切页各自载入、互不覆盖。
  页顶「数据链」三行实时显示 病例 / 数据 / 掩模，缺数据红字 + 【开始】灰（详见「三之六」）。
  旧的单一 `input_root` 仍兼容：没有 `inputs` 时把它归给 `module` 字段里记的那个模块（不是 ①）。
- **自动化/驱动脚本**：`QTest.mouseClick(拖拽区)` 会弹模态确认框，脚本必须打桩
  `QMessageBox.exec` 与 4 个静态方法，否则事件循环被堵住、看起来像卡死；
  被用户打断的工具调用不会杀死 `Start-Process` 起的子进程，记得清理。

---


## 七之二、版本号与「一键更新」（2026-10-15）

- 版本号就一行，在 `CEST_Studio\VERSION`（如 `1.0.1`）。启动时读进**窗口标题**
  和日志第一行（`CEST Studio 已启动。（v1.0.1）`）——用户报版本念这一句就够。
- 交付分两种：
  * **完整免安装包**（约 470 MB，带 `runtime\`）：只在首次安装、或运行环境 / 依赖变了时才发。
  * **代码更新包**（约 0.3 MB，`CEST-Studio-更新-vX.Y.Z.zip`）：平时改 .py / 文档就发这个。
- 怎么生成：双击 `_dev\导出代码更新包.bat`（即 `导出代码更新包.py`）。版本号默认自动 +1；
  也支持 `--version 1.1.0` / `--keep-version` / `--note "改了什么"` / `--dry-run`；默认输出到桌面。
  加 `--bootstrap` 会另出一个 6 KB「引导包」，给还没装过更新器的老用户手动覆盖一次。
- 用户怎么装：双击安装目录里的 **`应用更新.bat`** → 选那个 zip → 回车。
  实际逻辑在 `CEST_Studio\tools\apply_update.py`：
  1. **拦脏包** —— 包里一旦出现 `runtime\` / `_dev\` / `logs\` / `settings.json` /
     `__pycache__` / `*.bak_*` / `*.log`，整包拒绝（更新包只该装代码）；
  2. 按包内 `update_manifest.json` 逐文件核对 **sha256**，对不上就整包不装；
  3. 要覆盖的旧文件先**改名移动**到 `_bak_更新_<时间戳>\`（同盘改名，瞬间、不占额外空间）；
  4. 逐文件写入后再复核 sha256；**任何一步失败 → 自动全量回滚**，本地恢复原样；
  5. 报「旧版本 → 新版本」，提示关掉窗口重新双击「启动 CEST Studio.vbs」。
- **绝不动**：`runtime\`、`_dev\`、`logs\`、`config\settings.json`、用户自己的数据目录。
- 更新包内容白名单：顶层启动脚本 / 说明文件 + `CEST_Studio\` + `03_Python_全链路\`；
  自动排除 `logs/ __pycache__/ *.pyc *.pyo *.log *bak* settings.json`。
- 版本倒挂（包里比本地旧）会被拦下，要装得加 `--force`；本地同版本会提示「没什么可更新」。
- **2026-10-16 实测（真实老用户路径）**：把 2026-10-06 的完整包解压成安装目录（13832 文件 / 498 MB，
  没有 `应用更新.bat`），走一遍「引导包覆盖一次 → 双击 `应用更新.bat` 装 v1.0.1」：退出码 0、76 个文件更新、
  旧文件进 `_bak_更新_<时间戳>\`、`runtime\` 与 `config\settings.json` 哈希前后一致、逐文件与源码比对 75/75 一致；
  用安装目录自带 runtime 离屏启动，标题显示 `v1.0.1`。脏包 / 篡改包 / 装错目录 / 版本倒挂全部拦下。
- **便携「更新工具」文件夹 = 自包含、双击就更新（2026-10-16 改）**：`cest-gui\更新工具\` 里是
  `应用更新.bat` / `apply_update.py` / `怎么用.txt` / `更新包\CEST-Studio-更新-vX.Y.Z.zip`（自带包）。
  整个文件夹拷进**任意版本**的 cest-gui（放哪一层都行），**双击 `应用更新.bat` 就完事**：
  bat 自己往上找安装目录（认 `CEST_Studio\app\main.py` + `runtime\python.exe`），自己用 `更新包\` 里最新的 zip，
  带 `--yes` 一路装完（中途不用按键），末尾按任意键关窗；找不到目录红字报错（exit 1）。
  只有「把一个 zip 拖到 bat 上」时才用你拖的那个；`更新包\` 里没 zip 才退化成弹文件框。
  ⚠ 判断是走拖拽还是自带包，依据是 `if exist "%~1"`（%~1 真的是个存在的文件），**不是**「有没有参数」——
  否则带 `--dry-run` / `--yes` 之类开关时会退化成弹窗（这个坑踩过，已修）。
- **规则（写死，别偷懒）：代码 / 版本一改，`更新工具\` 必须跟着刷新。**
  `导出代码更新包.py` 出完包会**自动**调 `_dev\生成更新工具.py`：① 从 `CEST_Studio\tools\apply_update.py`
  拷一份最新的过去、重写 `应用更新.bat` + `怎么用.txt`（版本号写进说明抬头）；② 把刚出的 zip 放进 `更新包\`
  （旧包挪进 `更新包\_bak_<时间戳>\`，不删）；③ 在桌面另打一份 `发这个_更新工具_vX.Y.Z.zip`。
  所以 `更新工具\apply_update.py` 永远是**源码的一份拷贝**，不许手抄第二份逻辑、不许留旧版本；
  手动刷：双击 `_dev\生成更新工具.bat`（可加 `--pkg "…zip" --zip-out "桌面"`）；
  自检：`python _dev\生成更新工具.py --check`（exit 0 = 两份 sha256 一致）；出包时加 `--no-tool` 才跳过。
- **发更新给对方的标准动作（只有一个东西）**：把桌面 **`发这个_更新工具_vX.Y.Z.zip`**（约 350 KB）发过去。
  对方解压进自己的 cest-gui（和「启动 CEST Studio.vbs」同一层最省事）→ 双击 `更新工具\应用更新.bat` →
  不用点任何东西就装完 → 关掉窗口重新双击「启动 CEST Studio.vbs」。
  已装过旧「更新工具」的人，也可以照旧双击安装目录里的 `应用更新.bat`、手动选 `CEST-Studio-更新-vX.Y.Z.zip`。
- **已废弃**：`--bootstrap` 引导包（2026-10-16 删）——自包含的 `更新工具\` 已覆盖「老用户没有更新器」，发一个东西就够。

- **`CEST_Studio\VERSION` 永远等于「最近一次出包的版本号」**（出包时自动写进去，默认 +1）。
  出包前扫一眼：若它还停在老版本（例如已发出 1.0.1、本地还是 1.0.0），自动 +1 会再出一份 1.0.1，
  老用户会被「没什么可更新」挡住 —— 这种情况用 `--version 1.0.2` 明确指定新版本号。
