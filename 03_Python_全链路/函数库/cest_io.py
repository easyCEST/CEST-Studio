# -*- coding: utf-8 -*-
"""
cest_io.py —— PRF Python 工具集：mat 读写 / 文件定位 / 训练集统一保存
==========================================================================

对接关系
--------
前置（MATLAB「CEST完整代码包」的 runCESTpost.m）输出是三级布局::

    <CEST_post>/<病例>/full_interp_0803/                  ROI_total.mat / mask.mat / *_4D.mat
    <CEST_post>/<病例>/full_interp_0803/sliceN/           S0.mat / w_offset.mat / x_as.mat
    <CEST_post>/<病例>/full_interp_0803/sliceN/Self_corr/ V_exp.mat / V_as.mat …

下游（本工具集的 prf_analysis.py）读固定格式::

    <out>/trainData.mat    (N × F)  每行一个体素的 CEST 谱
    <out>/trainLabel.mat   (N × 1)  1/0 标签
    <out>/w_offset.mat     (1 × F)  频偏 ppm
    <out>/savepaths.mat    pathInfo  (M × 1 cell) 结果目录清单

关于 mat 格式
-------------
* 老数据（`结果路径/结果N/.../slice1`）是 **v7**，scipy 直接读。
* 新管线 runCESTpost.m 用 ``-v7.3`` 存 V_exp / V_as（HDF5 userblock），
  scipy 读不了，需要 **h5py**。本模块自动分辨两种格式。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from scipy.io import loadmat, savemat

# ---------------------------------------------------------------------------
# 候选文件名（大小写不敏感）
# ---------------------------------------------------------------------------
DATA_NAMES = ('V_exp.mat', 'V_as.mat')
S0_NAMES = ('S0.mat', 'S0m.mat', 's0.mat')
ROI_NAMES = ('ROI_total.mat', 'ROI.mat', 'roi_total.mat')
W_NAMES = ('w_offset.mat',)


# ===========================================================================
# 一、mat 读写
# ===========================================================================
def is_v73(path) -> bool:
    """判断是不是 MATLAB -v7.3（HDF5）文件。"""
    path = Path(path)
    with open(path, 'rb') as f:
        head = f.read(16)
        if head.startswith(b'MATLAB 5.0'):
            return False
        if head[:4] == b'\x89HDF':
            return True
        f.seek(512)                      # v7.3 的 HDF5 签名在 userblock 后面
        return f.read(4) == b'\x89HDF'


def _h5_value(f, ds):
    """把 h5py 数据集转成 numpy / cell / 字符串（注意轴序要翻转）。"""
    cls = ds.attrs.get('MATLAB_class', b'')
    if isinstance(cls, str):
        cls = cls.encode()

    if cls == b'cell':
        refs = np.asarray(ds)
        out = np.empty(refs.shape, dtype=object)
        flat = out.ravel()
        for i, r in enumerate(np.asarray(refs).ravel()):
            flat[i] = _h5_value(f, f[r])
        return out

    if cls == b'char':
        arr = np.array(ds)
        if arr.dtype.kind in 'ui':
            return ''.join(chr(int(c)) for c in arr.ravel())
        return ''.join(str(c) for c in arr.ravel())

    arr = np.array(ds)
    if arr.dtype.kind in 'US':
        return arr
    return arr.T          # MATLAB 列优先 → h5py 轴序是反的


def _load_v73(path) -> dict:
    import h5py
    out = {}
    with h5py.File(str(path), 'r') as f:
        for k in f.keys():
            obj = f[k]
            if isinstance(obj, h5py.Dataset):
                out[k] = _h5_value(f, obj)
    return out


def load_mat(path, names=None) -> dict:
    """
    读 .mat，自动兼容 v7 / v7.3。

    Parameters
    ----------
    path  : mat 文件路径
    names : None → 返回全部变量；字符串或列表 → 只返回命中的（大小写不敏感）

    Returns
    -------
    dict，键为请求的名字；找不到的键不会出现。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'找不到文件：{path}')

    if is_v73(path):
        raw = _load_v73(path)
    else:
        raw = {k: v for k, v in loadmat(str(path)).items() if not k.startswith('__')}

    if names is None:
        return raw
    if isinstance(names, str):
        names = [names]
    lower = {k.lower(): k for k in raw}
    out = {}
    for n in names:
        k = lower.get(str(n).lower())
        if k is not None:
            out[n] = raw[k]
    return out


