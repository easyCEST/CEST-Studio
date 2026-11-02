# -*- coding: utf-8 -*-
"""cest_roi.py —— ROI 定量曲线（Z 谱 + LD），Python 版

对应 MATLAB：``ROIprocessSXL1.m``（ROI 平均 Z 谱 + 三种归一化）
             + ``LD_spectra_plus_rex_0p7uT_filter.m``（边缘裁剪 → 洛伦兹拟合 → LD = 拟合 − 归一化Z）

口径（2026-10-05 用 MATLAB 样例逐位校准，见 _dev\\参考资料_ROI定量曲线_样例解析.md）::

    Raw      = ROI 内逐频偏平均 of V_exp_mask（V_exp/S0m.*liverMask）
    无后缀   = Raw / Raw.max(axis=0)          （每个 ROI 各除自己列的最大值）
    _0to1    = 每列 min-max 归一到 [0,1]
    _as 系列 = 同样三式，把 V_exp_mask 换成 V_as_mask（插值网格 x_as）
    边缘裁剪 = 「连续 3 点递增」剔除（左 cur+4 / 右 cur-3，与 MATLAB 逐位一致）
    拟合     = 复用 cest_ld.fit_lorentz / lorentz_n（Python 现有口径）
    LD       = Lorentzian_fit − z_norm（z_norm = 裁剪后 ROI 平均 Z 的 min-max 归一）
               ← 实测与 MATLAB 的 LD.mat max|diff| = 0

★ 本文件是**新增**，不动 cest_post / cest_ld / cest_display 里的任何函数。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import savemat as _savemat

import cest_ld as L
from cest_io import load_mat


def savemat(path, d, **kw):
    kw.setdefault('do_compression', True)
    return _savemat(path, d, **kw)


NORM_MODES = ('raw', 'div_max', 'z01')
NORM_LABEL = {'raw': 'Raw（不归一）', 'div_max': '÷列最大', 'z01': '0–1（每列 min-max）'}


# ===========================================================================
# 一、ROI 索引与平均
# ===========================================================================
def load_roi_layers(path):
    """读 ROI_total.mat 里的 **逐层 ROI**（ROI_total_layers 变量，⑧ 逐层画时才有）。

    返回 None，或 out[k][z]（k=ROI 序号 0-based，z=层号 0-based）的 0-based 索引数组：

    * 非空数组      → 该 ROI 在该层单独画的掩模（∩ 该层去噪掩模后的索引）
    * ``[-1]`` 哨兵 → 该 ROI 在该层确实没有（逐层画、这层没动过笔）
    * 空数组 (size 0) → 该 ROI 是 2D 共用，请回退用 ``ROI_total[k]``
    """
    got = load_mat(path, ['ROI_total_layers'])
    raw = got.get('ROI_total_layers')
    if raw is None:
        return None
    outer = np.asarray(raw, dtype=object)
    if outer.ndim == 0 or outer.size == 0:
        return None
    out = []
    for cel in outer.reshape(-1):                        # 每个元素 = 一个 ROI 的逐层 cell
        inner = np.asarray(cel, dtype=object).reshape(-1)
        row = []
        for el in inner:
            e = np.rint(np.asarray(el).ravel()).astype(np.int64)
            if e.size == 1 and e[0] == -1:              # 哨兵：该层确实没有 ROI
                row.append(e)
            else:
                row.append(e - 1)                       # 1-based → 0-based
        out.append(row)
    return out


def load_roi_total(path, layer=None) -> list:
    """读 ROI_total.mat → 每个 ROI 的 **0-based 列优先**线性索引数组列表。

    layer : 给定（**1-based 层号**）且文件里带逐层 ROI 时，优先用该层的索引：

            * 该层有单独画的 → 用它
            * 该层是 ``[-1]`` 哨兵 → 空数组（该 ROI 在该层没有）
            * 该层是空数组 → 回退到 2D 共用那张（= 全层平均上画的）

            不传 layer（或文件是纯 2D）→ 与旧版逐位一致。
    兼容 MATLAB 的 cell(N,1) / cell(1,N) / 单个矩阵。
    """
    got = load_mat(path, ['ROI_total', 'roi_total'])
    raw = got.get('ROI_total', got.get('roi_total'))
    if raw is None:
        raise ValueError(f'{path} 里没有 ROI_total 变量')
    arr = np.asarray(raw).ravel()
    out = []
    for el in arr:
        e = np.asarray(el).ravel()
        out.append(np.rint(e).astype(np.int64) - 1)          # 1-based → 0-based
    if layer is not None:
        lay = load_roi_layers(path)
        if lay is not None:
            z = int(layer) - 1
            for k in range(min(len(out), len(lay))):
                row = lay[k]
                if 0 <= z < len(row):
                    e = np.asarray(row[z], dtype=np.int64).ravel()
                    if e.size == 1 and e[0] == -1:
                        out[k] = np.array([], dtype=np.int64)
                    elif e.size > 0:
                        out[k] = e
    return out


def roi_ij(flat, plane_shape):
    """0-based 列优先线性索引 → (ii, jj)。"""
    return np.unravel_index(np.asarray(flat, dtype=np.int64), tuple(plane_shape), order='F')


def mean_spectra(V, ii, jj) -> np.ndarray:
    """ROI 内体素的逐频偏平均 → (nw,)。"""
    v = np.asarray(V, float)[ii, jj, :]
    return np.nanmean(v, axis=0)


def trim_edges(offset, z, negorpos: int = 0):
    """MATLAB 判据：边缘「连续 3 点递增」剔除。返回裁剪后的 (offset, z)。

    negorpos: 0 正频偏 / 1 负频偏 / 2 全频偏
    """
    z = np.asarray(z, float).copy()
    off = np.asarray(offset, float).copy()
    if negorpos == 1:
        left_lt, right_gt = -5.0, 1.5
    elif negorpos == 2:
        left_lt, right_gt = -5.0, 5.0
    else:
        left_lt, right_gt = -1.5, 5.0
    left = np.where(off < left_lt)[0]
    cnt = 0
    for i in range(len(left) - 1):
        cur, prv = left[-1 - i], left[-2 - i]
        cnt = cnt + 1 if z[cur] > z[prv] else 0
        if cnt >= 3:
            z, off = z[cur + 4:], off[cur + 4:]
            break
    right = np.where(off > right_gt)[0]
    cnt = 0
    for i in range(len(right) - 1):
        cur, nxt = right[i], right[i + 1]
        cnt = cnt + 1 if z[cur] > z[nxt] else 0
        if cnt >= 3:
            z, off = z[:cur - 3], off[:cur - 3]
            break
    return off, z


# ===========================================================================
# 二、归一化与拟合
# ===========================================================================
def norm_variants(m) -> dict:
    """三条曲线一次性给出：raw / ÷列最大 / 每列 min-max。m 形状 (nw, nROI)。"""
    m = np.asarray(m, float)
    mx = np.max(m, axis=0, keepdims=True)
    lo = np.min(m, axis=0, keepdims=True)
    div = np.where(np.abs(mx) < 1e-12, 1.0, mx)
    rng = np.where(np.abs(mx - lo) < 1e-12, 1.0, mx - lo)
    return dict(raw=m.copy(), div_max=m / div, z01=(m - lo) / rng)


def minmax(z) -> np.ndarray:
    z = np.asarray(z, float)
    lo, hi = np.nanmin(z), np.nanmax(z)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-12:
        return np.zeros_like(z)
    return (z - lo) / (hi - lo)


def fit_ppm_idx(w, ppm) -> np.ndarray:
    """三个拟合区间（l3~l2 ∪ l1~h1 ∪ h2~h3）在 w 上的索引（去重升序）。"""
    w = np.asarray(w, float)
    def n(t):
        return int(np.argmin(np.abs(w - t)))
    idx = np.r_[n(ppm['l3']):n(ppm['l2']) + 1,
                n(ppm['l1']):n(ppm['h1']) + 1,
                n(ppm['h2']):n(ppm['h3']) + 1]
    return np.unique(idx)


def fit_curve(w, z, ppm=None, par0=None, bounds=None):
    """对一条曲线做洛伦兹拟合（复用 cest_ld.fit_lorentz）。

    返回 (par, fit_full)；拟合失败返回 (None, None)。
    """
    ppm = dict(L.PPM if ppm is None else ppm)
    par0 = list(L.PAR0 if par0 is None else par0)
    bounds = L.BOUNDS if bounds is None else bounds
    w = np.asarray(w, float)
    z = np.asarray(z, float)
    idx = fit_ppm_idx(w, ppm)
    if idx.size < 4:
        return None, None
    par = L.fit_lorentz(w[idx], z[idx], par0, bounds)
    if par is None or not np.all(np.isfinite(par)):
        return None, None
    return par, L.lorentz_n(par, w)


# ===========================================================================
# 三、总入口：算一个病例/一层的全部 ROI 曲线
# ===========================================================================
def run_curves(z_exp, z_as, w_offset, x_as, roi_total_path, out_dir,
               cfg=None, log=print, progress=None, layer=None) -> dict:
    """算 ROI 曲线并落盘。

    z_exp/z_as : (X,Y,nw) / (X,Y,nx) 的 ROI 底图（mask 版）；z_as 可为 None
    out_dir    : 输出目录（不存在会建）
    cfg        : trim(True) / sym / negorpos / ppm / par0 / bounds / rois / dpi /
                 norm_mode / save_mat / save_xlsx / save_png
    layer      : 1-based 层号；有逐层 ROI 时，本层各取自己那张（缺层回退 2D 共用）
    返回 dict（含各 ROI 的曲线、拟合参数、文件清单）
    """
    cfg = dict(cfg or {})
    do_trim = bool(cfg.get('trim', True))
    negorpos = int(cfg.get('negorpos', 0) or 0)
    ppm = cfg.get('ppm') or dict(L.PPM)
    par0 = cfg.get('par0') or list(L.PAR0)
    bounds = cfg.get('bounds') or L.BOUNDS
    dpi = int(cfg.get('dpi', 150) or 150)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    z_exp = np.asarray(z_exp, float)
    w = np.asarray(w_offset, float).ravel()
    if z_exp.ndim != 3 or z_exp.shape[2] != w.size:
        raise ValueError(f'V_exp_mask 形状 {z_exp.shape} 与 {w.size} 个频偏不匹配')
    shape = z_exp.shape[:2]
    z_as = None if z_as is None else np.asarray(z_as, float)
    xa = None if x_as is None else np.asarray(x_as, float).ravel()
    if z_as is not None and (xa is None or z_as.shape[2] != xa.size):
        z_as, xa = None, None

    rois_all = load_roi_total(roi_total_path, layer=layer)
    pick = cfg.get('rois')
    if pick:
        idxs = [int(v) - 1 for v in pick]
        idxs = [k for k in idxs if 0 <= k < len(rois_all)]
    else:
        idxs = list(range(len(rois_all)))
    if not idxs:
        raise ValueError('没有可用的 ROI（检查 ROI_total.mat 或「要算哪些 ROI」）')
    # 逐层 ROI：这一层没画到那些 ROI → 跳过（不参与本层的平均/拟合）
    skipped = [k for k in idxs if np.asarray(rois_all[k]).size == 0]
    if skipped:
        log('   [跳过] 本层没有 ROI 的：'
            + '、'.join(f'ROI{k + 1}' for k in skipped)
            + ('（逐层 ROI 下有些层没勾画）' if layer is not None else ''))
        idxs = [k for k in idxs if np.asarray(rois_all[k]).size > 0]
    if not idxs:
        raise ValueError(f'第 {layer} 层没有可用的 ROI（逐层 ROI 下这一层没勾画）'
                         if layer is not None else '没有可用的 ROI')

    n = len(idxs)
    # Z 谱三口径：用**裁剪前**的 ROI 平均谱（= MATLAB 的 Zspc_*，全长 nw）
    full_cols = [_pad(mean_spectra(z_exp, *roi_ij(rois_all[k], shape)), w.size) for k in idxs]
    full_M = np.column_stack(full_cols)
    full = norm_variants(full_M)
    mSigE, mSigE_Raw, mSigE_0to1 = full['div_max'], full['raw'], full['z01']
    as_div, as_raw, as_z01 = [], [], []
    LD, FIT, WTRIM, ZTRIM = [], [], [], []
    summary = []
    for j, k in enumerate(idxs):
        if progress:
            progress(j, n, f'ROI{k + 1}')
        ii, jj = roi_ij(rois_all[k], shape)
        m_exp = mean_spectra(z_exp, ii, jj)
        if do_trim:
            off, zc = trim_edges(w, m_exp, negorpos)
        else:
            off, zc = w.copy(), m_exp.copy()
        z1 = minmax(zc)
        par, fit = fit_curve(off, z1, ppm, par0, bounds)
        if fit is None:
            fit = np.full_like(z1, np.nan)
            ld = np.full_like(z1, np.nan)
        else:
            ld = fit - z1
        ZTRIM.append(z1)
        LD.append(ld)
        FIT.append(fit)
        WTRIM.append(off)
        if z_as is not None:
            m_as = mean_spectra(z_as, ii, jj)
            o2, z2 = trim_edges(xa, m_as, negorpos) if do_trim else (xa.copy(), m_as.copy())
            as_raw.append(_pad(z2, xa.size))
            as_z01.append(_pad(minmax(z2), xa.size))
            as_div.append(_pad(_divmax(z2), xa.size))
        summary.append(dict(roi=k + 1, n_vox=int(ii.size),
                            ppm=('%.2f..%.2f' % (off.min(), off.max())) if off.size else '—',
                            par=None if par is None else [float(v) for v in par],
                            ld_min=float(np.nanmin(ld)) if np.any(np.isfinite(ld)) else None,
                            ld_max=float(np.nanmax(ld)) if np.any(np.isfinite(ld)) else None))
        log(f'   ROI{k + 1}：{ii.size} 体素，裁剪后 {off.size} 点'
            + (f'，拟合 par={np.round(par, 4)}' if par is not None else '，拟合失败'))

    N = len(idxs)
    files = []
    if cfg.get('save_mat', True):
        tag = f'{N}ROI'
        savemat(str(out_dir / f'Zspc_{tag}.mat'), {'mSigE': mSigE})
        savemat(str(out_dir / f'Zspc_{tag}_Raw.mat'), {'mSigE_Raw': mSigE_Raw})
        savemat(str(out_dir / f'Zspc_{tag}_0to1.mat'), {'mSigE_0to1': mSigE_0to1})
        savemat(str(out_dir / 'Zspc_filter.mat'), {'mSigE_trimmed': _cells(ZTRIM)})
        savemat(str(out_dir / 'w_offset_filter.mat'), {'w_offset_trimmed': _cells(WTRIM)})
        savemat(str(out_dir / 'LD.mat'), {'LD': _cells(LD)})
        savemat(str(out_dir / 'Lorentzian_fit.mat'), {'Lorentzian_fit': _cells(FIT)})
        files += [str(out_dir / x) for x in
                  (f'Zspc_{tag}.mat', f'Zspc_{tag}_Raw.mat', f'Zspc_{tag}_0to1.mat',
                   'Zspc_filter.mat', 'w_offset_filter.mat',
                   'LD.mat', 'Lorentzian_fit.mat')]
        if z_as is not None:
            tag2 = f'as_{N}ROI'
            savemat(str(out_dir / f'Zspc_{tag2}.mat'), {'mSig': np.column_stack(as_div)})
            savemat(str(out_dir / f'Zspc_{tag2}_Raw.mat'), {'mSig_Raw': np.column_stack(as_raw)})
            savemat(str(out_dir / f'Zspc_{tag2}_0to1.mat'), {'mSig_0to1': np.column_stack(as_z01)})
            files += [str(out_dir / x) for x in
                      (f'Zspc_{tag2}.mat', f'Zspc_{tag2}_Raw.mat', f'Zspc_{tag2}_0to1.mat')]
    if cfg.get('save_xlsx', True):
        fx = out_dir / 'ROI曲线.xlsx'
        try:
            _write_xlsx(fx, w, WTRIM, FIT, LD, N, xa, as_div, as_raw, as_z01)
            files.append(str(fx))
        except Exception as e:                                   # noqa: BLE001
            log(f'   [提示] xlsx 写失败（跳过）：{e}')
    if cfg.get('save_png', True):
        try:
            png = _plot(out_dir / 'ROI曲线.png', w, WTRIM, FIT, LD, N, dpi, ZTRIM)
            files.append(str(png))
        except Exception as e:                                   # noqa: BLE001
            log(f'   [提示] 曲线图失败（跳过）：{e}')

    meta = dict(n_roi=N, rois=[k + 1 for k in idxs], trim=do_trim, negorpos=negorpos,
                layer=(int(layer) if layer is not None else None),
                skipped_rois=[k + 1 for k in skipped],
                ppm={k: float(v) for k, v in ppm.items()},
                par0=[float(v) for v in par0], bounds=[[float(v) for v in bounds[0]],
                                                       [float(v) for v in bounds[1]]],
                has_as=z_as is not None, per_roi=summary)
    (out_dir / 'roi_meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    files.append(str(out_dir / 'roi_meta.json'))
    log(f'   已写 {out_dir}（{N} 个 ROI，{len(files)} 个文件）')
    return dict(out_dir=str(out_dir), n_roi=N, rois=meta['rois'], files=files,
                meta=meta, png=str(out_dir / 'ROI曲线.png'),
                curves=dict(w=w, w_trim=WTRIM, z_trim=ZTRIM, LD=LD, fit=FIT,
                            mSigE=mSigE, mSigE_Raw=mSigE_Raw, mSigE_0to1=mSigE_0to1))


# ----------------------------------------------------------------- 小工具
def _pad(v, n):
    v = np.asarray(v, float).ravel()
    if v.size == n:
        return v
    out = np.full(n, np.nan)
    out[:min(n, v.size)] = v[:min(n, v.size)]
    return out


def _divmax(v):
    v = np.asarray(v, float)
    mx = np.nanmax(v) if v.size else 1.0
    return v / (mx if abs(mx) > 1e-12 else 1.0)


def _trim_axis(ax, v):
    ax = np.asarray(ax, float).ravel()
    v = np.asarray(v, float).ravel()
    return ax[:v.size]


def _cells(list_of_arrays):
    """1×N object 数组（MATLAB 读出来是 cell）。"""
    arr = np.empty((1, len(list_of_arrays)), dtype=object)
    for i, a in enumerate(list_of_arrays):
        arr[0, i] = np.asarray(a, float).reshape(-1, 1)
    return arr


def _write_xlsx(path, w, WTRIM, FIT, LD, N, xa, as_div, as_raw, as_z01):
    """sheet1 Zspc（裁剪前全长，三口径）；sheet2 LD/拟合（裁剪后）；sheet3 插值版。"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'LD_fit'
    ws.append(['ppm'] + [f'Lorentzian_fit ROI{k + 1}' for k in range(N)]
              + [f'LD ROI{k + 1}' for k in range(N)])
    nmax = max((len(a) for a in FIT), default=0)
    for i in range(nmax):
        row = [float(WTRIM[0][i]) if i < len(WTRIM[0]) else None]
        row += [float(FIT[k][i]) if i < len(FIT[k]) else None for k in range(N)]
        row += [float(LD[k][i]) if i < len(LD[k]) else None for k in range(N)]
        ws.append(row)
    if as_div:
        ws2 = wb.create_sheet('Zspc_as')
        cols = max(len(a) for a in as_div)
        ws2.append(['ppm'] + [f'ROI{k + 1} (÷max)' for k in range(len(as_div))]
                   + [f'ROI{k + 1} (Raw)' for k in range(len(as_raw))]
                   + [f'ROI{k + 1} (0-1)' for k in range(len(as_z01))])
        for i in range(cols):
            row = [float(xa[i]) if xa is not None and i < len(xa) else None]
            for a in (as_div, as_raw, as_z01):
                for v in a:
                    row.append(float(v[i]) if i < len(v) else None)
            ws2.append(row)
    wb.save(str(path))


