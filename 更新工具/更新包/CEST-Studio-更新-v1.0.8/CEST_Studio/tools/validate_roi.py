# -*- coding: utf-8 -*-
"""CEST_Studio\\tools\\validate_roi.py —— ⑧ 画 ROI（ROI_total）与 MATLAB 已有结果的对照脚本。

参考数据（MATLAB 手工跑的 c1 / h1，2026-09）：
    C:\\Users\\Administrator\\Desktop\\2026\\09\\CEST_post\\<病例>\\full_interp_0803\\
        mask.mat            → 最终去噪掩模（Thmask = liverMask × snrMask，2D）
        mask_ROI\\ROI1.mat   → MATLAB roipoly 画出来的 ROI（变量 ROI，192×176 0/1）
        ROI_total.mat       → 勾画结果：cell(nROI,1)，元素 = 1-based 列优先线性索引
                              c1 → 8524 体素（首值 5470、最大 28315）
                              h1 → 9289 体素（首值 5086、最大 29470）
    （c1 的 ROI1.mat 在 mask_ROI\\ 下；h1 的 ROI1.mat 在 full_interp_0803\\ 下，两种都认。）

两种用法
--------
1) 口径验证（默认；★ 逐位最强）：把参考 ROI1.mat 当成「画好的 ROI」喂进 ⑧ 的保存路径，
       python tools\\validate_roi.py --reproduce
   期望：生成的 ROI_total.mat 与参考 **逐位一致（差异 0 体素）**，且满足
         ROI ⊂ Mask、cell(nROI,1)、元素是列向量、1-based 列优先、升序（= MATLAB find）。

2) 体检（把你在 GUI 里画完存到 PROJ\\00_掩模\\<病例>\\ 的结果拿来对拍）：
       python tools\\validate_roi.py --check --proj <PROJ> --case c1

3) 额外验证核心层 cest_post.roi_plot_song 的「有 ROI_total.mat 就复用」分支：
       python tools\\validate_roi.py --via-core
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent          # CEST_Studio\tools
STUDIO = HERE.parent                            # CEST_Studio
CORE = STUDIO.parent / '03_Python_全链路' / '函数库'
for _p in (str(STUDIO), str(CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cest_io import load_mat                    # noqa: E402
import cest_post as P                           # noqa: E402
from app.modules.m_roi import build_roi_total   # noqa: E402

REF_ROOT = Path(r'C:\Users\Administrator\Desktop\2026\09\CEST_post')
DEFAULT_PROJ = Path(r'C:\Users\Administrator\Desktop\2026\10\_roi_roi_proj')


def _roi_dir(proj, case):
    for n in ('00_掩模', '00_roi'):
        d = Path(proj) / n / case
        if d.exists():
            return d
    return Path(proj) / '00_掩模' / case


def _2d(a):
    a = np.asarray(a, dtype=float)
    return a[:, :, 0] if a.ndim == 3 else a


def _ref_paths(case):
    base = REF_ROOT / case / 'full_interp_0803'
    roi1 = base / 'mask_ROI' / 'ROI1.mat'
    if not roi1.exists():
        roi1 = base / 'ROI1.mat'
    return dict(roi1=roi1, mask=base / 'mask.mat',
                total=base / 'ROI_total.mat', total2=base / 'mask_ROI' / 'ROI_total.mat')


def _cell_arrays(v):
    """把 .mat 里读出来的 ROI_total（cell）摊平成 list[1D 数组]。"""
    arr = np.asarray(v, dtype=object)
    flat = arr.ravel()
    return [np.asarray(flat[i]).ravel() for i in range(flat.size)]


def _struct_ok(tag, lists, shape, n_expect=None):
    """cell(n,1) object、元素列向量、1-based、升序、范围合法。"""
    ok = True
    if n_expect is not None and len(lists) != n_expect:
        print(f'  {tag:12s} × ROI 个数 {len(lists)} ≠ {n_expect}')
        ok = False
    nmax = int(shape[0]) * int(shape[1])
    for k, v in enumerate(lists):
        v = np.asarray(v).ravel()
        bad = []
        if v.size and v.min() < 1:
            bad.append('不是 1-based')
        if v.size and v.max() > nmax:
            bad.append(f'超出 {shape} 范围')
        if v.size and not np.all(np.diff(v) >= 0):
            bad.append('不是升序（≠ MATLAB find）')
        if bad:
            print(f'  {tag:12s} × ROI{k + 1}：' + '、'.join(bad))
            ok = False
    if ok:
        print(f'  {tag:12s} √ cell({len(lists)},1)、元素列向量、1-based 列优先、升序')
    return ok


def _subset_ok(tag, lists, mask2d, shape):
    """ROI ⊂ Mask。"""
    ok = True
    m = _2d(mask2d).astype(bool)
    nm = int(m.sum())
    for k, v in enumerate(lists):
        v = np.asarray(v).ravel().astype(np.int64)
        if v.size:
            rr, cc = np.unravel_index(v - 1, shape, order='F')
            if not bool(m[rr, cc].all()):
                print(f'  {tag:12s} × ROI{k + 1} 不满足 ROI ⊂ Mask')
                ok = False
    if ok:
        print(f'  {tag:12s} √ ROI ⊂ Mask（Mask 共 {nm} 体素）')
    return ok


def _compare(tag, mine, ref):
    a = np.sort(np.asarray(mine).ravel().astype(np.int64))
    b = np.sort(np.asarray(ref).ravel().astype(np.int64))
    ident = (a.size == b.size and np.array_equal(a, b))
    inter = len(set(a.tolist()) & set(b.tolist()))
    dice = 2.0 * inter / max(1, len(a) + len(b))
    print(f'  {tag:12s} {"√ 逐位一致" if ident else "× 有差异  "}  '
          f'体素 {a.size} vs {b.size}  差异 {a.size + b.size - 2 * inter}  Dice {dice:.6f}')
    if a.size and b.size:
        print(f'  {"":12s} 首值 {a[0]} vs {b[0]}　最大 {a[-1]} vs {b[-1]}')
    return ident


def reproduce(cases, proj):
    """口径验证：参考 ROI1.mat 当「画好的 ROI」→ 走 ⑧ 的保存路径 → 与参考逐位比。"""
    proj = Path(proj)
    all_ok = True
    for case in cases:
        rp = _ref_paths(case)
        if not (rp['roi1'].exists() and rp['mask'].exists() and rp['total'].exists()):
            print(f'===== {case}：缺少参考文件，跳过（{rp}）')
            continue
        roi = _2d(load_mat(rp['roi1'], ['ROI'])['ROI']) > 0
        mask = _2d(load_mat(rp['mask'], ['Thmask', 'Mask', 'liverMask'])['Thmask']) > 0
        ref = _cell_arrays(load_mat(rp['total'], ['ROI_total'])['ROI_total'])
        shape = tuple(mask.shape)
        print(f'===== {case}　ROI1 {int(roi.sum())} 体素　Mask {int(mask.sum())} 体素')

        # ---- 走真实保存路径 ----
        lists = build_roi_total([roi], mask)
        out = _roi_dir(proj, case)
        out.mkdir(parents=True, exist_ok=True)
        P.savemat(str(out / 'ROI1.mat'), {'ROI': roi.astype(float)})
        P.savemat(str(out / 'ROI_total.mat'),
                  {'ROI_total': P._cell_of_arrays(lists)})
        back = _cell_arrays(load_mat(out / 'ROI_total.mat', ['ROI_total'])['ROI_total'])
        print(f'  已写 {out / "ROI1.mat"} 与 {out / "ROI_total.mat"}（走真实保存路径）')

        all_ok &= _struct_ok('结构', back, shape, n_expect=1)
        all_ok &= _subset_ok('ROI⊂Mask', back, mask, shape)
        all_ok &= _compare('ROI_total', back[0], ref[0])
        if rp['total2'].exists():
            ref2 = _cell_arrays(load_mat(rp['total2'], ['ROI_total'])['ROI_total'])
            all_ok &= _compare('参考另一份', ref2[0], ref[0])
    return all_ok


def via_core(cases):
    """证明「有 ROI_total.mat 就复用」这条核心层行为对参考文件也成立。"""
    ok = True
    for case in cases:
        rp = _ref_paths(case)
        if not (rp['total'].exists() and rp['mask'].exists()):
            continue
        ref = _cell_arrays(load_mat(rp['total'], ['ROI_total'])['ROI_total'])
        shape = _2d(load_mat(rp['mask'], ['Thmask'])['Thmask']).shape
        tmp = Path(tempfile.mkdtemp(prefix=f'roi_core_{case}_'))
        try:
            shutil.copy2(rp['total'], tmp / 'ROI_total.mat')
            got = P.roi_plot_song(1, ['ROI1'], np.ones(shape), tmp,
                                  np.ones(shape), verbose=False)
            same = np.array_equal(np.sort(np.asarray(got[0]).ravel()),
                                  np.sort(ref[0].ravel()))
            print(f'  核心层 roi_plot_song 复用 {case}：{"√ 一致" if same else "× 不一致"}')
            ok &= bool(same)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return ok


def check(proj, cases):
    """体检 PROJ\\00_掩模\\<病例> 里生成的 ROI 产物（自洽 + 与参考比 Dice）。"""
    proj = Path(proj)
    all_ok = True
    for case in cases:
        rp = _ref_paths(case)
        out = _roi_dir(proj, case)
        print(f'===== {case}　{out}')
        ft = out / 'ROI_total.mat'
        fm = out / 'Mask.mat'
        if not ft.exists() or not fm.exists():
            print('  × 还没有 ROI_total.mat / Mask.mat，先跑 ⑧')
            all_ok = False
            continue
        mask = _2d(load_mat(fm, ['Mask', 'Thmask'])['Mask']) > 0
        lists = _cell_arrays(load_mat(ft, ['ROI_total'])['ROI_total'])
        shape = tuple(mask.shape)
        all_ok &= _struct_ok('结构', lists, shape)
        all_ok &= _subset_ok('ROI⊂Mask', lists, mask, shape)
        for k in range(1, len(lists) + 1):
            fk = out / f'ROI{k}.mat'
            if not fk.exists():
                print(f'  ROI{k}.mat × 缺文件')
                all_ok = False
                continue
            mk = _2d(load_mat(fk, ['ROI'])['ROI']) > 0
            idx = np.nonzero(mk & mask)
            v = np.sort(np.ravel_multi_index(idx, shape, order='F') + 1)
            same = np.array_equal(v, np.sort(np.asarray(lists[k - 1]).ravel()))
            print(f'  ROI{k}.mat 反算 {"√ 与 ROI_total 一致" if same else "× 不一致"}'
                  f'（勾画 {int(mk.sum())}，∩ 掩模 {v.size}）')
            all_ok &= bool(same)
        if rp['total'].exists():
            ref = _cell_arrays(load_mat(rp['total'], ['ROI_total'])['ROI_total'])
            if len(ref) == len(lists):
                all_ok &= _compare('vs 参考', lists[0], ref[0])
    return all_ok


def main(argv=None):
    ap = argparse.ArgumentParser(description='⑧ 画 ROI（ROI_total）对照脚本')
    ap.add_argument('--reproduce', action='store_true',
                    help='把参考 ROI1.mat 当「画好的 ROI」走保存路径（默认）')
    ap.add_argument('--check', action='store_true', help='体检 PROJ\\00_掩模 里已有的结果')
    ap.add_argument('--via-core', action='store_true',
                    help='额外验证核心层 roi_plot_song 的「有就复用」分支')
    ap.add_argument('--proj', default=str(DEFAULT_PROJ))
    ap.add_argument('--case', default='c1,h1')
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    cases = [x.strip() for x in a.case.split(',') if x.strip()]
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                            # noqa: BLE001
        pass
    ok = check(a.proj, cases) if a.check else reproduce(cases, a.proj)
    if a.via_core:
        print('\n--- 核心层 roi_plot_song 的「有就复用」分支 ---')
        ok = via_core(cases) and ok
    print('\n结论：', '全部逐位一致 √' if ok else '存在差异（见上面明细）×')
    return 0


if __name__ == '__main__':
    sys.exit(main())
