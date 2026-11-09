# -*- coding: utf-8 -*-
"""
cest_recon.py —— 【第①步】DICOM → 重建 + S0 归一化（Python 版 runCESTrecon.m）
============================================================================

每个病例的流程（与 MATLAB 脚本逐行对应）::

    ① 检查 outroot\\<病例>\\ 下 s0.mat / ssat.mat / w_offset.mat 是否齐全
         · 齐全 → 跳过重建（完全不读 dicom），直接归一化
         · 缺失 → recon_one_case（重建）后再归一化
    ② 归一化：normZ = ssat ./ s0     （s0 第三维会自动广播到所有频偏）

重建部分
    * ``find_cest_dirs``  按前缀识别 S0 / Ssat / B0Ma（忽略文件夹末尾编号）
    * 逐帧读 DICOM（pydicom，JPEG 无损走 pylibjpeg-libjpeg），兼容 MATLAB
      ``dicomread(file,'Frames',1)`` 的语义：多帧文件只取第 1 帧
    * S0 / B0Ma 直接重建；Ssat 按 freq_list 生成频率序列并「去重平均」（3.5 ppm 重复帧合并）

输出（每病例，变量名与 MATLAB 完全一致；**轴序统一为「层优先」**）
    ★ 全工程约定：4D 一律 **[X, Y, 层, 频偏]**（即「先层后频率」的排布），
      每个 .mat 里都写了 ``axis_order = 'layer_freq'``，下游据此自检。
    s0.mat(s0) / b0.mat(b0) / ssat.mat(ssat,axis_order) / w_offset.mat(w_offset,freq_seq)
    / freq.mat(w_offset,freq_seq) / normZ.mat(normZ,axis_order,w_offset,freq_seq)
    以及总目录下的 recon_log.txt

★ 关于「先层后频率 / 先频率后层」
    这是**原始 DICOM 文件序**的循环嵌套问题，由 ``DICOM_ORDER`` 控制：
      'layer_freq' 先层后频率：固定一层，扫完该层所有频偏，再进下一层
      'freq_layer' 先频率后层：固定一个频偏，扫完所有层，再进下一个频偏
      'auto'（默认） 用 DICOM 标签自动判定（见 dicom_order.py）
    无论哪种，本模块**输出的数组一律「层优先」[X,Y,层,频]**，下游不用关心来源差异。
    想先跑一遍判定：``python 验证脚本\check_dicom_order.py``

依赖：pydicom（+ pylibjpeg/pylibjpeg-libjpeg 用于 JPEG 无损压缩）
"""

from __future__ import annotations

import itertools
import math
import re
import time
from pathlib import Path

import numpy as np
from scipy.io import savemat

import pydicom

# ===========================================================================
# 默认参数（与 runCESTrecon.m 一致）
# ===========================================================================
SEQ_BASE = 'cest_tra_fast_10slice_0.7ut'
TAGS = ('S0', 'Ssat', 'B0Ma')
SLICE_MAX = 20          # 620 帧 = 20 层 × 31 帧/层

# --- 轴序（全链路铁律）---
AXIS_ORDER = 'layer_freq'    # 输出数组一律 [X, Y, 层, 频偏]（层优先 = 先层后频率的排布）
DICOM_ORDER = 'auto'         # 原始 DICOM 文件序的嵌套：'auto' 用标签判定；判不出用 AXIS_FALLBACK
AXIS_FALLBACK = 'layer_freq'  # 'auto' 判定失败时的兜底（先层后频率）

FREQ_LIST = [-10, -8, -6, -5, -4, -3.5, -3, -2.1, -1, -0.5,
             0.5, 0.8, 1, 1.2, 1.7, 1.9, 2.1, 2.3, 2.5,
             3, 3.4, 3.5, 3.5, 3.6, 3.7, 4, 4.5, 5, 6, 8, 10]
N_REP = 1               # 每个频偏的重复次数


# ===========================================================================
# 一、文件与目录
# ===========================================================================
def expand_freq(freq_list, n_rep=1):
    """按重复次数把频偏列表展开成完整采集顺序（对应局部函数 expandFreq）。"""
    lst = np.asarray(freq_list, dtype=float).ravel()
    rep = np.asarray(n_rep, dtype=int).ravel()
    if rep.size == 1:
        rep = np.full(lst.size, int(rep[0]))
    if rep.size != lst.size:
        raise ValueError(f'n_rep 与 freq_list 长度不一致（{rep.size} vs {lst.size}）')
    return np.repeat(lst, rep)