def _plot(png, w, WTRIM, FIT, LD, N, dpi=150, ZTRIM=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 7.2), dpi=int(dpi),
                             gridspec_kw=dict(height_ratios=[3, 2], hspace=0.28))
    ax1, ax2 = axes
    for k in range(N):
        off = WTRIM[k]
        ax1.plot(off, ZTRIM[k], '.:', lw=1.1, ms=3, label=f'ROI{k + 1} Z')
        ax1.plot(off, FIT[k], '-', lw=1.6, label=f'ROI{k + 1} 拟合')
        ax2.plot(off, LD[k], '-', lw=1.4, label=f'ROI{k + 1} LD')
    ax1.set_xlabel('频偏 (ppm)')
    ax1.set_ylabel('归一化 Z（0–1）')
    ax1.set_title('ROI Z 谱 + 洛伦兹拟合曲线')
    ax1.set_xlim(float(w.min()), float(w.max()))
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=9)
    ax2.axhline(0, color='#888', lw=0.8, ls='--')
    ax2.set_xlabel('频偏 (ppm)')
    ax2.set_ylabel('LD = 拟合 − Z（残差）')
    ax2.set_title('LD（洛伦兹差值）曲线')
    ax2.set_xlim(float(w.min()), float(w.max()))
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=9)
    try:
        fig.tight_layout()
    except Exception:                                            # noqa: BLE001
        pass
    fig.savefig(str(png))
    plt.close(fig)
    return png
