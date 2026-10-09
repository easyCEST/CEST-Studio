# -*- coding: utf-8 -*-
"""
dicom_order.py —— 判定多层 CEST 原始 DICOM 的采集嵌套顺序（先层后频率 / 先频率后层）
==========================================================================

背景
----
一个病例的 Ssat 序列是 620 帧 = 20 层 × 31 个频偏。要把它重排成 4D，就必须知道
采集时两个循环谁在外、谁在内：

  · **先层后频率**（layer_freq）：固定一层，把该层所有频偏采完，再进下一层
        文件序 = [层1 的 31 个频偏, 层2 的 31 个频偏, ...]
        → ``reshape(nx, ny, 31, 20, order='F')`` 得到 ``[X, Y, 频偏, 层]``

  · **先频率后层**（freq_layer）：固定一个频偏，把所有层采一遍，再进下一个频偏
        文件序 = [频偏1 的 20 层, 频偏2 的 20 层, ...]
        → ``reshape(nx, ny, 20, 31, order='F')`` 得到 ``[X, Y, 层, 频偏]``

判据（**全部来自 DICOM 标签，不依赖像素**）
-------------------------------------------
  (0020,0013) InstanceNumber                 真实采集顺序
  (0020,0100) TemporalPositionIdentifier     时间点（CEST 频偏步）
  (0020,1041) SliceLocation / (0020,0032) ImagePositionPatient   层位置

做法：按 InstanceNumber 升序排帧（**不是按文件名**），再看「层位置」序列的周期：

  · 层位置每 Z 帧重复一次（Z = 层数）        → 层是**内层**循环 → ``freq_layer``
  · 整个序列只有 Z 个连续相同的 run（长度 F）→ 频率是**内层**循环 → ``layer_freq``

顺带检查一个常见陷阱：**文件名排序是否等于 InstanceNumber 顺序**——
很多管线用 ``sorted(glob('*.dcm'))`` 排序，一旦两者不一致，重排就会错位。

用法
----
    from dicom_order import report, detect_nesting, read_series_meta
    print(report(r'...\\cest_tra_fast_10slice_0.7ut_Ssat_1202', slice_max=20))

命令行： ``python check_dicom_order.py``（见 验证脚本\\check_dicom_order.py）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# 标签候选（有的机器用 SliceLocation，有的只有 ImagePositionPatient）
# ---------------------------------------------------------------------------
TAG_INSTANCE = 'InstanceNumber'
TAG_TPI = 'TemporalPositionIdentifier'
TAG_NTP = 'NumberOfTemporalPositions'


def _z_of(ds):
    """取层位置：优先 SliceLocation，退化到 ImagePositionPatient 的 Z 分量。"""
    v = getattr(ds, 'SliceLocation', None)
    if v is not None:
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            pass
    ipp = getattr(ds, 'ImagePositionPatient', None)
    if ipp is not None and len(ipp) >= 3:
        return round(float(ipp[2]), 4)
    return float('nan')


def read_series_meta(dicom_dir, verbose=False):
    """
    读一个 DICOM 序列目录里每帧的关键标签（不读像素）。

    Returns
    -------
    list[dict]，按**文件名**顺序，每项含：
        fname / inst(采集序) / tpi(时间点) / z(层位置) / time(采集时刻)
    """
    import pydicom

    d = Path(dicom_dir)
    if not d.is_dir():
        raise FileNotFoundError(f'目录不存在：{d}')
    files = sorted([p for p in d.iterdir() if p.is_file()])
    if not files:
        raise ValueError(f'目录为空：{d}')

    out = []
    for f in files:
        ds = pydicom.dcmread(str(f), stop_before_pixels=True, force=True)
        out.append(dict(
            fname=f.name,
            inst=int(getattr(ds, TAG_INSTANCE, -1) or -1),
            tpi=int(getattr(ds, TAG_TPI, -1) or -1),
            ntp=int(getattr(ds, TAG_NTP, -1) or -1),
            z=_z_of(ds),
            time=str(getattr(ds, 'AcquisitionTime', '') or ''),
        ))
    if verbose:
        print(f'  [dicom_order] {d} 共 {len(out)} 帧')
    return out


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _min_period(seq):
    """序列的最小循环周期（严格周期，非近似）。找不到返回 len(seq)。"""
    n = len(seq)
    for p in range(1, n):
        if n % p:
            continue
        if all(seq[i] == seq[i % p] for i in range(n)):
            return p
    return n


def _runs(seq):
    """连续相同值的 run：(值, 长度) 列表。"""
    out, cur, k = [], seq[0], 1
    for v in seq[1:]:
        if v == cur:
            k += 1
        else:
            out.append((cur, k)); cur, k = v, 1
    out.append((cur, k))
    return out


def detect_nesting(meta, slice_max=None):
    """
    判定采集嵌套顺序。

    Returns
    -------
    dict:
        order      : 'layer_freq' | 'freq_layer' | 'unknown'
        n_frames / n_slices / n_tpi / frames_per_slice
        filename_sorted_ok : 文件名序 == InstanceNumber 序 ?
        evidence   : list[str]，可直接打印
    """
    meta = list(meta)
    n = len(meta)
    ev = []

    # ---- 1) 真实采集顺序：按 InstanceNumber 升序 ----
    inst = [m['inst'] for m in meta]
    by_name_ok = inst == sorted(inst)
    if not by_name_ok:
        ev.append('⚠ 文件名顺序 ≠ InstanceNumber 顺序：管线若按文件名排序会打乱采集序，'
                  '必须按 InstanceNumber 重排！')
    order = sorted(range(n), key=lambda i: meta[i]['inst'])
    m = [meta[i] for i in order]

    z = [x['z'] for x in m]
    tpi = [x['tpi'] for x in m]
    n_slices = len(dict.fromkeys(z))
    n_tpi = len(dict.fromkeys(tpi)) if tpi[0] >= 0 else 0
    ev.append(f'帧数 {n}，层数(唯一层位置) {n_slices}，'
              f'时间点数(唯一 TPI) {n_tpi if n_tpi else "无 TPI 标签"}')

    if slice_max and n_slices != slice_max:
        ev.append(f'⚠ 唯一层位置 {n_slices} ≠ 预期层数 slice_max={slice_max}，请核对')

    # ---- 2) 用「层位置」序列的周期判定 ----
    per = _min_period(z)
    runs = _runs(z)
    run_lens = sorted(set(k for _, k in runs))
    F = n // n_slices if n_slices else 0

    if per == n_slices and n_slices > 1:
        res = 'freq_layer'
        ev.append(f'层位置序列的最小周期 = {per} = 层数 → 每 {per} 帧层位置就轮回一遍，'
                  f'说明**层是内层循环** → 采集是【先频率后层】(freq_layer)')
    elif len(runs) == n_slices and len(run_lens) == 1 and run_lens[0] > 1:
        res = 'layer_freq'
        ev.append(f'层位置序列只有 {len(runs)} 个连续 run（每个长度 {run_lens[0]}）'
                  f'→ 说明**频率是内层循环** → 采集是【先层后频率】(layer_freq)')
    elif per == n:
        res = 'layer_freq'
        ev.append(f'层位置序列的最小周期 = {per} = 总帧数 → 层在整段内不变直到走完一轮，'
                  f'倾向【先层后频率】(layer_freq)')
    else:
        res = 'unknown'
        ev.append(f'层位置序列周期 {per}、run 数 {len(runs)}、run 长度 {run_lens}，无法唯一判定')
    res_from_slice = None if res == 'unknown' else res

    # ---- 3) 用 TPI 交叉验证 ----
    #    TPI = 时间点（CEST 频偏步）。
    #      · TPI 只有少数几个很长的 run（长度 = 层数）→ TPI 变化很慢 = 外层循环
    #        → 频率是外层 → freq_layer
    #      · TPI 每 n_tpi 帧就轮回一遍（周期 = 时间点数）→ TPI 是内层循环
    #        → 频率是内层 → layer_freq
    if n_tpi > 1:
        t_per = _min_period(tpi)
        t_runs = _runs(tpi)
        if len(t_runs) == n_tpi and len(set(k for _, k in t_runs)) == 1:
            ev.append(f'TPI 只有 {len(t_runs)} 个 run（每个长度 {t_runs[0][1]}，= 层数）'
                      f'→ TPI 变化很慢 = 外层循环 → 再次确认【先频率后层】(freq_layer)')
            res = 'freq_layer'
        elif t_per == n_tpi:
            ev.append(f'TPI 每 {t_per} 帧轮回一遍（周期 = 时间点数）→ TPI 是内层循环 '
                      f'→ 再次确认【先层后频率】(layer_freq)')
            res = 'layer_freq'
        else:
            ev.append(f'TPI 周期 {t_per}、run 数 {len(t_runs)}，与层位置结论 {res} 交叉核对')

        if res_from_slice is not None and res_from_slice != res:
            ev.append(f'⚠ 两条判据（层位置 → {res_from_slice}；TPI → {res}）不一致，'
                      f'请人工确认该序列的采集协议！')

    return dict(order=res, n_frames=n, n_slices=n_slices, n_tpi=n_tpi,
                frames_per_slice=F, filename_sorted_ok=by_name_ok,
                min_period=per, evidence=ev, meta_in_acq_order=m)


def report(dicom_dir, slice_max=None, show_frames=24):
    """生成一份可直接打印/落盘的文字报告。"""
    meta = read_series_meta(dicom_dir)
    r = detect_nesting(meta, slice_max=slice_max)

    zh = {'layer_freq': '先层后频率（固定一层，扫完该层所有频偏，再进下一层）',
          'freq_layer': '先频率后层（固定一个频偏，扫完所有层，再进下一个频偏）',
          'unknown': '无法判定'}
    lines = []
    lines.append('=' * 78)
    lines.append(f'DICOM 采集顺序分析：{dicom_dir}')
    lines.append('=' * 78)
    lines.append(f'结论：{zh[r["order"]]}')
    lines.append(f'      代码取值 order = {r["order"]!r}'
                 f'（喂给 cest_recon.read_series_4d(order=...) / DICOM_ORDER）')
    lines.append(f'帧数 {r["n_frames"]}；层数 {r["n_slices"]}；'
                 f'每层帧数 {r["frames_per_slice"]}；时间点 {r["n_tpi"] or "无"}')
    lines.append('')
    lines.append('判据：')
    for e in r['evidence']:
        lines.append(f'  · {e}')
    lines.append('')
    lines.append(f'--- 按 InstanceNumber（真实采集序）前 {show_frames} 帧 ---')
    lines.append(f'{"#":>4} {"file":>14} {"inst":>6} {"TPI":>5} {"z":>10}')
    for i, x in enumerate(r['meta_in_acq_order'][:show_frames]):
        lines.append(f'{i + 1:>4} {x["fname"]:>14} {x["inst"]:>6} {x["tpi"]:>5} {x["z"]:>10}')
    lines.append('')
    lines.append('— 据此，重排 4D 应当用：')
    if r['order'] == 'freq_layer':
        lines.append(f'   img4d = img.reshape(nx, ny, {r["n_slices"]}, '
                     f'{r["frames_per_slice"]}, order="F")   # → [X,Y,层,频偏]')
    elif r['order'] == 'layer_freq':
        lines.append(f'   img4d = img.reshape(nx, ny, {r["frames_per_slice"]}, '
                     f'{r["n_slices"]}, order="F")'
                     f'.swapaxes(2,3)          # → [X,Y,层,频偏]')
    lines.append('   （本工程统一输出 [X, Y, 层, 频偏]，即「层优先」）')
    return '\n'.join(lines), r


def check_order(dicom_dir, slice_max=None, save_txt=None, verbose=True):
    """打印报告（可落盘），返回 detect_nesting 的结果 dict。"""
    txt, r = report(dicom_dir, slice_max=slice_max)
    if verbose:
        print(txt)
    if save_txt:
        Path(save_txt).parent.mkdir(parents=True, exist_ok=True)
        Path(save_txt).write_text(txt + '\n', encoding='utf-8')
        if verbose:
            print(f'报告已保存：{save_txt}')
    return r