def scan_dir(root_dir):
    """递归列出目录下所有文件（对应 scanDir.m，返回完整路径列表，未排序）。"""
    out = []
    for p in Path(root_dir).iterdir():
        if p.is_dir():
            out.extend(scan_dir(p))
        else:
            out.append(str(p))
    return out


def find_cest_dirs(rootpath, seq_base=SEQ_BASE, tags=TAGS):
    """
    按「序列名前缀 + 类型」识别文件夹，忽略末尾编号（对应 findCESTDirs.m）。

    Returns
    -------
    dict: ``{'root':…, 'num':[n, n+1, n+2], 'S0':…, 'Ssat':…, 'B0Ma':…}``
    """
    rootpath = Path(rootpath)
    if not rootpath.is_dir():
        raise FileNotFoundError(f'主路径不存在：{rootpath}')
    if len(tags) != 3:
        raise ValueError('tags 需要正好 3 个类型（默认 S0 / Ssat / B0Ma）')

    pfx = seq_base + '_'
    tag_name, tag_num, tag_path = [], [], []
    for name in sorted(d for d in rootpath.iterdir() if d.is_dir()):
        nm = name.name
        if len(nm) <= len(pfx) or not nm.startswith(pfx):
            continue
        rest = nm[len(pfx):]                       # 例：'S0_1201'
        m = re.fullmatch(r'(?P<tag>.+)_(?P<num>\d+)', rest)
        if not m:
            continue
        tag_name.append(m.group('tag'))
        tag_num.append(int(m.group('num')))
        tag_path.append(str(name))
    tag_num = np.asarray(tag_num, dtype=int)

    cand = []
    for t in tags:
        idx = [i for i, v in enumerate(tag_name) if v == t]
        if not idx:
            raise ValueError(f'主路径下没有找到 {seq_base}_{t}_<编号> 形式的文件夹：\n  {rootpath}')
        cand.append({'num': tag_num[idx], 'path': [tag_path[i] for i in idx]})

    # 找编号依次相连的那一组（n → n+1 → n+2）
    combo = [(a, b, c)
             for a in range(len(cand[0]['num']))
             for b in range(len(cand[1]['num'])) if cand[1]['num'][b] == cand[0]['num'][a] + 1
             for c in range(len(cand[2]['num'])) if cand[2]['num'][c] == cand[1]['num'][b] + 1]
    if not combo:
        print(f'  [提示] 未找到编号严格按 {tags[0]}→{tags[1]}→{tags[2]} 递增的组合，'
              f'改为按「编号连续」匹配')
        combo = [(a, b, c)
                 for a in range(len(cand[0]['num']))
                 for b in range(len(cand[1]['num']))
                 for c in range(len(cand[2]['num']))
                 if np.array_equal(np.diff(np.sort([cand[0]['num'][a], cand[1]['num'][b],
                                                    cand[2]['num'][c]])), [1, 1])]
        if not combo:
            msg = f'主路径：{rootpath}\n各类型找到的候选编号：\n'
            for t, cc in zip(tags, cand):
                msg += f'  {t:<4} : {cc["num"]}\n'
            raise ValueError(msg + '三个序列的编号并不相邻，请确认是否同批导出，或检查 seq_base 是否正确。')

    if len(combo) > 1:
        last = [max(cand[0]['num'][a], cand[1]['num'][b], cand[2]['num'][c]) for a, b, c in combo]
        kmax = int(np.argmax(last))
        print(f'  提示：匹配到 {len(combo)} 组相邻编号，已取编号最大的一组（{last[kmax]}）；'
              f'其余为 {sorted(set(last) - {last[kmax]})}')
        combo = [combo[kmax]]

    a, b, c = combo[0]
    dirs = {'root': str(rootpath),
            'num': [int(cand[0]['num'][a]), int(cand[1]['num'][b]), int(cand[2]['num'][c])]}
    for i, t in enumerate(tags):
        dirs[t] = cand[i]['path'][combo[0][i]]

    print('已按前缀识别（忽略编号）：')
    for t in tags:
        print(f'  {t:<4} : {dirs[t]}')
    print(f'编号：{dirs["num"]}')
    return dirs


