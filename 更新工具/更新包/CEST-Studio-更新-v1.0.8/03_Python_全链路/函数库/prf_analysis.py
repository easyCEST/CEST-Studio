# -*- coding: utf-8 -*-
"""
prf_analysis.py —— 【分析】训练集 → 随机森林特征重要性 → 出图 / Excel
==========================================================================

把你原有的 13 个 ``prf_*.py`` 合并成一套（去重复）：

  单组   run_single          一个训练集出图 + Excel          ← prf_lkx_single / singlenew / smple
  多组   run_compare         多个训练集合并对比出图           ← prf_lkx_0720 / prf_循环 / prf_smple
  分组   run_group           按父目录分组（3H/1W/5W 各一组）   ← prf_lkx_group / group_mean / group_single_img
  左右   run_left_right      左/右脑分别分析（读 Left/Right 文件）← prf_lkx_left_right / left__right_mean / zrr

命令行：
    python prf_analysis.py --mode compare --savepaths "...\\savepaths.mat" --labels 0.40 1.20 1.60

公共约定
--------
* 有效频偏范围两套规格（3T 临床用 clinical，7T 大鼠用 rat7t），--range 切换。
* 5 个 CEST 峰区间合并（NOE / G-Am / PCr / Amine / APT），合并时可选求和或取均值。
* 随机森林固定随机种子，置换重要性重复 n_permute 次取均值±标准差。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import matplotlib                       # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt         # noqa: E402

from sklearn.ensemble import RandomForestClassifier      # noqa: E402
from sklearn.inspection import permutation_importance    # noqa: E402
from sklearn.metrics import r2_score                     # noqa: E402

from cest_io import load_mat, read_path_info             # noqa: E402

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.unicode_minus'] = False

# ---------------------------------------------------------------------------
# 默认参数（和 prf_lkx_0720.py / prf_循环.py 对齐）
# ---------------------------------------------------------------------------
N_PERMUTE = 10
N_ESTIMATORS = 100
MAX_DEPTH = 8
MIN_SAMPLES_LEAF = 3
RANDOM_STATE = 42

# 随机森林 + 置换重要性超参（主脚本 05 会显示；要改就改这几个常量）
RF_PARAMS = dict(n_estimators=N_ESTIMATORS, max_depth=MAX_DEPTH,
                 min_samples_leaf=MIN_SAMPLES_LEAF, random_state=RANDOM_STATE,
                 n_permute=N_PERMUTE, perm_scoring='r2')

RANGE_PRESETS = {
    'clinical': ((-4.5, -1.2), (1.2, 4.5)),     # 3T 临床（肌少症）
    'rat7t':    ((-3.0, -0.5), (0.5, 3.0)),     # 7T 大鼠
}

PEAKS = [('NOE', -3.5, 0.2), ('G-Am', 2.0, 0.2), ('PCr', 2.6, 0.1),
         ('Amine', 3.0, 0.2), ('APT', 3.5, 0.2)]

# 与 MATLAB comb_prf_data_zunjian.m 的柱状图完全一致的峰名/位置
PEAKS_MATLAB = [('NOE', -3.5, 0.2), ('G-Amine', 2.0, 0.2), ('PCr', 2.6, 0.1),
                ('Amine', 3.0, 0.2), ('APT', 3.5, 0.2)]

COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#ffd700']


# ===========================================================================
# 一、数据装载
# ===========================================================================
def load_dataset(data_dir, range_mode='clinical', verbose=True):
    """
    读一个训练集目录，返回 ``(X, y, w_selected, valid_idx, w_full)``。

    ``valid_idx`` 是有效频偏在原始 w 里的下标（0-based），和旧脚本一致：
    ``trainData[:, valid_indices]``。
    """
    data_dir = Path(data_dir)

    def _need(name):
        got = load_mat(data_dir / name)
        if not got:
            raise FileNotFoundError(f'{data_dir / name} 不存在或为空')
        return list(got.values())[0]

    X = np.asarray(_need('trainData.mat'))
    y = np.asarray(_need('trainLabel.mat')).ravel()
    w_full = np.round(np.asarray(_need('w_offset.mat')).ravel(), 1)

    presets = RANGE_PRESETS.get(range_mode, RANGE_PRESETS['clinical'])
    mask = np.zeros(w_full.size, dtype=bool)
    for lo, hi in presets:
        mask |= (w_full > lo) & (w_full < hi)
    valid_idx = np.where(mask)[0]

    if verbose:
        print(f'  [load_dataset] {data_dir}')
        print(f'    数据 {X.shape}，标签分布 {int((y == 1).sum())}/{int((y == 0).sum())}，'
              f'有效频偏 {valid_idx.size}/{w_full.size} 个（{range_mode}）')
    return X, y, w_full[valid_idx], valid_idx, w_full


# ===========================================================================
# 二、随机森林 + 置换重要性
# ===========================================================================
def rf_importance(X, y, feature_names, n_permute=N_PERMUTE, n_estimators=N_ESTIMATORS,
                  max_depth=MAX_DEPTH, min_samples_leaf=MIN_SAMPLES_LEAF,
                  random_state=RANDOM_STATE, scoring='r2', verbose=True):
    """
    训练一个随机森林（固定种子），用 ``n_permute`` 次置换算重要性。

    Returns
    -------
    dict: raw (n_permute×F) / mean / std / n_sample / train_r2 / oob
    """
    feat_names = [f'{round(float(f), 2)}ppm' for f in np.asarray(feature_names).ravel()]
    df = pd.DataFrame(np.asarray(X), columns=feat_names)
    df['label'] = np.asarray(y)

    t0 = time.time()
    clf = RandomForestClassifier(
        n_estimators=n_estimators, n_jobs=-1, oob_score=True,
        max_depth=max_depth, min_samples_leaf=min_samples_leaf,
        random_state=random_state)
    clf.fit(df[feat_names], df['label'])

    res = permutation_importance(clf, df[feat_names], df['label'], scoring=scoring,
                                n_repeats=n_permute, random_state=123, n_jobs=-1)

    # ★ sklearn 版本差异：新版本 importances 是 (n_features, n_repeats)，
    #   旧版本（写 prf_lkx*.py 时用的）是 (n_repeats, n_features)。
    #   这里统一成 (n_repeats, n_features)，后续求和/标准差语义才和旧脚本一致。
    imp = np.asarray(res.importances)
    n_feat = len(feat_names)
    if imp.ndim == 1:
        imp = imp.reshape(1, -1)
    elif imp.shape[1] != n_feat and imp.shape[0] == n_feat:
        imp = imp.T

    out = {
        'raw': imp,
        'mean': np.asarray(res.importances_mean).ravel(),
        'std': np.asarray(res.importances_std).ravel(),
        'n_sample': int(len(df)),
        'train_r2': float(r2_score(df['label'], clf.predict(df[feat_names]))),
        'oob': float(getattr(clf, 'oob_score_', np.nan)),
        'feature_names': np.asarray(feature_names).ravel(),
    }
    if verbose:
        print(f'    训练R²={out["train_r2"]:.4f}  OOB={out["oob"]:.4f}  '
              f'每特征置换 {n_permute} 次 | 耗时 {time.time() - t0:.2f}s')
    return out


# ===========================================================================
# 三、CEST 峰区间合并
# ===========================================================================
def merge_peaks(w, mean_imp, std_imp, raw_imp, peaks=PEAKS, how='sum', verbose=True):
    """
    把 5 个 CEST 峰区间内的频偏合并成一个点，区间外的点各自保留。

    ``how``：'sum'（旧 Python 脚本的语义，区间内重要性求和）
             'mean'（MATLAB 柱状图的语义，区间内取均值）
    """
    w = np.asarray(w).ravel()
    raw = np.asarray(raw_imp)
    mean_imp = np.asarray(mean_imp).ravel()

    merged_mask = np.zeros(w.size, dtype=bool)
    new_raw, new_mean, new_w = [], [], []

    for center, err in [(p[1], p[2]) for p in peaks]:
        idx = np.where((w >= center - err) & (w <= center + err))[0]
        if idx.size == 0:
            continue
        blk = raw[:, idx]
        new_raw.append(blk.sum(axis=1, keepdims=True) if how == 'sum'
                       else blk.mean(axis=1, keepdims=True))
        new_mean.append(np.array([mean_imp[idx].sum() if how == 'sum' else mean_imp[idx].mean()]))
        new_w.append(np.array([center]))
        merged_mask |= (w >= center - err) & (w <= center + err)

    keep = np.where(~merged_mask)[0]
    if keep.size:
        new_raw.append(raw[:, keep])
        new_mean.append(mean_imp[keep])
        new_w.append(w[keep])

    if not new_raw:
        return w, mean_imp, std_imp, raw

    w2 = np.concatenate(new_w)
    raw2 = np.hstack(new_raw)
    mean2 = np.concatenate(new_mean)
    std2 = raw2.std(axis=0)

    order = np.argsort(w2)
    if verbose:
        print(f'    区间合并完成：{w.size} → {w2.size} 个特征（{how} 语义）')
    return w2[order], mean2[order], std2[order], raw2[:, order]


# ===========================================================================
# 四、前置数据第一张图：均值 / 标准差柱状图
#      —— 逐行对应 MATLAB comb_prf_data_zunjian.m 的后半段
# ===========================================================================
def bar_table(voxel1, voxel2, w_offset, ppm_range='clinical', peaks=None):
    """
    算出柱状图要画的峰区间合并表。

    规则（与原 MATLAB 脚本一致）：
      * 有效范围默认 (-4.5,-1.2) ∪ (1.2,4.5) ppm
      * 5 个峰区间内的频偏合并成一根柱：把「体素 × 区间内所有频偏点」全部展平后求均值/标准差
      * 其余有效频偏点各自一根柱
      * 最后按 ppm 降序排列
    """
    ppm_all = np.asarray(w_offset).ravel()
    voxel1 = np.asarray(voxel1, dtype=float)
    voxel2 = np.asarray(voxel2, dtype=float)
    mean1 = np.nanmean(voxel1, axis=0)
    std1 = np.nanstd(voxel1, axis=0, ddof=1)      # MATLAB std 是 N-1
    mean2 = np.nanmean(voxel2, axis=0)
    std2 = np.nanstd(voxel2, axis=0, ddof=1)

    rng = RANGE_PRESETS.get(ppm_range, RANGE_PRESETS['clinical'])
    valid = np.zeros(ppm_all.size, dtype=bool)
    for lo, hi in rng:
        valid |= (ppm_all > lo) & (ppm_all < hi)

    peaks = peaks or PEAKS_MATLAB
    labels, pos = [], []
    bm1, bs1, bm2, bs2 = [], [], [], []
    processed = np.zeros(ppm_all.size, dtype=bool)

    for name, center, radius in peaks:
        in_pk = (ppm_all >= center - radius) & (ppm_all <= center + radius) & valid
        idx = np.where(in_pk)[0]
        if idx.size == 0:
            continue
        labels.append(name)
        pos.append(center)
        d1 = voxel1[:, idx].ravel()
        d2 = voxel2[:, idx].ravel()
        bm1.append(np.nanmean(d1))
        bs1.append(np.nanstd(d1, ddof=1))
        bm2.append(np.nanmean(d2))
        bs2.append(np.nanstd(d2, ddof=1))
        processed[idx] = True

    for k in np.where(valid & ~processed)[0]:
        labels.append(f'{ppm_all[k]:.2f}')
        pos.append(ppm_all[k])
        bm1.append(mean1[k]); bs1.append(std1[k])
        bm2.append(mean2[k]); bs2.append(std2[k])

    if not labels:
        return {'labels': [], 'pos': np.array([]), 'mean1': np.array([]),
                'std1': np.array([]), 'mean2': np.array([]), 'std2': np.array([])}

    order = np.argsort(-np.asarray(pos, dtype=float))      # ppm 降序
    pick = lambda a: np.asarray(a, dtype=float)[order]     # noqa: E731
    return {'labels': [labels[i] for i in order], 'pos': pick(pos),
            'mean1': pick(bm1), 'std1': pick(bs1),
            'mean2': pick(bm2), 'std2': pick(bs2)}


def plot_bar_pair(voxel1, voxel2, w_offset, label1, label2, out_dir,
                  ppm_range='clinical', tag='', dpi=300, verbose=True):
    """
    画「有效范围内信号均值/标准差对比（CEST 峰区间已合并）」两张柱状图。

    对应 MATLAB comb_prf_data_zunjian.m 的图 1 / 图 2，文件名也一致：
      ``Mean_bar{tag}.png`` / ``Std_bar{tag}.png``（tag 一般形如 ``_2vs3``）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t = bar_table(voxel1, voxel2, w_offset, ppm_range=ppm_range)
    if not t['labels']:
        print('  [警告] 有效范围内没有频偏点，未画柱状图')
        return t

    x = np.arange(len(t['labels']))
    for kind, ylab, fname in (('mean', '信号均值', f'Mean_bar{tag}.png'),
                              ('std', '信号标准差', f'Std_bar{tag}.png')):
        fig, ax = plt.subplots(figsize=(14, 4.5))
        ax.bar(x - 0.175, t[f'{kind}1'], width=0.35, color=COLORS[0], label=label1)
        ax.bar(x + 0.175, t[f'{kind}2'], width=0.35, color=COLORS[1], label=label2)
        ax.set_xticks(x)
        ax.set_xticklabels(t['labels'], rotation=45, ha='right')
        ax.set_xlabel('ppm / CEST峰', fontsize=12)
        ax.set_ylabel(ylab, fontsize=12)
        ax.set_title(f'有效范围内{ylab}对比（CEST峰区间已合并）', fontsize=13)
        ax.grid(True, alpha=0.4)
        ax.legend(loc='best')
        fig.tight_layout()
        fig.savefig(out_dir / fname, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        if verbose:
            print(f'    {ylab}柱状图已保存：{out_dir / fname}')
    return t


# ===========================================================================
# 五、出图
# ===========================================================================
def _tick_labels(w):
    """刻度文字和原脚本一致：round(x,2) 后按最短写法（1.3 而不是 1.30）。"""
    return [f'{round(float(v), 2):g}' for v in np.asarray(w).ravel()]


def plot_single(w, mean_imp, std_imp, n_sample, out_png, title=None,
                n_permute=N_PERMUTE, dpi=300):
    """单组柱状图（带误差棒）。"""
    w = np.asarray(w).ravel()
    x = np.arange(w.size)
    fig_w = max(12, w.size * 0.3)
    fig, ax = plt.subplots(figsize=(fig_w, 7))
    ax.bar(x, mean_imp, width=0.5, color=COLORS[0], label=f'n={n_sample}')
    ax.errorbar(x, mean_imp, yerr=std_imp, fmt='none', ecolor='black', capsize=3, alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(_tick_labels(w), rotation=45, ha='right')
    ax.set_xlabel('Saturation Frequency Offset (ppm)', fontsize=14)
    ax.set_ylabel('Feature Importance (R² Drop)', fontsize=14)
    ax.set_title(title or f'Single RF Model, {n_permute} Permutations per Feature',
                 fontsize=15, pad=14)
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(bottom=0)
    ax.legend(loc='upper right')
    fig.tight_layout()
    fig.savefig(out_png, dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    print(f'    图已保存：{out_png}')


def plot_multi(results, labels, out_png, colors=None, title=None,
               n_permute=N_PERMUTE, dpi=300):
    """
    多组并排柱状图（只显示均值）。

    ``results``：list of dict，每个含 w / mean / n
    """
    colors = colors or COLORS
    w = np.asarray(results[0]['w']).ravel()
    n_features, n_groups = w.size, len(results)
    bar_w = 0.5 / n_groups
    x = np.arange(n_features)

    fig, ax = plt.subplots(figsize=(max(14, n_features * 0.3), 7))
    for i, res in enumerate(results):
        offset = (i - (n_groups - 1) / 2) * bar_w
        ax.bar(x + offset, res['mean'], width=bar_w,
               color=colors[i % len(colors)],
               label=f'{labels[i]} (n={res["n"]})')
    ax.set_xticks(x)
    ax.set_xticklabels(_tick_labels(w), rotation=45, ha='right')
    ax.set_xlabel('Saturation Frequency Offset (ppm)', fontsize=14)
    ax.set_ylabel('Feature Importance (R² Drop)', fontsize=14)
    ax.set_title(title or f'Single RF Model, {n_permute} Permutations per Feature',
                 fontsize=15, pad=14)
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(bottom=0)
    ax.legend(loc='upper right')
    fig.tight_layout()
    fig.savefig(out_png, dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    print(f'    合并图已保存：{out_png}')


def save_excel(out_xlsx, w, raw_imp, mean_imp, std_imp, n_sample, max_perm_rows=10):
    """导出 Excel：每个频偏一列 + 各次置换 + 均值 + 标准差 + 样本量。"""
    w = np.asarray(w).ravel()
    cols = {'频率(ppm)': np.round(w, 2)}
    for r in range(min(max_perm_rows, raw_imp.shape[0])):
        cols[f'permutation第{r + 1}次'] = raw_imp[r, :]
    cols[f'{raw_imp.shape[0]}次均值'] = np.asarray(mean_imp).ravel()
    cols['标准差'] = np.asarray(std_imp).ravel()
    cols['样本量'] = [n_sample] * w.size
    pd.DataFrame(cols).to_excel(out_xlsx, index=False)
    print(f'    Excel 已保存：{out_xlsx}')


# ===========================================================================
# 六、四种分析入口
# ===========================================================================
def _analyze_one(data_dir, range_mode, n_permute, merge_mode, how, verbose=True):
    """读一个训练集 → RF → （可选）区间合并，返回结果 dict。"""
    X, y, w_sel, valid_idx, _ = load_dataset(data_dir, range_mode, verbose=verbose)
    X = X[:, valid_idx]
    if X.shape[1] == 0:
        raise ValueError(f'{data_dir} 在 {range_mode} 范围内没有有效频偏')
    r = rf_importance(X, y, w_sel, n_permute=n_permute, verbose=verbose)
    w, mean, std, raw = np.asarray(w_sel), r['mean'], r['std'], r['raw']
    if merge_mode:
        w, mean, std, raw = merge_peaks(w, mean, std, raw, how=how, verbose=verbose)
    return {'w': w, 'mean': mean, 'std': std, 'raw': raw, 'n': r['n_sample'],
            'r2': r['train_r2'], 'oob': r['oob'], 'dir': str(data_dir)}


def run_single(data_dir, out_dir, range_mode='clinical', n_permute=N_PERMUTE,
               merge_mode=1, how='sum', tag=None, excel=True, verbose=True):
    """单组分析（出图 + Excel）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f'===== 单组分析：{data_dir} =====')
    res = _analyze_one(data_dir, range_mode, n_permute, merge_mode, how, verbose)
    tag = tag or Path(data_dir).name
    plot_single(res['w'], res['mean'], res['std'], res['n'],
                out_dir / f'importance_{tag}.png', n_permute=n_permute)
    if excel:
        save_excel(out_dir / f'importance_{tag}.xlsx', res['w'],
                   res['raw'], res['mean'], res['std'], res['n'])
    return res


def run_compare(savepaths_or_dirs, out_dir, labels=None, range_mode='clinical',
                n_permute=N_PERMUTE, merge_mode=1, how='sum',
                fname='importance_combined.png', verbose=True):
    """
    多组对比（默认按 savepaths.mat 的 pathInfo 顺序）。

    ``savepaths_or_dirs`` 可以是 savepaths.mat 路径，也可以是目录列表。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sp = Path(savepaths_or_dirs)
    if sp.is_file():
        dirs = read_path_info(sp)
        base = sp.parent
    else:
        dirs = [str(d) for d in np.atleast_1d(savepaths_or_dirs)]
        base = out_dir
    if not dirs:
        raise ValueError('没有可用的数据目录')
    labels = labels or [Path(d).name for d in dirs]

    print(f'===== 多组对比：{len(dirs)} 组 =====')
    results = []
    for i, d in enumerate(dirs):
        print(f'\n-- [{labels[i]}] {d}')
        results.append(_analyze_one(d, range_mode, n_permute, merge_mode, how, verbose))

    for j in range(1, len(results)):
        if results[j]['w'].size != results[0]['w'].size or \
                not np.allclose(results[j]['w'], results[0]['w']):
            print('  [警告] 各组合并后的频率不完全一致，将按索引对齐绘图')

    plot_multi(results, labels, out_dir / fname, n_permute=n_permute)
    for i, res in enumerate(results):
        save_excel(out_dir / f'importance_{labels[i]}.xlsx', res['w'],
                   res['raw'], res['mean'], res['std'], res['n'])
    return results


def run_group(savepaths_file, range_mode='clinical', n_permute=N_PERMUTE,
              merge_mode=1, how='sum', verbose=True):
    """
    按父目录分组分析（大鼠数据：3H / 1W / 5W 各一组，组内多只动物合并对比）。
    对应 prf_lkx_group.py / prf_lkx_group_mean.py。
    """
    dirs = read_path_info(savepaths_file)
    if not dirs:
        raise ValueError(f'{savepaths_file} 里没有 pathInfo')

    grouped = {}
    for d in dirs:
        grouped.setdefault(str(Path(d).parent), []).append(d)

    all_out = {}
    for group_root, members in grouped.items():
        gname = Path(group_root).name
        print(f'\n========== 组别 {gname}（{len(members)} 个）==========')
        results, labels = [], []
        for m in members:
            try:
                results.append(_analyze_one(m, range_mode, n_permute, merge_mode, how, verbose))
                labels.append(Path(m).name)
            except Exception as e:                       # noqa: BLE001
                print(f'  [跳过] {m}：{e}')
        if results:
            plot_multi(results, labels, Path(group_root) / f'{gname}_合并柱状图.png',
                       n_permute=n_permute,
                       title=f'{gname} - CEST 频率重要性')
        all_out[gname] = results
    return all_out


def run_left_right(savepaths_file, range_mode='clinical', n_permute=N_PERMUTE,
                   merge_mode=1, how='sum', verbose=True):
    """
    左 / 右分别分析（读 trainDataLeft_ROI.mat + trainLabelLeft.mat 这类文件）。
    对应 prf_lkx_left_right.py / prf_lkx_left__right_mean.py。
    """
    dirs = read_path_info(savepaths_file)
    out = {}
    for side, (fn_data, vn_data, fn_lab, vn_lab) in {
            'left':  ('trainDataLeft_ROI.mat', 'dataLeft', 'trainLabelLeft.mat', 'labelLeft'),
            'right': ('trainDataRight_ROI.mat', 'dataRight', 'trainLabelRight.mat', 'labelRight'),
    }.items():
        results, labels = [], []
        for d in dirs:
            d = Path(d)
            try:
                X = np.asarray(load_mat(d / fn_data, [vn_data])[vn_data])
                y = np.asarray(load_mat(d / fn_lab, [vn_lab])[vn_lab]).ravel()
                w_full = np.round(np.asarray(
                    load_mat(d / 'w_offset.mat', ['w_offset'])['w_offset']).ravel(), 1)
            except Exception as e:                       # noqa: BLE001
                print(f'  [跳过] {side} {d}：{e}')
                continue
            presets = RANGE_PRESETS.get(range_mode, RANGE_PRESETS['clinical'])
            mask = np.zeros(w_full.size, dtype=bool)
            for lo, hi in presets:
                mask |= (w_full > lo) & (w_full < hi)
            vi = np.where(mask)[0]
            r = rf_importance(X[:, vi], y, w_full[vi], n_permute=n_permute, verbose=verbose)
            w, mean, std, raw = w_full[vi], r['mean'], r['std'], r['raw']
            if merge_mode:
                w, mean, std, raw = merge_peaks(w, mean, std, raw, how=how, verbose=verbose)
            results.append({'w': w, 'mean': mean, 'std': std, 'raw': raw,
                            'n': r['n_sample'], 'r2': r['train_r2'], 'oob': r['oob']})
            labels.append(d.name)
        if results:
            out_path = Path(savepaths_file).parent / f'importance_{side}.png'
            plot_multi(results, labels, out_path, n_permute=n_permute,
                       title=f'{side.upper()} - CEST 频率重要性')
        out[side] = results
    return out


# ===========================================================================
# 七、命令行
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description='PRF 频率重要性分析（Python 版）')
    ap.add_argument('--mode', default='compare',
                    choices=['single', 'compare', 'group', 'left_right'])
    ap.add_argument('--savepaths', default=None, help='savepaths.mat 路径')
    ap.add_argument('--dirs', nargs='+', default=None, help='直接给训练集目录（compare/single）')
    ap.add_argument('--out', default=None, help='输出目录（默认与数据同级）')
    ap.add_argument('--labels', nargs='+', default=None)
    ap.add_argument('--range', dest='range_mode', default='clinical',
                    choices=list(RANGE_PRESETS))
    ap.add_argument('--permute', type=int, default=N_PERMUTE)
    ap.add_argument('--merge', type=int, default=1, help='1=合并 5 个 CEST 峰区间')
    ap.add_argument('--how', default='sum', choices=['sum', 'mean'])
    a = ap.parse_args(argv)

    common = dict(range_mode=a.range_mode, n_permute=a.permute,
                  merge_mode=bool(a.merge), how=a.how)

    if a.mode == 'single':
        assert a.dirs, 'single 模式需要 --dirs'
        run_single(a.dirs[0], a.out or Path(a.dirs[0]).parent, **common)
    elif a.mode == 'compare':
        src = a.savepaths or a.dirs
        assert src, 'compare 模式需要 --savepaths 或 --dirs'
        out = a.out or (Path(a.savepaths).parent if a.savepaths else Path(a.dirs[0]).parent)
        run_compare(src, out, labels=a.labels, **common)
    elif a.mode == 'group':
        assert a.savepaths, 'group 模式需要 --savepaths'
        run_group(a.savepaths, **common)
    else:
        assert a.savepaths, 'left_right 模式需要 --savepaths'
        run_left_right(a.savepaths, **common)

