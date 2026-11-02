# -*- coding: utf-8 -*-
"""detect/manual_recon.py —— 用「显式指定的文件夹」重建（不依赖命名规则）。

为什么需要：核心层 `cest_recon.recon_one_case` 是**按前缀自动找**三个序列的；
当用户在「目录预览」里手动挑文件夹（命名不规范、或想指定某几个）时，自动找就不好使。
这里用核心层的**公开构件**（`read_series_4d` / `make_ssat` / `norm_one_case`）拼一遍，
产出与 `recon_one_case` 完全一致，**核心层一行不改**。
"""
from __future__ import annotations

from pathlib import Path


def recon_with_dirs(dirs, out_dir, *, slice_max=20, freq_seq=None, order='auto',
                    do_normalize=True, do_layer_check=True) -> dict:
    """dirs = {'S0': 路径, 'Ssat': 路径, 'B0Ma': 路径}（缺的可以没有）。

    freq_seq 是频率表（**含重复的采集顺序表**或**去重后的频偏表**都行：make_ssat 只取唯一频偏，
    并按谱线物理判据自动剔除多余帧）；只在有 Ssat 时用；
    输出 s0.mat / b0.mat / ssat.mat / w_offset.mat / freq.mat / normZ.mat。
    """
    import numpy as np
    import cest_recon as R

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    res = {'S0': 'OFF', 'B0': 'OFF', 'Ssat': 'OFF', 'Norm': 'OFF'}

    p = dirs.get('S0')
    if p:
        try:
            s0 = R.read_series_4d(p, slice_max, 'S0', order=order)
            R._save(out / 's0.mat', s0=s0)
            if do_layer_check:
                R.layer_check(s0, 'S0')
            res['S0'] = str(s0.shape)
        except Exception as e:                                   # noqa: BLE001
            print(f'  S0   | 读取失败：{e}')

    p = dirs.get('B0Ma')
    if p:
        try:
            b0 = R.read_series_4d(p, slice_max, 'B0Ma', order=order)
            R._save(out / 'b0.mat', b0=b0)
            res['B0'] = str(b0.shape)
        except Exception as e:                                   # noqa: BLE001
            print(f'  B0Ma | 读取失败：{e}')

    p = dirs.get('Ssat')
    if p:
        try:
            img = R.read_series_4d(p, slice_max, 'Ssat', order=order)
            ssat, w = R.make_ssat(img, freq_seq, slice_max)
            R._save(out / 'ssat.mat', zdata=ssat, ssat=ssat, axis_order=R.AXIS_ORDER)
            R._save(out / 'w_offset.mat', w_offset=w, freq_seq=w)
            R._save(out / 'freq.mat', w_offset=w, freq_seq=w)
            res['Ssat'] = str(ssat.shape)
        except Exception as e:                                   # noqa: BLE001
            print(f'  Ssat | {e}')

    if do_normalize:
        if (out / 's0.mat').exists() and (out / 'ssat.mat').exists():
            res['Norm'] = R.norm_one_case(out, True)
        else:
            res['Norm'] = 'SKIP（缺 s0 或 ssat，无法归一化）'
            print('       Norm | 跳过：没有 s0 与 ssat 同时存在')
    return res


def recon_single(dicom_dir, out_dir, *, slice_max=20, order='auto', name=None) -> dict:
    """把**一个**文件夹当单序列重建，只出一个 3D（X, Y, 层）。

    用在「目录预览」里勾选了「其它」命名的文件夹时（角色不明，没法当 S0/Ssat/B0Ma）。
    若原始每层帧数 > 1：主输出取层内平均得到 3D，另存一份 4D 备查（不丢数据）。
    """
    import numpy as np
    import cest_recon as R
    from . import series

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = name or Path(dicom_dir).name

    # 层数不写死：先读 DICOM 标签实测（层数/帧数），再读数据
    d = series.scan_dims(dicom_dir, slice_max=None)
    tries = []
    if d.n_slices and d.n_frames and d.n_frames % d.n_slices == 0:
        tries.append(d.n_slices)
    if d.n_frames:
        tries.append(d.n_frames)          # 兜底：把所有帧当层
    a, err, used = None, None, None
    for sm in dict.fromkeys([t for t in tries if t and t > 0]):
        try:
            a = R.read_series_4d(dicom_dir, sm, '单序列', order=order)
            used = sm
            break
        except Exception as e:                                   # noqa: BLE001
            err = e
    if a is None:
        raise RuntimeError(f'{name}：读不出来（{err}）')
    print(f'  单序列 | {name}：按层数 {used} 读（帧 {d.n_frames} / 层 {d.n_slices}）')
    res = {}
    if a.ndim == 4 and a.shape[3] == 1:
        vol = a[:, :, :, 0]
        R._save(out / f'{name}.mat', zdata=vol, axis_order='layer')
        res['3D'] = f'{tuple(vol.shape)}（每层 1 帧，直接取）'
    elif a.ndim == 3:
        R._save(out / f'{name}.mat', zdata=a, axis_order='layer')
        res['3D'] = f'{tuple(a.shape)}'
    else:
        vol = a.mean(axis=3)
        R._save(out / f'{name}.mat', zdata=vol, axis_order='layer')
        R._save(out / f'{name}_4d.mat', zdata4d=a, axis_order='layer_freq')
        res['3D'] = (f'{tuple(vol.shape)}（由每层 {a.shape[3]} 帧平均；'
                     f'4D 原样另存 {name}_4d.mat）')
    print(f'  单序列 3D | {name} → {res["3D"]}')
    return res
