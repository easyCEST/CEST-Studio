# -*- coding: utf-8 -*-
"""tools/validate_prf_extract.py —— 一键自检 ⑤ PRF 提取（组内/组间两模式）。

自带合成 cestpost 数据（病例名 ctrl_3H_小鼠1 这种），跑三种用法并核对产物：
    1) 组内·按组  → 组数份（每组都有 trainData/trainLabel + 左/右）
    2) 组内·单个  → 病例数份
    3) 组间       → 训练集最终版\\<配对级>\\ 下 dataLeft/labelLeft + dataRight/labelRight
只写系统临时目录。用法（在 cest-gui 根目录）::

    runtime\\python.exe -X utf8 CEST_Studio\\tools\\validate_prf_extract.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
from scipy.io import savemat

HERE = Path(__file__).resolve()
CG = HERE.parent.parent.parent
sys.path.insert(0, str(CG / 'CEST_Studio'))
sys.path.insert(0, str(CG / '03_Python_全链路' / '函数库'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('CEST_SETTINGS_FILE', str(Path(tempfile.gettempdir()) / 'v_prf5.json'))

from app.modules.m5_prf import (PrfExtractModule, TASK_IN, TASK_BET,   # noqa: E402
                                UNIT_GROUP, UNIT_ONE)
from jobs.ctx import JobContext                                        # noqa: E402


def make_fake(root: Path, names, n_slice=1, n_roi=2, nxy=6, nfreq=31):
    w = np.linspace(-4.5, 4.5, nfreq)
    for nm in names:
        stage = root / nm / 'full_interp_0803'
        stage.mkdir(parents=True, exist_ok=True)
        roi = np.empty((n_roi, 1), dtype=object)
        for k in range(n_roi):
            roi[k, 0] = np.arange(k * 6 + 1, k * 6 + 7).reshape(-1, 1)
        savemat(str(stage / 'ROI_total.mat'), {'ROI_total': roi})
        for s in range(n_slice):
            d = stage / f'slice{s + 1}' / 'Self_corr'
            d.mkdir(parents=True, exist_ok=True)
            rs = np.random.RandomState(abs(hash(nm)) % 9999)
            savemat(str(d / 'V_exp.mat'), {'V_exp': 100 + rs.randn(nxy, nxy, nfreq) * 5})
            savemat(str(d / 'S0.mat'), {'S0': 100 + rs.randn(nxy, nxy) * 5})
            savemat(str(d / 'w_offset.mat'), {'w_offset': w.reshape(1, -1)})
    return root


def _base_params(src, out, **over):
    p = dict(src=str(src), out_dir=str(out), mode='cestpost（③ 后处理产物）',
             task=TASK_IN, unit=UNIT_GROUP, sep='_', region_roi_idx='',
             norm='不除 S0（原始信号）', epsilon=1e-8, bar_plot=False, bar_tag='',
             bar_range='clinical（3T：±1.2~4.5）', level1='ctrl,model', level2='3H,1W',
             max_animal=0, pick_rule='min', filename='', stage_prefix='full_interp',
             sub_path='', ratio_list='', reset_savepaths=False)
    p.update(over)
    return p


def _run(mod, src, out, **over):
    ctx = JobContext(proj=str(out))
    ctx.bind(sink=lambda s: None, progress=lambda c, t, x: None)
    with redirect_stdout(io.StringIO()):
        return mod.run(_base_params(src, out, **over), ctx)


def main() -> int:
    ok = []
    tmp = Path(tempfile.mkdtemp(prefix='v_prf5_'))
    names = [f'{g}_{t}_小鼠{n}' for g in ('ctrl', 'model') for t in ('3H', '1W')
             for n in (1, 2)]
    make_fake(tmp / 'data', names)
    mod = PrfExtractModule()

    print('=== 1) 组内·按组（应 4 份）===')
    r1 = _run(mod, tmp / 'data', tmp / 'o1', task=TASK_IN, unit=UNIT_GROUP)
    d1 = tmp / 'o1' / '组内_按组'
    subs1 = sorted(x.name for x in d1.iterdir() if x.is_dir())
    f1 = sorted(x.name for x in (d1 / subs1[0]).iterdir())
    print('  组:', subs1, '| groupPaths:', (d1 / 'groupPaths.mat').exists())
    ok += [len(subs1) == 4, r1['n_ok'] == 4,
           {'trainDataLeft_ROI.mat', 'trainDataRight_ROI.mat'} <= set(f1),
           (d1 / 'groupPaths.mat').exists()]

    print('=== 2) 组内·单个（应 8 份）===')
    r2 = _run(mod, tmp / 'data', tmp / 'o2', task=TASK_IN, unit=UNIT_ONE)
    d2 = tmp / 'o2' / '组内_单个'
    subs2 = sorted(x.name for x in d2.iterdir() if x.is_dir())
    f2 = sorted(x.name for x in (d2 / subs2[0]).iterdir())
    print('  个数:', len(subs2), '| resultPaths:', (d2 / 'resultPaths.mat').exists())
    ok += [len(subs2) == 8, {'trainDataLeft_ROI1.mat', 'trainDataRight_ROI2.mat'} <= set(f2),
           (d2 / 'resultPaths.mat').exists()]

    print('=== 3) 组间（应 2 个配对级目录 3H/1W）===')
    r3 = _run(mod, tmp / 'data', tmp / 'o3', task=TASK_BET)
    d3 = tmp / 'o3' / '组间' / '训练集最终版'
    ng = tmp / 'o3' / '组间' / 'newGroupPaths.mat'
    subs3 = sorted(x.name for x in d3.iterdir() if x.is_dir()) if d3.exists() else []
    files3 = sorted(x.name for x in (d3 / subs3[0]).iterdir()) if subs3 else []
    print('  配对级:', subs3, '| 文件:', files3)
    print('  newGroupPaths:', ng.exists())
    ok += [subs3 == ['1W', '3H'],
           {'trainDataLeft_ROI.mat', 'trainLabelLeft.mat',
            'trainDataRight_ROI.mat', 'trainLabelRight.mat', 'w_offset.mat'} <= set(files3),
           ng.exists()]

    # 组间 左/右 是否真的“奇对奇、偶对偶”：左右体素数应各等于两组成员左/右之和
    from cest_io import load_mat
    if subs3:
        L = np.asarray(load_mat(d3 / subs3[0] / 'trainDataLeft_ROI.mat')['dataLeft'])
        yl = np.asarray(load_mat(d3 / subs3[0] / 'trainLabelLeft.mat')['labelLeft'])
        ok += [L.shape[1] == 31, yl.size == L.shape[0],
               set(np.unique(yl.astype(int)).tolist()) == {0, 1}]
        print(f'  左: dataLeft {L.shape}, labelLeft 1/0 = '
              f'{int((yl==1).sum())}/{int((yl==0).sum())}')

    print('\n结论：' + ('⑤ PRF 提取（组内/组间）自检全过' if all(ok) else '有项未过'))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(ok) else 1


if __name__ == '__main__':
    raise SystemExit(main())
