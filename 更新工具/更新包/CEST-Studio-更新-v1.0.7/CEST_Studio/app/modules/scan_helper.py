# -*- coding: utf-8 -*-
"""app/modules/scan_helper.py —— ⓪ 的目录扫描（只读文件头，不加载数据）。

两件事：
  * scan_data(root)  → 找「S0 + Z 谱（+ w）」的成对数据，按病例列成行；
  * scan_masks(root) → 找掩模/ROI，按病例列成行（Mask / 器官 mask / snrMask / ROI_total）。

★ 只读变量名与 shape：v7 走 scipy.whosmat，v7.3 走 h5py（不拉进大数组）。
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------- 变量识别
S0_VARS = ('s0', 'S0')
W_VARS = ('w_offset', 'w', 'freq_seq', 'freq', 'W', 'x_as', 'xas')
B0_VARS = ('b0', 'B0')
Z_NAME_HINTS = ('img_interp', 'interp', 'zspect', 'zspec', 'norm_z', 'normz',
                'ssat', 'zdata', 'cest')

# 器官名（掩模文件名/变量名里出现就认作「器官掩模」）
ORGAN_WORDS = ('liver', 'brain', 'kidney', 'heart', 'lung', 'muscle', 'spleen',
               'tumor', 'tumour', 'organ', 'tissue', 'pancreas', 'testis',
               'prostate', 'breast', 'liver', 'head', 'thigh', 'leg')

# 跳过：备份、缓存、明显的非掩模产物
SKIP_RE = re.compile(r'\.bak|_bak|~$|^\.', re.I)
MASK_SKIP_VARS = ('axis_order', 'note', 'ROI', 'V_as', 'V_exp', 'V_as_mask',
                  'V_exp_mask')
# 太通用的变量名：形状像谱但不是原始 Z 谱（展示/缓存产物）
GENERIC_VARS = ('data', 'img', 'image', 'out', 'result', 'tmp', 'temp')


def mat_head(path):
    """→ [(变量名, shape, dtype)]；失败返回 []。不加载数据。"""
    from cest_io import is_v73
    p = str(path)
    try:
        if is_v73(p):
            import h5py
            out = []
            with h5py.File(p, 'r') as f:
                for k in f.keys():
                    if k.startswith('#'):
                        continue
                    try:
                        out.append((k, tuple(f[k].shape), str(f[k].dtype)))
                    except Exception:                             # noqa: BLE001
                        out.append((k, (), ''))
            return out
        from scipy.io import whosmat
        return [(n, tuple(s), c) for n, s, c in whosmat(p)]
    except Exception:                                             # noqa: BLE001
        return []


def _iter_mats(root: Path):
    """递归找 .mat；跳过备份文件与「输出目录」（避免把自己的产物当源再扫一遍）。"""
    root = Path(root)
    for f in sorted(root.rglob('*.mat')):
        if SKIP_RE.search(f.name):
            continue
        if _in_output_dir(f.parent, root):
            continue
        yield f


# 本软件写盘的固定子目录名（中英双份）+ 提取输出目录
OUT_DIR_NAMES = ('00_roi', '00_掩模', '01_recon', '01_重建', '02_display', '02_展示',
                 '03_post', '03_CEST后处理', '04_ld', '04_逐体素LD',
                 '05_prf_extract', '05_PRF提取', '06_prf_analysis', '06_PRF分析',
                 '掩膜提取', '_cache')


def _in_output_dir(d: Path, root: Path) -> bool:
    try:
        rel = d.relative_to(root)
    except ValueError:
        return False
    return any(part in OUT_DIR_NAMES for part in rel.parts)


def case_name_for(dirpath: Path, root: Path) -> str:
    """病例名 = 相对 root 的路径用 _ 连起来（ctrl/1W/小鼠1 → ctrl_1W_小鼠1）。

    目录就是 root 本身（没有层级）时返回 ''，交给调用方用文件名主干。
    """
    try:
        rel = Path(dirpath).relative_to(Path(root))
    except ValueError:
        return ''
    parts = [x for x in rel.parts if x not in ('', '.')]
    return '_'.join(parts) if parts else ''


def short_case(stem: str) -> str:
    """文件主干 → 病例名：去掉 _interp / _img_interp 这类后缀（GS_interp → GS）。"""
    s = re.sub(r'(?i)[_-]?(img[_-]?)?interp$', '', str(stem or ''))
    return (s or str(stem or '')).strip(' _-')


# ---------------------------------------------------------------- 数据模式
def _is_z_candidate(var, shape):
    """Z 谱：3D/4D，最后一维像频率点（>=8），且不是掩模/w/b0。"""
    low = var.lower()
    if low in MASK_SKIP_VARS or 'mask' in low or low in ('w_offset', 'w', 'b0'):
        return False
    if not shape or len(shape) not in (3, 4):
        return False
    if shape[-1] < 8:
        return False
    if low in [v.lower() for v in S0_VARS]:
        return False
    if _is_weak_z(var):          # V_as / V_exp / data / img… 是后处理产物，不当候选
        return False
    return True


# 名字明显不是原始 Z 谱的（③ 产物 / 展示缓存 / LD 图谱）→ 不当候选
WEAK_Z_VARS = ('v_as', 'v_exp', 'data', 'img', 'image', 'out', 'result', 'tmp', 'temp',
               'ld1', 'ld2', 'ld3', 'ld_', 'ldmap')


def _is_s0(var, shape):
    low = var.lower()
    if low not in [v.lower() for v in S0_VARS]:
        return False
    return bool(shape) and len(shape) in (2, 3, 4)


def _is_weak_z(var):
    """名字像后处理产物（V_as / data / img…）但**不是** Z 谱常见名（img_interp 不算）。"""
    low = str(var).lower()
    if any(h in low for h in Z_NAME_HINTS):
        return False
    return low.startswith(WEAK_Z_VARS)


def _w_spec_of(path):
    """读 w 变量，返回 (个数, '起始:步长:终点' 或 '非均匀' / '')。

    非均匀序列（比如插值过的 x_as）没法用三个数表达，但**照样能用**——
    这时返回 '非均匀 31 点'，别让用户以为缺频偏。
    """
    try:
        from cest_io import load_first
        v = load_first(str(path), W_VARS)
        if v is None:
            return 0, ''
        import numpy as np
        a = np.asarray(v, dtype=float).ravel()
        a = a[np.isfinite(a)]
        n = a.size
        if n < 2:
            return n, ''
        d = a[1] - a[0]
        ok = np.allclose(np.diff(a), d, atol=abs(d) * 1e-3 + 1e-9)
        if not ok:
            return n, f'非均匀 {n} 点'
        return n, f'{a[0]:g}:{d:g}:{a[-1]:g}'
    except Exception:                                             # noqa: BLE001
        return 0, ''


def scan_data(root):
    """扫数据目录 → 行列表。**每个含 Z 谱的 .mat 单独一行**（同目录多个也不合并）。"""
    root = Path(root)
    # 先按目录收集 S0 / w 的来源（可能是单独的文件）
    dir_s0, dir_w = {}, {}
    zfiles = []
    for f in _iter_mats(root):
        head = mat_head(f)
        if not head:
            continue
        d = str(f.parent)
        s0 = next((n for n, s, _c in head if _is_s0(n, s)), None)
        zs = [n for n, s, _c in head if _is_z_candidate(n, s)]
        ws = next((n for n, s, _c in head if n.lower() in
                   [v.lower() for v in W_VARS]), None)
        if s0:
            dir_s0.setdefault(d, str(f))
        if ws:
            dir_w.setdefault(d, str(f))
        if zs:                       # 一个文件里多个 Z 变量 → 各占一行
            for zv in sorted(zs, key=_z_score, reverse=True):
                zfiles.append((d, str(f), zv, head))

    rows = []
    for d, zpath, zvar, head in sorted(zfiles):
        s0p = dir_s0.get(d)
        # 整合式优先：Z 文件里自带 S0
        if any(n.lower() in [v.lower() for v in S0_VARS] for n, _s, _c in head):
            s0p = zpath
        wp = (zpath if any(n.lower() in [v.lower() for v in W_VARS]
                           for n, _s, _c in head) else dir_w.get(d, ''))
        nw, spec = _w_spec_of(wp) if wp else (0, '')
        shape = '×'.join(str(x) for x in dict((n, s) for n, s, _c in head).get(zvar, ()))
        stat = []
        if not s0p:
            stat.append('缺 S0')
        if not nw:
            stat.append('缺频偏')
        elif nw != (prob_n := _n_freq(shape)):
            stat.append(f'频偏 {nw} ≠ 频点 {prob_n}')
        status = '就绪' if not stat else '；'.join(stat)
        # 同目录多个 Z 谱 → 病例名用「文件主干」区分（GS_interp → GS）
        base = case_name_for(Path(d), root) or Path(d).name
        case = short_case(Path(zpath).stem) if len(
            [1 for x in zfiles if x[0] == d]) > 1 else base
        rows.append(dict(case=case, dir=d, s0=s0p, z=zpath, z_var=zvar, w=wp,
                         w_spec=spec, shape=shape, status=status,
                         ok=bool(s0p and nw), n_more=0))
    return rows


def _z_score(vars_):
    """变量名越像 Z 谱分越高。"""
    best = -1
    for v in vars_:
        low = v.lower()
        if any(h in low for h in Z_NAME_HINTS):
            best = max(best, 3)
        elif any(low.startswith(w) for w in WEAK_Z_VARS):
            best = max(best, 0)          # 兜底：只有它时才用
        elif 'mask' in low:
            continue
        else:
            best = max(best, 2)
    return max(best, 0)


def _n_freq(shape_text):
    try:
        parts = [int(x) for x in str(shape_text).split('×') if x]
        return parts[-1] if parts else 0
    except Exception:                                             # noqa: BLE001
        return 0


# ---------------------------------------------------------------- 掩模模式
def _mask_role(var, shape):
    """→ ('generic'|'organ'|'snr'|'thmask'|'roi'|None, organ_name)。"""
    low = var.lower()
    if low in ('axis_order', 'note'):
        return None, ''
    if 'roi_total' in low:
        return 'roi', 'roi'
    if not shape or len(shape) not in (2, 3, 4):
        return None, ''
    if any(x in low for x in ('v_as', 'v_exp', 's0', 'w_offset', 'x_as')):
        return None, ''
    if 'snr' in low:
        return 'snr', 'snr'
    if low == 'thmask':
        return 'thmask', ''
    if low == 'mask':
        return 'generic', ''
    if 'mask' not in low:
        return None, ''
    organ = next((w for w in ORGAN_WORDS if w in low), '')
    return ('organ', organ) if organ else ('generic', '')


def _nvox(shape):
    n = 1
    for x in shape:
        n *= int(x)
    return n


def scan_masks(root, organ='liver'):
    """扫掩模目录 → 行列表（每个「有掩模变量」的目录一行）。"""
    root = Path(root)
    by_dir = {}
    for f in _iter_mats(root):
        head = mat_head(f)
        if not head:
            continue
        for n, s, c in head:
            role, organ_name = _mask_role(n, s)
            if role is None:
                continue
            if c in ('cell',) and role != 'roi':
                continue
            d = str(f.parent)
            e = by_dir.setdefault(d, {'dir': d, 'items': []})
            e['items'].append(dict(var=n, role=role, organ=organ_name,
                                   file=str(f), shape=s, nvox=_nvox(s)))

    rows = []
    for d, e in sorted(by_dir.items()):
        items = e['items']
        generic = [it for it in items if it['role'] == 'generic']
        organs = [it for it in items if it['role'] == 'organ']
        thmasks = [it for it in items if it['role'] == 'thmask']
        snrs = [it for it in items if it['role'] == 'snr']
        rois = [it for it in items if it['role'] == 'roi']

        # 最终 Mask 优先级：精确 Mask > 器官 mask > Thmask（SNR 阈值，含背景杂散）
        def rank(it):
            return ({'generic': 0, 'organ': 1, 'thmask': 2}.get(it['role'], 3),
                    -it['nvox'])
        pool = sorted(generic + thmasks + organs, key=rank)
        mk = pool[0] if pool else None
        organ_it = organs[0] if organs else None
        thmask_it = thmasks[0] if thmasks else None
        snr_it = snrs[0] if snrs else None
        roi_it = rois[0] if rois else None

        miss = []
        if mk is None:
            miss.append('缺 Mask')
        if roi_it is None:
            miss.append('缺 ROI_total')
        if organ_it is None and mk is not None:
            miss.append('无器官 mask（可用 Mask 顶）')
        shape = '×'.join(str(x) for x in (mk['shape'] if mk else ()))
        status = '齐全' if not [m for m in miss if m.startswith('缺')] else '；'.join(miss)
        rows.append(dict(
            case=case_name_for(Path(d), root) or Path(d).name, dir=d,
            mask_var=(mk or {}).get('var', ''), mask_file=(mk or {}).get('file', ''),
            mask_shape=(mk or {}).get('shape', ()),
            mask_from=('Mask' if mk and mk['role'] == 'generic' else
                       ('Thmask' if mk and mk['role'] == 'thmask' else
                        (f"器官 {organ_it['var']}" if mk and mk['role'] == 'organ' else ''))),
            organ_var=(organ_it or {}).get('var', ''),
            organ_file=(organ_it or {}).get('file', ''),
            snr_var=(snr_it or {}).get('var', ''),
            snr_file=(snr_it or {}).get('file', ''),
            thmask_var=(thmask_it or {}).get('var', ''),
            thmask_file=(thmask_it or {}).get('file', ''),
            roi_file=(roi_it or {}).get('file', ''),
            organ_name=(organ_it or {}).get('organ', '') or organ,
            shape=shape, status=status,
            ok=not [m for m in miss if m.startswith('缺')]))
    return rows
