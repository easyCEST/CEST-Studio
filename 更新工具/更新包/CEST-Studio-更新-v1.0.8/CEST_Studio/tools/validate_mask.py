# -*- coding: utf-8 -*-
"""CEST_Studio\\tools\\validate_mask.py —— ⑧ 去噪掩模 与 MATLAB 已有结果的对照脚本。

参考数据（MATLAB 手工跑出来的，2026-09-14）：
    C:\\Users\\Administrator\\Desktop\\2026\\09\\CEST_post\\<病例>\\full_interp_0803\\
        Thmask.mat     → MATLAB 的 SNR 阈值掩模（= 噪声 σ 阈值，snr=15）
        liverMask.mat  → MATLAB 手画的组织/肝脏掩模（roipoly）
        mask.mat       → 最终去噪掩模（Thmask = liverMask × snrMask）+ img_mask_base

两种用法
--------
1) 复现（不需要手工画，直接用参考 liverMask + 参考反推的 σ 跑一遍 ⑧ 模块）：
       python tools\\validate_mask.py --reproduce
   期望：Mask / liverMask / snrMask 与参考 max|diff| = 0

2) 体检（把你在 GUI 里画完存到 PROJ\\00_掩模 的结果拿来对拍）：
       python tools\\validate_mask.py --proj <PROJ> --case c1
   输出 Dice / 差异体素数 / 逐像素 max|diff|，并列出差异位置统计。

口径提醒：Mask.mat / Thmask.mat 存的是 **3D (X,Y,层)**（② 的 apply_mask 需要 3D），
参考文件是 2D；本脚本比较时取第 1 层，逐层相同则等价。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent          # CEST_Studio\tools
STUDIO = HERE.parent                            # CEST_Studio
CORE = STUDIO.parent / '03_Python_全链路' / '函数库'
for _p in (str(STUDIO), str(CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cest_io import load_mat                    # noqa: E402

def _roi_dir(proj, case):
    """PROJ\\00_掩模\\<病例>（兼容旧的 00_roi）。"""
    for n in ('00_掩模', '00_roi'):
        d = Path(proj) / n / case
        if d.exists():
            return d
    return Path(proj) / '00_掩模' / case


REF_ROOT = Path(r'C:\Users\Administrator\Desktop\2026\09\CEST_post')
RECON_ROOT = Path(r'C:\Users\Administrator\Desktop\2026\09\CEST_result_gui\01_recon')
# 参考掩模反推出的噪声 σ（阈值 = 15σ；见文档第 13 节）
REF_SIGMA = {'c1': 25.7933, 'h1': 16.3333}


def _2d(a):
    a = np.asarray(a, dtype=float)
    return a[:, :, 0] if a.ndim == 3 else a


def _ref(case):
    d = REF_ROOT / case / 'full_interp_0803'
    out = {}
    for key, fn, var in (('liverMask', 'liverMask.mat', 'liverMask'),
                         ('snrMask', 'Thmask.mat', 'Thmask'),
                         ('Mask', 'mask.mat', 'Thmask')):
        f = d / fn
        if f.exists():
            got = load_mat(f, [var])
            if got:
                out[key] = _2d(got[var])
    if (d / 'mask.mat').exists():
        got = load_mat(d / 'mask.mat', ['img_mask_base'])
        if got:
            out['img_mask_base'] = np.asarray(got['img_mask_base'], dtype=float)
    return out


def _cmp(tag, mine, ref):
    m, r = _2d(mine), _2d(ref)
    if m.shape != r.shape:
        print(f'  {tag:12s} 形状不一致：{m.shape} vs {r.shape}')
        return False
    inter = int(((m > 0) & (r > 0)).sum())
    nm, nr = int((m > 0).sum()), int((r > 0).sum())
    dice = 2.0 * inter / max(1, nm + nr)
    diff = int(((m > 0) ^ (r > 0)).sum())
    mx = float(np.abs(m - r).max())
    ok = (diff == 0 and mx == 0.0)
    print(f'  {tag:12s} {"√ 逐位一致" if ok else "× 有差异  "}  '
          f'体素 {nm} vs {nr}  差异 {diff}  Dice {dice:.6f}  max|diff| {mx:.4e}')
    return ok


def reproduce(cases, proj):
    """用参考 liverMask + 参考 σ 跑 ⑧ 模块，验证「口径一致」。"""
    from app.modules.m_roi import ROIModule
    proj = Path(proj)

    class Ctx:
        def __init__(self):
            self.proj = str(proj)
            self.cancelled = False

        def log(self, s=''):
            print('   ', s)

        def progress(self, *a):
            pass

        def out_dir(self, sub):
            from detect.rules import cn_name        # 与 GUI 一致：写中文目录名
            d = proj / cn_name(sub)
            d.mkdir(parents=True, exist_ok=True)
            return d

    all_ok = True
    for case in cases:
        ref = _ref(case)
        if not ref:
            print(f'===== {case}：没有参考数据，跳过')
            continue
        sigma = REF_SIGMA.get(case, 0.0)
        print(f'===== {case}（σ={sigma}，参考 liverMask 当手画结果）')
        m = ROIModule()
        m._draw = dict(case=case, n_layer=20, shape=ref['liverMask'].shape,
                       base='S0 层间平均', ppm=3.5, liver=ref['liverMask'],
                       noise=None, snr=None, snr_sigma=sigma, snr_factor=15.0,
                       snr_thr=0.0, snr_src='手输', per_layer=[],
                       n_liver=int((ref['liverMask'] > 0).sum()), n_noise=0, n_snr=0)
        p = dict(src=str(RECON_ROOT / case), case=case, snr=15.0,
                 noise_sigma=sigma, mask_file='', dpi=110)
        m.run(p, Ctx())
        out = _roi_dir(proj, case)
        got = {k: _2d(load_mat(out / f, [v])[v])
               for k, f, v in (('liverMask', 'liverMask.mat', 'liverMask'),
                               ('snrMask', 'snrMask.mat', 'snrMask'),
                               ('Mask', 'Mask.mat', 'Mask'))}
        # 底图口径（参考里存了 img_mask_base）
        if 'img_mask_base' in ref:
            got0 = load_mat(RECON_ROOT / case / 's0.mat', ['s0', 'S0'])
            s0 = np.asarray(got0.get('s0', got0.get('S0')), dtype=float)
            base = s0[:, :, :, 0].mean(axis=2) if s0.ndim == 4 else s0.mean(axis=2)
            d = float(np.abs(base - ref['img_mask_base']).max())
            print(f'  {"底图 mean(S0)":12s} {"√ 逐位一致" if d == 0 else "× 有差异"}  '
                  f'max|diff| {d:.4e}（＝img_mask_base）')
            all_ok &= (d == 0)
        for tag in ('liverMask', 'snrMask', 'Mask'):
            all_ok &= _cmp(tag, got[tag], ref[tag])
    return all_ok


def check(proj, cases):
    """体检：拿 PROJ\\00_roi\\<病例> 里已有的结果与参考对拍。"""
    proj = Path(proj)
    all_ok = True
    for case in cases:
        ref = _ref(case)
        out = _roi_dir(proj, case)
        print(f'===== {case}　{out}')
        if not ref:
            print('  没有参考数据，跳过')
            continue
        if not out.exists():
            print('  × 这个病例还没有 00_掩模 结果')
            all_ok = False
            continue
        pairs = (('liverMask', out / 'liverMask.mat', 'liverMask'),
                 ('snrMask', out / 'snrMask.mat', 'snrMask'),
                 ('Mask', out / 'Mask.mat', 'Mask'))
        for tag, f, var in pairs:
            if not f.exists():
                print(f'  {tag:12s} × 缺文件 {f.name}')
                all_ok = False
                continue
            got = load_mat(f, [var])
            if not got:
                print(f'  {tag:12s} × {f.name} 里没有变量 {var}')
                all_ok = False
                continue
            all_ok &= _cmp(tag, got[var], ref[tag])
        meta = out / 'roi_meta.json'
        if meta.exists():
            print('  roi_meta.json：', meta.read_text(encoding='utf-8').replace('\n', ' ')[:200])
    return all_ok


def main(argv=None):
    ap = argparse.ArgumentParser(description='⑧ 去噪掩模 对照脚本')
    ap.add_argument('--reproduce', action='store_true',
                    help='用参考 liverMask + 参考 σ 跑一遍 ⑧ 模块（验证口径）')
    ap.add_argument('--check', action='store_true',
                    help='体检 PROJ\\00_roi 里已有的结果')
    ap.add_argument('--proj', default=r'C:\Users\Administrator\Desktop\2026\09\_roi_mask_proj')
    ap.add_argument('--cases', default='c1,h1')
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    cases = [x.strip() for x in a.cases.split(',') if x.strip()]
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                            # noqa: BLE001
        pass
    if a.check:
        ok = check(a.proj, cases)
    else:
        ok = reproduce(cases, a.proj)
    print('\n结论：', '全部逐位一致 √' if ok else '存在差异（见上面明细）×')
    return 0


if __name__ == '__main__':
    sys.exit(main())
