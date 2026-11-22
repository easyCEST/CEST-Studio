# -*- coding: utf-8 -*-
"""cest_agent.ortho —— 三正交面（横断 / 冠状 / 矢状）出图。

和 GUI ② 的「三视图」窗口是**同一份取数逻辑**（cest_display.load_display_frames
+ build_mode_data + apply_mask），只是把构图换成无界面版，供智能体在后台跑。

★ 不 import PySide6：智能体跑在命令行 / 后台线程里，出图只用 matplotlib。
★ 不自己重算数据：LD 用的仍是 ④ 落盘的 voxel_LD.mat，只切面、不改数值。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

# 与 GUI 三视图一致的平面顺序（合成图里 2×2 的左上 / 右上 / 左下）
PLANES = ('ax', 'co', 'sa')
PLANE_NAME = {'ax': '横断面 (X×Y)', 'co': '冠状面 (Z×X)', 'sa': '矢状面 (Z×Y)'}


def _sub(proj, key, case):
    """PROJ 下某个阶段目录里的病例目录（不存在就 None）。"""
    from detect import rules as R
    try:
        d = R.find_subdir(proj, key)
    except Exception:                                    # noqa: BLE001
        d = None
    if not d:
        return None
    c = Path(d) / str(case)
    return c if c.is_dir() else None


def _pick_data(proj, case):
    """挑一个能出图的 4D 数据文件：优先 01_重建（v7.3，按帧读秒开）。"""
    recon = _sub(proj, '01_recon', case)
    if recon:
        for n in ('normZ.mat', 'ssat.mat', 'zdata.mat'):
            p = recon / n
            if p.exists():
                return p, recon
        mts = sorted(recon.glob('*.mat'))
        for p in mts:
            return p, recon
    ld = _sub(proj, '04_ld', case)
    if ld:
        for n in ('0.7ut_img_interp.mat', '0p7P_interp.mat', '0p7N_interp.mat'):
            p = ld / n
            if p.exists():
                return p, ld
    return None, None


def _load(proj, case, data, src_dir):
    """按帧读；文件不是 v7.3 就退回整本读。返回 (d, 说明)。"""
    import cest_display as D
    from detect import chain

    try:
        mf = chain.mask_file(proj, case)
    except Exception:                                    # noqa: BLE001
        mf = None

    d = D.load_display_frames(data_path=str(data), mask_file=mf,
                             src_dirs=[str(src_dir)] if src_dir else [])
    if d is not None:
        return d, '按帧读'
    d = D.load_display_input(str(data), mask_file=mf)
    return d, '整本读（不是 v7.3）'


def _q95(vol):
    """自适应色标：非 0 / 有限体素的 5~95%（= GUI 的「自动」）。"""
    v = np.asarray(vol, dtype=float)
    v = v[np.isfinite(v) & (v != 0)]
    if not v.size:
        return (0.0, 1.0)
    lo, hi = float(np.percentile(v, 5)), float(np.percentile(v, 95))
    return (lo, hi) if hi > lo else (lo, lo + 1e-6)


def _draw(vol, ix, iy, iz, caxis, title, cmap_n_black=1):
    """画 2×2：三切面 + 色标。返回 matplotlib Figure。"""
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    import cest_display as D

    plt.rcParams['font.sans-serif'] = D.FONT_FAMILY
    plt.rcParams['axes.unicode_minus'] = False

    nx, ny, nz = vol.shape
    ax_im = vol[:, :, iz]                     # 横断：行 X，列 Y
    co_im = vol[:, iy, :].T                   # 冠状：行 Z，列 X
    sa_im = vol[ix, :, :].T                   # 矢状：行 Z，列 Y

    cmap = D.jet_with_black(cmap_n_black)
    fig = plt.figure(figsize=(13.2, 10.4), dpi=110, facecolor='w')
    slots = (('ax', [0.055, 0.52, 0.40, 0.42], ax_im),
             ('co', [0.525, 0.52, 0.40, 0.42], co_im),
             ('sa', [0.055, 0.06, 0.40, 0.42], sa_im))
    im = None
    for key, rect, arr in slots:
        a = fig.add_axes(rect)
        im = a.imshow(arr, cmap=cmap, origin='upper', interpolation='nearest',
                      aspect='auto')
        im.set_clim(*caxis)
        a.set_axis_off()
        a.set_title(f'{PLANE_NAME[key]}', fontsize=12)
        # 十字线：另外两个坐标
        if key == 'ax':
            a.axhline(iy, color='#e6b422', lw=0.9, ls='--')
            a.axvline(ix, color='#e6b422', lw=0.9, ls='--')
        elif key == 'co':
            a.axhline(iz, color='#e6b422', lw=0.9, ls='--')
            a.axvline(ix, color='#e6b422', lw=0.9, ls='--')
        else:
            a.axhline(iz, color='#e6b422', lw=0.9, ls='--')
            a.axvline(iy, color='#e6b422', lw=0.9, ls='--')

    fig.suptitle(title, fontsize=13, y=0.975)
    cax = fig.add_axes([0.525, 0.12, 0.40, 0.035])
    cb = fig.colorbar(im, cax=cax, orientation='horizontal')
    cb.ax.tick_params(labelsize=9)

    fig.text(0.525, 0.20,
             f'横断 层 {iz + 1}/{nz}　｜　冠状 Y {iy + 1}/{ny}　｜　矢状 X {ix + 1}/{nx}',
             fontsize=11)
    fig.text(0.525, 0.16, f'色标 {caxis[0]:.4g} ~ {caxis[1]:.4g}（5~95%）', fontsize=10)
    return fig


def _single(vol_slice, caxis, title, cmap_n_black=1):
    """单张切面图（带色标），导出用。"""
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    import cest_display as D

    plt.rcParams['font.sans-serif'] = D.FONT_FAMILY
    plt.rcParams['axes.unicode_minus'] = False
    cmap = D.jet_with_black(cmap_n_black)
    fig = plt.figure(figsize=(5.4, 5.0), dpi=110, facecolor='w')
    a = fig.add_axes([0.02, 0.10, 0.96, 0.84])
    im = a.imshow(vol_slice, cmap=cmap, origin='upper', interpolation='nearest',
                  aspect='auto')
    im.set_clim(*caxis)
    a.set_axis_off()
    a.set_title(title, fontsize=11)
    cax = fig.add_axes([0.10, 0.04, 0.80, 0.025])
    cb = fig.colorbar(im, cax=cax, orientation='horizontal')
    cb.ax.tick_params(labelsize=8)
    return fig


def export(proj, case, mode='LD', w=3.5, ld_mode='POS', use_mask=True,
           x=None, y=None, z=None, caxis=None, dpi=200, cmap_n_black=1):
    """出一张三视图（合成 + 三个单面），落到 02_展示\\<病例>\\<MODE> <w>ppm_ortho\\。

    返回 dict(ok, out_dir, files, shape, slices, caxis, mode, w, sec)
    失败返回 dict(ok=False, error=...)，绝不抛异常给上层工具。
    """
    t0 = time.time()
    mode = str(mode or 'LD').upper()
    if mode not in ('RAW', 'ST', 'APTW', 'LD'):
        return dict(ok=False, error=f'mode 只能是 raw/ST/APTW/LD，收到 {mode!r}')

    try:
        import cest_display as D
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'加载 cest_display 失败：{type(e).__name__}: {e}')

    data, src_dir = _pick_data(proj, case)
    if data is None:
        return dict(ok=False, error=f'{case} 找不到可用的 4D 数据'
                                    f'（01_重建 里的 normZ.mat 或 04_逐体素LD 里的 img_interp）')

    if mode == 'LD':
        ld_d = _sub(proj, '04_ld', case)
        has_ld = bool(ld_d and (ld_d / 'voxel_LD.mat').exists())
        if not has_ld:
            return dict(ok=False, error=f'{case} 还没有 voxel_LD.mat，先在 ④ 跑逐体素 LD',
                        goto=dict(mid='m4_ld', case=case))

    try:
        d, how = _load(proj, case, data, src_dir)
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'读数据失败：{type(e).__name__}: {e}')
    if not d:
        return dict(ok=False, error=f'读不了 {data}')

    try:
        vol, _mask = D.build_mode_data(d, mode=mode, w_target=float(w), w_mirror=-float(w),
                                       ld_mode=str(ld_mode).upper(), use_mask=bool(use_mask),
                                       verbose=False)
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'取数失败（{mode} {w:g}ppm）：{type(e).__name__}: {e}')

    vol = np.asarray(vol, dtype=float)
    if vol.ndim != 3:
        return dict(ok=False, error=f'取到的是 {vol.shape}，不是三维体')
    nx, ny, nz = vol.shape

    def _idx(v, n):
        return int(np.clip(int(v) - 1 if v else n // 2, 0, n - 1))
    ix, iy, iz = _idx(x, nx), _idx(y, ny), _idx(z, nz)

    cax = tuple(float(v) for v in caxis) if caxis else _q95(vol)
    target = (f'{mode} {float(w):g}ppm').replace('.', '_') + '_ortho'

    # 落盘目录：02_展示\<病例>\<target>\（与 GUI 三视图一致）
    from detect import rules as R
    try:
        disp = R.find_subdir(proj, '02_display')
    except Exception:                                    # noqa: BLE001
        disp = None
    out = Path(disp) / str(case) / target if disp else (Path(proj) / '02_展示' / str(case) / target)
    if out.exists():
        out = out.with_name(f'{out.name}_{time.strftime("%Y%m%d_%H%M%S")}')

    title = f'{case}　{mode} {float(w):+g} ppm'
    files = []
    try:
        out.mkdir(parents=True, exist_ok=True)
        spec = [(f'{target}.png', _draw(vol, ix, iy, iz, cax, title, cmap_n_black)),
                ('横断面.png', _single(vol[:, :, iz], cax,
                                       f'{title}　层 {iz + 1}/{nz}', cmap_n_black)),
                ('冠状面.png', _single(vol[:, iy, :].T, cax,
                                       f'{title}　Y {iy + 1}/{ny}', cmap_n_black)),
                ('矢状面.png', _single(vol[ix, :, :].T, cax,
                                       f'{title}　X {ix + 1}/{nx}', cmap_n_black))]
        from matplotlib import pyplot as plt
        for name, fig in spec:
            p = out / name
            fig.savefig(str(p), dpi=int(dpi), bbox_inches='tight', facecolor='w')
            plt.close(fig)
            files.append(str(p))
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'保存失败：{type(e).__name__}: {e}')

    return dict(ok=True, out_dir=str(out), files=files, mode=mode, w=float(w),
                shape=dict(nx=nx, ny=ny, nz=nz),
                slices=dict(x=ix + 1, y=iy + 1, z=iz + 1),
                caxis=[cax[0], cax[1]], read=how,
                data_file=str(data), sec=round(time.time() - t0, 1))
