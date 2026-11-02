# -*- coding: utf-8 -*-
"""CEST_Studio\\tools\\validate_ld.py —— ④ 逐体素LD 的验证脚本。

三种用法
--------
1) 与核心层对拍（★ 验证「壳里的编排」没写错）：
       python tools\\validate_ld.py --parity --src <重建结果目录> --mask <Mask.mat> [--proj <PDIR>]
   做法：同一份数据/掩模/参数，一遍走 ④ 模块（m4_ld.run），一遍直接调核心层
         cest_ld.make_voxel_ld；然后逐位比 voxel_LD.mat。
   期望：逐位一致（差异 0）。不一致说明壳的编排偏离了核心层。

2) 两个 voxel_LD.mat 对比（与 MATLAB 参考/旧结果对拍）：
       python tools\\validate_ld.py --check --ours <ours\\voxel_LD.mat> --ref <ref\\voxel_LD.mat>
   输出：逐层的「有效体素图样一致率 / Dice / 交集上的中位差 / 相关系数」。

3) 跑一遍 + 自动对拍：
       python tools\\validate_ld.py --run --src <重建结果目录> --mask <Mask.mat>
            --proj <PDIR> --case h1 --ref <ref\\voxel_LD.mat> [--layers all] [--exact]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parent
CORE = STUDIO.parent / '03_Python_全链路' / '函数库'
for _p in (str(STUDIO), str(CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cest_io import load_mat                      # noqa: E402
import cest_ld as L                               # noqa: E402
from app.modules.m4_ld import LDModule            # noqa: E402


def _load_ld(p):
    got = load_mat(p, ['voxel_ld', 'Voxel_LD_all'])
    v = got.get('voxel_ld', got.get('Voxel_LD_all'))
    if v is None:
        raise SystemExit(f'{p} 里没有 voxel_ld / Voxel_LD_all')
    return np.asarray(v, dtype=float)


def _valid(a):
    return np.isfinite(a) & (a != 0)


def check(ours_f, ref_f, layers=None):
    a = _load_ld(ours_f)
    b = _load_ld(ref_f)
    if (a.shape != b.shape and a.ndim == 4 and b.ndim == 4
            and a.shape[2] == b.shape[3] and a.shape[3] == b.shape[2]):
        print('（参考是 [X,Y,频,层] 旧轴序 → 已 swap 成层优先再比）')
        b = np.swapaxes(b, 2, 3)
    print(f'ours {tuple(a.shape)}   ref {tuple(b.shape)}')
    if a.shape != b.shape:
        print('× 形状不一致，无法逐层比')
        return False
    n = a.shape[2]
    print(f'{"层":>4}  {"有效图样一致":>10}  {"Dice":>7}  {"交集":>7}  {"中位差":>9}  {"相关":>7}')
    di_s, md_s, cc_s, pat_s = [], [], [], []
    for k in range(n):
        va, vb = _valid(a[:, :, k, :]), _valid(b[:, :, k, :])
        pat = float(np.mean(va == vb))
        inter = int(np.count_nonzero(va & vb))
        dice = 2 * inter / max(1, int(va.sum()) + int(vb.sum()))
        md = cc = float('nan')
        if inter:
            x = a[:, :, k, :][va & vb]
            y = b[:, :, k, :][va & vb]
            md = float(np.median(np.abs(x - y)))
            if x.size > 2 and np.std(x) > 0 and np.std(y) > 0:
                cc = float(np.corrcoef(x, y)[0, 1])
        pat_s.append(pat); di_s.append(dice); md_s.append(md); cc_s.append(cc)
        if (k + 1) % 5 == 0 or k == n - 1:
            print(f'{k + 1:>4}  {pat:>10.4f}  {dice:>7.4f}  {inter:>7d}  '
                  f'{md:>9.5f}  {cc:>7.4f}')
    print(f'\n汇总：图样一致 {np.mean(pat_s):.4f}（最低 {np.min(pat_s):.4f}）　'
          f'Dice {np.nanmean(di_s):.4f}　'
          f'中位差 中位 {np.nanmedian(md_s):.5f}（最大 {np.nanmax(md_s):.5f}）　'
          f'相关 中位 {np.nanmedian(cc_s):.4f}')
    return bool(np.mean(pat_s) > 0.99 and np.nanmedian(di_s) > 0.97)


def parity(src, mask, proj, case, layers, max_voxels, fast=True):
    """④ 模块的编排 vs 核心层 make_voxel_ld（同参数） → 逐位比。"""
    import shutil
    from pathlib import Path as P

    class Ctx:
        def __init__(s): s.proj = str(proj); s.cancelled = False; s.lines = []
        def log(s, m=''): s.lines.append(str(m)); print('   ', m)
        def progress(s, *a): pass
        def check_cancel(s): pass
        def out_dir(s, sub):
            from detect.rules import cn_name
            d = P(proj) / cn_name(sub); d.mkdir(parents=True, exist_ok=True); return d

    mod = LDModule()
    p = mod.suggest(None, str(proj))
    p.update(dict(src=str(src), case=case or P(src).name, mask_file=str(mask),
                  mask_layer=1, fit=('fast' if fast else 'exact'),
                  layers=','.join(str(x) for x in layers), max_voxels=str(max_voxels),
                  save_interp=False))
    errs = mod.validate(p, Ctx())
    if errs:
        print('参数有问题：', errs)
        return False
    print('--- 走 ④ 模块（m4_ld.run）---')
    res = mod.run(p, Ctx())
    ours = P(res['out_dir']) / 'voxel_LD.mat'

    print('--- 直接调核心层 cest_ld.make_voxel_ld（同参数）---')
    out2 = P(res['out_dir']).parent / '_parity_core'
    if out2.exists():
        shutil.rmtree(out2)
    out2.mkdir(parents=True)
    L.make_voxel_ld(str(src), str(out2), layers=list(layers), tol=0.05,
                    step_interp=0.1, save_interp=False, verbose=True,
                    max_voxels=max_voxels, fast=fast,
                    mask_file=str(P(res['out_dir']) / '_thmask_used.mat'),
                    ppm=dict(l1=-0.6, l2=-6.0, l3=-10.0, h1=0.6, h2=6.0, h3=10.0),
                    par0=[0.0, -50.0, 20.0, 1.0],
                    bounds=([-np.inf, -np.inf, 0.0, -np.inf], [0.0, np.inf, 1.8, np.inf]),
                    sym_threshold=5.0,
                    c_grid=np.arange(-2.0, 0.0 + 1e-9, 0.05),
                    g_grid=np.arange(0.2, 1.8 + 1e-9, 0.05))
    core = out2 / 'voxel_LD.mat'
    a = _load_ld(ours)
    b = _load_ld(core)
    same = False
    d = float('nan')
    if a.shape == b.shape:
        nan_a, nan_b = np.isnan(a), np.isnan(b)
        d = float(np.nanmax(np.abs(a - b))) if np.any(~nan_a | ~nan_b) else 0.0
        same = (np.array_equal(nan_a, nan_b)
                and np.array_equal(np.nan_to_num(a, nan=0.0),
                                   np.nan_to_num(b, nan=0.0)))
    print(f'\n模块编排 {tuple(a.shape)}  vs  核心层 {tuple(b.shape)}')
    print(f'NaN 图样一致：{np.array_equal(np.isnan(a), np.isnan(b))}　'
          f'max|diff| = {d}　逐位一致：{"√ 是" if same else "× 否"}')
    # 顺手比一下 0.7ut_img_interp.mat 里的 img_interp
    try:
        ia = np.asarray(load_mat(P(ours).parent / '0.7ut_img_interp.mat',
                                 ['img_interp'])['img_interp'], float)
        ib = np.asarray(load_mat(P(core).parent / '0.7ut_img_interp.mat',
                                 ['img_interp'])['img_interp'], float)
        di = float(np.nanmax(np.abs(ia - ib))) if ia.shape == ib.shape else float('nan')
        print(f'img_interp {tuple(ia.shape)} max|diff| = {di}')
        same = same and di == 0.0
    except Exception as e:                                       # noqa: BLE001
        print(f'[提示] 比 img_interp 时出错：{e}')
    return bool(same)


def main(argv=None):
    ap = argparse.ArgumentParser(description='④ 逐体素LD 验证')
    ap.add_argument('--parity', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--src', default='')
    ap.add_argument('--mask', default='')
    ap.add_argument('--proj', default=r'C:\Users\Administrator\Desktop\2026\10\_ld_validate')
    ap.add_argument('--case', default='')
    ap.add_argument('--ref', default='')
    ap.add_argument('--ours', default='')
    ap.add_argument('--layers', default='1')
    ap.add_argument('--max-voxels', type=int, default=300)
    ap.add_argument('--exact', action='store_true')
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                            # noqa: BLE001
        pass

    if a.parity:
        layers = [int(x) for x in str(a.layers).split(',') if x.strip()] or [1]
        ok = parity(a.src, a.mask, a.proj, a.case or Path(a.src).name, layers,
                    a.max_voxels, fast=not a.exact)
        print('\n结论：', '编排与核心层一致 √' if ok else '不一致 ×')
        return 0 if ok else 1

    if a.run:
        import shutil
        class Ctx:
            def __init__(s): s.proj = str(a.proj); s.cancelled = False
            def log(s, m=''): print('   ', m)
            def progress(s, *a_): pass
            def check_cancel(s): pass
            def out_dir(s, sub):
                from detect.rules import cn_name
                d = Path(a.proj) / cn_name(sub); d.mkdir(parents=True, exist_ok=True); return d
        mod = LDModule()
        p = mod.suggest(None, str(a.proj))
        p.update(dict(src=str(a.src), case=a.case or Path(a.src).name,
                      mask_file=str(a.mask), mask_layer=1,
                      fit=('exact' if a.exact else 'fast'),
                      layers=('' if str(a.layers) in ('all', 'All', '*') else a.layers),
                      max_voxels=('' if a.max_voxels in (0, None) else str(a.max_voxels)),
                      save_interp=True))
        errs = mod.validate(p, Ctx())
        if errs:
            print('参数有问题：', errs)
            return 1
        res = mod.run(p, Ctx())
        ours = Path(res['out_dir']) / 'voxel_LD.mat'
        if a.ref:
            print('\n--- 与参考对拍 ---')
            ok = check(ours, a.ref)
            print('结论：', '在阈值内 √' if ok else '差异偏大（看上面的表）')
            return 0 if ok else 1
        print('\n产物：', sorted(x.name for x in Path(res['out_dir']).iterdir()))
        return 0

    if a.check:
        ok = check(a.ours, a.ref)
        print('结论：', '在阈值内 √' if ok else '差异偏大（看上面的表）')
        return 0 if ok else 1

    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
