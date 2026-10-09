# -*- coding: utf-8 -*-
"""
cest_ld.py —— 逐体素 LD（洛伦兹）拟合（Python 版 make_voxel_ld.m + processLD_0909.m + Voxel_LD_filterV0909.m）
============================================================================

流程（与 MATLAB 一致）::

    CEST_result/<病例>/w_offset.mat, ssat.mat, s0.mat
        │ 1) 未归一化则 Z = ssat ./ s0（s0<=0 置 NaN）
        │ 2) normalizeSpect0916(Z, 0.05, 1, 99, doRescale=False) 只裁剪不重标定
        │ 3) 存 0.7ut_img_interp.mat（S0 去掉单帧维 / img_interp / w）
        ▼ 4) 逐层逐体素洛伦兹拟合（lsqcurvefit → scipy curve_fit/trf）
    voxel_LD.mat        voxel_ld / Voxel_LD_all         [X Y nLayer Nw]
    voxel_LD_interp.mat voxel_ld_interp / ..._all        [X Y nLayer Nwi] + w_offsetLD_interp

★ 轴序：本工程一律「层优先」[X, Y, 层, 频]（先层后频率的排布），
  每个 .mat 内写 axis_order='layer_freq'；读到旧序 [X,Y,频,层] 会自动 swapaxes 转正。

模型：``lorentz_N(par, x) = par(4) - par(2) ./ (1 + 4*((x-par(1))./par(3)).^2)``
拟合区间：[-10,-6] ∪ [-0.6,0.6] ∪ [6,10]（即 3 段，跳过 CEST 区）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import curve_fit
from scipy.io import savemat

from cest_io import AXIS_LAYER_FIRST, load_first, load_mat

# ---------------------------------------------------------------------------
# 参数（与 processLD_0909 / Voxel_LD_filterV0909 一致）
# ---------------------------------------------------------------------------
BOUNDS = ([-np.inf, -np.inf, 0.0, -np.inf], [0.0, np.inf, 1.8, np.inf])
PAR0 = [0.0, -50.0, 20.0, 1.0]
PPM = dict(l1=-0.6, l2=-6.0, l3=-10.0, h1=0.6, h2=6.0, h3=10.0)
SYM_THRESHOLD = 5.0
STEP_INTERP = 0.1
INTERP_METHOD = 'spline'


# ===========================================================================
# 一、归一化辅助（normalizeSpect0916 / isAlreadyNorm）
# ===========================================================================
def _round_half_up(x):
    return int(np.floor(float(x) + 0.5))


def is_already_norm(data, tol=0.05):
    v = np.asarray(data, dtype=float).ravel()
    v = v[np.isfinite(v)]
    if v.size == 0:
        return False
    s = np.sort(v)
    n = s.size
    lo = s[max(0, _round_half_up(n * 0.01) - 1)]
    hi = s[min(n, _round_half_up(n * 0.99)) - 1]
    return (lo >= -tol) and (hi <= 1 + tol)


def normalize_spect0916(data, tol=0.05, p_low=1, p_high=99, do_rescale=True):
    """只裁剪不重标定（do_rescale=False）+ 统一裁到 [0,1] + NaN/Inf → 0。"""
    data = np.asarray(data, dtype=float)
    v = data.ravel()
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise ValueError('normalizeSpect0916: 输入全是 NaN/Inf')
    s = np.sort(v)
    n = s.size
    lo = s[max(0, _round_half_up(n * p_low / 100) - 1)]
    hi = s[min(n, _round_half_up(n * p_high / 100)) - 1]
    med = s[max(0, _round_half_up(n * 50 / 100) - 1)]

    if not (hi > lo):
        return np.zeros_like(data), dict(scaled=False, lo=lo, hi=hi, median=med,
                                         reason='degenerate')
    already = (lo >= -tol) and (hi <= 1 + tol)
    scaled = (not already) and do_rescale
    if scaled:
        d = np.clip(data, lo, hi)
        out = (d - lo) / (hi - lo)
    else:
        out = data.copy()
    n_low, n_high = int(np.sum(out < 0)), int(np.sum(out > 1))
    out = np.clip(out, 0, 1)
    out[~np.isfinite(out)] = 0
    return out, dict(scaled=scaled, lo=lo, hi=hi, median=med, nClipLow=n_low,
                     nClipHigh=n_high, reason='ok')


# ===========================================================================
# 二、洛伦兹模型与单体素拟合
# ===========================================================================
def lorentz_n(par, x):
    """lorentz_N.m 的等价实现。"""
    return par[3] - par[1] / (1 + 4 * ((np.asarray(x, float) - par[0]) / par[2]) ** 2)


def fit_lorentz(w, z, par0=PAR0, bounds=BOUNDS):
    """lsqcurvefit(@lorentz_N, par0, xdata, ydata, lb, ub) 的等价实现。"""
    lb, ub = bounds
    p0 = np.clip(np.asarray(par0, float), np.asarray(lb), np.asarray(ub))  # MATLAB 会把越界初值拉回边界
    try:
        f = lambda x, *p: lorentz_n(p, x)       # curve_fit 传参顺序是 f(x, *params)
        par, _ = curve_fit(f, w, z, p0=p0, bounds=(lb, ub),
                           method='trf', max_nfev=int(1e6),
                           ftol=1e-10, xtol=1e-10, gtol=1e-10)
        return np.asarray(par, float)
    except Exception:                                            # noqa: BLE001
        return None


def _trim_symmetry(z, offset, sym_threshold=SYM_THRESHOLD):
    """左右对称性异常剔除（逐字对应 Voxel_LD_filterV0909 里那两段）。"""
    left = np.where(offset < -sym_threshold)[0]
    cnt = 0
    for ii in range(len(left) - 1):
        cur = left[len(left) - 1 - ii]          # MATLAB: left_indices(end-ii+1)
        prv = left[len(left) - 2 - ii]          # MATLAB: left_indices(end-ii)
        cnt = cnt + 1 if z[cur] > z[prv] else 0
        if cnt >= 3:
            z, offset = z[cur + 4:], offset[cur + 4:]      # 丢掉 1..(cur+3)（1-based）
            break

    right = np.where(offset > sym_threshold)[0]
    cnt = 0
    for ii in range(len(right) - 1):
        cur = right[ii]
        nxt = right[ii + 1]
        cnt = cnt + 1 if z[cur] > z[nxt] else 0
        if cnt >= 3:
            z, offset = z[:cur - 3], offset[:cur - 3]      # 丢掉 (cur-3)..end（1-based）
            break
    return z, offset


def voxel_ld_filter(v_exp_roi, w_offset, step_interp=STEP_INTERP,
                    interp_method=INTERP_METHOD, ppm=PPM, verbose=False,
                    max_voxels=None, sym_threshold=SYM_THRESHOLD,
                    par0=PAR0, bounds=BOUNDS):
    """
    对应 ``Voxel_LD_filterV0909``：返回
    (voxel_LD, w_offset_1, voxel_LD_interp, Lorentzian_fits, linewidth_map, w_offset_interp)
    """
    v = np.nan_to_num(np.asarray(v_exp_roi, dtype=float), nan=0.0)
    row, col, nw = v.shape
    w_offset_1 = np.asarray(w_offset, float).ravel()
    w_int = np.arange(w_offset_1.min(), w_offset_1.max() + step_interp / 2, step_interp)
    nwi = w_int.size

    voxel_ld = np.full((row, col, nw), np.nan)
    fits_map = np.full((row, col, nw), np.nan)
    ld_int = np.full((row, col, nwi), np.nan)
    fits_int = np.full((row, col, nwi), np.nan)
    lw_map = np.full((row, col), np.nan)

    w_orig_rounded = np.round(w_offset_1, 4)
    n_fit_ok = 0

    for i in range(row):
        if max_voxels is not None and n_fit_ok >= max_voxels:
            break
        for j in range(col):
            if max_voxels is not None and n_fit_ok >= max_voxels:
                break
            z = v[i, j, :].copy()
            offset = w_offset_1.copy()
            if np.all(z == 0) or offset.size != z.size:
                continue

            z, offset = _trim_symmetry(z, offset, sym_threshold)
            if z.size == 0:
                continue
            order = np.argsort(offset)
            w, z = offset[order], z[order]
            if w.size < 5:
                continue

            def _idx(val):
                return int(np.argmin(np.abs(w - val)))

            idx_fit = np.r_[_idx(ppm['l3']):_idx(ppm['l2']) + 1,
                            _idx(ppm['l1']):_idx(ppm['h1']) + 1,
                            _idx(ppm['h2']):_idx(ppm['h3']) + 1]
            idx_fit = idx_fit[(idx_fit >= 0) & (idx_fit < w.size)]
            if idx_fit.size < 4:
                continue

            par = fit_lorentz(w[idx_fit], z[idx_fit], par0, bounds)
            if par is None:
                continue
            n_fit_ok += 1
            lw_map[i, j] = par[2]

            fit_curve = lorentz_n(par, w)
            ld_res = fit_curve - z
            mtr_res = 1.0 / (z + 1e-5) - 1.0 / (fit_curve + 1e-5)

            # 只把「保留下来的原始频偏」写回原位（MATLAB 用 ismember(round(...)) 对齐）
            pos = {round(float(vv), 4): kk for kk, vv in enumerate(w_orig_rounded)}
            idx_v = np.array([pos[round(float(vv), 4)] for vv in w if round(float(vv), 4) in pos],
                             dtype=int)
            if idx_v.size == w.size:
                voxel_ld[i, j, idx_v] = ld_res
                fits_map[i, j, idx_v] = fit_curve

            # 拟合后上采样到密网格（只到数据支撑范围）
            m = (w_int >= w[0] - 1e-6) & (w_int <= w[-1] + 1e-6)
            if not np.any(m):
                continue
            wq = w_int[m]
            zq = CubicSpline(w, z)(wq)                    # interp1(...,'spline')
            fq = lorentz_n(par, wq)
            ld_int[i, j, m] = fq - zq
            fits_int[i, j, m] = fq

    if verbose:
        print(f'      有效拟合体素 {n_fit_ok}')
    return voxel_ld, w_offset_1, ld_int, fits_map, lw_map, w_int.reshape(-1, 1)


# ===========================================================================
# 三、整合 + 逐层批处理
# ===========================================================================
def voxel_ld_filter_fast(v_exp_roi, w_offset, step_interp=STEP_INTERP,
                         ppm=PPM, verbose=False, max_voxels=None,
                         sym_threshold=SYM_THRESHOLD, bounds=BOUNDS, par0=PAR0,
                         c_grid=None, g_grid=None):
    """
    与 ``voxel_ld_filter`` 同语义，但把「逐体素 Python 循环」换成
    「(中心, 线宽) 网格搜索 + 幅度/基线线性闭式解」并对全部体素向量化。

    模型 y = par4 - par2 / (1 + 4((x-par1)/par3)²)：固定 (par1, par3) 后，
    对 (par2, par4) 是线性的 → 可用正态方程直接解，无需迭代。
    因此速度提升几十倍（每层从几分钟降到 1~3 秒），代价是解为全局最小二乘解
    （MATLAB 的 lsqcurvefit 是局部解），会略有差异。
    """
    v = np.nan_to_num(np.asarray(v_exp_roi, dtype=float), nan=0.0)
    X, Y, Nw = v.shape
    w = np.asarray(w_offset, float).ravel()
    Z = v.reshape(-1, Nw)
    keep = ~np.all(Z == 0, axis=1)
    idx_keep = np.nonzero(keep)[0]
    if max_voxels is not None:
        idx_keep = idx_keep[:max_voxels]
    m = idx_keep.size

    out_ld = np.full((X, Y, Nw), np.nan)
    w_int = np.arange(w.min(), w.max() + step_interp / 2, step_interp)
    out_int = np.full((X, Y, w_int.size), np.nan)
    lw_map = np.full((X, Y), np.nan)
    if m == 0:
        return out_ld, w, out_int, None, lw_map, w_int.reshape(-1, 1)

    Zk = Z[idx_keep]

    # ---- 1) 对称性剔除（向量化：看左尾 / 右头连续 3 次递增）----
    valid = np.ones((m, Nw), bool)
    L = np.where(w < -sym_threshold)[0]
    R = np.where(w > sym_threshold)[0]
    if L.size >= 4:
        d = Zk[:, L[1:]] > Zk[:, L[:-1]]
        hit = d[:, -3:].all(axis=1)
        if np.any(hit):
            valid[hit, :L[L.size - 3] + 4] = False
    if R.size >= 4:
        d = Zk[:, R[:-1]] > Zk[:, R[1:]]
        hit = d[:, :3].all(axis=1)
        if np.any(hit):
            valid[hit, R[2] - 3:] = False

    # ---- 2) 拟合列（三段区间，在整条 w 上算一次）----
    cols = []
    for lo, hi in ((-10.0, -6.0), (-0.6, 0.6), (6.0, 10.0)):
        a = int(np.argmin(np.abs(w - lo)))
        b = int(np.argmin(np.abs(w - hi)))
        cols.extend(range(min(a, b), max(a, b) + 1))
    cols = np.array(sorted(set(cols)), dtype=int)
    fitmask = valid.copy()
    fm = np.zeros(Nw, bool)
    fm[cols] = True
    fitmask &= fm[None, :]
    npts = fitmask.sum(axis=1)
    ok_vox = (npts >= 4) & (valid.sum(axis=1) >= 5)

    Wsub = w[cols]
    Vm = fitmask[:, cols].astype(float)
    Zm = Zk[:, cols] * Vm                       # 无效列已置 0
    Sy = Zm.sum(axis=1)
    Syy = (Zm * Zm).sum(axis=1)
    n = Vm.sum(axis=1)

    if c_grid is None:
        c_grid = np.arange(-2.0, float(bounds[1][0]) + 1e-9, 0.05)
    if g_grid is None:
        g_grid = np.arange(0.2, float(bounds[1][2]) + 1e-9, 0.05)

    best = np.zeros((m, 4))
    best_sse = np.full(m, np.inf)

    def _update(c, g, B):
        """B: (k,) 或 (m,k)，算出 (a,b,sse) 并只在更优时更新"""
        nonlocal best, best_sse
        if B.ndim == 1:
            B = np.broadcast_to(B, Zm.shape)
        SB = (Vm * B).sum(axis=1)
        SBB = (Vm * B * B).sum(axis=1)
        SBy = (Zm * B).sum(axis=1)
        det = n * SBB - SB * SB
        good = det > 1e-12
        safe = np.where(good, det, 1.0)
        a = np.where(good, (n * SBy - SB * Sy) / safe, 0.0)
        b = np.where(good, (SBB * Sy - SB * SBy) / safe, 0.0)
        sse = np.where(good, Syy - a * SBy - b * Sy, np.inf)
        better = (sse < best_sse) & ok_vox
        if np.any(better):
            bi = np.nonzero(better)[0]
            best[bi, 0] = np.broadcast_to(c, (m,))[bi] if np.ndim(c) else c
            best[bi, 1] = np.broadcast_to(g, (m,))[bi] if np.ndim(g) else g
            best[bi, 2] = a[bi]           # par2（★修正：闭式解拟合的是 z ≈ a·B + b，
            #                                 模型写成 par4 + par2·B，故 par2 必须 = +a；
            #                                 原写成 -a 会把洛伦兹翻成镜像（近水处冒假峰），
            #                                 与 voxel_ld_filter / MATLAB 对不上，2026-10-04 修）
            best[bi, 3] = b[bi]           # par4
            best_sse[bi] = sse[bi]

    for c in c_grid:
        for g in g_grid:
            _update(c, g, 1.0 / (1.0 + 4.0 * ((Wsub - c) / g) ** 2))

    # ---- 3) 两轮局部细化（按体素各自的网格）----
    for step in (0.02, 0.005):
        for dc in (-1, 0, 1):
            for dg in (-1, 0, 1):
                if dc == 0 and dg == 0:
                    continue
                c = np.clip(best[:, 0] + dc * step, -10.0, float(bounds[1][0]))   # ub(1)=0
                g = np.clip(best[:, 1] + dg * step, 0.05, float(bounds[1][2]))    # ub(3)=1.8
                B = 1.0 / (1.0 + 4.0 * ((Wsub[None, :] - c[:, None]) / g[:, None]) ** 2)
                _update(c, g, B)

    # ---- 4) 写回：拟合谱在全部频偏上取值，LD = 拟合 - 实测 ----
    Bfull = 1.0 / (1.0 + 4.0 * ((w[None, :] - best[:, 0:1]) / best[:, 1:2]) ** 2)
    model = best[:, 3:4] + best[:, 2:3] * Bfull
    res = np.where(valid, model - Zk, np.nan)
    lut = dict(zip(idx_keep.tolist(), range(m)))
    ii, jj = np.unravel_index(idx_keep, (X, Y))
    out_ld[ii, jj, :] = res
    lw_map[ii, jj] = best[:, 1]

    # ---- 5) 上采样到密网格（实测谱走 spline）----
    for kk in range(m):
        vmask = valid[kk]
        if vmask.sum() < 5:
            continue
        wk, zk = w[vmask], Zk[kk][vmask]
        mq = (w_int >= wk[0] - 1e-6) & (w_int <= wk[-1] + 1e-6)
        if not np.any(mq):
            continue
        wq = w_int[mq]
        zq = CubicSpline(wk, zk)(wq)
        fq = best[kk, 3] + best[kk, 2] / (1.0 + 4.0 * ((wq - best[kk, 0]) / best[kk, 1]) ** 2)
        out_int[ii[kk], jj[kk], mq] = fq - zq

    if verbose:
        print(f'      有效拟合体素 {int(np.sum(np.isfinite(lw_map)))}')
    return out_ld, w, out_int, None, lw_map, w_int.reshape(-1, 1)


def make_voxel_ld(input_folder, out_folder=None, layers=None, tol=0.05,
                  step_interp=STEP_INTERP, save_interp=True, verbose=True,
                  max_voxels=None, fast=True, mask_file=None,
                  ppm=PPM, par0=PAR0, bounds=BOUNDS,
                  sym_threshold=SYM_THRESHOLD, interp_method=INTERP_METHOD,
                  c_grid=None, g_grid=None):
    """
    对应 ``make_voxel_ld.m``（整合 + 归一化）+ ``processLD_0909.m``（逐层拟合）。

    input_folder : 含 w_offset.mat / ssat.mat / s0.mat 的病例目录
    out_folder   : 输出目录，默认与 input_folder 相同
    layers       : 只算哪几层（1-based），None = 全部；写盘时层号仍是原层号
    """
    in_dir = Path(input_folder)
    out_dir = Path(out_folder) if out_folder else Path(input_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1) 读数据 ----
    w = np.asarray(load_first(in_dir / 'w_offset.mat', ('w_offset', 'w')), float).ravel()
    ssat = np.asarray(load_first(in_dir / 'ssat.mat', ('zdata', 'ssat')), float)  # zdata=统一名
    s0 = np.asarray(load_first(in_dir / 's0.mat', ('s0', 'S0')), float)
    _bf = in_dir / 'b0.mat'
    _b0 = load_first(_bf, ('b0', 'B0')) if _bf.exists() else None   # b0 可选
    b0 = np.asarray(_b0, float) if _b0 is not None else np.empty(0)

    # ---- 1.5) ★ 轴序统一为「层优先」[X, Y, 层, 频] ----
    #   ① 的产物已是层优先；这里兜底旧文件：[X,Y,频,层]（dim3 长度 = 频偏个数）自动转正。
    if ssat.ndim == 3:
        ssat = ssat[:, :, :, None]
    elif ssat.ndim == 4 and ssat.shape[3] != w.size and ssat.shape[2] == w.size:
        ssat = np.swapaxes(ssat, 2, 3)                       # 视图，不复制
    if ssat.ndim != 4 or ssat.shape[3] != w.size:
        raise ValueError(f'ssat 形状 {ssat.shape} 与 {w.size} 个频偏不匹配（应为 [X,Y,层,频]）')

    # s0 → (X, Y, 层)
    s0 = np.asarray(s0, dtype=float)
    if s0.ndim == 3:
        s0_3d = s0
    elif s0.ndim == 4 and s0.shape[2] == 1:
        s0_3d = s0[:, :, 0, :]                               # 旧序 (X,Y,1,层)
    elif s0.ndim == 4 and s0.shape[3] == 1:
        s0_3d = s0[:, :, :, 0]                               # (X,Y,层,1)
    else:
        s0_3d = np.squeeze(s0)
    if s0_3d.shape[0] != ssat.shape[0] or s0_3d.shape[1] != ssat.shape[1]:
        raise ValueError(f's0 {s0_3d.shape} 与数据 {ssat.shape[:2]} 尺寸不一致')

    # b0 → (X, Y, 层)（b0 没有频偏维，同样统一成层优先）
    if np.size(b0):
        if b0.ndim == 4 and b0.shape[2] == 1:
            b0 = b0[:, :, 0, :]                              # 旧序 (X,Y,1,层)
        elif b0.ndim == 4 and b0.shape[3] == 1:
            b0 = b0[:, :, :, 0]

    # ---- 2) 归一化（层优先下：ssat[X,Y,层,频] ./ s0[X,Y,层,1]）----
    if is_already_norm(ssat, tol):
        if verbose:
            print('【检查】已在 [0,1] 附近 → 跳过除 S0')
        z = ssat
    else:
        den = s0_3d.copy()
        den[den <= 0] = np.nan
        try:
            z = ssat / den[:, :, :, None]
        except ValueError as e:
            raise ValueError(f'S0 {den.shape} 与 Z 谱 {ssat.shape} 维度无法广播：{e}') from e
        if verbose:
            fin = z[np.isfinite(z)]
            print(f'  除 S0 后 median = {np.median(fin):.4f}')

    # ---- 3) 只裁剪不重标定 ----
    img_interp, info = normalize_spect0916(z, tol, 1, 99, False)
    if verbose:
        print(f'  优化后 : min {img_interp.min():.4f}  max {img_interp.max():.4f}  '
              f'median {np.median(img_interp):.4f}')
        print(f'  被裁剪 : <0 共 {info["nClipLow"]} 个，>1 共 {info["nClipHigh"]} 个')

    # ---- 4) 存 0.7ut_img_interp.mat（层优先：s0 (X,Y,层) / img_interp (X,Y,层,频)）----
    s0_sq = s0_3d
    savemat(str(out_dir / '0.7ut_img_interp.mat'),
            {'s0': s0_sq, 'b0': b0, 'norm_z': img_interp,
             'img_interp': img_interp, 'w': w.reshape(1, -1),
             'axis_order': AXIS_LAYER_FIRST,
             'S0': s0_sq, 'w_offset': w.reshape(1, -1)},   # S0 / w_offset = 旧名兼容
            do_compression=True)
    if verbose:
        print(f'  已整合保存：{out_dir / "0.7ut_img_interp.mat"}  '
              f'(s0 {s0_sq.shape} / b0 {np.shape(b0)} / norm_z=img_interp {img_interp.shape})  '
              f'[轴序 {AXIS_LAYER_FIRST}]')

    # ---- 5) 维度：img_interp 保持层优先 [X, Y, 层, 频] ----
    if img_interp.ndim != 4:
        raise ValueError(f'img_interp 维度 {img_interp.shape} 不是 4D')
    if img_interp.shape[3] != w.size and img_interp.shape[2] == w.size:
        img_interp = np.swapaxes(img_interp, 2, 3)            # 兜底旧序
    if img_interp.shape[3] != w.size:
        raise ValueError(f'img_interp 维度 {img_interp.shape} 无法匹配 w（{w.size} 个频偏）')
    mz = img_interp
    nx, ny, n_layer, nw = mz.shape

    # ---- 6) 掩模 ----
    # 优先级：显式给的 mask_file → 病例目录里的 Mask.mat → （找不到则全 1，并提示）
    # 说明：拟合本身不需要组织 ROI，只要有掩模即可；
    #      缺掩模时 MATLAB 才会去画「噪声 ROI + 组织 ROI」。
    if mask_file is not None:
        mask = np.asarray(load_first(Path(mask_file),
                                     ('Mask', 'Thmask', 'liverMask')), float)
        if verbose:
            print(f'  掩模来自：{mask_file}  {mask.shape}')
    else:
        mask_f = in_dir / 'Mask.mat'
        if not mask_f.exists():
            mask_f = out_dir / 'Mask.mat'
        if mask_f.exists():
            mask = np.asarray(load_mat(mask_f, ['Mask'])['Mask'], float)
            if verbose:
                print(f'  已从 {mask_f.name} 加载掩码文件 {mask.shape}')
        else:
            mask = np.ones((nx, ny, n_layer))
            print('  [提示] 没找到 Mask.mat —— 默认全 1；建议用 --mask 指定现成掩模')
    thmask = mask[:, :, 0] if mask.ndim == 3 else mask
    if thmask.ndim == 2 and thmask.shape != (nx, ny):
        raise ValueError(f'掩模形状 {thmask.shape} 与数据 {(nx, ny)} 不一致')

    # ---- 7) 逐层拟合（数组一律层优先 [X, Y, 层, 频]）----
    nwi = int(round((w.max() - w.min()) / step_interp)) + 1
    ld_all = np.zeros((nx, ny, n_layer, nw))
    ld_interp_all = np.full((nx, ny, n_layer, nwi), np.nan)
    w_interp = np.arange(w.min(), w.max() + step_interp / 2, step_interp).reshape(-1, 1)

    layer_list = list(range(1, n_layer + 1)) if layers is None else [int(v) for v in layers]
    for s in layer_list:
        t0 = time.time()
        v_slice = np.squeeze(mz[:, :, s - 1, :])            # X x Y x Nw
        v_mask = v_slice * np.asarray(thmask)[:, :, None]      # NormZ(ones, V, Thmask)
        _filt = voxel_ld_filter_fast if fast else voxel_ld_filter
        _kw = dict(ppm=ppm, par0=par0, bounds=bounds, sym_threshold=sym_threshold,
                   c_grid=c_grid, g_grid=g_grid, verbose=verbose, max_voxels=max_voxels)
        if not fast:
            _kw["interp_method"] = interp_method
        ld, _, ld_i, _, lw, _ = _filt(v_mask, w, step_interp, **_kw)
        ld_all[:, :, s - 1, :] = ld
        if ld_i.shape[2] == ld_interp_all.shape[3]:
            ld_interp_all[:, :, s - 1, :] = ld_i
        if verbose:
            print(f'  第 {s}/{n_layer} 层完成（{time.time() - t0:.1f}s，'
                  f'有效体素 {int(np.sum(np.isfinite(lw)))}，线宽中位数 '
                  f'{np.nanmedian(lw):.3f}）')

    # ---- 8) 保存（带 axis_order 标记）----
    savemat(str(out_dir / 'voxel_LD.mat'),
            {'voxel_ld': ld_all, 'Voxel_LD_all': ld_all,      # voxel_ld=统一名
             'axis_order': AXIS_LAYER_FIRST},
            do_compression=True)
    if save_interp:
        savemat(str(out_dir / 'voxel_LD_interp.mat'),
                {'voxel_ld_interp': ld_interp_all,
                 'Voxel_LD_interp_all': ld_interp_all,
                 'axis_order': AXIS_LAYER_FIRST,
                 'w_offset_ld_interp': w_interp,
                 'w_offsetLD_interp': w_interp},
                do_compression=True)
    if verbose:
        print(f'  已保存：voxel_LD.mat (voxel_ld {ld_all.shape} = [X,Y,层,频])'
              + (f' / voxel_LD_interp.mat ({ld_interp_all.shape})'
                 if save_interp else ''))
    return ld_all, ld_interp_all, w_interp


def make_img_interp(input_folder, out_folder=None, mask_file=None, tol=0.05, verbose=True):
    """
    【轻量】只生成展示要用的 ``0.7ut_img_interp.mat``：读 → 除 S0 → 归一化裁剪 → 存盘。

    与 ``make_voxel_ld`` 的区别：**不做洛伦兹拟合**（拟合是第⑥步里画 LD 图才需要的昂贵部分，
    10 层约几分钟）。第⑥步展示 raw / ST / APTW 只需要这一步。

    输出（层优先，带 axis_order）：
        s0 (X,Y,层) / b0 (X,Y,层) / norm_z = img_interp (X,Y,层,频) / w
    """
    in_dir = Path(input_folder)
    out_dir = Path(out_folder) if out_folder else Path(input_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    w = np.asarray(load_first(in_dir / 'w_offset.mat', ('w_offset', 'w')), float).ravel()
    ssat = np.asarray(load_first(in_dir / 'ssat.mat', ('zdata', 'ssat')), float)
    s0 = np.asarray(load_first(in_dir / 's0.mat', ('s0', 'S0')), float)
    _bf = in_dir / 'b0.mat'
    _b0 = load_first(_bf, ('b0', 'B0')) if _bf.exists() else None
    b0 = np.asarray(_b0, float) if _b0 is not None else np.empty(0)

    # 轴序统一为层优先
    if ssat.ndim == 3:
        ssat = ssat[:, :, :, None]
    elif ssat.ndim == 4 and ssat.shape[3] != w.size and ssat.shape[2] == w.size:
        ssat = np.swapaxes(ssat, 2, 3)
    if ssat.ndim != 4 or ssat.shape[3] != w.size:
        raise ValueError(f'ssat 形状 {ssat.shape} 与 {w.size} 个频偏不匹配（应为 [X,Y,层,频]）')

    if s0.ndim == 3:
        s0_3d = s0
    elif s0.ndim == 4 and s0.shape[2] == 1:
        s0_3d = s0[:, :, 0, :]
    elif s0.ndim == 4 and s0.shape[3] == 1:
        s0_3d = s0[:, :, :, 0]
    else:
        s0_3d = np.squeeze(np.asarray(s0, dtype=float))
    if np.size(b0):
        if b0.ndim == 4 and b0.shape[2] == 1:
            b0 = b0[:, :, 0, :]
        elif b0.ndim == 4 and b0.shape[3] == 1:
            b0 = b0[:, :, :, 0]

    if is_already_norm(ssat, tol):
        z = ssat
    else:
        den = s0_3d.copy()
        den[den <= 0] = np.nan
        z = ssat / den[:, :, :, None]

    img_interp, info = normalize_spect0916(z, tol, 1, 99, False)

    # 掩模：顺手写到 out_dir\\Mask.mat，这样第⑥步展示模块能自动找到它
    if mask_file and Path(mask_file).exists():
        _m = np.asarray(load_first(Path(mask_file), ('Mask', 'Thmask', 'liverMask')), float)
        if _m.ndim == 4 and _m.shape[2] == 1:
            _m = _m[:, :, 0, :]
        elif _m.ndim == 4 and _m.shape[3] == 1:
            _m = _m[:, :, :, 0]
        savemat(str(out_dir / 'Mask.mat'), {'Mask': _m}, do_compression=True)
        if verbose:
            print(f'  掩模已写入 {out_dir / "Mask.mat"}  {np.shape(_m)}')

    savemat(str(out_dir / '0.7ut_img_interp.mat'),
            {'s0': s0_3d, 'b0': b0, 'norm_z': img_interp,
             'img_interp': img_interp, 'w': w.reshape(1, -1),
             'axis_order': AXIS_LAYER_FIRST,
             'S0': s0_3d, 'w_offset': w.reshape(1, -1)},
            do_compression=True)
    if verbose:
        print(f'  已保存 {out_dir / "0.7ut_img_interp.mat"}  '
              f'(s0 {s0_3d.shape} / img_interp {img_interp.shape})  [轴序 {AXIS_LAYER_FIRST}]'
              f'  裁剪 <0 {info["nClipLow"]} / >1 {info["nClipHigh"]}')
    return img_interp


# ===========================================================================
# 四、命令行
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description='逐体素 LD 洛伦兹拟合（Python 版）')
    ap.add_argument('--folder', default=r'C:\Users\Administrator\Desktop\2026\09\CEST_result\h1')
    ap.add_argument('--out', default=None, help='输出目录（默认与 --folder 相同）')
    ap.add_argument('--layers', nargs='+', type=int, default=None, help='只算哪几层，如 --layers 1')
    ap.add_argument('--no-interp', action='store_true', help='不写 voxel_LD_interp.mat')
    ap.add_argument('--max-voxels', type=int, default=None,
                    help='每层最多拟合多少个体素（测试用；默认不限）')
    ap.add_argument('--exact', action='store_true',
                    help='用逐体素 lsqcurvefit 等价实现（慢，但与 MATLAB 最接近）')
    ap.add_argument('--mask', dest='mask_file', default=None,
                    help='指定掩模 .mat（变量名 Mask/Thmask/liverMask 均可）')
    a = ap.parse_args(argv)
    make_voxel_ld(a.folder, a.out, layers=a.layers, save_interp=not a.no_interp,
                  max_voxels=a.max_voxels, fast=not a.exact, mask_file=a.mask_file)
    return 0