def load_first(path, names):
    """按候选名顺序取第一个命中的变量；都找不到返回 None。"""
    got = load_mat(path, names)
    for n in names:
        if n in got:
            return got[n]
    return None


# ===========================================================================
# 一点五、轴序统一（★全链路铁律）
# ---------------------------------------------------------------------------
# 原始采集是「先固定一个层面、把该层所有频偏采完，再进下一层」，
# 所以本工程**所有 4D 数组一律用「层优先」排布**：
#
#     [X, Y, nLayer, nFreq]        dim2 = 层（外层），dim3 = 频偏（内层）
#
# 即「先层后频率」。对应文件里的 `axis_order = 'layer_freq'`。
# 旧文件可能是反过来的 [X, Y, nFreq, nLayer]，下面两个函数负责统一。
# ===========================================================================
AXIS_LAYER_FIRST = 'layer_freq'
AXIS_FREQ_FIRST = 'freq_layer'


def to_layer_first_4d(arr, n_layer, n_freq):
    """
    把 4D 数据统一成 **[X, Y, nLayer, nFreq]**（层优先）。

    已经是层优先 → 原样返回；是旧序 [X,Y,nFreq,nLayer] → ``swapaxes(2,3)``（视图，不复制）。
    """
    a = np.asarray(arr)
    if a.ndim != 4:
        raise ValueError(f'期望 4D 数组，得到 {a.shape}')
    if a.shape[2] == n_layer and a.shape[3] == n_freq:
        return a
    if a.shape[2] == n_freq and a.shape[3] == n_layer:
        return np.swapaxes(a, 2, 3)
    raise ValueError(f'4D 形状 {a.shape} 与 (nLayer={n_layer}, nFreq={n_freq}) 不匹配：'
                     f'既不是 [X,Y,层,频] 也不是 [X,Y,频,层]')


def to_layer_first_3d(arr, n_layer):
    """
    把「每层一份」的 3D 数据统一成 **[X, Y, nLayer]**。

    支持 (X,Y,nLayer) 与旧序 (X,Y,1,nLayer)。都不匹配时原样返回（不报错）。
    """
    a = np.asarray(arr)
    if a.ndim == 3:
        return a
    if a.ndim == 4 and a.shape[2] == 1:
        return a[:, :, 0, :]
    if a.ndim == 4 and a.shape[3] == 1:
        return a[:, :, :, 0]
    return a


# ===========================================================================
# 二、文件定位（对接前置包的三级布局）
# ===========================================================================
def search_dirs(any_path):
    """给定三级布局里的任意一级，返回要搜索的目录顺序（本级→Self_corr→上一级→上两级）。"""
    p = Path(any_path)
    if p.is_file():
        p = p.parent
    cands = [p, p / 'Self_corr', p.parent, p.parent.parent, p.parent / 'Self_corr']
    out = []
    for c in cands:
        if c is None:
            continue
        s = str(c)
        if s and s not in out:
            out.append(s)
    return [Path(s) for s in out]


def find_files(any_path, data=None, s0=None, roi=None, w=None, verbose=False) -> dict:
    """
    在 CEST 后处理输出里定位 PRF 需要的四个文件。

    传 ``…/sliceN`` 或 ``…/sliceN/Self_corr`` 都行；
    「所有文件堆在同一目录」的老数据也能用。

    Returns
    -------
    dict: ``{'data': Path|None, 's0': …, 'roi': …, 'w': …}``
    """
    dirs = search_dirs(any_path)

    def _find(names, manual):
        if manual:
            return Path(manual)
        for d in dirs:
            if not d.is_dir():
                continue
            try:
                files = {f.name.lower(): f for f in os.scandir(d) if f.is_file()}
            except OSError:
                continue
            for n in names:
                f = files.get(n.lower())
                if f is not None:
                    return Path(f.path)
        return None

    res = {
        'data': _find(DATA_NAMES, data),
        's0':   _find(S0_NAMES, s0),
        'roi':  _find(ROI_NAMES, roi),
        'w':    _find(W_NAMES, w),
    }
    res['ok'] = all(res[k] is not None for k in ('data', 's0', 'roi', 'w'))

    if verbose:
        print(f'[find_files] 起点：{any_path}')
        for k, label in (('data', '数据    '), ('s0', 'S0      '),
                         ('roi', 'ROI     '), ('w', 'w_offset')):
            print(f'  {label}: {res[k] if res[k] else "(未找到)"}')
        if not res['ok']:
            print('  [注意] 有文件没找到：前置包是不是还没跑完这一层？')
    return res


