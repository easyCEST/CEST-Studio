# -*- coding: utf-8 -*-
"""
cest_post.py —— 【第②步】后处理（Python 版 runCESTpost.m）
============================================================================

输入：第①步的 ``CEST_result/<病例>/``（normZ / w_offset / s0 / b0）
输出（与 MATLAB 完全一致的目录布局）::

    <out_root>/<病例>/<filename>/                 ROI_total.mat / mask.mat / （可选 4D 汇总）
    <out_root>/<病例>/<filename>/sliceN/          S0 / S0m / S0_temp / Thmask / w_offset / x_as
    <out_root>/<病例>/<filename>/sliceN/Self_corr/ V_exp / V_as / V_as_uncorrect
                                                  V_exp_mask / V_as_mask / sft_map

核心计算（与 MATLAB 逐行对应）
  * ``normalize_spect``                归一化到 [0,1]（本流程用 tol=0.02, pLow=2, pHigh=98）
  * ``thre_mask_snr`` / ``generate_thmask`` / ``generate_liver_mask``  掩模
  * ``roi_plot_song``                  ROI 勾画（可复用已有 ROI_total.mat，也可交互勾画）
  * ``zspec_interp_b0corr``            频率插值 + B0 自校正（pchip 插值 / spline 找谷）
  * ``norm_z``                         V_exp / S0 .* mask

交互式部分（画噪声 ROI / 肝脏 ROI / 功能 ROI）用 matplotlib 实现，与 MATLAB 的 roipoly 对应；
若对应目录里已有 ``Thmask.mat`` / ``liverMask.mat`` / ``ROI_total.mat``，则直接加载复用。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline, PchipInterpolator
from scipy.io import savemat as _savemat_scipy

COMPRESS = True          # 存盘统一用 zlib 压缩（与 MATLAB save 默认一致，体积小很多）


def savemat(path, d, **kw):
    """包一层：统一压缩，其余与 scipy.io.savemat 完全一致。"""
    kw.setdefault('do_compression', COMPRESS)
    return _savemat_scipy(path, d, **kw)

from cest_io import load_mat, save_set  # noqa: F401

# ---------------------------------------------------------------------------
# 默认参数（与 runCESTpost.m 一致）
# ---------------------------------------------------------------------------
DEFAULTS = dict(
    NegOrPos=2,            # 0 正 / 1 负 / 2 全频偏
    register=0,            # 0 不配准 / 1 刚性 / 2 RPCA
    B0correct=2,           # 0 不校正 / 1 用 B0map / 2 自校正
    roi_display_mode=1,    # 0 S0 平均 / 1 Z 原始 / 2 1−Z
    target_freq=3.5,
    snr=15,
    nROI=1,
    x_lw=0.025,
    total_slice_num=20,
    multi_slice=1,
    N_slice=0,
    rotate_data=False,
    do_selfcorr_qc=True,
    snr_noise_mask=None,   # 噪声 ROI（可选，给第①次自动掩模用）
    liver_mask_file=None,  # 直接指定 liverMask.mat（跳过交互勾画）
    roi_total_file=None,   # 直接指定 ROI_total.mat
    thmask_file=None,      # 直接指定 Thmask.mat
    save_FULL_Vas=True,
    save_uncorrect=False,
    save_single=True,
    save_4d_aggregates=False,   # 4D 汇总件（体积很大，默认不写；PRF 链路不需要）
    b0_scale=None, b0_offset=0.0,
    date_suffix='0803',
)


# ===========================================================================
# 一、归一化 / 掩模 / ROI
# ===========================================================================
def _round_half_up(x):
    """MATLAB round：四舍五入（远离零），Python round 是银行家舍入，不能用。"""
    return int(np.floor(float(x) + 0.5))


def normalize_spect(data, tol=0.05, p_low=1, p_high=99):
    """归一化到 [0,1]（对应 normalizeSpect.m，4 参数版）。

    ★ 2026-10-05：「已归一」的判据由 **min/max** 改成 **分位（p_low/p_high 分位）**，
      与 cest_ld.is_already_norm 统一。原因：已是 Z 谱（0–1）的数据里常有 <1% 的
      越界点（噪声/边缘，如 max 13.4 / min -1.3），用 min/max 会被单点触发，误判成
      「未归一」而重标定（= 多重除/多算一次）。改后只有**分位区间**也越界才重标定；
      p_low/p_high 默认 1/99，故等价于「1%/99% 分位」判据。
      对真正未归一的数据（分位超出 [−tol, 1+tol]）行为完全不变。
    """
    data = np.asarray(data, dtype=float)
    v = data[np.isfinite(data)]
    if v.size == 0:
        return data
    s = np.sort(v.ravel())
    n = s.size
    lo = s[max(0, _round_half_up(n * p_low / 100) - 1)]
    hi = s[min(n, _round_half_up(n * p_high / 100)) - 1]
    if lo >= -tol and hi <= 1 + tol:              # 分位判据：已在 [0,1] 附近 → 不动
        return data
    dmin, dmax = np.nanmin(data), np.nanmax(data)
    if dmin >= -tol and dmax <= 1 + tol:          # 双保险：整体也在范围内
        return data
    out = np.clip(data, lo, hi)
    return (out - lo) / (hi - lo)


def thre_mask_snr(S0, snr, noise_mask=None, verbose=True):
    """
    SNR 阈值掩模（对应 ThreMask_SNR.m）。

    MATLAB 里 noise ROI 是交互勾画的；这里 ``noise_mask`` 给逻辑矩阵，
    不传就调 matplotlib 交互勾画。
    """
    S0 = np.asarray(S0, dtype=float)
    if noise_mask is None:
        noise_mask = interactive_polygon(S0, title='请圈选噪声区域（双击/回车结束）', cmap='gray')
    noise = S0[np.asarray(noise_mask, dtype=bool)]
    if noise.size == 0:
        raise ValueError('噪声 ROI 为空')
    N = float(np.std(noise))
    mask = (S0 > snr * N).astype(float)
    if verbose:
        print(f'    噪声 σ={N:.4g}，阈值 {snr}σ → 有效体素 {int(mask.sum())}')
    return mask


def _resize_like(mask, target_shape, tag=''):
    """对应 MATLAB imresize(mask, size(S0))；尺寸一致时原样返回。"""
    mask = np.asarray(mask, dtype=float)
    if np.array_equal(mask.shape, tuple(target_shape)):
        return mask
    from scipy.ndimage import zoom
    zy, zx = target_shape[0] / mask.shape[0], target_shape[1] / mask.shape[1]
    out = zoom(mask, (zy, zx), order=1)
    out = out[:target_shape[0], :target_shape[1]]
    if out.shape != tuple(target_shape):
        pad = [(0, target_shape[i] - out.shape[i]) for i in range(2)]
        out = np.pad(out, pad)
    print(f'    [提示] {tag} 尺寸 {mask.shape} → imresize 到 {target_shape}')
    return out


def generate_thmask(maskpath, S0, snr, thmask_file=None, noise_mask=None, verbose=True):
    """掩模（对应 GenerateThmask.m）：有 Thmask.mat 就复用，否则按 SNR 生成并保存。"""
    maskpath = Path(maskpath)
    f = Path(thmask_file) if thmask_file else maskpath / 'Thmask.mat'
    if f.exists():
        got = load_mat(f, ['Thmask'])
        if 'Thmask' in got:
            if verbose:
                print(f'    复用已有 Thmask：{f}')
            return _resize_like(got['Thmask'], S0.shape, 'Thmask')
    Thmask = thre_mask_snr(S0, snr, noise_mask=noise_mask, verbose=verbose)
    maskpath.mkdir(parents=True, exist_ok=True)
    savemat(str(maskpath / 'Thmask.mat'), {'Thmask': Thmask})
    return Thmask


def generate_liver_mask(S0, savepath, liver_mask_file=None, verbose=True):
    """肝脏/组织掩模（对应 GenerateliverMask.m）：有 liverMask.mat 就复用，否则交互勾画。"""
    savepath = Path(savepath)
    f = Path(liver_mask_file) if liver_mask_file else savepath / 'liverMask.mat'
    if f.exists():
        got = load_mat(f, ['liverMask'])
        if 'liverMask' in got:
            if verbose:
                print(f'    复用已有 liverMask：{f}')
            return _resize_like(got['liverMask'], S0.shape, 'liverMask')
    print('    请勾画组织/肝脏区域（双击或回车结束）')
    liverMask = interactive_polygon(S0, title='请圈选组织（肝脏）区域', cmap='gray').astype(float)
    savepath.mkdir(parents=True, exist_ok=True)
    savemat(str(savepath / 'liverMask.mat'), {'liverMask': liverMask})
    return liverMask


def interactive_polygon(img, title='请圈选 ROI（双击或回车结束）', cmap='jet'):
    """
    matplotlib 版的 roipoly：鼠标点若干顶点，围成多边形，返回逻辑 mask。

    需要图形界面（PyCharm/Spyder 里能弹窗）。无界面时请改用现成的 mask 文件。
    """
    import matplotlib
    if matplotlib.get_backend().lower() in ('agg',):
        matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.imshow(np.asarray(img), cmap=cmap)
    ax.set_title(title, fontsize=11)
    ax.set_axis_off()
    pts = plt.ginput(n=-1, timeout=0, show_clicks=True)     # 双击/回车结束
    plt.close(fig)
    if len(pts) < 3:
        raise ValueError('多边形顶点少于 3 个，已取消')
    return points_to_mask(pts, np.asarray(img).shape[:2])


def points_to_mask(points, shape):
    """多边形顶点 → 逻辑 mask（用 matplotlib.path 的 inpolygon 语义）。"""
    from matplotlib.path import Path as MplPath
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    pts = np.column_stack([xx.ravel(), yy.ravel()])
    inside = MplPath(np.asarray(points, dtype=float)).contains_points(pts)
    return inside.reshape(shape)


def roi_plot_song(nROI, legends, thmask, roi_dir, img, caxis_range=None,
                  roi_total_file=None, polygons=None, verbose=True):
    """
    ROI 勾画（对应 ROIplotSong_0708.m）。

    * 有 ``ROI_total.mat`` → 直接复用
    * 给了 ``polygons``（每 ROI 一组顶点）→ 用它们生成
    * 否则交互勾画

    返回 ``ROI_total``：**1-based 列优先索引** 的 cell 列表（与 MATLAB 存的格式一致）。
    """
    roi_dir = Path(roi_dir)
    f = Path(roi_total_file) if roi_total_file else roi_dir / 'ROI_total.mat'
    if f.exists():
        got = load_mat(f, ['ROI_total'])
        if 'ROI_total' in got:
            if verbose:
                print(f'    复用已有 ROI_total：{f}')
            raw = got['ROI_total']
            out = [np.asarray(raw, dtype=object).ravel()[k]
                   for k in range(len(np.asarray(raw, dtype=object).ravel()))]
            dst = roi_dir / 'ROI_total.mat'
            try:
                if f.resolve() != dst.resolve():      # 复用的是别处的，拷一份过来保持结构一致
                    roi_dir.mkdir(parents=True, exist_ok=True)
                    savemat(str(dst), {'ROI_total': raw})
            except OSError:
                pass
            return out

    shape = np.asarray(thmask).shape
    roi_total = []
    for k in range(nROI):
        name = legends[k] if k < len(legends) else f'ROI{k + 1}'
        if polygons is not None and k < len(polygons):
            roi = points_to_mask(polygons[k], shape)
        else:
            print(f'    请勾画 {name}（双击或回车结束）')
            roi = interactive_polygon(np.asarray(img), title=f'请勾画 {name}')
            savemat(str(roi_dir / f'ROI{k + 1}.mat'), {f'ROI': roi.astype(float)})
        idx = np.nonzero(roi.astype(bool) & np.asarray(thmask).astype(bool))
        if idx[0].size == 0:
            raise ValueError(f'{name} 与掩模没有交集，请重新勾画')
        flat0 = np.ravel_multi_index(idx, shape, order='F')      # 列优先线性索引（0-based）
        roi_total.append(flat0 + 1)                              # MATLAB 是 1-based
        if verbose:
            print(f'    {name}：{idx[0].size} 个体素')
    roi_dir.mkdir(parents=True, exist_ok=True)
    savemat(str(roi_dir / 'ROI_total.mat'), {'ROI_total': _cell_of_arrays(roi_total)})
    return roi_total


def _cell_of_arrays(list_of_arrays):
    """1×N object 数组，savemat 后 MATLAB 读出来是 cell（列向量元素）。"""
    arr = np.empty((len(list_of_arrays), 1), dtype=object)
    for i, a in enumerate(list_of_arrays):
        arr[i, 0] = np.asarray(a, dtype=float).reshape(-1, 1)
    return arr


# ===========================================================================
# 二、频率插值 + B0 自校正（核心）
# ===========================================================================
def zspec_interp_b0corr(Vo_ori_use, w1, Thmask, B0map=None, B0correct=2,
                        NegOrPos=2, x_lw=0.025):
    """
    对应 ``Zspec_InterpandB0Corr_LKX.m``，返回 ``(x_as, V_as, V_as_uncorrect, w_offset, V_exp)``。
    """
    Vo_ori_use = np.asarray(Vo_ori_use, dtype=float)
    w1 = np.asarray(w1, dtype=float).ravel()

    # ---- 1) 丢掉整体为 0 的频偏帧 ----
    keep = [t for t in range(len(w1)) if np.mean(Vo_ori_use[:, :, t]) != 0]
    Vo_tmp = Vo_ori_use[:, :, keep]
    w_offsetOri = w1[keep]

    # ---- 2) 频率排序（MATLAB 里 w_offset 排了序，但 Vo_cest 没跟着排）----
    w_offset = np.sort(w_offsetOri)
    Vo_cest = Vo_tmp
    n_rows, n_cols = Vo_cest.shape[0], Vo_cest.shape[1]

    # ---- 3) 插值网格 x_as = min:x_lw:max ----
    n_as = int(round((w_offset.max() - w_offset.min()) / x_lw)) + 1
    x_as = np.linspace(w_offset.min(), w_offset.max(), n_as)
    V_as = np.zeros((n_rows, n_cols, n_as))
    V_exp = np.zeros((n_rows, n_cols, len(w_offsetOri)))
    V_as_uncorrect = np.zeros((n_rows, n_cols, n_as))
    V_exp_uncorrect = np.zeros((n_rows, n_cols, len(w_offsetOri)))

    rows, cols = np.nonzero(np.asarray(Thmask).astype(bool))
    for px, py in zip(rows, cols):
        spectra = Vo_cest[px, py, :]
        if B0correct == 1:
            if B0map is None:
                raise ValueError('B0correct=1 需要 B0map')
            sft = float(B0map[px, py])
            V_as[px, py, :] = PchipInterpolator(w_offset - sft, spectra)(x_as)
            V_exp[px, py, :] = PchipInterpolator(w_offset - sft, spectra)(w_offset)
        elif B0correct == 0:
            if len(w_offset) > 2:
                V_as[px, py, :] = PchipInterpolator(w_offset, spectra)(x_as)
                V_exp[px, py, :] = PchipInterpolator(w_offset, spectra)(w_offset)
            else:
                V_as[px, py, :] = spectra[spectra != 0]
                V_exp[px, py, :] = spectra[spectra != 0]
        elif B0correct == 2:
            yi = CubicSpline(w_offset, spectra)(x_as)          # MATLAB spline = not-a-knot
            if NegOrPos == 2:
                sft = float(x_as[int(np.argmin(yi))])          # 全频偏搜谷
            else:
                sel = np.where((x_as >= -1.5) & (x_as <= 1.5))[0]
                sft = float(x_as[sel[int(np.argmin(yi[sel]))]])
            V_as[px, py, :] = PchipInterpolator(w_offset - sft, spectra)(x_as)
            V_exp[px, py, :] = PchipInterpolator(w_offset - sft, spectra)(w_offset)
        else:
            raise ValueError('B0correct 只能是 0/1/2')

        # 未校正版本（用于对比）
        V_as_uncorrect[px, py, :] = PchipInterpolator(w_offset, spectra)(x_as)
        V_exp_uncorrect[px, py, :] = PchipInterpolator(w_offset, spectra)(w_offset)

    return x_as, V_as, V_as_uncorrect, w_offset, V_exp


def selfcorr_sft_map(Zdata, w_offsetOri, x_as, Thmask):
    """逐体素 spline 找谷 → sft_map（对应 runCESTpost 里的自校正 QC 块）。"""
    sft_map = np.zeros(Zdata.shape[:2])
    rows, cols = np.nonzero(np.asarray(Thmask).astype(bool))
    for px, py in zip(rows, cols):
        yi = CubicSpline(w_offsetOri, Zdata[px, py, :])(x_as)
        sft_map[px, py] = x_as[int(np.argmin(yi))]
    return sft_map


def norm_z(S0, V_exp, brain_mask):
    """V_exp / S0 .* mask（对应 NormZ.m）。"""
    S0 = np.asarray(S0, dtype=float)
    out = np.zeros_like(V_exp, dtype=float)
    for k in range(V_exp.shape[2]):
        m1 = V_exp[:, :, k] / S0
        out[:, :, k] = m1 * np.asarray(brain_mask, dtype=float)
    return out


# ===========================================================================
# 三、主流程
# ===========================================================================
def _stack3d(A):
    A = np.asarray(A)
    if A.ndim >= 3:
        return A.reshape(A.shape[0], A.shape[1], -1)
    return A.reshape(A.shape[0], A.shape[1], 1)


def _to4d(A):
    A = np.asarray(A)
    if A.ndim == 3:
        return A.reshape(A.shape[0], A.shape[1], A.shape[2], 1)
    return A


def run_post(cfg):
    """
    第②步总入口（对应 runCESTpost.m 主体）。

    cfg
    ---
    recon_root / out_root / all_cases / case_sel
    其余见 ``DEFAULTS``；交互相关的可用 thmask_file / liver_mask_file / roi_total_file
    直接指定现成文件（批量时推荐，免得每例都要手画）。
    """
    p = dict(DEFAULTS)
    p.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    recon_root = Path(cfg['recon_root'])
    out_root = Path(cfg['out_root'])
    all_cases = cfg.get('all_cases', ['c1', 'c2', 'c3', 'h1', 'h2', 'h3', 'hp1', 'hp2', 'hp3'])
    case_sel = cfg.get('case_sel', 0)
    if isinstance(case_sel, int) and case_sel == 0:
        case_idx = list(range(len(all_cases)))
    else:
        case_idx = [int(i) - 1 for i in np.atleast_1d(case_sel)]

    prefix = ['pos', 'neg', 'full'][int(p['NegOrPos'])]
    filename = f'{prefix}_interp_{p["date_suffix"]}'
    b0corr_tag = ["No_corr", "B0map_corr", "Self_corr"][p['B0correct']]
    total_slice_num = int(p['total_slice_num'])
    x_lw = float(p['x_lw'])

    out_root.mkdir(parents=True, exist_ok=True)
    print(f'>>> 本次处理 {len(case_idx)} 个病例，输出子目录 {filename}/')

    status = []
    for i in case_idx:
        case = all_cases[i]
        print(f'\n===== [{i + 1}/{len(all_cases)}] {case} =====')
        t0 = time.time()
        try:
            datapath = recon_root / case
            save_root = out_root / case / filename
            save_root.mkdir(parents=True, exist_ok=True)

            # ---- 4.0 读输入 ----
            _nz = load_mat(datapath / 'normZ.mat', ['norm_z', 'normZ'])
            Mz_all = np.asarray(_nz.get('norm_z', _nz.get('normZ')), dtype=float)
            w_offset = np.asarray(load_mat(datapath / 'w_offset.mat', ['w_offset'])['w_offset'],
                                  dtype=float).ravel()
            S0_all = np.asarray(
                load_mat(datapath / 's0.mat', ['s0', 'S0']).get('s0'), dtype=float)
            b0_all = None
            if p['B0correct'] == 1:
                b0_all = np.asarray(load_mat(datapath / 'b0.mat', ['b0'])['b0'], dtype=float)

            Mz_all = _to4d(Mz_all)
            S0_all = _stack3d(S0_all)
            if b0_all is not None:
                b0_all = _stack3d(b0_all)
            # ★ 轴序统一：本工程 normZ 一律是「层优先」[X,Y,层,频]；而本模块的后续算法
            #   是按旧的 [X,Y,频,层] 写的。这里换成频优先（swapaxes 是视图，不复制），
            #   后面的代码一行都不用改；旧文件（本身就是 [X,Y,频,层]）则原样跳过。
            if Mz_all.shape[2] != w_offset.size and Mz_all.shape[3] == w_offset.size:
                Mz_all = np.swapaxes(Mz_all, 2, 3)
            if Mz_all.shape[3] != total_slice_num:
                raise ValueError(f'normZ 层数 {Mz_all.shape[3]} ≠ {total_slice_num}')
            if S0_all.shape[2] != total_slice_num:
                raise ValueError(f's0 层数 {S0_all.shape[2]} ≠ {total_slice_num}')
            if p['rotate_data']:
                Mz_all = np.rot90(Mz_all, 1)
                S0_all = np.rot90(S0_all, 1)
                if b0_all is not None:
                    b0_all = np.rot90(b0_all, 1)

            Nx, Ny, Nfreq = Mz_all.shape[0], Mz_all.shape[1], Mz_all.shape[2]
            if w_offset.size != Nfreq:
                raise ValueError(f'w_offset 个数 {w_offset.size} ≠ {Nfreq}')
            bg = int(np.argmin(np.abs(w_offset - p['target_freq'])))
            print(f'  normZ {Mz_all.shape}，{p["target_freq"]} ppm → 索引 {bg + 1}')

            n_nan = int((~np.isfinite(Mz_all)).sum())
            if n_nan:
                print(f'  非有限值 {n_nan} 个已置 0')
                Mz_all[~np.isfinite(Mz_all)] = 0

            Mz = np.zeros_like(Mz_all)
            for ns in range(total_slice_num):
                Mz[:, :, :, ns] = normalize_spect(Mz_all[:, :, :, ns],
                                               p.get('norm_tol', 0.02),
                                               p.get('norm_plow', 2),
                                               p.get('norm_phigh', 98))
            w_offset_ori = w_offset.copy()

            # ---- 4.1 掩模 + ROI ----
            img_mask_base = S0_all.mean(axis=2)
            z35 = Mz[:, :, bg, :].mean(axis=2).reshape(Nx, Ny)
            img_roi_base = img_mask_base if p['roi_display_mode'] == 0 else \
                (1 - z35 if p['roi_display_mode'] == 2 else z35)

            # 首层掩模（逐层共用）
            Thmask = generate_thmask(save_root, img_mask_base, p['snr'],
                                     thmask_file=p['thmask_file'],
                                     noise_mask=p['snr_noise_mask'])
            liverMask = generate_liver_mask(img_mask_base, save_root,
                                           liver_mask_file=p['liver_mask_file'])
            Thmask = np.asarray(liverMask) * np.asarray(Thmask)
            savemat(str(save_root / 'mask.mat'),
                    {'Thmask': Thmask, 'liverMask': liverMask, 'img_mask_base': img_mask_base})
            savemat(str(save_root / 'liverMask.mat'), {'liverMask': liverMask})
            savemat(str(save_root / 'Thmask.mat'), {'Thmask': Thmask})
            print(f'  共用掩模 {Thmask.shape}，有效体素 {int((Thmask > 0).sum())}')
            if Thmask.shape != (Nx, Ny):
                raise ValueError(f'掩模 {Thmask.shape} 与数据 {(Nx, Ny)} 不一致')

            img_display = img_roi_base * Thmask
            ROI_total = roi_plot_song(int(p['nROI']), cfg.get('roi_names', ['ROI1']),
                                      Thmask, save_root, img_display,
                                      caxis_range=cfg.get('caxis_fixed'),
                                      roi_total_file=p['roi_total_file'])

            # ---- 4.2 逐层 ----
            slice_list = [1] if not p['multi_slice'] else (
                list(range(1, total_slice_num + 1)) if p['N_slice'] == 0 else [int(p['N_slice'])])
            got_layer = []
            for ns in slice_list:
                sp = save_root / f'slice{ns}' / b0corr_tag
                sp.mkdir(parents=True, exist_ok=True)
                try:
                    Zdata = np.squeeze(Mz[:, :, :, ns - 1])
                    S0 = S0_all[:, :, ns - 1]
                    S0m, S0_temp = S0, np.ones((Nx, Ny))
                    if p['B0correct'] == 1:
                        if p['b0_scale'] is None:
                            raise ValueError('请先标定 b0_scale / b0_offset')
                        B0map = (b0_all[:, :, ns - 1] - p['b0_offset']) * p['b0_scale']
                        B0map[~(Thmask > 0)] = 0
                    else:
                        B0map = np.zeros((Nx, Ny))

                    # register==0 → 不做配准（registration.m 直接返回原数据）
                    x_as, V_as, V_as_unc, w_out, V_exp = zspec_interp_b0corr(
                        Zdata, w_offset_ori, Thmask, B0map, p['B0correct'],
                        p['NegOrPos'], x_lw)

                    if p['do_selfcorr_qc'] and p['B0correct'] == 2:
                        try:
                            sft_map = selfcorr_sft_map(Zdata, w_offset_ori, x_as, Thmask)
                            s = sft_map[Thmask > 0]
                            print(f'  slice {ns:2d} | 自校正 median {np.median(s):+.2f} ppm | '
                                  f'|sft|>1 占 {100 * np.mean(np.abs(s) > 1):.1f}%')
                            savemat(str(sp / 'sft_map.mat'), {'sft_map': sft_map})
                        except Exception as eqc:                     # noqa: BLE001
                            print(f'  slice {ns:2d} | QC 失败（已跳过）：{eqc}')

                    if p['save_single']:
                        V_as = V_as.astype(np.float32)
                        V_exp = V_exp.astype(np.float32)
                        V_as_unc = V_as_unc.astype(np.float32)

                    # 非 ROI 文件 → sliceN 根目录
                    base = save_root / f'slice{ns}'
                    for nm, val in (('S0', S0), ('S0m', S0m), ('S0_temp', S0_temp),
                                    ('Thmask', Thmask), ('w_offset', w_out), ('x_as', x_as)):
                        savemat(str(base / f'{nm}.mat'), {nm: val})

                    savemat(str(sp / 'V_exp.mat'), {'V_exp': V_exp})
                    if p['save_FULL_Vas']:
                        savemat(str(sp / 'V_as.mat'), {'V_as': V_as})
                    if p['save_uncorrect']:
                        savemat(str(sp / 'V_as_uncorrect.mat'), {'V_as_uncorrect': V_as_unc})

                    V_exp_mask = norm_z(S0_temp, V_exp, liverMask)
                    V_as_mask = norm_z(S0_temp, V_as, liverMask)
                    if p['save_single']:
                        V_exp_mask = V_exp_mask.astype(np.float32)
                        V_as_mask = V_as_mask.astype(np.float32)
                    savemat(str(sp / 'V_exp_mask.mat'), {'V_exp_mask': V_exp_mask})
                    savemat(str(sp / 'V_as_mask.mat'), {'V_as_mask': V_as_mask})

                    # 4D 汇总用
                    got_layer.append(True)
                    print(f'  slice {ns:2d}/{total_slice_num} | OK')
                except Exception as es:                              # noqa: BLE001
                    got_layer.append(False)
                    print(f'  slice {ns:2d} | 失败：{es}')

            # ---- 4.3 病例总目录的坐标轴 + 4D 汇总 ----
            savemat(str(save_root / 'w_offset.mat'), {'w_offset': w_out})
            savemat(str(save_root / 'x_as.mat'), {'x_as': x_as})
            if p['save_4d_aggregates'] and any(got_layer):
                # ★ 封存（2026-10-05）：aggregate_4d 在本文件里**没有定义**（只有这一处调用），
                #   开了 save_4d_aggregates 会 NameError。GUI 不暴露该开关；要启用请先补上
                #   aggregate_4d（对齐 MATLAB save_4d_aggregates 的产物）。
                aggregate_4d(save_root, total_slice_num, b0corr_tag)

            status.append(f'{case:<4} | 完成 {sum(got_layer)}/{len(got_layer)} 层 | '
                          f'{time.time() - t0:.1f} s')
        except Exception as e:                                       # noqa: BLE001
            status.append(f'{case:<4} | 主流程失败：{e}')
        print(status[-1])

    print('\n========== 汇总 ==========')
    for s in status:
        print(s)
    log = out_root / 'post_log.txt'
    log.write_text('\n'.join(status) + '\n', encoding='utf-8')
    print(f'结果目录：{out_root}\n日志：{log}')
    return status

