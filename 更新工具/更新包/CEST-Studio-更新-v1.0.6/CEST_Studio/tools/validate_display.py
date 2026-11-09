# -*- coding: utf-8 -*-
"""tools/validate_display.py —— ② 多层图谱展示（展示类型四选一）自检。

全程只写系统临时目录（合成单层 & 多层病例），不碰用户数据盘。

覆盖：
    自动（单层→全频率）、固定频偏（逐层）、固定层（全频率）、特定频率（单层 1 张 / 多层每频率 1 张）
    + APTW 在按频率展示时被拦下
    + 逐子图标题 = 各频点 ppm（检查 png 里的 fig title 与 json 记的 layers）

用法（在 cest-gui 根目录）::

    runtime\\python.exe -X utf8 CEST_Studio\\tools\\validate_display.py
"""
from __future__ import annotations

import os
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
                      str(Path(tempfile.gettempdir()) / 'cest_display_validate.json'))

W = np.round(np.linspace(-5, 5, 31), 1)


class Ctx:
    def __init__(self, proj):
        self.proj = str(proj)

    def log(self, *a):
        print('   ', *a)

    def progress(self, *a):
        pass

    def check_cancel(self):
        pass


def mk_case(proj: Path, name: str, Z: int, seed: int) -> str:
    c = proj / '01_重建' / name
    c.mkdir(parents=True, exist_ok=True)
    rs = np.random.RandomState(seed)
    ssat = (1e4 * np.clip(0.3 + rs.rand(40, 40, Z, W.size), 0, 1)).astype(np.float32)
    s0 = (1e4 + rs.rand(40, 40, Z) * 1000).astype(np.float32)
    savemat(str(c / 'ssat.mat'), {'ssat': ssat})
    savemat(str(c / 's0.mat'), {'s0': s0})
    savemat(str(c / 'normZ.mat'), {'normZ': (ssat / s0[:, :, :, None]).astype(np.float32)})
    savemat(str(c / 'w_offset.mat'), {'w_offset': W.reshape(1, -1)})
    m = proj / '00_掩模' / name
    m.mkdir(parents=True, exist_ok=True)
    # 单层掩模给 2D（专门覆盖 apply_mask 广播 bug）
    msk = (rs.rand(40, 40, Z) > 0.3).astype(float)
    savemat(str(m / 'Mask.mat'), {'Mask': msk[:, :, 0] if Z == 1 else msk})
    return str(c)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='disp_val_'))
    # 单层 / 多层各用独立 PROJ：② 的插值缓存按 PROJ 共享，混用会撞缓存
    proj1 = tmp / 'PROJ1'
    proj3 = tmp / 'PROJ3'
    (proj1 / '01_重建').mkdir(parents=True, exist_ok=True)
    (proj3 / '01_重建').mkdir(parents=True, exist_ok=True)
    c1 = mk_case(proj1, 'one_slice', 1, 1)          # 单层
    c3 = mk_case(proj3, 'multi_slice', 3, 2)        # 多层

    from app.modules import registry
    from app.modules.m2_display import (DisplayModule, AXIS_AUTO, AXIS_LAYER,
                                        AXIS_FREQ, AXIS_PICK)

    m = DisplayModule()
    base = dict(src='', case='', mode='ST', w=3.5, cols=5, caxis_mode='manual',
                caxis_lo='', caxis_hi='', mask_file='', use_mask=True, ld_mode='POS',
                layers='', dpi=100, layer=1, freq1=-3.5, freq2=-2.0, freq3=2.0,
                freq4=2.6, freq5=3.5)
    ok = True

    def check(tag, p, proj_dir, expect_pngs=1, subfolder=None, raise_=False):
        nonlocal ok
        try:
            r = m.run(p, Ctx(proj_dir))
        except Exception as e:                                    # noqa: BLE001
            if raise_:
                print(f'   [{tag}] 按预期拦下：{type(e).__name__}: {e}')
                return
            ok = False
            print(f'   [{tag}] ✗ 异常：{type(e).__name__}: {e}')
            return
        if raise_:
            ok = False
            print(f'   [{tag}] ✗ 本应拦下却跑通了')
            return
        pngs = [Path(x) for x in r['files'] if str(x).endswith('.png')]
        got = len(pngs)
        ex = all(x.exists() for x in pngs)
        good = (got >= expect_pngs) and ex
        ok &= good
        print(f'   [{tag}] png {got} 张  exists={ex}  {"OK" if good else "✗"}')

    print('== 自动（单层）→ 全频率 ==')
    check('自动·单层', dict(base, src=c1, axis=AXIS_AUTO), proj1)
    print('== 自动（多层）→ 固定频偏逐层 ==')
    check('自动·多层', dict(base, src=c3, axis=AXIS_AUTO), proj3)
    print('== 固定层（全频率）==')
    check('固定层全频率', dict(base, src=c1, axis=AXIS_FREQ, layer=1), proj1)
    print('== 特定频率 · 单层（1 张）==')
    check('特定频率·单层', dict(base, src=c1, axis=AXIS_PICK), proj1, expect_pngs=1)
    print('== 特定频率 · 多层（每频率 1 张 = 5 张）==')
    check('特定频率·多层', dict(base, src=c3, axis=AXIS_PICK), proj3, expect_pngs=5)
    print('== APTW × 特定频率 → 拦下 ==')
    check('APTW 拦截', dict(base, src=c1, axis=AXIS_PICK, mode='APTW'), proj1, raise_=True)

    # 注册 / 导航
    order = ''.join(x.badge for x in registry.ALL)
    if order != '⓪①⑧③④⑤⑥②⑦' or 'm2_display' not in registry.BY_ID:
        ok = False
        print('   ✗ 注册/导航异常: ' + order)

    print('\nEXIT', 0 if ok else 1)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