# ===========================================================================
# 二、DICOM 读取 → 4D
# ===========================================================================
def read_one_frame(path, apply_rescale=False):
    """
    读一帧像素（对应 MATLAB ``dicomread(file,'Frames',1)``）。

    * 多帧文件只取第 1 帧
    * **默认不应用 RescaleSlope/RescaleIntercept**，与 MATLAB dicomread 在本批数据
      上的实测行为一致（B0Ma 存的是 ±4096 原始值，RescaleSlope=0.0254 被忽略；
      而本流程正是把 ±4096 当「满量程整数 B0 图」用，校正系数另外标定）。
      若确实需要缩放，把 ``apply_rescale=True`` 传进来。
    """
    ds = pydicom.dcmread(str(path), force=True)
    arr = ds.pixel_array
    if arr.ndim == 3:                      # 多帧 → 只要第 1 帧
        arr = arr[0]
    arr = arr.astype(np.float64)

    if apply_rescale:
        slope = float(getattr(ds, 'RescaleSlope', 1) or 1)
        inter = float(getattr(ds, 'RescaleIntercept', 0) or 0)
        if slope != 1.0 or inter != 0.0:
            arr = arr * slope + inter
    return arr


def read_series_4d(p, slice_max=SLICE_MAX, tag='', verbose=True, apply_rescale=False,
                   order=None):
    """
    逐帧读 dicom 并重排为 4D，**输出统一是层优先** ``(Nx, Ny, 层数, 每层帧数)``。

    order
    -----
    'layer_freq' 先层后频率（文件序：层1 的所有频偏 → 层2 的所有频偏 → …）
    'freq_layer' 先频率后层（文件序：频偏1 的所有层 → 频偏2 的所有层 → …）
    'auto' / None  用 DICOM 标签自动判定（读不出标签时用 AXIS_FALLBACK）

    两种输入都会重排成同一个 ``[X, Y, 层, 帧]``，所以下游不必关心采集协议差异。
    """
    files = [f for f in sorted(scan_dir(p))
             if Path(f).suffix.lower() in ('', '.dcm', '.ima', '.dicom')]   # 只收 DICOM
    nf = len(files)
    if nf == 0:
        raise ValueError(f'{tag}：文件夹里没有 DICOM 文件')
    if nf % slice_max != 0:
        raise ValueError(f'{tag}：总帧数 {nf} 不能被层数 {slice_max} 整除')

    # ---- 先确定采集嵌套顺序 ----
    od = order if order is not None else DICOM_ORDER
    if od in ('auto', None):
        try:
            from dicom_order import detect_nesting, read_series_meta
            _r = detect_nesting(read_series_meta(p), slice_max=slice_max)
            od = _r['order'] if _r['order'] in ('layer_freq', 'freq_layer') else AXIS_FALLBACK
            if verbose:
                print(f'  {tag:<4} | DICOM 采集顺序自动判定：{_r["order"]}'
                      f'（按标签；帧 {_r["n_frames"]} / 层 {_r["n_slices"]}）')
        except Exception as e:                                   # noqa: BLE001
            od = AXIS_FALLBACK
            if verbose:
                print(f'  {tag:<4} | 自动判定失败（{e}），回退为 {od}')
    if od not in ('layer_freq', 'freq_layer'):
        raise ValueError(f"order 只能是 'auto'/'layer_freq'/'freq_layer'，收到 {od!r}")

    img = None
    for j, f in enumerate(files):
        try:
            A = read_one_frame(f, apply_rescale=apply_rescale)
        except Exception as e:                                   # noqa: BLE001
            raise RuntimeError(f'{tag}：第 {j + 1} 个文件读取失败 {f}：{e}') from e
        if img is None:
            img = np.zeros((A.shape[0], A.shape[1], nf))
        if A.shape != img.shape[:2]:
            raise ValueError(f'{tag}：第 {j + 1} 帧尺寸 {A.shape} 与首帧 {img.shape[:2]} 不一致')
        img[:, :, j] = A

    # ★ 列优先 reshape（order='F'），把「文件序」还原成 4D
    n_fr = nf // slice_max                      # 每层帧数（Ssat=31，S0/B0Ma=1）
    if od == 'layer_freq':
        # 文件序：层外层、频率内层 → reshape 得 [X,Y,帧,层]，再换成层优先
        img4d = img.reshape(img.shape[0], img.shape[1], n_fr, slice_max, order='F')
        img4d = np.swapaxes(img4d, 2, 3)
    else:                                        # freq_layer：层内层 → 直接就是层优先
        img4d = img.reshape(img.shape[0], img.shape[1], slice_max, n_fr, order='F')
    if verbose:
        print(f'  {tag:<4} | {nf} 帧 | 采集序 {od} | 重排后 [X,Y,层,帧] = {img4d.shape}')
    return img4d


def _stable_unique(a):
    """MATLAB ``unique(a,'stable')`` 的等价实现：按首次出现顺序返回值和分组号。"""
    pos, vals, idx = {}, [], np.empty(len(a), dtype=int)
    for i, v in enumerate(np.asarray(a).ravel()):
        if v not in pos:
            pos[v] = len(vals)
            vals.append(v)
        idx[i] = pos[v]
    return np.asarray(vals), idx


