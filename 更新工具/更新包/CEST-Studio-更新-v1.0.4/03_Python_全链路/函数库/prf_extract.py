# -*- coding: utf-8 -*-
"""
prf_extract.py —— 【提取】把重建好的 CEST 数据变成随机森林能吃的训练集
============================================================================

三个功能（对应 MATLAB 版 prf_build_regions / prf_build_subjects / prf_batch）：

  1. ``build_regions``   同一个体 → 不同部位（数据在一个文件夹里）
  2. ``build_subjects``  不同个体 → 同一部位（数据在两个文件夹里）
  3. ``batch``           批量循环 + 统一保存 + 生成 savepaths.mat

加上底层工具：

  * ``load_features``  目录 → (X [N×F], S0v [N], meta)
  * ``gen_paths``      扫目录 → 记录表（flat / hier / cestpost 三种结构）

约定（与 MATLAB 版完全一致，便于对照）
--------------------------------------
* **ROI 编号从 1 开始**（``roi_idx=1`` 表示第 1 个 ROI），和 MATLAB 习惯一致。
* **ROI 奇偶定左右**：奇数 ROI = 左侧（标签 1），偶数 ROI = 右侧（标签 0）。
* 数组按 **列优先（order='F'）** 展平，和 MATLAB ``reshape(V,[],F)`` 逐位一致。
* 频偏点数 F 自动识别，不写死。
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from cest_io import (find_files, load_first, load_mat, save_set)

VAR_DATA = ('V_exp', 'V_as')
VAR_S0 = ('S0', 'S0m')
VAR_ROI = ('ROI_total', 'ROI', 'roi_total')
VAR_W = ('w_offset',)


# ===========================================================================
# 一、底层：ROI 规范化
# ===========================================================================
def _normalize_roi(roi, nxy):
    """
    把各种形态的 ROI 统一成 ``[0-based 索引数组, …]`` 列表。

    支持：scipy 读出来的 cell（object 数组）/ 逻辑 mask / 0-1 图 / 1-based 索引向量。
    """
    if roi is None:
        return [np.arange(nxy)]

    if isinstance(roi, (list, tuple)):
        items = list(roi)
    else:
        a = np.asarray(roi)
        if a.dtype == object:                     # MATLAB cell
            items = list(a.ravel())
        else:
            items = [a]

    out = []
    for item in items:
        it = item if isinstance(item, np.ndarray) else np.asarray(item)

        if it.dtype == bool:                      # 逻辑 mask
            out.append(np.where(it.ravel(order='F'))[0])
            continue
        if it.dtype.kind in 'iu':
            v = it.ravel(order='F').astype(np.int64)
            if v.size == nxy and v.min() >= 0 and v.max() <= 1:
                out.append(np.where(v > 0)[0])    # 0/1 图
            else:
                out.append(np.maximum(v - 1, -1))  # 1-based → 0-based
            continue
        if it.dtype.kind == 'f':
            v = it.ravel(order='F')
            out.append(np.maximum(v.astype(np.int64) - 1, -1))
            continue
        raise TypeError(f'无法识别的 ROI 类型：{it.dtype}')
    return out


# ===========================================================================
# 二、底层：目录 → 特征矩阵
# ===========================================================================
def _layer_of_dir(p):
    """从 .../sliceN/... 目录名里抠出 1-based 层号；抠不到 → None。"""
    for part in reversed(str(p).replace('\\', '/').split('/')):
        m = re.fullmatch(r'slice[ _-]?(\d+)', part.strip(), re.I)
        if m:
            return int(m.group(1))
    return None


def _roi_at_layer(roi_file, nxy, layer, var_roi=VAR_ROI):
    """取 ROI 列表（0-based 线性索引，口径同 _normalize_roi）。

    若 ROI 文件里有 ROI_total_layers（⑧ 逐层画过）且给了 layer（1-based）：
    每个 ROI 优先用该层单独画的那张；该 ROI 是 2D 共用的 → 回退到 ROI_total；
    该层确实没画（哨兵 -1）→ 空数组（上层按「本层该 ROI 0 体素」处理）。
    """
    raw = load_first(roi_file, var_roi) if roi_file else None
    base = _normalize_roi(raw, nxy)
    if roi_file is None or layer is None:
        return base
    per = load_first(roi_file, ('ROI_total_layers',))
    if per is None:
        return base
    outer = np.asarray(per, dtype=object).reshape(-1)
    if outer.size != len(base):
        return base
    z = int(layer) - 1
    out = []
    for k, cel in enumerate(outer):
        inner = np.asarray(cel, dtype=object).reshape(-1)
        if inner.size == 0 or not (0 <= z < inner.size):
            out.append(base[k])                             # 2D 共用 → 回退
            continue
        e = np.rint(np.asarray(inner[z]).ravel()).astype(np.int64)
        if e.size == 1 and e[0] == -1:
            out.append(np.array([], dtype=np.int64))        # 该层确实没有
        elif e.size:
            out.append(np.maximum(e - 1, -1))               # 1-based → 0-based
        else:
            out.append(base[k])
    return out


def load_features(src_dirs, norm=0, roi_idx=None, roi_dir=None, epsilon=1e-8,
                  verbose=True, var_data=VAR_DATA, var_s0=VAR_S0,
                  var_roi=VAR_ROI, var_w=VAR_W, layer=None):
    """
    从 slice 目录提取「体素 × 频偏」特征矩阵。

    Parameters
    ----------
    src_dirs : 目录 或 目录列表（多个会纵向拼成一个大矩阵）
    norm     : 0 = 直接取原始信号（默认）；1 = 除以 S0 + epsilon
    roi_idx  : 用哪几个 ROI（**1 开始**）。None = 全部；1 = 只取 ROI 1
    roi_dir  : ROI 与数据不同目录时指定（可为列表，与 src_dirs 对应）
    layer    : 1-based 层号。None（默认）= 按每个 src_dir 的 sliceN 名自动认层；
               ⑧ 逐层画过 ROI 时各层取自己那张（缺层回退 2D 共用）

    Returns
    -------
    X    : (N, F) 特征矩阵
    S0v  : (N,) 对应体素的 S0
    meta : dict，含 w_offset / n_freq / n_voxel / n_left / n_right /
           idx_left / idx_right（0-based 行号）/ src_dirs / per_src
    """
    if isinstance(src_dirs, (str, Path)):
        src_dirs = [src_dirs]
    src_dirs = [Path(d) for d in src_dirs]
    if roi_dir is not None and isinstance(roi_dir, (str, Path)):
        roi_dir = [Path(roi_dir)]

    blocks, s0_blocks, per_src = [], [], []
    idx_left, idx_right = [], []
    n_vox, n_freq, w_off = 0, None, None
    eps = float(epsilon)

    for i, d in enumerate(src_dirs):
        ff = find_files(d, verbose=False)
        if ff['data'] is None:
            raise FileNotFoundError(
                f'在 {d}（含 Self_corr / 上两级）里找不到 {" / ".join(VAR_DATA)}。'
                f'前置包 runCESTpost 跑完了吗？')

        # ---- 数据 ----
        V = load_first(ff['data'], var_data)
        if V is None:
            raise ValueError(f'{ff["data"]} 里没有 {" / ".join(var_data)}')
        V = np.asarray(V)
        if V.ndim == 2:
            V = V[:, :, None]
        else:
            V = np.squeeze(V)
            if V.ndim == 2:
                V = V[:, :, None]
        if V.ndim == 4:
            V = V.reshape(V.shape[0], V.shape[1], -1)
        if V.ndim != 3:
            raise ValueError(f'{d} 的数据维度是 {V.shape}，需要 [x × y × F]')

        nx, ny, F = V.shape
        nxy = nx * ny
        V2D = V.reshape(nxy, F, order='F')     # ★ 列优先，和 MATLAB 一致

        if n_freq is None:
            n_freq = F
        elif F != n_freq:
            print(f'  [跳过] {d}：频偏点数 {F} 与首个来源的 {n_freq} 不一致')
            continue

        # ---- 参考图 S0 ----
        S0 = load_first(ff['s0'], var_s0)
        if S0 is None:
            S0f = np.ones(nxy)
            if verbose:
                print(f'  [提示] {d} 没有 S0，按全 1 处理')
        else:
            S0f = np.asarray(S0).ravel(order='F')
            if S0f.size < nxy:
                raise ValueError(f'{d} 的 S0 只有 {S0f.size} 个元素，少于图像的 {nxy}')
            S0f = S0f[:nxy]

        # ---- ROI ----
        rdir = roi_dir[i] if roi_dir is not None and i < len(roi_dir) else None
        if rdir is None:
            roi_file = ff['roi']
        else:
            roi_file = find_files(rdir, verbose=False)['roi']
        roi_list = _roi_at_layer(roi_file, nxy,
                                 layer if layer is not None else _layer_of_dir(d),
                                 var_roi=var_roi)

        if roi_idx is None:
            sel = list(range(1, len(roi_list) + 1))
        else:
            sel = [int(k) for k in np.atleast_1d(roi_idx)]
        sel = [k for k in sel if 1 <= k <= len(roi_list)]
        if not sel:
            raise ValueError(f'{d} 里没有可用的 ROI（roi_idx={roi_idx}）')

        # ---- 按 ROI 取时间序列 ----
        cnt = []
        for k in sel:
            idx = roi_list[k - 1]
            idx = idx[idx >= 0]
            if idx.size == 0:
                cnt.append(0)
                continue
            blk = V2D[idx, :]
            if norm == 1:
                blk = blk / (S0f[idx] + eps)[:, None]
            blocks.append(blk)
            s0_blocks.append(S0f[idx])
            cnt.append(idx.size)

        start = n_vox
        ends = np.cumsum(cnt)
        starts = ends - np.array(cnt) + 1
        for j, k in enumerate(sel):
            if cnt[j] == 0:
                continue
            rows = np.arange(start + starts[j] - 1, start + ends[j])   # 0-based
            (idx_left if k % 2 == 1 else idx_right).append(rows)       # 奇=左，偶=右

        n_vox += int(sum(cnt))
        per_src.append({'dir': str(d), 'n_voxel': int(sum(cnt)), 'roi_sel': sel})

        # ---- 频偏（取第一个非空的）----
        if w_off is None:
            w = load_first(ff['w'], var_w)
            if w is not None:
                w_off = np.asarray(w).ravel()

    if not blocks:
        raise ValueError('没有任何可用的数据来源')

    X = np.vstack(blocks)
    S0v = np.concatenate(s0_blocks) if s0_blocks else np.zeros(X.shape[0])
    meta = {
        'w_offset': w_off,
        'n_freq': n_freq,
        'n_voxel': X.shape[0],
        'idx_left': np.concatenate(idx_left) if idx_left else np.array([], dtype=int),
        'idx_right': np.concatenate(idx_right) if idx_right else np.array([], dtype=int),
        'src_dirs': [str(d) for d in src_dirs],
        'per_src': per_src,
        'norm': norm,
    }
    meta['n_left'] = int(meta['idx_left'].size)
    meta['n_right'] = int(meta['idx_right'].size)

    if verbose:
        print(f'[load_features] {len(src_dirs)} 个来源 → {X.shape[0]} 体素 × {X.shape[1]} 频偏'
              f'（左 {meta["n_left"]} / 右 {meta["n_right"]}），norm={norm}，roi_idx={roi_idx}')
    return X, S0v, meta


# ===========================================================================
# 三、功能 1：同一个体 → 不同部位
# ===========================================================================
def build_regions(src_dirs, out_dir, region_roi_idx=None, region_names=None,
                  label_style='binary', norm=0, epsilon=1e-8, roi_dir=None,
                  add_to_path_list=True, path_list_file=None, verbose=True):
    """
    把 ``ROI_total`` 里的多个 ROI 当成「部位」，输出 trainData / trainLabel / w_offset。

    Parameters
    ----------
    region_roi_idx : 每个部位用哪些 ROI（**1 开始**）的列表。
                     默认按 ROI 奇偶分两组：``[[1,3,5…], [2,4,6…]]``
    label_style    : 'binary'（默认，两组时 = 1/0）| 'index'（= 1..G）

    Returns
    -------
    (rec, X_list)：rec 是保存信息；X_list 是每个部位的特征矩阵
    """
    if isinstance(src_dirs, (str, Path)):
        src_dirs = [src_dirs]
    src_dirs = [Path(d) for d in src_dirs]

    # 先数一下有几个 ROI
    ff = find_files(src_dirs[0], verbose=False)
    rdir = roi_dir[0] if isinstance(roi_dir, (list, tuple)) else roi_dir
    if rdir is None:
        roi_file = ff['roi']
    else:
        roi_file = find_files(rdir, verbose=False)['roi']
    raw_roi = load_first(roi_file, VAR_ROI) if roi_file else None
    # 数 ROI：MATLAB cell 经 scipy 读出来是 ndarray(dtype=object)，不是 list/tuple
    if raw_roi is None:
        n_roi = 1
    elif isinstance(raw_roi, (list, tuple)):
        n_roi = len(raw_roi)
    else:
        _a = np.asarray(raw_roi)
        n_roi = int(_a.ravel().size) if _a.dtype == object else 1

    if region_roi_idx is None:
        if n_roi < 2:
            raise ValueError(
                f'{roi_file} 里只有 {n_roi} 个 ROI，没法按部位划分；'
                f'请显式给 region_roi_idx，或改用 build_subjects')
        region_roi_idx = [list(range(1, n_roi + 1, 2)), list(range(2, n_roi + 1, 2))]
    G = len(region_roi_idx)
    if region_names is None:
        region_names = [f'部位{g + 1}' for g in range(G)]

    print('========== build_regions（同一个体 / 不同部位）==========')
    print('数据：' + '  +  '.join(str(d) for d in src_dirs))
    print('部位划分：' + '  '.join(
        f'{region_names[g]}=ROI{region_roi_idx[g]}' for g in range(G)))

    X_list, n_vox_g = [], []
    for g in range(G):
        X, _, m = load_features(src_dirs, norm=norm, epsilon=epsilon,
                                roi_idx=region_roi_idx[g], roi_dir=roi_dir,
                                verbose=verbose)
        X_list.append(X)
        n_vox_g.append(X.shape[0])
        if g == 0:
            w_off = m['w_offset']

    X_all = np.vstack(X_list)
    if label_style == 'index':
        y_all = np.concatenate([np.full(n_vox_g[g], g + 1) for g in range(G)])
    else:
        y_all = np.concatenate([np.full(n_vox_g[g], G - g - 1) for g in range(G)])  # 两组 → 1/0

    rec = save_set(out_dir, X_all, y_all, w_off,
                   add_to_path_list=add_to_path_list,
                   path_list_file=path_list_file, verbose=verbose)
    rec['n_region'] = G
    rec['n_voxel_g'] = n_vox_g
    return rec, X_list


# ===========================================================================
# 四、功能 2：不同个体 → 同一部位
# ===========================================================================
def build_subjects(src1, src2, out_dir, roi_idx=1, norm=0, epsilon=1e-8,
                   roi_dir1=None, roi_dir2=None, name1='标签1', name2='标签0',
                   add_to_path_list=True, path_list_file=None, verbose=True,
                   bar_plot=False, bar_tag=None, bar_range='clinical'):
    """
    标签 1 / 标签 0 两个来源各取「同一个部位」，合并成一个训练集。

    ``src1`` / ``src2`` 可以是单个目录，也可以是目录列表（多个文件夹合并成同一标签）。
    ``roi_idx`` 指定同一个部位（**1 开始**），默认 1。

    ``bar_plot=True`` 时额外画「均值 / 标准差柱状图」（CEST 峰区间已合并），
    即 MATLAB ``comb_prf_data_zunjian.m`` 后半段那两张图，文件名
    ``Mean_bar{tag}.png`` / ``Std_bar{tag}.png``，``tag`` 默认取路径里的「结果N」
    拼成 ``_2vs3``。
    """
    print('========== build_subjects（不同个体 / 同一部位）==========')
    print('标签1：' + '  +  '.join(str(d) for d in np.atleast_1d(_as_list(src1))))
    print('标签0：' + '  +  '.join(str(d) for d in np.atleast_1d(_as_list(src2))))
    print(f'部位(ROI)：{roi_idx}')

    X1, _, m1 = load_features(_as_list(src1), norm=norm, epsilon=epsilon,
                              roi_idx=roi_idx, roi_dir=roi_dir1, verbose=verbose)
    X2, _, m2 = load_features(_as_list(src2), norm=norm, epsilon=epsilon,
                              roi_idx=roi_idx, roi_dir=roi_dir2, verbose=verbose)

    X = np.vstack([X1, X2])
    y = np.concatenate([np.ones(X1.shape[0]), np.zeros(X2.shape[0])])
    w_off = m1['w_offset'] if m1['w_offset'] is not None else m2['w_offset']
    if w_off is None:
        print('  [警告] 两个来源都没有 w_offset')

    rec = save_set(out_dir, X, y, w_off,
                   add_to_path_list=add_to_path_list,
                   path_list_file=path_list_file, verbose=verbose)
    rec['n_voxel1'] = X1.shape[0]
    rec['n_voxel2'] = X2.shape[0]

    # ---- 前置数据的第一张图（对应 comb_prf_data_zunjian.m 后半段）----
    if bar_plot:
        from prf_analysis import plot_bar_pair
        n1 = _path_num(_as_list(src1)[0], '1')
        n2 = _path_num(_as_list(src2)[0], '2')
        tag = bar_tag if bar_tag is not None else f'_{n1}vs{n2}'
        plot_bar_pair(X1, X2, w_off, f'{n1}号 (标签1)', f'{n2}号 (标签0)',
                      out_dir, ppm_range=bar_range, tag=tag, verbose=verbose)
    return rec, (X1, X2)


def _path_num(p, default):
    """从路径里取「结果N」的 N（和 MATLAB 的 regexp '(?<=结果)\\d+' 一致）。"""
    m = re.search(r'结果(\d+)', str(p))
    return m.group(1) if m else str(default)


def _as_list(x):
    return [x] if isinstance(x, (str, Path)) else list(x)


# ===========================================================================
# 五、路径扫描
# ===========================================================================
def gen_paths(root_dir, mode='flat', sub_path='full_interp_0716/slice1',
              ratio_list=(), level1=('ctrl', 'model'), level2=('3H', '1W', '5W'),
              max_animal=6, pick_rule='min', filename='', stage_prefix='full_interp',
              verbose=True):
    """
    扫数据目录 → 记录表（dict 列表），每项含 g1 / g2 / name / dir / roi_dir。

    mode
    ----
    'flat'     临床数据：root/结果N/[ratio/]sub_path
    'hier'     大鼠数据：root/ctrl/1W/动物/MAT_from_*/数字/slice1（ROI 在 ROIs）
    'cestpost' 前置包输出：root/病例/full_interp_0803/sliceN/Self_corr
    """
    root = Path(root_dir)
    recs = []

    if mode == 'flat':
        ratios = list(ratio_list) if ratio_list else ['']
        for g1 in _sub_dirs(root):
            for g2 in ratios:
                d = root / g1 / g2 / sub_path if g2 else root / g1 / sub_path
                if d.is_dir():
                    recs.append({'g1': g1, 'g2': g2, 'name': _safe(f'{g1}_{g2}' if g2 else g1),
                                 'dir': d, 'roi_dir': d})

    elif mode == 'hier':
        for g1 in level1:
            for g2 in level2:
                p2 = root / g1 / g2
                if not p2.is_dir():
                    continue
                for animal in _sub_dirs(p2)[:max_animal]:
                    p3 = p2 / animal
                    sub = None
                    for cand in ('MAT_from_dicom', 'MAT_from_2dseq'):
                        if (p3 / cand).is_dir():
                            sub = cand
                            break
                    if sub is None:
                        continue
                    mat_path = p3 / sub
                    pick = _pick_num_dir(mat_path, pick_rule)
                    if pick is None:
                        continue
                    slice_dir = mat_path / pick / 'slice1'
                    if not slice_dir.is_dir():
                        continue
                    recs.append({'g1': g1, 'g2': g2, 'name': _safe(f'{g1}_{g2}_{animal}'),
                                 'dir': slice_dir, 'roi_dir': mat_path / 'ROIs'})

    elif mode == 'cestpost':
        for case in _sub_dirs(root):
            stage = _stage_dir(root / case, filename, stage_prefix)
            if stage is None:
                print(f'  [跳过] {case}：找不到 {stage_prefix}* 这类结果目录')
                continue
            for sl in _slice_dirs(stage):
                slice_dir = stage / sl
                self_dir = _selfcorr_dir(slice_dir)
                recs.append({'g1': case, 'g2': sl, 'name': _safe(f'{case}_{sl}'),
                             'dir': self_dir, 'roi_dir': stage})

    else:
        raise ValueError("mode 只能是 'flat' / 'hier' / 'cestpost'")

    if verbose:
        print(f'========== 扫描 {root}（{mode}）==========')
        print(f'  共 {len(recs)} 条记录')
        for i, r in enumerate(recs):
            print(f'  [{i + 1:02d}] {r["g1"]:<10} {r["g2"]:<8} {r["dir"]}')
        if not recs:
            print('  [警告] 一条都没扫到，检查 root_dir / sub_path / ratio_list')
    return recs


def _sub_dirs(p):
    p = Path(p)
    if not p.is_dir():
        return []
    return sorted([d.name for d in p.iterdir() if d.is_dir()])


def _slice_dirs(stage):
    nums = []
    for name in _sub_dirs(stage):
        m = re.fullmatch(r'slice(\d+)', name)
        if m:
            nums.append((int(m.group(1)), name))
    return [n for _, n in sorted(nums)]


def _pick_num_dir(mat_path, rule):
    nums = [(int(n), n) for n in _sub_dirs(mat_path) if n.isdigit()]
    if not nums:
        return None
    if rule == 'maxIfTwo':
        return sorted(nums)[1][1] if len(nums) == 2 else None
    return sorted(nums)[0][1]


def _stage_dir(case_dir, filename, prefix):
    if filename and (case_dir / filename).is_dir():
        return case_dir / filename
    cands = [case_dir / s for s in _sub_dirs(case_dir) if _slice_dirs(case_dir / s)]
    if not cands:
        return None
    pref = [c for c in cands if c.name.startswith(prefix)]
    return (pref or cands)[0]


def _selfcorr_dir(slice_dir):
    for sub in _sub_dirs(slice_dir):
        cand = slice_dir / sub
        if (cand / 'V_exp.mat').exists() or (cand / 'V_as.mat').exists():
            return cand
    return slice_dir


def _safe(s):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', str(s))


# ===========================================================================
# 六、功能 3：批量
# ===========================================================================
def batch(cfg):
    """
    批量循环 → 统一保存 → 生成 savepaths.mat。

    cfg 常用键
    ----------
    root_dir, out_dir               必填
    mode      'flat' / 'hier' / 'cestpost'
    task      'subject'（默认，不同个体同一部位）/ 'region'（同一个体不同部位）
    group_by  None / 'g1' / 'g2' / ('g1','g2')  每组一个训练集
    label1, label0    task='subject' 时：标签1 / 标签0 用到的 g1 名单
    roi_idx           task='subject' 时：同一个部位（1 开始），默认 1
    region_roi_idx    task='region' 时的部位划分
    norm, filename, sub_path, ratio_list, level1, level2, pick_rule, max_animal

    Returns
    -------
    (path_info, log)
    """
    root_dir = cfg['root_dir']
    out_dir = Path(cfg['out_dir'])
    out_dir.mkdir(parents=True, exist_ok=True)

    mode = cfg.get('mode', 'flat')
    task = cfg.get('task', 'subject')
    group_by = cfg.get('group_by', None)
    path_list_file = out_dir / 'savepaths.mat'

    print('\n%%%%%%%%%%%%%%%%%%%% batch 开始 %%%%%%%%%%%%%%%%%%%%')
    print(f'任务={task}  结构={mode}  分组={group_by}')

    recs = gen_paths(root_dir, mode=mode,
                     sub_path=cfg.get('sub_path', 'full_interp_0716/slice1'),
                     ratio_list=cfg.get('ratio_list', ()),
                     level1=cfg.get('level1', ('ctrl', 'model')),
                     level2=cfg.get('level2', ('3H', '1W', '5W')),
                     max_animal=cfg.get('max_animal', 6),
                     pick_rule=cfg.get('pick_rule', 'min'),
                     filename=cfg.get('filename', ''),
                     stage_prefix=cfg.get('stage_prefix', 'full_interp'))
    if not recs:
        print('  [警告] 没有扫到任何数据')
        return [], []

    # ---- 分组 ----
    if group_by is None or group_by == 'none':
        keys = ()
    elif isinstance(group_by, str):
        keys = (group_by,)
    else:
        keys = tuple(group_by)
    for k in keys:
        if k not in ('g1', 'g2'):
            raise ValueError("group_by 只能是 None / 'g1' / 'g2' / ('g1','g2')")

    grouped = {}
    for r in recs:
        gkey = tuple(r[k] for k in keys)
        grouped.setdefault(gkey, []).append(r)

    path_info, log = [], []
    task_verbose = cfg.get('verbose', True)

    for gkey, members in grouped.items():
        g_dir = out_dir
        for part in gkey:
            if part:
                g_dir = g_dir / _safe(part)
        print(f'\n---- 组 {"/".join(gkey) if gkey else "(全部)"}（{len(members)} 条记录）----')

        if task == 'subject':
            lab1 = set(np.atleast_1d(cfg.get('label1', [])))
            lab0 = set(np.atleast_1d(cfg.get('label0', [])))
            s1 = [m['dir'] for m in members if m['g1'] in lab1]
            s2 = [m['dir'] for m in members if m['g1'] in lab0]
            r1 = [m['roi_dir'] for m in members if m['g1'] in lab1]
            r2 = [m['roi_dir'] for m in members if m['g1'] in lab0]
            if not s1 or not s2:
                print(f'  [跳过] 标签1 {len(s1)} 条 / 标签0 {len(s2)} 条，缺一侧')
                continue
            rec, _ = build_subjects(s1, s2, g_dir, roi_idx=cfg.get('roi_idx', 1),
                                    norm=cfg.get('norm', 0),
                                    epsilon=cfg.get('epsilon', 1e-8),
                                    roi_dir1=r1, roi_dir2=r2,
                                    bar_plot=cfg.get('bar_plot', False),
                                    bar_tag=cfg.get('bar_tag') or None,
                                    bar_range=cfg.get('bar_range', 'clinical'),
                                    add_to_path_list=True, path_list_file=path_list_file,
                                    verbose=task_verbose)
            path_info.append(rec['out_dir'])
            log.append(rec)

        elif task == 'region':
            if cfg.get('merge_in_group', False):
                rec, _ = build_regions([m['dir'] for m in members], g_dir,
                                       region_roi_idx=cfg.get('region_roi_idx'),
                                       norm=cfg.get('norm', 0),
                                       epsilon=cfg.get('epsilon', 1e-8),
                                       roi_dir=[m['roi_dir'] for m in members],
                                       add_to_path_list=True, path_list_file=path_list_file,
                                       verbose=task_verbose)
                path_info.append(rec['out_dir'])
                log.append(rec)
            else:
                for m in members:
                    rec, _ = build_regions(m['dir'], g_dir / m['name'],
                                           region_roi_idx=cfg.get('region_roi_idx'),
                                           norm=cfg.get('norm', 0),
                                           epsilon=cfg.get('epsilon', 1e-8),
                                           roi_dir=m['roi_dir'],
                                           add_to_path_list=True,
                                           path_list_file=path_list_file,
                                           verbose=task_verbose)
                    path_info.append(rec['out_dir'])
                    log.append(rec)
        else:
            raise ValueError("task 只能是 'subject' / 'region'")

    if path_list_file.exists():
        from cest_io import read_path_info
        path_info = read_path_info(path_list_file)
        print(f'\n================ 批量完成 ================')
        print(f'共 {len(path_info)} 个训练集，路径清单：{path_list_file}')
        for i, p in enumerate(path_info):
            print(f'  [{i + 1:02d}] {p}')
    print('%%%%%%%%%%%%%%%%%%%% batch 结束 %%%%%%%%%%%%%%%%%%%%\n')
    return path_info, log
