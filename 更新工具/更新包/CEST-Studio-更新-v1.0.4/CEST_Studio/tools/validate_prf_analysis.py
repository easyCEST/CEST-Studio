# -*- coding: utf-8 -*-
"""tools/validate_prf_analysis.py —— 一键验证 ⑥ PRF 分析（3 模式 × 2 样式 + 自检）。

全程只写系统临时目录，不碰用户数据盘。

流程：
    造 ⑤ 的三套假产物（组内_按组 / 组内_单个 / 组间）
    → 组内·仅均值        （run_compare 等价出口，图内 1W/3H/5W 三柱）
    → 组内·均值±SD       （误差棒）
    → 单个对比·仅均值    （图内每只鼠一根柱）
    → 组间·仅均值        （左脑/右脑各一张，图内 1W/3H/5W）
    → 自检 png / xlsx / meta 都出得来 + 模块注册 / 导航顺序

用法（在 cest-gui 根目录）::

    runtime\\python.exe -X utf8 CEST_Studio\\tools\\validate_prf_analysis.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import savemat

HERE = Path(__file__).resolve()
CG = HERE.parent.parent.parent
for p in (str(CG / 'CEST_Studio'), str(CG / '03_Python_全链路' / '函数库')):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('CEST_SETTINGS_FILE',
                      str(Path(tempfile.gettempdir()) / 'cest_prf6_validate_settings.json'))

W = np.round(np.linspace(-5, 5, 21), 1)          # 21 个频偏，-5..5


class Ctx:
    def __init__(self, proj):
        self.proj = str(proj)

    def log(self, *a):
        print('   ', *a)

    def progress(self, *a):
        pass

    def check_cancel(self):
        pass


def make_train(d: Path, n_vox=240, seed=0, w=None) -> Path:
    """⑤组内 产物：trainData.mat / trainLabel.mat / w_offset.mat。"""
    d.mkdir(parents=True, exist_ok=True)
    w = W if w is None else w
    rs = np.random.RandomState(seed)
    X = 100 + rs.randn(n_vox, w.size) * 5
    y = np.r_[np.ones(n_vox // 2), np.zeros(n_vox - n_vox // 2)]
    savemat(str(d / 'trainData.mat'), {'trainData': X.astype(np.float32)})
    savemat(str(d / 'trainLabel.mat'), {'trainLabel': y.reshape(-1, 1)})
    savemat(str(d / 'w_offset.mat'), {'w_offset': w.reshape(1, -1)})
    return d


def make_lr(d: Path, nL=120, nR=110, seed=0, w=None) -> Path:
    """⑤组间 产物：trainDataLeft_ROI(dataLeft) + trainLabelLeft(labelLeft) + 右同 + w_offset。"""
    d.mkdir(parents=True, exist_ok=True)
    w = W if w is None else w
    rs = np.random.RandomState(seed)
    XL = 100 + rs.randn(nL, w.size) * 5
    XR = 100 + rs.randn(nR, w.size) * 5
    yL = np.r_[np.ones(nL // 2), np.zeros(nL - nL // 2)]     # 1=ctrl / 0=model
    yR = np.r_[np.ones(nR // 2), np.zeros(nR - nR // 2)]
    savemat(str(d / 'trainDataLeft_ROI.mat'), {'dataLeft': XL.astype(np.float32)})
    savemat(str(d / 'trainLabelLeft.mat'), {'labelLeft': yL.reshape(-1, 1)})
    savemat(str(d / 'trainDataRight_ROI.mat'), {'dataRight': XR.astype(np.float32)})
    savemat(str(d / 'trainLabelRight.mat'), {'labelRight': yR.reshape(-1, 1)})
    savemat(str(d / 'w_offset.mat'), {'w_offset': w.reshape(1, -1)})
    return d


def make_savepaths(f: Path, key: str, dirs) -> None:
    cells = np.empty((1, len(dirs)), dtype=object)
    for i, s in enumerate(dirs):
        cells[0, i] = str(s)
    savemat(str(f), {key: cells, 'pathInfo': cells})


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='prf6_'))
    proj = tmp / 'PROJ'
    p5 = proj / '05_PRF提取'
    p5.mkdir(parents=True, exist_ok=True)

    # ---- ⑤ 组内_按组：ctrl/model × 3H/1W/5W ----
    g_grp = p5 / '组内_按组'
    grp_dirs = []
    for cond in ('ctrl', 'model'):
        for t in ('3H', '1W', '5W'):
            grp_dirs.append(str(make_train(g_grp / f'{cond}_{t}',
                                           seed=hash((cond, t)) % 100)))
    make_savepaths(g_grp / 'groupPaths.mat', 'groupPaths', grp_dirs)

    # ---- ⑤ 组内_单个：每只鼠一份 ----
    g_one = p5 / '组内_单个'
    one_dirs = []
    for cond in ('ctrl', 'model'):
        for t in ('3H', '1W'):
            for m in (1, 2, 3):
                one_dirs.append(str(make_train(g_one / f'{cond}_{t}_小鼠{m}',
                                               seed=hash((cond, t, m)) % 100)))
    make_savepaths(g_one / 'resultPaths.mat', 'resultPaths', one_dirs)

    # ---- ⑤ 组间：训练集最终版\<配对级> ----
    g_bet = p5 / '组间' / '训练集最终版'
    bet_dirs = []
    for t in ('3H', '1W', '5W'):
        bet_dirs.append(str(make_lr(g_bet / t, seed=hash(t) % 100)))
    make_savepaths(p5 / '组间' / 'newGroupPaths.mat', 'newGroupPaths', bet_dirs)

    from app.modules import registry
    from app.modules.m6_prf_analysis import (PrfAnalysisModule, MODE_GROUP,
                                             MODE_SINGLE, MODE_BETWEEN,
                                             STYLE_MEAN, STYLE_SD)

    ok = True
    m = PrfAnalysisModule()
    base = dict(savepaths='', style=STYLE_MEAN, n_repeats=3,
                range='clinical（3T：±1.2~4.5）', water_left=-0.5, water_right=0.5,
                edge_left=-3.7, edge_right=3.6, merge=True, how='sum',
                n_permute=3, n_estimators=30, max_depth=5, min_samples_leaf=2,
                random_state=42, dpi=100, fmt='png')

    def check(files, tag):
        nonlocal ok
        for f in files:
            if not f.exists():
                ok = False
                print('   ✗ 缺产物', f)
        print(f'   {tag} 产物：', sorted(x.name for x in files[0].parent.iterdir()))

    # ---------- 组内 · 仅均值 ----------
    out = tmp / 'out_ingroup'
    p1 = dict(base, mode=MODE_GROUP, out_dir=str(out))
    print('== 组内·仅均值 validate:', m.validate(p1, Ctx(proj)) or 'OK')
    m.run(p1, Ctx(proj))
    d = out / '组内'
    check([d / 'ctrl_频率重要性.png', d / 'ctrl_频率重要性.xlsx',
           d / 'model_频率重要性.png', d / 'model_频率重要性.xlsx',
           d / 'prf_analysis_meta.json'], '组内·仅均值')

    # ---------- 组内 · 均值±SD ----------
    out2 = tmp / 'out_ingroup_sd'
    p2 = dict(base, mode=MODE_GROUP, style=STYLE_SD, out_dir=str(out2))
    m.run(p2, Ctx(proj))
    check([out2 / '组内' / 'ctrl_频率重要性.png'], '组内·均值±SD')

    # ---------- 单个对比 · 仅均值 ----------
    out3 = tmp / 'out_single'
    p3 = dict(base, mode=MODE_SINGLE, out_dir=str(out3))
    m.run(p3, Ctx(proj))
    check([out3 / '单个对比' / 'ctrl_3H_频率重要性.png',
           out3 / '单个对比' / 'ctrl_1W_频率重要性.xlsx',
           out3 / '单个对比' / 'model_3H_频率重要性.png'], '单个对比')

    # ---------- 组间 · 仅均值 ----------
    out4 = tmp / 'out_between'
    p4 = dict(base, mode=MODE_BETWEEN, out_dir=str(out4))
    print('== 组间 validate:', m.validate(p4, Ctx(proj)) or 'OK')
    m.run(p4, Ctx(proj))
    check([out4 / '组间' / '左脑_频率重要性.png', out4 / '组间' / '左脑_频率重要性.xlsx',
           out4 / '组间' / '右脑_频率重要性.png', out4 / '组间' / '右脑_频率重要性.xlsx',
           out4 / '组间' / 'prf_analysis_meta.json'], '组间')

    # ---------- 注册 / 导航 / 数据链 ----------
    order = ''.join(x.badge for x in registry.ALL)
    print('== 导航:', order)
    if order != '⓪①⑧③④⑤⑥②⑦':
        ok = False
        print('   ✗ 导航顺序不对')
    from detect import chain
    if 'm6_prf_analysis' not in registry.BY_ID or not chain.resolve('', 'm6_prf_analysis').ready:
        ok = False
        print('   ✗ ⑥ 注册/数据链异常')

    print('\nEXIT', 0 if ok else 1)
    if ok:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print('（保留临时目录排查）', tmp)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