DUP_CORR = 0.995        # 判定「同一频偏重复采集」的相关系数阈值（极高，避免误判）
MAX_COMBO = 20000       # 「哪些帧是额外帧」穷举上限；超过则退化为只试连续块
ASYM_WARN = 0.10        # 谱线 ±k 不对称度告警线（10%）


def _frame_scalar(img4d):
    """每帧一个标量（全层全像素均值）——供「谱线是否合理」快速判据用。"""
    return np.asarray(img4d, dtype=np.float64).mean(axis=(0, 1, 2))


def _mirror_asym(v_by_off):
    """±k 镜像对的不对称度（越小越合理）；远共振对权重更高。

    真实的 z 谱在远共振处（|w| 大）必须近似对称，近水处本来就有真实不对称，
    所以按 |w| 加权 → 帧序/频偏若配错，这个数会从 ~2% 跳到 ~25%。
    """
    num = den = 0.0
    for k, v in v_by_off.items():
        if k <= 0 or (-k) not in v_by_off:
            continue
        a, b = v_by_off[-k], v
        e = abs(a - b) / (0.5 * (abs(a) + abs(b)) + 1e-9)
        w = float(k)
        num += w * e
        den += w
    return (num / den) if den > 0 else float('inf')


def _corr_flat(a, b):
    x = np.asarray(a, dtype=np.float64).ravel()
    y = np.asarray(b, dtype=np.float64).ravel()
    x = x - x.mean()
    y = y - y.mean()
    n = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / n) if n > 1e-12 else 0.0


def resolve_frame_offsets(img4d, w_offset, dup_corr=DUP_CORR, max_combo=MAX_COMBO):
    """决定「每一帧属于哪个频偏」——多出来的帧按**谱线物理合理性**自动判定并丢弃。

    规则（不猜，用数据说话）：
      · 帧数 == 频偏数  → 一一对应；
      · 帧数  > 频偏数  → 穷举「丢掉哪几帧」，取 ±k 镜像最对称（最像真实 z 谱）的那种，
        被丢掉的 = 额外帧；若某个被丢帧与相邻帧几乎完全相同（corr ≥ dup_corr），
        则判定为**同一频偏的重复采集**，并入平均而不是丢弃。

    返回 (assign, info)；assign 长度 = 帧数，>=0 是频偏序号，-1 是额外帧（丢弃）。
    """
    img4d = np.asarray(img4d, dtype=np.float64)
    N = int(img4d.shape[3])
    offs = [float(x) for x in np.asarray(w_offset, dtype=float).ravel()]
    M = len(offs)
    if M > N:
        raise ValueError(f'频偏 {M} 个 > 每层帧数 {N} 帧（频偏数不能多于帧数）')
    if M < 2:
        raise ValueError(f'频偏只有 {M} 个（至少 2 个）')
    prof = _frame_scalar(img4d)
    if M == N:
        return (list(range(N)),
                dict(dropped=[], repeats=[],
                     score=_mirror_asym(dict(zip(offs, prof))), n_frame=N, n_offset=M))

    d = N - M
    try:
        n_comb = math.comb(N, d)
    except AttributeError:                                     # 兼容老 Python
        n_comb = math.factorial(N) // (math.factorial(d) * math.factorial(N - d))
    if n_comb <= max_combo:
        cand = itertools.combinations(range(N), d)
    else:                                                      # 退化：只试连续块
        cand = (tuple(range(st, st + d)) for st in range(0, N - d + 1))
    best_score, best_drop = float('inf'), None
    for drop in cand:
        dset = set(drop)
        keep = [i for i in range(N) if i not in dset]
        s = _mirror_asym({o: prof[i] for o, i in zip(offs, keep)})
        if s < best_score:
            best_score, best_drop = s, tuple(drop)
    keep = [i for i in range(N) if i not in set(best_drop)]
    assign = [-1] * N
    for k, i in enumerate(keep):
        assign[i] = k
    repeats = []
    for f in best_drop:                                        # 重复采集识别（并入平均）
        if assign[f] >= 0:
            continue
        for nb in (f - 1, f + 1):
            if 0 <= nb < N and assign[nb] >= 0:
                if _corr_flat(img4d[:, :, :, f], img4d[:, :, :, nb]) >= dup_corr:
                    assign[f] = assign[nb]
                    repeats.append((f, nb))
                    break
    dropped = [i for i in range(N) if assign[i] < 0]
    return assign, dict(dropped=dropped, repeats=repeats, score=float(best_score),
                        n_frame=N, n_offset=M)


