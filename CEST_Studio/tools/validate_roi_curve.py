# -*- coding: utf-8 -*-
"""tools/validate_roi_curve.py —— 一键验证 ③ CEST 后处理 → ROI 定量曲线。

（ROI 定量曲线已并入 ③ 页的【ROI 定量曲线…】对话框，不再是独立模块；
  本脚本直接调核心层 cest_roi.run_curves，等价于对话框里的导出。）

流程（全程写临时 PROJ，不碰用户数据盘）：
    1) 建临时 PROJ 的 00_掩模\\<病例>\\（从 MATLAB 参考里拷 Mask/liverMask/ROI_total）
    2) 用 ③ 跑一层
    3) 直接调 cest_roi.run_curves 出 ROI 曲线
    4) 自检：LD == 拟合 − min-max(Z)；三条 Z 谱口径的关系

用法（在 cest-gui 根目录）::

    runtime\\python.exe -X utf8 CEST_Studio\\tools\\validate_roi_curve.py
    runtime\\python.exe -X utf8 CEST_Studio\\tools\\validate_roi_curve.py ^
        --recon <重建结果目录> --ref <MATLAB 的 CEST_post\\<病例>\\*_interp_* 目录> ^
        --case c1 --layer 1 --tag Self_corr
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
CG = HERE.parent.parent.parent
for p in (str(CG / 'CEST_Studio'), str(CG / '03_Python_全链路' / '函数库')):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np                                          # noqa: E402

DEF_RECON = r'C:\Users\Administrator\Desktop\2026\09\CEST_result\c1'
DEF_REF = r'C:\Users\Administrator\Desktop\2026\09\CEST_post\c1\full_interp_0803'


def main(argv=None):
    ap = argparse.ArgumentParser(description='验证 ③→ROI 定量曲线')
    ap.add_argument('--recon', default=DEF_RECON)
    ap.add_argument('--ref', default=DEF_REF)
    ap.add_argument('--case', default='c1')
    ap.add_argument('--layer', type=int, default=1)
    ap.add_argument('--tag', default='Self_corr')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args(argv)

    from app.modules.m3_post import PostModule
    from jobs.ctx import JobContext

    ref = Path(str(a.ref))
    if not ref.is_dir():
        print(f'✗ 参考目录不存在：{ref}')
        return 2
    proj = Path(tempfile.mkdtemp(prefix='cest_roi_curve_'))
    print(f'临时 PROJ：{proj}')
    mk = proj / '00_掩模' / a.case
    mk.mkdir(parents=True, exist_ok=True)
    for src, dst in (('Thmask.mat', 'Mask.mat'), ('Mask.mat', 'Mask.mat'),
                     ('liverMask.mat', 'liverMask.mat'), ('ROI_total.mat', 'ROI_total.mat')):
        s = ref / src
        if s.exists():
            shutil.copy2(s, mk / dst)
    ctx = JobContext(proj=str(proj))
    ctx.bind(sink=lambda s: print('  |', s), progress=lambda c, t, x: None)

    m3 = PostModule()
    p3 = dict(src=str(a.recon), case=a.case, b0correct='自校正', negorpos='全频偏',
              target_freq=3.5, date_suffix='0803', mask_file='', layers=str(a.layer),
              save_full_vas=False, save_uncorrect=False, norm_tol=0.02, norm_plow=2.0,
              norm_phigh=98.0, x_lw=0.025, do_selfcorr_qc=True, rotate_data=False,
              b0_scale='', b0_offset=0.0, total_slice_num='', snr=15.0, nROI=1,
              roi_display_mode='Z 原始', save_single=True, mask_layer=1)
    e3 = m3.validate(p3, ctx)
    if e3:
        print('✗ ③ 校验不过：', e3)
        return 2
    t0 = time.time()
    r3 = m3.run(p3, ctx)
    print(f'③ 完成：{r3["out_dir"]}（{time.time() - t0:.0f}s）')

    # ---------------- ROI 曲线（等价于 ③ 页对话框的导出）----------------
    import cest_roi as CR
    from cest_io import load_first
    sln = Path(r3['out_dir']) / f'slice{a.layer}'      # sliceN 根：w_offset / x_as 在这
    stag = sln / a.tag                                 # tag 子目录：V_exp_mask / V_as_mask
    w = np.asarray(load_first(sln / 'w_offset.mat', ('w_offset', 'w')), float).ravel()
    z = np.asarray(load_first(stag / 'V_exp_mask.mat', ('V_exp_mask',)), float)
    if z.ndim == 2:
        z = z[:, :, None]
    va, xa = None, None
    if (stag / 'V_as_mask.mat').exists() and (sln / 'x_as.mat').exists():
        va = np.asarray(load_first(stag / 'V_as_mask.mat', ('V_as_mask',)), float)
        xa = np.asarray(load_first(sln / 'x_as.mat', ('x_as',)), float).ravel()
    out5 = proj / '03_CEST后处理' / a.case / 'ROI曲线' / 'validate'
    t0 = time.time()
    r5 = CR.run_curves(z, va, w, xa, mk / 'ROI_total.mat', out5,
                       dict(trim=True, negorpos=0, dpi=150), log=lambda s: print('  ', s))
    print(f'ROI 曲线完成：{r5["out_dir"]}（{time.time() - t0:.0f}s，{r5["n_roi"]} 个 ROI）')

    # ---------------- 自检 ----------------
    out = Path(r5['out_dir'])
    print('\n===== 自检 =====')
    ok = True
    zt = load_first(out / 'Zspc_filter.mat', ('mSigE_trimmed',))
    ft = load_first(out / 'Lorentzian_fit.mat', ('Lorentzian_fit',))
    ld = load_first(out / 'LD.mat', ('LD',))
    wt = load_first(out / 'w_offset_filter.mat', ('w_offset_trimmed',))
    for k in range(r5['n_roi']):
        zz = np.asarray(np.asarray(zt).ravel()[k], float).ravel()
        f = np.asarray(np.asarray(ft).ravel()[k], float).ravel()
        l = np.asarray(np.asarray(ld).ravel()[k], float).ravel()
        off = np.asarray(np.asarray(wt).ravel()[k], float).ravel()
        d = float(np.abs((f - CR.minmax(zz)) - l).max())
        good = np.isfinite(d) and d < 1e-10
        ok = ok and good
        print(f'  ROI{k + 1}: LD == 拟合 − min-max(Z)  max|diff|={d:.3g}  '
              f'{"✓" if good else "✗"}（{off.size} 点，{off.min():.1f}..{off.max():.1f} ppm）')
    raw = np.asarray(load_first(out / f'Zspc_{r5["n_roi"]}ROI_Raw.mat', ('mSigE_Raw',)), float)
    div = np.asarray(load_first(out / f'Zspc_{r5["n_roi"]}ROI.mat', ('mSigE',)), float)
    z01 = np.asarray(load_first(out / f'Zspc_{r5["n_roi"]}ROI_0to1.mat', ('mSigE_0to1',)), float)
    d1 = float(np.abs(div - raw / raw.max(axis=0, keepdims=True)).max())
    d2 = float(np.abs(z01 - ((raw - raw.min(0, keepdims=True))
                             / (raw.max(0, keepdims=True) - raw.min(0, keepdims=True)))).max())
    ok = ok and d1 < 1e-10 and d2 < 1e-10
    print(f'  三口径：÷列最大 max|diff|={d1:.3g}　每列 min-max max|diff|={d2:.3g}  '
          f'{"✓" if (d1 < 1e-10 and d2 < 1e-10) else "✗"}（全长 {raw.shape[0]} 点）')
    print('\n结论：' + ('链路通 + 自检全过' if ok else '链路通，但自检有项未过'))
    if not a.keep:
        shutil.rmtree(proj, ignore_errors=True)
        print('（临时 PROJ 已清理；要保留加 --keep）')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