# ===========================================================================
# 三、训练集统一保存（+ savepaths.mat 路径清单）
# ===========================================================================
def _cellstr(paths):
    """把字符串列表装成 MATLAB 能识别的 cell 字符串数组 (M×1)。"""
    arr = np.empty((len(paths), 1), dtype=object)
    for i, s in enumerate(paths):
        arr[i, 0] = np.array(str(s))
    return arr


def read_path_info(savepaths_file):
    """读 savepaths.mat 里的 pathInfo，返回目录字符串列表。"""
    savepaths_file = Path(savepaths_file)
    if not savepaths_file.exists():
        return []
    got = load_mat(savepaths_file, ['pathInfo', 'groupPaths', 'resultPaths',
                                    'newGroupPaths', 'savepaths'])
    key = next((k for k in ('pathInfo', 'groupPaths', 'resultPaths',
                            'newGroupPaths', 'savepaths') if k in got), None)
    if key is None:
        return []
    raw = got[key]
    out = []
    for item in np.asarray(raw, dtype=object).ravel():
        if isinstance(item, (str, np.str_)):
            out.append(str(item))
        else:
            a = np.asarray(item)
            if a.dtype.kind == 'U':
                out.append(''.join(a.ravel().astype(str).tolist()))
            elif a.dtype.kind == 'S':
                out.append(b''.join(a.ravel()).decode('utf-8', 'ignore'))
            else:
                out.append(str(a))
    return out


def save_set(out_dir, train_data, train_label, w_offset=None,
             add_to_path_list=True, path_list_file=None, verbose=True) -> dict:
    """
    统一保存训练集（全项目统一的文件命名规范）。

    * trainData.mat  (N×F)
    * trainLabel.mat (N×1)
    * w_offset.mat   (1×F)
    * 可选：把 out_dir 追加进 ``savepaths.mat`` 的 pathInfo（去重）
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    train_data = np.asarray(train_data)
    train_label = np.asarray(train_label).reshape(-1, 1)
    savemat(str(out_dir / 'trainData.mat'), {'trainData': train_data})
    savemat(str(out_dir / 'trainLabel.mat'), {'trainLabel': train_label})

    if w_offset is not None:
        savemat(str(out_dir / 'w_offset.mat'),
                {'w_offset': np.asarray(w_offset).reshape(1, -1)})

    path_info = []
    if add_to_path_list:
        if path_list_file is None:
            path_list_file = out_dir.parent / 'savepaths.mat'
        path_list_file = Path(path_list_file)
        path_info = read_path_info(path_list_file)
        cur = str(out_dir)
        if cur not in path_info:
            path_info.append(cur)
        savemat(str(path_list_file), {'pathInfo': _cellstr(path_info)})

    n1 = int(np.sum(train_label == 1))
    n0 = int(np.sum(train_label == 0))
    rec = {'out_dir': str(out_dir), 'n_voxel': train_data.shape[0],
           'n_freq': train_data.shape[1], 'n_label1': n1, 'n_label0': n0,
           'path_info': path_info,
           'path_list_file': str(path_list_file) if add_to_path_list else None}
    if verbose:
        print(f'[save_set] {rec["n_voxel"]} 体素 × {rec["n_freq"]} 频偏'
              f'（标签1:{n1} / 标签0:{n0}）→ {out_dir}')
    return rec
