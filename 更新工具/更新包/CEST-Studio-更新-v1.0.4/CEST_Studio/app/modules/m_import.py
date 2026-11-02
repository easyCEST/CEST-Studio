# -*- coding: utf-8 -*-
"""app/modules/m_import.py —— ⓪ 导入重建

把**已有的** s0 / Z 谱 / w 直接组装成 ① 的产物结构，下游（③④⑤②）开箱即用，
不用再从 DICOM 重建。

输出（`<输出根>\\01_重建\\<病例>\\`，文件名/变量名与 ① 完全一致）：
    s0.mat        变量 s0       (X,Y,层)
    normZ.mat     变量 norm_z   (X,Y,层,频)   ← 归一化 Z 谱（本模块的主产物）
    ssat.mat      变量 ssat     (X,Y,层,频) + axis_order='layer_freq'
                  （数据本身已是 Z 谱 → 与 normZ 内容相同；④ 靠 is_already_norm 自动跳过除 S0）
    w_offset.mat  变量 w_offset (1,频数) + freq_seq (频数,1)
    import_meta.json          来源文件 / 变量映射 / 形状 / 频偏来源（留痕）
b0 有就一起写，没有就跳过；原始信号（未除 S0 的 ssat）没有也不管。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from .base import Field, Module

from detect import rules as R

S0_FILES = ('s0.mat', 'S0.mat')
S0_VARS = ('s0', 'S0')
W_FILES = ('w_offset.mat', 'w.mat', 'W.mat')
W_VARS = ('w_offset', 'w', 'freq_seq', 'freq', 'W')
B0_FILES = ('b0.mat', 'B0.mat')
B0_VARS = ('b0', 'B0')
Z_VARS = ('img_interp', 'zspect_dataavecorrect', 'zspect', 'zspec', 'norm_z', 'normZ',
          'ssat', 'zdata', 'z', 'Z')


def _mat_vars(path) -> list:
    """只读 .mat 的变量名（v7 用 whosmat，v7.3 用 h5py）。"""
    from cest_io import is_v73
    p = str(path)
    try:
        if is_v73(p):
            import h5py
            with h5py.File(p, 'r') as f:
                return [k for k in f.keys() if not k.startswith('#')]
        from scipy.io import whosmat
        return [n for n, _s, _c in whosmat(p)]
    except Exception:                                            # noqa: BLE001
        return []


def _infer_w(nw: int, step: float = 0.1):
    """按频率点数推断均匀频偏：201 → −10..10，241 → −12..12。"""
    if nw < 2:
        return None
    half = (nw - 1) * step / 2
    return np.arange(-half, half + step / 2, step)[:nw]


def _prefer_same_source(zf, s0f):
    """整合式数据：若 Z 谱文件里自带 S0，就用它（比目录里另一个文件的 S0 更同源）。"""
    if zf is None or zf == s0f:
        return s0f
    try:
        if set(_mat_vars(zf)) & set(S0_VARS):
            return zf
    except Exception:                                            # noqa: BLE001
        pass
    return s0f


def _backup(path, stamp, log=print):
    path = Path(path)
    if path.exists():
        try:
            shutil.copy2(path, path.with_name(f'{path.stem}.bak_{stamp}{path.suffix}'))
            log(f'  已有 {path.name} → 备份 .bak_{stamp}')
        except OSError as e:                                     # noqa: BLE001
            log(f'  [提示] 备份失败（继续）：{e}')


class ImportModule(Module):
    id = 'm_import'
    badge = '⓪'
    title = '导入重建'
    input_label = '已有 .mat 数据'
    out_subdir = '01_recon'
    case_in_out = True
    accepts = ()                    # 吃的是任意 .mat 目录，不做识别门禁
    produces = ('recon_mat',)
    schema = (
        Field('src', '输入（目录或 .mat）', 'file',
              tip='含 s0 / Z谱 / w 的目录或单个 .mat（整合式的 S0+img_interp 一个文件也行）'),
        Field('case', '病例名', 'text', tip='输出到 01_重建\\<病例>\\'),
        Field('out_root', '输出根（留空 = 当前 PROJ）', 'dir',
              tip='想单独放一处（如 Desktop\\2026\\10\\数据重构）就填这里；留空写当前输出总目录'),
        Field('z_file', 'Z 谱文件（留空=自动）', 'text',
              tip='目录里有多个候选时才需要填；自动识别 img_interp / zspect_* / norm_z / ssat 等'),
        Field('w_offset', '频偏（留空=按点数推断）', 'text',
              tip='如 -10:0.1:10；留空则：有 w_offset.mat 就用它，否则按频率点数推断'),
    )

    # ================================================================ 参数
    def suggest(self, det, proj):
        p = {'case': '', 'out_root': '', 'z_file': '', 'w_offset': ''}
        if det is not None:
            p['src'] = det.path
            p['case'] = (det.cases[0] if getattr(det, 'cases', None) else Path(det.path).name)
        return p

    # ================================================================ 定位
    def _locate(self, src: Path, p):
        """→ (s0 文件, z 文件, w 文件或 None, b0 文件或 None)。"""
        if src.is_file() and src.suffix.lower() == '.mat':
            return src, src, None, None
        d = src
        def first(names):
            for n in names:
                f = d / n
                if f.exists():
                    return f
            return None
        s0f = first(S0_FILES)
        wf = first(W_FILES)
        b0f = first(B0_FILES)
        if s0f is None:                                   # 兜底：按变量名找
            for f in sorted(d.glob('*.mat')):
                if any(v in _mat_vars(f) for v in S0_VARS):
                    s0f = f
                    break
        zf = None
        zname = str(p.get('z_file') or '').strip()
        if zname:
            zf = (d / zname) if not Path(zname).is_absolute() else Path(zname)
            if not zf.exists():
                zf = None
        if zf is None:
            cands = []
            for f in sorted(d.glob('*.mat')):
                if f in (s0f, wf, b0f):
                    continue
                vs = set(_mat_vars(f))
                if vs & set(Z_VARS):
                    cands.append(f)
            zf = cands[0] if cands else None
            if len(cands) > 1:
                s0f = _prefer_same_source(zf, s0f)
                return s0f, zf, wf, b0f, [c.name for c in cands]
        s0f = _prefer_same_source(zf, s0f)
        return s0f, zf, wf, b0f, None

    # ================================================================ 校验
    def validate(self, p, ctx=None):
        errs = []
        src = str(p.get('src') or '').strip()
        if not src or not Path(src).exists():
            errs.append(f'⓪ 需要输入（含 s0 / Z谱 / w 的目录或 .mat）：{src or "（空）"}')
            return errs
        if not str(p.get('case') or '').strip():
            errs.append('⓪ 需要病例名（输出到 01_重建\\<病例>\\）')
        got = self._locate(Path(src), p)
        s0f, zf, wf, _b0f = got[:4]
        if zf is None:
            errs.append('⓪ 没找到 Z 谱文件（认的变量名：img_interp / zspect_* / norm_z / ssat…）；'
                        '请在「Z 谱文件」里指明')
        if s0f is None:
            errs.append('⓪ 没找到 S0（认的变量名：s0 / S0）')
        return errs

    # ================================================================ 面板
    def preview_files(self, p, proj):
        root = str(p.get('out_root') or proj or '').strip()
        if not root:
            return []
        case = str(p.get('case') or '').strip() or '<病例>'
        d = Path(root) / R.cn_name('01_recon') / case
        return [str(d / x) for x in ('s0.mat', 'normZ.mat', 'ssat.mat',
                                     'w_offset.mat', 'import_meta.json')]

    def preview_image(self, res):
        return None

    def extra_panel(self, page):
        from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout
        pan = QGroupBox('导入重建')
        v = QVBoxLayout(pan)
        tip = QLabel('把已有的 s0 / Z 谱 / w 组装成 ① 的产物（01_重建\\<病例>\\），\n'
                     '下游 ③④⑤② 直接可用。b0 有就写、没有跳过。')
        tip.setObjectName('muted')
        tip.setWordWrap(True)
        v.addWidget(tip)
        pan.state = QLabel('还没准备。')
        pan.state.setObjectName('muted')
        pan.state.setWordWrap(True)
        v.addWidget(pan.state)
        return pan

    def refresh_extra(self, panel, det, proj, params):
        if panel is None or not hasattr(panel, 'state'):
            return
        try:
            src = str(params.get('src') or '').strip()
            if not src or not Path(src).exists():
                panel.state.setText('填好「输入」后这里会显示识别到的文件与变量。')
                return
            got = self._locate(Path(src), params)
            s0f, zf, wf, b0f = got[:4]
            many = got[4] if len(got) > 4 else None
            def nm(f):
                return f.name if f else '—'
            txt = (f'S0：{nm(s0f)}　Z 谱：{nm(zf)}　w：{nm(wf)}　b0：{nm(b0f)}')
            if many:
                txt += f'\n⚠ 目录里有多个 Z 谱候选：{many} → 请在上面「Z 谱文件」里选一个'
            panel.state.setText(txt)
        except Exception:                                        # noqa: BLE001
            pass

    # ================================================================ 执行
    def run(self, p, ctx):
        from cest_io import load_first
        from scipy.io import savemat

        stamp = time.strftime('%Y%m%d_%H%M%S')
        src = Path(str(p.get('src') or '').strip())
        case = str(p.get('case') or '').strip() or src.stem or src.name
        out_root = str(p.get('out_root') or '').strip() or getattr(ctx, 'proj', None)
        if not out_root:
            raise RuntimeError('⓪ 没有输出根：请填「输出根」，或先选输出总目录 PROJ。')
        out = Path(out_root) / R.cn_name('01_recon') / case
        out.mkdir(parents=True, exist_ok=True)

        got = self._locate(src, p)
        s0f, zf, wf, b0f = got[:4]
        many = got[4] if len(got) > 4 else None
        if zf is None:
            raise RuntimeError('⓪ 没找到 Z 谱文件（在「Z 谱文件」里指明）')
        if s0f is None:
            raise RuntimeError('⓪ 没找到 S0')
        if many:
            raise RuntimeError(f'⓪ 目录里有多个 Z 谱候选：{many}；'
                               f'请在「Z 谱文件」里选一个')
        ctx.log(f'⓪ {case}：S0 ← {s0f.name}　Z 谱 ← {zf.name}　w ← {wf.name if wf else "（推断）"}')

        # ---------------- 读 ----------------
        s0 = np.asarray(load_first(s0f, S0_VARS), float)
        z = np.asarray(load_first(zf, Z_VARS), float)
        b0 = None
        if b0f is not None:
            b0 = np.asarray(load_first(b0f, B0_VARS), float)
        ctx.log(f'   S0 {s0.shape}　Z 谱 {z.shape}' + (f'　b0 {b0.shape}' if b0 is not None else '　（无 b0，跳过）'))

        # ---------------- 形状整理 ----------------
        if s0.ndim == 4 and s0.shape[3] == 1:
            s0 = s0[:, :, :, 0]
        elif s0.ndim == 4 and s0.shape[2] == 1:
            s0 = s0[:, :, 0, :]
        if s0.ndim == 2:
            s0 = s0[:, :, None]
        if s0.ndim != 3:
            raise RuntimeError(f'S0 形状 {s0.shape} 不是 2D/3D（(X,Y) 或 (X,Y,层)）')
        n_layer = s0.shape[2]

        if z.ndim == 3:                                  # (X,Y,频) 单层
            z = z[:, :, None, :]
        elif z.ndim == 4:
            if z.shape[0] != s0.shape[0] or z.shape[1] != s0.shape[1]:
                raise RuntimeError(f'Z 谱 {z.shape} 与 S0 {s0.shape} 前两维不一致')
        else:
            raise RuntimeError(f'Z 谱形状 {z.shape} 不是 3D/4D')
        if z.shape[2] != n_layer and z.shape[3] == n_layer:   # 兜底：旧序 (X,Y,频,层)
            z = np.swapaxes(z, 2, 3)
        nw = z.shape[3]
        ctx.log(f'   → s0 (X,Y,层)={s0.shape}　Z 谱 (X,Y,层,频)={z.shape}')

        # ---------------- 频偏 ----------------
        wtxt = str(p.get('w_offset') or '').strip()
        wsrc = ''
        if wf is not None:
            w = np.asarray(load_first(wf, W_VARS), float).ravel()
            wsrc = wf.name
        elif wtxt:
            parts = [float(x) for x in wtxt.replace('，', ',').replace(':', ',').split(',') if x.strip()]
            w = (np.arange(parts[0], parts[1] + parts[2] / 2, parts[2])[:nw]
                 if len(parts) == 3 else np.asarray(parts, float))
            wsrc = '按参数'
        else:
            w = _infer_w(nw)
            wsrc = f'按点数推断（{nw} 点）'
        if w is None or w.size != nw:
            raise RuntimeError(f'⓪ 频偏个数 {None if w is None else w.size} 与 Z 谱频率维 {nw} 不一致；'
                               f'请在「频偏」里给（如 -10:0.1:10）')
        ctx.log(f'   w {nw} 点 {w.min():.2f}..{w.max():.2f}（{wsrc}）')

        # ---------------- 写（同一个 ① 结构） ----------------
        for fn in ('s0.mat', 'normZ.mat', 'ssat.mat', 'w_offset.mat', 'b0.mat'):
            _backup(out / fn, stamp, ctx.log)
        savemat(str(out / 's0.mat'), {'s0': s0}, do_compression=True)
        savemat(str(out / 'normZ.mat'), {'norm_z': z}, do_compression=True)
        savemat(str(out / 'ssat.mat'), {'ssat': z, 'axis_order': 'layer_freq'},
                do_compression=True)
        savemat(str(out / 'w_offset.mat'),
                {'w_offset': w.reshape(1, -1), 'freq_seq': w.reshape(-1, 1)},
                do_compression=True)
        if b0 is not None:
            savemat(str(out / 'b0.mat'), {'b0': b0}, do_compression=True)
        meta = dict(case=case, out=str(out), src=str(src),
                    s0_file=s0f.name, z_file=zf.name,
                    w_file=(wf.name if wf else None), b0_file=(b0f.name if b0f else None),
                    w_source=wsrc, w=list(np.round(w, 6).astype(float)),
                    shape_s0=list(s0.shape), shape_z=list(z.shape),
                    z_is_normalized=True,
                    note='数据本身已是归一化 Z 谱（1%/99% 分位判为已归一 → 下游不要再除 S0）',
                    time=time.strftime('%Y-%m-%d %H:%M:%S'))
        (out / 'import_meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        _tail = '/b0' if b0 is not None else ''
        ctx.log(f'   已写 {out}（s0/normZ/ssat/w_offset{_tail} + import_meta.json）')
        return dict(out_dir=str(out), case=case, shape_s0=list(s0.shape),
                    shape_z=list(z.shape), n_freq=int(nw),
                    files=[str(out / x) for x in
                           ('s0.mat', 'normZ.mat', 'ssat.mat', 'w_offset.mat',
                            'import_meta.json')])