def make_ssat(img4d, fseq, slice_max=SLICE_MAX, dup_corr=DUP_CORR, verbose=True):
    """按频率去重 + 自动剔除「额外帧」（对应 makeSsat，但**不再凭空补重复频偏**）。

    fseq 可以是「采集顺序表（含重复）」或「去重后的频偏表」——这里只取它的**唯一频偏**
    （按首次出现顺序）。每层帧数 > 频偏数时，多出来的帧用 ±k 镜像对称判据自动识别为
    额外帧并**丢弃**（旧实现会按 .prot 猜「重复的 3.5」，位置可能猜错 → LD 基线被拖坏）。

    img4d 为层优先 ``[X, Y, 层, 帧]``，输出 ``ssat = [X, Y, 层, 唯一频偏]``（层优先）。
    """
    if fseq is None:
        raise ValueError('缺少频率表（4D 序列必须有频率才能重建）')
    img4d = np.asarray(img4d, dtype=np.float64)
    w_offset, _ = _stable_unique(fseq)
    w_offset = np.asarray(w_offset, dtype=float)
    assign, info = resolve_frame_offsets(img4d, w_offset, dup_corr=dup_corr)
    M = int(w_offset.size)
    ssat = np.zeros((img4d.shape[0], img4d.shape[1], img4d.shape[2], M), dtype=np.float64)
    for k in range(M):
        sel = [i for i, a in enumerate(assign) if a == k]
        if sel:
            ssat[:, :, :, k] = img4d[:, :, :, sel].mean(axis=3)
        else:
            ssat[:, :, :, k] = np.nan
    if verbose:
        msg = f'每层 {info["n_frame"]} 帧 / 频偏 {info["n_offset"]} 个'
        if info['dropped']:
            msg += '；判定第 ' + '、'.join(str(i + 1) for i in info['dropped']) + ' 帧为额外帧 → 丢弃'
        if info['repeats']:
            msg += '；第 ' + '、'.join(str(f + 1) for f, _ in info['repeats']) + ' 帧判定为重复采集 → 已并入平均'
        msg += f'（谱线 ±k 不对称度 {info["score"] * 100:.2f}%）'
        print('      Ssat | ' + msg)
        if info['score'] > ASYM_WARN:
            print(f'      Ssat | ⚠ 不对称度偏高（>{ASYM_WARN * 100:.0f}%）：频偏表或帧序可能仍不对，请核对')
    return ssat, w_offset.reshape(1, -1)


def layer_check(img4d, tag):
    """比较第 k 层与第 k+10 层（对应 layerCheck）。"""
    A = np.squeeze(img4d)
    if A.ndim < 3 or A.shape[2] < 20:
        return
    cc = [float(np.corrcoef(A[:, :, k].ravel(), A[:, :, k + 10].ravel())[0, 1]) for k in range(10)]
    print(f'       {tag} 前后 10 层相关性: min {min(cc):.3f} / median {np.median(cc):.3f}')


# ===========================================================================
# 三、单病例：重建 + 归一化
# ===========================================================================
def _save(path, **kw):
    savemat(str(path), kw)


