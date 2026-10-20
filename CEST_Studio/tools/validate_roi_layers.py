# -*- coding: utf-8 -*-
"""validate_roi_layers.py —— ⑧ 逐层 ROI（3D）全链路自检。

覆盖：
  1) m_roi.build_roi_total_layers：2D 共用 ROI + 逐层 ROI 的 base / 逐层索引；
  2) m_roi._cell_of_layer_arrays 落盘 → cest_roi.load_roi_layers / load_roi_total(layer=) 读回；
  3) prf_extract._layer_of_dir / _roi_at_layer：⑤ 按 sliceN 自动认层、取该层 ROI；
  4) cest_roi.run_curves(layer=)：本层取对应 ROI；该层没画的自动跳过 / 全无则报错；
  5) mask_canvas_dialog._split_layers + MaskTarget.as_array：画布 3D 往返一致。

跑法：runtime\\python.exe CEST_Studio\\tools\\validate_roi_layers.py
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = Path(__file__).resolve()
STUDIO = HERE.parent.parent                 # CEST_Studio
ROOT = STUDIO.parent                        # cest-gui
CORE = ROOT / '03_Python_全链路' / '函数库'
for p in (str(STUDIO), str(CORE)):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import warnings

warnings.filterwarnings('ignore')            # 随机数据拟合的 OptimizeWarning 忽略

import cest_post as P
import cest_roi as CR
import prf_extract as PE
from app.modules.m_roi import build_roi_total_layers, _cell_of_layer_arrays
from app.widgets.mask_canvas import MaskTarget
from app.dialogs.mask_canvas_dialog import _split_layers

OK = []
BAD = []


def check(name, cond, extra=None):
    tail = '' if extra is None else ('  ' + str(extra))
    (OK if bool(cond) else BAD).append(name + tail)
    print(('  [ok] ' if bool(cond) else '  [!!] ') + name + tail)


X, Y, L = 8, 9, 5
SHAPE = (X, Y, L)
np.random.seed(0)
tmp = Path(tempfile.mkdtemp(prefix='roi_layers_'))

# ---------------------------------------------------------------- 1. 构造
th3 = np.ones(SHAPE, bool)
r2d = np.zeros((X, Y), bool)
r2d[1, 1:4] = True                                   # 2D 共用：3 体素
r3d = np.zeros(SHAPE, bool)
for z in range(L):                                   # 逐层：第 z 层挪一个像素
    r3d[z, z, z] = True
r_only3 = np.zeros(SHAPE, bool)                      # 纯逐层：只有第 3 层画了
r_only3[4, 4, 2] = True

print('== 1. build_roi_total_layers ==')
base, layers, uni = build_roi_total_layers([r2d, r3d, r_only3], th3, L)
check('2D ROI 判为 uniform / 无逐层表', uni[0] is True and layers[0] is None)
check('2D ROI base = 3 体素', base[0].size == 3, base[0])
check('逐层 ROI 判为非 uniform', uni[1] is False and len(layers[1]) == L)
want = [int(np.ravel_multi_index((z, z), (X, Y), order='F') + 1) for z in range(L)]
got = [int(e[0]) if e is not None and e.size == 1 else None for e in layers[1]]
check('逐层 ROI 每层索引 = 该层像素(1-based)', got == want, (got, want))
check('逐层 ROI base = 各层并集(L 个体素)', base[1].size == L, base[1].size)
check('纯逐层 ROI：只有第 3 层有',
      layers[2][2] is not None and all(layers[2][i] is None for i in (0, 1, 3, 4)))

# ---------------------------------------------------------------- 2. 落盘/读回
print('== 2. 落盘 → 读回 ==')
f = tmp / 'ROI_total.mat'
P.savemat(str(f), {'ROI_total': P._cell_of_arrays(base),
                   'ROI_total_layers': _cell_of_layer_arrays(layers, L)})
g2 = CR.load_roi_total(str(f))
check('load_roi_total(不带 layer) 回退 2D 口径',
      g2[0].size == 3 and np.array_equal(np.sort(g2[0]), np.sort(base[0] - 1)),
      (g2[0],))
lay = CR.load_roi_layers(str(f))
check('load_roi_layers：2D ROI 给空表（回退标志）', lay is not None and len(lay[0]) == 0)
check('load_roi_layers：逐层 ROI 给 L 层', len(lay[1]) == L)
for z in (0, 2, 4):
    gz = CR.load_roi_total(str(f), layer=z + 1)
    check(f'第 {z + 1} 层：逐层 ROI 取该层',
          np.array_equal(np.sort(gz[1]), np.array([want[z] - 1])), gz[1])
    check(f'第 {z + 1} 层：2D ROI 回退共用', np.array_equal(np.sort(gz[0]), np.sort(base[0] - 1)))
g1 = CR.load_roi_total(str(f), layer=1)
check('第 1 层：纯逐层 ROI(未画) → 空索引', g1[2].size == 0, g1[2])
check('第 3 层：纯逐层 ROI(已画) → 1 个体素', CR.load_roi_total(str(f), layer=3)[2].size == 1)

# ---------------------------------------------------------------- 3. ⑤ 取层
print('== 3. prf_extract 按 sliceN 认层 ==')
check('slice3/Self_corr → 第 3 层', PE._layer_of_dir(r'C:\x\slice3\Self_corr') == 3)
check('slice10 → 第 10 层', PE._layer_of_dir(r'D:\a\b\slice10') == 10)
check('认不出 → None', PE._layer_of_dir(r'D:\a\full_interp_0803') is None)
for z in (0, 2, 4):
    rr = PE._roi_at_layer(str(f), X * Y, z + 1)
    check(f'⑤ 第 {z + 1} 层：逐层 ROI 进对应层',
          np.array_equal(np.sort(rr[1]), np.array([want[z] - 1])), rr[1])
    check(f'⑤ 第 {z + 1} 层：2D ROI 回退', np.array_equal(np.sort(rr[0]), np.sort(base[0] - 1)))
rr1 = PE._roi_at_layer(str(f), X * Y, 1)
check('⑤ 第 1 层：未画的逐层 ROI → 空（cnt=0）', rr1[2].size == 0)

# ---------------------------------------------------------------- 4. run_curves
print('== 4. run_curves(layer=) ==')
w = np.array([-3.0, -2.0, -1.0, 1.0, 2.0, 3.0])
zz = np.random.rand(X, Y, w.size) * 1e4
res = CR.run_curves(zz, None, w, None, str(f), tmp / 'curves3',
                    {'trim': False, 'save_png': False}, log=lambda s: None, layer=3)
check('run_curves(layer=3) 成功', res['meta']['n_roi'] == 3, res['meta'].get('n_roi'))
check('meta.layer 记下 3', res['meta'].get('layer') == 3, res['meta'].get('layer'))
f_only = tmp / 'ROI_total_only3.mat'
P.savemat(str(f_only), {'ROI_total': P._cell_of_arrays([base[2]]),
                        'ROI_total_layers': _cell_of_layer_arrays([layers[2]], L)})
_raised = False
try:
    CR.run_curves(zz, None, w, None, str(f_only), tmp / 'curves1',
                  {'trim': False, 'save_png': False}, log=lambda s: None, layer=1)
except ValueError as e:
    _raised = '没有可用的 ROI' in str(e)
check('第 1 层没画 → 拦下报错', _raised)

# ---------------------------------------------------------------- 5. 画布往返
print('== 5. 画布 3D 往返（_split_layers + as_array）==')
tg = MaskTarget('ROI1', kind='roi')
_split_layers(tg, r3d, L)
back = np.asarray(tg.as_array(L))
check('逐层 ROI 原样往返 (X,Y,L)', back.shape == SHAPE and np.array_equal(back, r3d), back.shape)
check('逐层 ROI 被拆成「共用 + 逐层覆盖」', len(tg.per_layer) >= 1,
      f'shared={"None" if tg.shared is None else int(np.asarray(tg.shared).sum())}'
      f' per_layer={sorted(tg.per_layer)}')
tg2 = MaskTarget('ROI2', kind='roi')
_split_layers(tg2, np.repeat(r2d[:, :, None], L, axis=2), L)
b2 = np.asarray(tg2.as_array(L))
check('全层一样的 3D → 还原成 2D 共用', b2.shape == (X, Y) and np.array_equal(b2, r2d), b2.shape)
tg3 = MaskTarget('ROI3', kind='roi')
tg3.set(r2d)                                   # 直接 set 2D
tg3.set(np.where(r3d[:, :, 2], r3d[:, :, 2], tg3.shared), layer=2, per_layer=True)
b3 = np.asarray(tg3.as_array(L))
check('2D 共用 + 单层覆盖 → as_array 出 3D', b3.shape == SHAPE, b3.shape)
check('覆盖层 = 2D 那张 + 本层覆盖',
      np.array_equal(b3[:, :, 2], np.logical_or(r2d, r3d[:, :, 2])))
check('其余层 = 2D 共用那张', np.array_equal(b3[:, :, 0], r2d) and np.array_equal(b3[:, :, 4], r2d))

# -------------------------------------------------- 6. ③ 复用 ROI 文件时别丢逐层表
print('== 6. cest_post.roi_plot_song 复用外部文件 ==')
src2 = tmp / 'src'; src2.mkdir(exist_ok=True)
dst2 = tmp / 'dst'; dst2.mkdir(exist_ok=True)
shutil.copy2(str(f), str(src2 / 'ROI_total.mat'))
P.roi_plot_song(3, ['ROI1', 'ROI2', 'ROI3'], th3[:, :, 0].astype(float), dst2,
                np.ones((X, Y)), roi_total_file=str(src2 / 'ROI_total.mat'), verbose=False)
got2 = P.load_mat(str(dst2 / 'ROI_total.mat'), ['ROI_total', 'ROI_total_layers'])
check('复用拷贝后 ROI_total_layers 还在', 'ROI_total_layers' in got2, sorted(got2))

# ---------------------------------------------------------------- 汇总
print('\n通过 %d / 失败 %d   （临时目录 %s）' % (len(OK), len(BAD), tmp))
if BAD:
    print('失败项：')
    for b in BAD:
        print('  - ' + b)
    sys.exit(1)
print('EXIT 0')
sys.exit(0)
