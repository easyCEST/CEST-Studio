# -*- coding: utf-8 -*-
"""
cest_display.py —— 【第⑥步】多层图像展示（Python 版）
==============================================================================
把 MATLAB 那 14 个 `dispaly_*` + 7 个 `save_combined_plot*` 家族收敛成一套参数化实现。

设计要点
--------
1. **统一命名**：读进来只有 6 个 canonical 名 ——
       s0 (X,Y,层) / img_interp (X,Y,层,频) / w (1,频) / layer_idx (层号)
       mask (X,Y,层) / voxel_ld (X,Y,层,频)
   旧名（S0 / norm_z / img_raw / w_offset / Mask / Voxel_LD_all …）在 `norm_names()` 里映射一次。
2. **统一轴序**：一律「层优先」[X,Y,层,频]（见 cest_io.AXIS_LAYER_FIRST），
   读到旧序自动 swapaxes 转正；每个 .mat 里的 axis_order 字段优先。
3. **一个函数覆盖全部拼接**：`gen_mosaic(img3d, opts)`；
   raw / ST / APTw / LD 只是「取哪个频偏、要不要 1-Z、要不要相减」的差别，由 `build_mode_data` 定。
4. 色标三种策略：`manual`（默认，对齐 MATLAB 手输）/ `per_layer_5_95` / `global_5_95`。
5. 出图落三件：`<name>_combined.png`（300 dpi）、`<name>_combined.mat`（data/layers/caxis_range，
   与 MATLAB 同名同内容，方便逐位对拍）、`<name>_combined.json`（本次全部参数）。

对应关系
--------
process_img_interp        → process_img_interp
altered_colormap          → jet_with_black
plotLayerQuantileCurve_new→ layer_quantile
find_freq_index           → nearest_freq_index
save_combined_plot*       → gen_mosaic（含 title_fmt / cbar_pos / cmap_n_black 参数）
dispaly_all_img*          → display_single / display_batch
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from cest_io import (AXIS_LAYER_FIRST, load_first, load_mat, to_layer_first_3d,
                     to_layer_first_4d)

# ---------------------------------------------------------------------------
# 统一命名：canonical → 旧名别名（大小写不敏感，按顺序取第一个命中的）
# ---------------------------------------------------------------------------
NAME_ALIASES = {
    's0':         ('s0', 'S0', 'S0m', 'S0_temp'),
    'img_interp': ('img_interp', 'norm_z', 'normZ', 'img_raw', 'img', 'zdata'),
    'w':          ('w', 'w_offset', 'freq', 'freq_seq', 'freq_sequence'),
    'mask':       ('mask', 'Mask', 'Thmask', 'liverMask'),
    'voxel_ld':   ('voxel_ld', 'Voxel_LD_all', 'voxel_ld_interp', 'Voxel_LD_interp_all'),
}
# 主数据文件的候选（按优先级）
DATA_FILE_CANDIDATES = ('0.7ut_img_interp.mat', '0p7P_interp.mat', '0p7N_interp.mat',
                        '2APT_interp.mat', '0.7ut_img_raw.mat')

FONT_FAMILY = ['Microsoft YaHei', 'SimHei', 'Arial']


# ===========================================================================
# 一、读入（统一命名 + 统一轴序）
# ===========================================================================
def norm_names(raw: dict) -> dict:
    """把任意来源的变量字典映射成 canonical 名（大小写不敏感，取第一个命中）。"""
    lower = {str(k).lower(): k for k in raw}
    out = {}
    for canon, aliases in NAME_ALIASES.items():
        for a in aliases:
            k = lower.get(a.lower())
            if k is not None:
                out[canon] = raw[k]
                break
    return out


def find_data_file(path) -> Path:
    """定位主数据 .mat：给定文件直接用；给目录则按候选名优先，再退化为含 img_interp 的 mat。"""
    p = Path(path)
    if p.is_file():
        return p
    for name in DATA_FILE_CANDIDATES:
        f = p / name
        if f.exists():
            return f
    mts = sorted(p.glob('*.mat'))
    for f in mts:
        try:
            got = load_mat(f, list(NAME_ALIASES['img_interp']))
        except Exception:                                        # noqa: BLE001
            continue
        if got:
            return f
    raise FileNotFoundError(f'在 {p} 下找不到含 img_interp 的 .mat')


def detect_axis_order(d: dict, w: np.ndarray, arr: np.ndarray) -> str:
    """判断 4D 数组是 [X,Y,层,频] 还是 [X,Y,频,层]（优先信文件里的 axis_order）。"""
    ao = d.get('axis_order')
    if ao is not None:
        s = (ao if isinstance(ao, str) else str(np.ravel(np.asarray(ao))[0]))
        if 'layer' in s:
            return AXIS_LAYER_FIRST
        if 'freq' in s:
            return 'freq_layer'
    if arr.ndim == 4 and w is not None and np.size(w) in (arr.shape[2], arr.shape[3]):
        return AXIS_LAYER_FIRST if arr.shape[3] == np.size(w) else 'freq_layer'
    return AXIS_LAYER_FIRST


def load_display_input(path, verbose=True, mask_file=None, ld_file=None):
    """
    读入展示需要的数据，返回统一命名的 dict：

        {'s0': (X,Y,层), 'img_interp': (X,Y,层,频), 'w': (1,频), 'layer_idx': (1,层),
         'mask': (X,Y,层)|None, 'voxel_ld': (X,Y,层,频)|None, 'meta': {...}}

    path 可以是主数据 .mat，也可以是含它的目录（会自动找 Mask.mat / voxel_LD.mat）。
    """
    f = find_data_file(path)
    d = Path(f).parent
    raw = load_mat(f)
    canon = norm_names(raw)

    img = canon.get('img_interp')
    if img is None:
        raise KeyError(f'{f} 里没有 img_interp / norm_z / img_raw 之类的 4D 数据')
    img = np.asarray(img, dtype=float)
    w = canon.get('w')
    w = np.asarray(w, dtype=float).ravel() if w is not None else None

    if img.ndim == 3:                       # (X,Y,频) 单层
        img = img[:, :, None, :]
    order = detect_axis_order(raw, w, img)
    if w is not None:
        n_freq = int(np.size(w))
        n_layer = img.shape[3] if img.shape[2] == n_freq else img.shape[2]
        img = to_layer_first_4d(img, n_layer, n_freq)
    nx, ny, n_layer, n_freq = img.shape

    s0 = canon.get('s0')
    s0 = to_layer_first_3d(np.asarray(s0, dtype=float), n_layer) if s0 is not None else None

    # mask / voxel_ld：显式给 → 用例目录 → 数据目录
    mf = Path(mask_file) if mask_file else (d / 'Mask.mat' if (d / 'Mask.mat').exists() else None)
    mask = None
    if mf is not None and Path(mf).exists():
        g = load_mat(mf, list(NAME_ALIASES['mask']))
        if g:
            mask = to_layer_first_3d(np.asarray(list(g.values())[0], dtype=float), n_layer)

    lf = Path(ld_file) if ld_file else (d / 'voxel_LD.mat' if (d / 'voxel_LD.mat').exists() else None)
    vld = None
    if lf is not None and Path(lf).exists():
        g = load_mat(lf, list(NAME_ALIASES['voxel_ld']))
        if g:
            v = np.asarray(list(g.values())[0], dtype=float)
            if v.ndim == 4 and np.size(v) and n_freq in v.shape:
                v = to_layer_first_4d(v, n_layer, n_freq)
            vld = v

    meta = dict(data_file=str(f), axis_order=order, shape=img.shape,
                has_s0=s0 is not None, mask_file=str(mf) if mf else None,
                ld_file=str(lf) if lf else None)
    if verbose:
        print(f'[display] 数据 {Path(f).name} {img.shape} (X,Y,层,频) 轴序={order}  '
              f's0={"有" if s0 is not None else "无"}  mask={"有" if mask is not None else "无"}  '
              f'voxel_ld={"有" if vld is not None else "无"}')
    return dict(s0=s0, img_interp=img, w=w, layer_idx=np.arange(1, n_layer + 1),
                mask=mask, voxel_ld=vld, meta=meta)


# ===========================================================================
# 二、MATLAB 支撑函数（逐个 Python 化）
# ===========================================================================
def process_img_interp(img_interp):
    """对应 process_img_interp.m：clip 到 [0,1] 后 1 - ·（生成 ST/信号图）。"""
    a = np.array(img_interp, dtype=float)
    a[a > 1] = 1
    a[a < 0] = 0
    return 1.0 - a


def apply_mask(img3d, mask):
    """
    对应 MATLAB 的两步：`all_img_full = img .* Mask;  img = all_img_full - (~Mask);`
    即掩模外强制成 **-1**（不是 0），掩模内保持原值。这是跟参考图对得上的关键。
    """
    m = (np.asarray(mask, dtype=float) > 0).astype(float)
    img = np.asarray(img3d, dtype=float)
    # ★ 修复广播 bug：单层时 img3d=(X,Y,1) 而掩模是 2D (X,Y)，numpy 会广播成
    #   (X,Y,1)*(X,Y)->(X,Y,X)（层轴炸成一整维）→ 多层展示没法用。补成 (X,Y,1)。
    if m.ndim == 2 and img.ndim == 3:
        m = m[:, :, None]
    return img * m + m - 1.0


def jet_with_black(n_rows=1, n=256):
    """对应 altered_colormap / cmap(1,:)=0：jet 色图前 n_rows 行置黑（背景黑）。"""
    from matplotlib import colormaps
    cmap = colormaps['jet'].resampled(n)
    colors = cmap(np.linspace(0, 1, n))
    colors[:n_rows, :3] = 0.0
    from matplotlib.colors import ListedColormap
    return ListedColormap(colors)


def nearest_freq_index(w, target):
    """对应 find_freq_index / get_closest_index：返回最接近 target 的索引（0 基）。"""
    w = np.asarray(w, dtype=float).ravel()
    return int(np.argmin(np.abs(w - float(target))))


def layer_quantile(img, layers=None, q=(5, 95), nonzero_only=True):
    """
    对应 plotLayerQuantileCurve_new：逐层 5%/95% 分位（默认只统计非 0 体素）。
    返回 (lower(1×L), upper(1×L))。
    """
    a = np.asarray(img, dtype=float)
    idx = range(a.shape[2]) if layers is None else [int(v) - 1 for v in layers]
    lo, hi = [], []
    for k in idx:
        v = a[:, :, k].ravel()
        v = v[np.isfinite(v)]
        if nonzero_only:
            v = v[v != 0]
        if v.size:
            lo.append(float(np.percentile(v, q[0])))
            hi.append(float(np.percentile(v, q[1])))
        else:
            lo.append(0.0); hi.append(0.0)
    return np.array(lo), np.array(hi)


# ===========================================================================
# 三、按 mode 组装待画的三维数据
# ===========================================================================
MODES = ('raw', 'ST', 'APTW', 'LD')


def build_mode_data(d, mode='APTW', w_target=3.5, w_mirror=-3.5,
                    ld_mode='POS', use_mask=True, verbose=True):
    """
    从 load_display_input 的结果里取出要画的 (X,Y,层) 三维数组。

    mode: 'raw'   → img_interp 取 w_target
          'ST'    → 1 - clip(img_interp,0,1) 取 w_target
          'APTW'  → clip(img_interp,0,1)[-3.5] - clip(img_interp,0,1)[+3.5]（MTRasym 型差值）
          'LD'    → voxel_LD 取 w_target（ld_mode='POS'）/ -w_target（'NEG'）
    返回 (img3d, mask3d|None)
    """
    mode = str(mode).upper()
    img = d['img_interp']
    w = d['w']
    mask = d['mask'] if use_mask else None

    if mode in ('RAW', 'ST'):
        src = process_img_interp(img) if mode == 'ST' else img
        if w is None:
            raise ValueError('raw/ST 模式需要 w（频偏轴）')
        k = nearest_freq_index(w, w_target)
        out = np.array(src[:, :, :, k], dtype=float)
    elif mode == 'APTW':
        if w is None:
            raise ValueError('APTW 模式需要 w')
        kp = nearest_freq_index(w, w_target)
        km = nearest_freq_index(w, w_mirror)
        src = np.clip(img, 0.0, 1.0)      # 对应 MATLAB 的 process_img_interp（clip 到 [0,1]）
        out = np.array(src[:, :, :, km] - src[:, :, :, kp], dtype=float)
    elif mode == 'LD':
        if d.get('voxel_ld') is None:
            raise ValueError('LD 模式需要 voxel_LD.mat（先在 ③ 里跑逐体素 LD）')
        v = np.asarray(d['voxel_ld'], dtype=float)
        if v.ndim != 4:
            raise ValueError(f'voxel_ld 形状 {v.shape} 不是 4D')
        if w is not None:
            k = nearest_freq_index(w, w_target if str(ld_mode).upper() == 'POS' else -w_target)
        else:
            k = int(v.shape[3] // 2)
        out = np.array(v[:, :, :, k], dtype=float)
    else:
        raise ValueError(f"mode 只能是 {MODES}，收到 {mode!r}")

    if use_mask and mask is not None:
        out = apply_mask(out, mask)          # 对应 MATLAB：.* Mask 再 - (~Mask)

    if verbose:
        fin = out[np.isfinite(out)]
        print(f'[display] mode={mode} w={w_target:+.1f} → {out.shape}  '
              f'min {fin.min():.4g} max {fin.max():.4g} median {np.median(fin):.4g}')
    return out, mask


# ===========================================================================
# 四、核心：多层拼接出图（覆盖 save_combined_plot / _1111 / _3D_new / _LD / _ST …）
# ===========================================================================
def gen_mosaic(img, opts=None):
    """
    多层拼接出图。

    img  : (X, Y, 层) —— 已选好频偏、已按 mode 处理、已 mask 的数据
    opts : dict
        title_prefix : 'APTW 3.5ppm'         子图标题前缀
        layers       : [1..L]                要画的层号（用于标题）
        cols         : 5                     每行子图数
        caxis        : (lo, hi)              统一色标
        cmap_n_black : 1                     jet 前 n 行置黑
        title_fmt    : '{prefix}  {val:.1f} slice'   占位符 {prefix} {val} {layer}
        title_values : 标题里 {val} 的取值（None → 用 layers）
        cbar_pos     : 'bottom' | 'right'
        fig_w, row_h : 2100, 400             图窗尺寸（像素，dpi=100 时）
        dpi          : 300                   保存分辨率
        save_dir/name: 落盘目录 / 文件名（None = 只返回 Figure）
        save_mat     : True                  额外存 <name>_combined.mat
        extra        : dict                  写进 json 的附加信息
    """
    from matplotlib import pyplot as plt

    o = dict(opts or {})
    layers = list(o.get('layers') or range(1, int(img.shape[2]) + 1))
    cols = int(o.get('cols') or 5)
    rows = int(np.ceil(len(layers) / cols))
    caxis = o.get('caxis')
    n_black = int(o.get('cmap_n_black', 1))
    title_prefix = o.get('title_prefix', '')
    title_fmt = o.get('title_fmt', '{prefix}  {val:.1f} slice')
    title_values = o.get('title_values')
    cbar_pos = o.get('cbar_pos', 'bottom')
    fig_w = float(o.get('fig_w', 2100))
    row_h = float(o.get('row_h', 400))
    dpi = int(o.get('dpi', 300))

    plt.rcParams['font.sans-serif'] = FONT_FAMILY
    plt.rcParams['axes.unicode_minus'] = False

    fig = plt.figure(figsize=(fig_w / 100.0, row_h * rows / 100.0), dpi=100, facecolor='w')
    cmap = jet_with_black(n_black)
    ims = []
    for i, lay in enumerate(layers):
        ax = fig.add_subplot(rows, cols, i + 1)
        im = ax.imshow(img[:, :, int(lay) - 1], cmap=cmap, origin='upper', interpolation='nearest')
        if caxis is not None:
            im.set_clim(float(caxis[0]), float(caxis[1]))
        ax.set_axis_off()
        ax.set_aspect('equal')
        val = (title_values[i] if title_values is not None else lay)
        ax.set_title(title_fmt.format(prefix=title_prefix, val=float(val), layer=int(lay)),
                     fontsize=10)
        ims.append(im)

    if caxis is None:
        caxis = (float(np.nanmin(img)), float(np.nanmax(img)))
        for im in ims:
            im.set_clim(*caxis)

    if cbar_pos == 'bottom':
        cax = fig.add_axes([0.10, 0.05, 0.70, 0.025])
        cb = fig.colorbar(ims[0], cax=cax, orientation='horizontal')
    else:
        cax = fig.add_axes([0.92, 0.12, 0.02, 0.75])
        cb = fig.colorbar(ims[0], cax=cax, orientation='vertical')
    cb.set_label('')
    try:
        fig.tight_layout(rect=(0, 0.10 if cbar_pos == 'bottom' else 0, 1, 1))
    except Exception:                                            # noqa: BLE001
        pass

    save_dir = o.get('save_dir')
    name = o.get('name')
    if save_dir and name:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        png = save_dir / f'{name}.png'
        fig.savefig(str(png), dpi=dpi, facecolor='w')
        if o.get('save_mat', True):
            from scipy.io import savemat
            savemat(str(save_dir / f'{name}.mat'),
                    {'data': np.asarray(img, dtype=float),
                     'layers': np.asarray(layers, dtype=float).reshape(1, -1),
                     'caxis_range': np.asarray(caxis, dtype=float).reshape(1, -1)})
        info = dict(title_prefix=title_prefix, mode=o.get('mode'), w=o.get('w'),
                     layers=layers, cols=cols, caxis=list(map(float, caxis)),
                     cmap_n_black=n_black, title_fmt=title_fmt, cbar_pos=cbar_pos,
                     dpi=dpi, source=o.get('source'), extra=o.get('extra'))
        (save_dir / f'{name}.json').write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(f'  已保存 {png.name}  (+ .mat/.json)')
    return fig


def _matlab_name(text):
    """MATLAB 保存名：小数点 → 下划线（'APTW 3.5ppm' → 'APTW 3_5ppm'）。"""
    return str(text).replace('.', '_')


# ===========================================================================
# 五、顶层入口
# ===========================================================================
def display_single(src, mode='APTW', w=3.5, save_root=None, cols=5,
                   caxis=None, caxis_mode='manual', name=None, sub_dir=None,
                   save_fig=True, **kw):
    """
    单例出图。

    src        : 主数据 .mat 或目录（0.7ut_img_interp.mat / 0p7P_interp.mat …）
    mode       : raw / ST / APTW / LD
    w          : 目标频偏（ST/APTW 会再取对称频偏）
    save_root  : 输出根目录（None = 只算不存）
    sub_dir    : 输出子目录（如 '0.7ut_img_interp'）
    caxis_mode : 'manual'（用 caxis）/ 'per_layer_5_95' / 'global_5_95'
    """
    d = load_display_input(src, verbose=kw.pop('verbose', True),
                           mask_file=kw.pop('mask_file', None),
                           ld_file=kw.pop('ld_file', None))
    img3d, mask = build_mode_data(d, mode=mode, w_target=w,
                                  w_mirror=kw.pop('w_mirror', -w),
                                  ld_mode=kw.pop('ld_mode', 'POS'),
                                  use_mask=kw.pop('use_mask', True))
    layers = kw.pop('layers', None) or list(range(1, img3d.shape[2] + 1))

    if caxis_mode == 'per_layer_5_95':
        lo, hi = layer_quantile(img3d, layers)
        caxis = (float(np.min(lo)), float(np.max(hi)))
        print(f'  自适应色标(逐层 5~95%) = [{caxis[0]:.6f}, {caxis[1]:.6f}]')
    elif caxis_mode == 'global_5_95':
        v = img3d[:, :, [int(v) - 1 for v in layers]]
        v = v[np.isfinite(v) & (v != 0)]
        caxis = (float(np.percentile(v, 5)), float(np.percentile(v, 95)))
        print(f'  自适应色标(全局 5~95%) = [{caxis[0]:.6f}, {caxis[1]:.6f}]')

    prefix = name if name else f'{mode} {w}ppm'
    png_name = f'{_matlab_name(prefix)}_combined'
    out_dir = None
    if save_root:
        out_dir = Path(save_root) / (sub_dir or '')
    fig = gen_mosaic(img3d, dict(
        title_prefix=prefix, layers=layers, cols=cols, caxis=caxis,
        save_dir=out_dir if save_fig else None, name=png_name if save_fig else None,
        mode=mode, w=w, source=d['meta']['data_file'],
        **{k: v for k, v in kw.items() if k in
           ('cmap_n_black', 'title_fmt', 'title_values', 'cbar_pos', 'dpi', 'fig_w', 'row_h')}))
    return fig, dict(img3d=img3d, pipe=d, out_dir=out_dir, name=png_name, caxis=caxis)


def display_batch(jobs, **common):
    """
    批量出图。jobs = [(src, mode, w), ...] 或 [dict(src=…, mode=…, w=…, save_root=…), …]
    失败不中断，末尾汇总。
    """
    ok, bad = [], []
    for j in jobs:
        if isinstance(j, dict):
            kw = dict(common); kw.update(j)
        else:
            src, mode, w = j
            kw = dict(common, mode=mode, w=w)
            kw['src'] = src
        try:
            fig, rec = display_single(**kw)
            import matplotlib.pyplot as plt
            plt.close(fig)
            ok.append(f'{Path(kw["src"]).name} {kw.get("mode")} w={kw.get("w")} → {rec["name"]}')
        except Exception as e:                                   # noqa: BLE001
            bad.append(f'{kw.get("src")} {kw.get("mode")} → {type(e).__name__}: {e}')
            print(f'  [失败] {bad[-1]}')
    print(f'\n批量出图：成功 {len(ok)} / 失败 {len(bad)}')
    for s in ok:
        print('  OK  ' + s)
    for s in bad:
        print('  ERR ' + s)
    return ok, bad