def recon_one_case(rootdata, case_name, outdir, seq_base=SEQ_BASE, tags=TAGS,
                   slice_max=SLICE_MAX, freq_seq=None, do_layer_check=True, order=None):
    """单个病例的重建（对应 reconOneCase），返回各序列尺寸字符串。

    order = 原始 DICOM 文件序的嵌套：'auto'（默认，用标签判定）/ 'layer_freq' / 'freq_layer'。
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    R = {'S0': 'FAIL', 'B0': 'FAIL', 'Ssat': 'FAIL'}

    dirs = find_cest_dirs(Path(rootdata) / case_name, seq_base, tags)

    # ---- S0 ----
    try:
        s0 = read_series_4d(dirs['S0'], slice_max, 'S0', order=order)
        _save(outdir / 's0.mat', s0=s0)
        if do_layer_check:
            layer_check(s0, 'S0')
        R['S0'] = str(s0.shape)
    except Exception as e:                                       # noqa: BLE001
        print(f'  S0   | 读取失败：{e}')

    # ---- B0Ma ----
    try:
        b0 = read_series_4d(dirs['B0Ma'], slice_max, 'B0Ma', order=order)
        _save(outdir / 'b0.mat', b0=b0)
        R['B0'] = str(b0.shape)
    except Exception as e:                                       # noqa: BLE001
        print(f'  B0Ma | 读取失败：{e}')

    # ---- Ssat ----
    try:
        ssat4d = read_series_4d(dirs['Ssat'], slice_max, 'Ssat', order=order)
        ssat, w_offset = make_ssat(ssat4d, freq_seq, slice_max)
        _save(outdir / 'ssat.mat', zdata=ssat, ssat=ssat,       # zdata=统一名, ssat=旧名兼容
              axis_order=AXIS_ORDER)                            # ★ 层优先标记
        _save(outdir / 'w_offset.mat', w_offset=w_offset, freq_seq=w_offset)
        _save(outdir / 'freq.mat', w_offset=w_offset, freq_seq=w_offset)
        R['Ssat'] = str(ssat.shape)
    except Exception as e:                                       # noqa: BLE001
        print(f'  Ssat | {e}')

    return R


def recon_philips_cest(cfg):
    """
    通用单序列 CEST 重建（用于非 UIH 三序列导出的数据，如赵磊 / 040patientPET 的 Philips 导出）。

    与 `recon_one_case` 的区别：目录里只有**一个** CEST 序列（不区分 S0/Ssat/B0Ma），
    S0（M0）是同一序列里的几个特定时相（TPI）。

    cfg
    ---
    dicom_dir   含该序列全部 *.dcm 的目录
    out_dir     输出目录（默认 = dicom_dir）
    n_layer     层数（赵磊 0.7uT：10）
    m0_tpi      M0 帧的时相号（1 基），如 [1, 2, 33]；None = 从 freq_seq_file 里的哨兵值反推
    freq_list   频偏 ppm 列表（长度须等于「每层帧数 - M0 帧数」）；None = 从 freq_seq_file 取
    freq_seq_file  含 ``freq_sequence`` 的 .mat（赵磊：APT_0.7uT_ref\\freq_sequence.mat），
                里面的哨兵值（如 -1560）表示 M0
    order       原始文件序嵌套：'auto'（默认）/ 'layer_freq' / 'freq_layer'
    do_normalize  是否顺带写 normZ.mat（默认 True）

    输出（层优先，与 UIH 路线完全一致）：
        s0.mat(s0 (X,Y,层)) / ssat.mat(zdata,ssat,axis_order (X,Y,层,频))
        w_offset.mat(w_offset,freq_seq) / freq.mat / normZ.mat(norm_z,axis_order)
    """
    dicom_dir = Path(cfg['dicom_dir'])
    out_dir = Path(cfg.get('out_dir') or dicom_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n_layer = int(cfg.get('n_layer', SLICE_MAX))
    order = cfg.get('order', DICOM_ORDER)

    img4d = read_series_4d(dicom_dir, n_layer, 'CEST', order=order)     # [X,Y,层,帧]
    n_fr = img4d.shape[3]
    print(f'  重排后每层 {n_fr} 帧')

    # ---- 频偏表 / M0 帧 ----
    freq_list = cfg.get('freq_list')
    m0_tpi = cfg.get('m0_tpi')
    seq = None
    fsq = cfg.get('freq_seq_file')
    if fsq and Path(fsq).exists():
        from cest_io import load_first
        seq = np.asarray(load_first(Path(fsq), ('freq_sequence', 'freq_seq', 'w')),
                         float).ravel()
        print(f'  freq_sequence（{seq.size} 个，含哨兵）：{np.round(seq, 1).tolist()}')
        if m0_tpi is None and seq.size == n_fr:
            m0_idx = [i for i, v in enumerate(seq) if abs(v) > 100]
            print(f'  按哨兵值（|f|>100）定出 M0 帧：{m0_idx}')
        else:
            m0_idx = None
    else:
        m0_idx = None

    if m0_idx is None:
        if m0_tpi is None:
            m0_tpi = [1]
        m0_idx = [int(v) - 1 for v in m0_tpi]
    freq_idx = [i for i in range(n_fr) if i not in m0_idx]
    print(f'  M0 帧（0 基）：{m0_idx} → S0 取均值；频偏帧数 {len(freq_idx)}')

    if freq_list is None:
        if seq is None:
            raise ValueError('需要 freq_list 或 freq_seq_file 之一')
        real = [v for v in seq if abs(v) <= 100]
        if len(real) != len(freq_idx):
            raise ValueError(f'freq_sequence 里真实频偏 {len(real)} 个 ≠ 频偏帧数 {len(freq_idx)}，'
                             f'请显式给 freq_list')
        w = np.asarray(real, dtype=float)
    else:
        w = np.asarray(freq_list, dtype=float).ravel()
        if w.size != len(freq_idx):
            raise ValueError(f'freq_list {w.size} 个 ≠ 频偏帧数 {len(freq_idx)}')

    s0 = img4d[:, :, :, m0_idx].mean(axis=3)                  # (X,Y,层)
    ssat = img4d[:, :, :, freq_idx]                           # (X,Y,层,频)
    s0 = np.asarray(s0, dtype=np.float64)
    print(f'  s0 {s0.shape}   ssat {ssat.shape}   w {w.size} 个：'
          f'{np.round(w, 1).tolist()}')

    _save(out_dir / 's0.mat', s0=s0)
    _save(out_dir / 'ssat.mat', zdata=ssat, ssat=ssat, axis_order=AXIS_ORDER)
    _save(out_dir / 'w_offset.mat', w_offset=w.reshape(1, -1), freq_seq=w.reshape(1, -1))
    _save(out_dir / 'freq.mat', w_offset=w.reshape(1, -1), freq_seq=w.reshape(1, -1))
    print(f'  已保存：{out_dir / "s0.mat"} / ssat.mat / w_offset.mat')

    if cfg.get('do_normalize', True):
        norm_one_case(out_dir, cfg.get('nan_report', True))
    return dict(s0=s0.shape, ssat=ssat.shape, n_freq=w.size)


def norm_one_case(outdir, nan_report=True):
    """用 s0 对 ssat 逐层归一化：normZ = ssat ./ s0（对应 normOneCase）。"""
    outdir = Path(outdir)
    try:
        f_s0, f_ss = outdir / 's0.mat', outdir / 'ssat.mat'
        if not f_s0.exists():
            raise FileNotFoundError('缺少 s0.mat')
        if not f_ss.exists():
            raise FileNotFoundError('缺少 ssat.mat')
        from cest_io import load_mat
        s0 = np.asarray(load_mat(f_s0, ['s0', 'S0']).get('s0'), dtype=float)
        _g = load_mat(f_ss, ['zdata', 'ssat'])
        ssat = np.asarray(_g.get('zdata', _g.get('ssat')), dtype=float)

        # w_offset 先读，用它区分 ssat 是 [X,Y,层,频] 还是旧序 [X,Y,频,层]
        w_offset, freq_seq = None, None
        fw = outdir / 'w_offset.mat'
        if fw.exists():
            W = load_mat(fw, ['w_offset', 'freq_seq'])
            w_offset, freq_seq = W.get('w_offset'), W.get('freq_seq')
        n_freq = int(np.size(w_offset)) if w_offset is not None else None

        # ---- 统一轴序：s0 → [X,Y,层,1]；ssat → [X,Y,层,频] ----
        if s0.ndim == 3:                                  # (X,Y,层)
            s0 = s0[:, :, :, None]
        elif s0.ndim == 4 and s0.shape[2] == 1 and s0.shape[3] > 1:
            s0 = np.swapaxes(s0, 2, 3)                     # 旧序 (X,Y,1,层)
        elif s0.ndim == 4 and s0.shape[2] > 1 and s0.shape[3] > 1:
            s0 = s0.mean(axis=3, keepdims=True)            # 每层存了多帧则先平均
        n_layer = s0.shape[2]

        if ssat.ndim == 3:
            ssat = ssat[:, :, :, None]
        if n_freq and ssat.shape[3] != n_freq and ssat.shape[2] == n_freq:
            ssat = np.swapaxes(ssat, 2, 3)                 # 旧序 [X,Y,频,层] → [X,Y,层,频]

        if s0.shape[0] != ssat.shape[0] or s0.shape[1] != ssat.shape[1] \
                or ssat.shape[2] != n_layer:
            raise ValueError(f'轴序统一后维度不匹配：s0 {s0.shape} vs ssat {ssat.shape}')

        den = np.array(s0, dtype=float)[:, :, :, 0].copy()
        bad = ~np.isfinite(den) | (den <= 0)
        den[bad] = np.nan
        normZ = ssat / den[:, :, :, None]                  # 广播到每个频偏

        _save(outdir / 'normZ.mat', norm_z=normZ, normZ=normZ,
              axis_order=AXIS_ORDER,
              w_offset=w_offset, freq_seq=freq_seq)
        print(f'       Norm | s0 → normZ.mat  {normZ.shape}')
        if nan_report and int(bad.sum()) > 0:
            print(f'       Norm | 注意：{int(bad.sum())} 个体素 s0<=0 或非有限值，已置 NaN')
        return str(normZ.shape)
    except Exception as e:                                       # noqa: BLE001
        print(f'       Norm | 失败：{e}')
        return 'FAIL'


def _mat_size_str(f, var_name):
    """不加载数据只报尺寸（对应 matSizeStr）。"""
    f = Path(f)
    if not f.exists():
        return 'missing'
    try:
        from scipy.io import whosmat
        w = whosmat(str(f))
        for name, shape, _cls in w:
            if name == var_name:
                return str(list(shape))
    except Exception:                                            # noqa: BLE001
        pass
    return 'no-var'


def run_recon(cfg):
    """
    第①步总入口（对应 runCESTrecon.m 主体）。

    cfg
    ---
    root_data   数据主目录，例 ``C:\\...\\2026\\09\\20260909``
    out_root    总结果目录，例 ``C:\\...\\2026\\09\\CEST_result``
    sub_list    要处理的病例，默认 c1..hp3
    seq_base / tags / slice_max / freq_list / n_rep / do_normalize / nan_report
    dicom_order 原始 DICOM 文件序的嵌套：'auto'（默认）/ 'layer_freq' / 'freq_layer'
    clear_out_root  是否先清空 out_root（默认 False）

    Returns
    -------
    status : list[str]，也写进 ``<out_root>\\recon_log.txt``
    """
    root_data = cfg['root_data']
    out_root = Path(cfg['out_root'])
    sub_list = cfg.get('sub_list', ['c1', 'c2', 'c3', 'h1', 'h2', 'h3', 'hp1', 'hp2', 'hp3'])
    seq_base = cfg.get('seq_base', SEQ_BASE)
    tags = tuple(cfg.get('tags', TAGS))
    slice_max = int(cfg.get('slice_max', SLICE_MAX))
    freq_list = cfg.get('freq_list', FREQ_LIST)
    n_rep = cfg.get('n_rep', N_REP)
    do_normalize = cfg.get('do_normalize', True)
    nan_report = cfg.get('nan_report', True)

    if cfg.get('clear_out_root', False):
        import shutil
        if out_root.exists():
            shutil.rmtree(out_root)
            print(f'已清空旧结果目录：{out_root}')
    out_root.mkdir(parents=True, exist_ok=True)

    freq_seq = expand_freq(freq_list, n_rep)
    print(f'频率序列：{freq_seq.size} 帧/层，唯一频偏 {len(np.unique(freq_seq))} 个')
    dicom_order = cfg.get('dicom_order', DICOM_ORDER)
    print(f'轴序：原始 DICOM 文件序 = {dicom_order}（auto = 用标签自动判定）；'
          f'输出数组一律 [X,Y,层,频]（{AXIS_ORDER}）')

    status = []
    for i, case_name in enumerate(sub_list):
        print(f'\n===== [{i + 1}/{len(sub_list)}] {case_name} =====')
        t0 = time.time()
        outdir = out_root / case_name
        r_s0 = r_b0 = r_ss = r_n = 'OFF'
        try:
            outdir.mkdir(parents=True, exist_ok=True)
            need = ['s0.mat', 'ssat.mat', 'w_offset.mat']
            miss = [n for n in need if not (outdir / n).exists()]
            if not miss:
                print('  发现已有重建结果，跳过重建，直接归一化')
                r_s0 = _mat_size_str(outdir / 's0.mat', 's0')
                r_b0 = _mat_size_str(outdir / 'b0.mat', 'b0')
                r_ss = _mat_size_str(outdir / 'ssat.mat', 'ssat')
                if not (outdir / 'b0.mat').exists():
                    print('  提示：b0.mat 不存在（不影响归一化；如需 B0 校正请单独重建该病例）')
            else:
                print(f'  缺少 {", ".join(miss)}，调用重建流程')
                R = recon_one_case(root_data, case_name, outdir, seq_base, tags,
                                   slice_max, freq_seq, cfg.get('do_layer_check', True),
                                   cfg.get('dicom_order', DICOM_ORDER))
                r_s0, r_b0, r_ss = R['S0'], R['B0'], R['Ssat']

            if do_normalize:
                r_n = norm_one_case(outdir, nan_report)

            msg = (f'{case_name:<4} | S0 {r_s0:<16} | B0 {r_b0:<16} | '
                   f'Ssat {r_ss:<18} | Norm {r_n:<18} | {time.time() - t0:.1f} s')
            status.append(msg)
        except Exception as e:                                   # noqa: BLE001
            status.append(f'{case_name:<4} | 主流程失败：{e}')
        print(status[-1])

    print('\n========== 汇总 ==========')
    for s in status:
        print(s)
    log = out_root / 'recon_log.txt'
    log.write_text('\n'.join(status) + '\n', encoding='utf-8')
    print(f'结果目录：{out_root}\n日志：{log}')
    return status

