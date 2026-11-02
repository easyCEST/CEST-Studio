# -*- coding: utf-8 -*-
"""tools/validate_chain.py —— 数据链自检（detect.chain）全覆盖。

造全套产物 → stage_status 应全部识别；③ 两种布局 + ④ 子目录 + ② 子文件夹都认。
全程只写系统临时目录。

用法（在 cest-gui 根目录）::

    runtime\\python.exe -X utf8 CEST_Studio\\tools\\validate_chain.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import savemat

CG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CG / 'CEST_Studio'))
sys.path.insert(0, str(CG / '03_Python_全链路' / '函数库'))
from detect import chain  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix='chain_val_'))
OK = True
PER_CASE = ('m1_recon', 'm_roi', 'm3_post', 'm4_ld', 'm2_display')
ALL = PER_CASE + ('m5_prf', 'm6_prf_analysis')


def sv(p, d):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    savemat(str(p), d)


def wr(p):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'x')


def make_full(name, post_sub=False):
    sv(tmp / '01_重建' / name / 's0.mat', {'x': np.zeros(3)})
    sv(tmp / '01_重建' / name / 'ssat.mat', {'x': np.zeros(3)})
    sv(tmp / '01_重建' / name / 'w_offset.mat', {'x': np.zeros(3)})
    sv(tmp / '00_掩模' / name / 'Mask.mat', {'Mask': np.ones((4, 4))})
    if post_sub:
        wr(tmp / '03_CEST后处理' / name / 'full_interp' / 'slice1' / 'post_meta.json')
    else:
        wr(tmp / '03_CEST后处理' / name / 'post_meta.json')
    sv(tmp / '04_逐体素LD' / name / '1' / 'voxel_LD.mat', {'voxel_ld': np.ones((4, 4, 1, 2))})
    sv(tmp / '05_PRF提取' / '组内_按组' / 'groupPaths.mat',
       {'groupPaths': np.array(['a'], dtype=object)})
    wr(tmp / '06_PRF分析' / 'prf_analysis_meta.json')
    wr(tmp / '02_展示' / name / '特定频率' / 'ST 特定频率_combined.png')


def expect(tag, st, keys, want):
    bad = [k for k in keys if bool(st[k]) != want]
    print(f'  {tag}: ' + ('OK' if not bad else f'FAIL 不一致 {bad}') + f'  {st}')
    return not bad


print('== ③ post_meta 在病例目录 ==')
make_full('c1')
OK &= expect('逐病例', chain.stage_status(str(tmp), 'c1'), ALL, True)
OK &= expect('项目级', chain.stage_status(str(tmp), ''), ALL, True)

print('== ③ post_meta 在子目录 也要认 ==')
make_full('c2', post_sub=True)
OK &= expect('c2', chain.stage_status(str(tmp), 'c2'), ALL, True)

print('== 空病例：逐病例阶段应 False（⑤⑥ 项目级本就 True）==')
(tmp / '03_CEST后处理' / 'empty').mkdir(parents=True, exist_ok=True)
OK &= expect('empty', chain.stage_status(str(tmp), 'empty'), PER_CASE, False)

print('EXIT', 0 if OK else 1)
sys.exit(0 if OK else 1)
