# -*- coding: utf-8 -*-
"""app/modules/m_import.py —— ⓪ 导入重建

两种模式（参数「模式」切换）：

【数据】把**已有的** s0 / Z 谱 / w 直接组装成 ① 的产物结构，下游开箱即用：
    输出（`<输出根>\\01_重建\\<病例>\\`）：
        s0.mat        变量 s0       (X,Y,层)
        normZ.mat     变量 norm_z   (X,Y,层,频)
        ssat.mat      变量 ssat     (X,Y,层,频) + axis_order='layer_freq'
        w_offset.mat  变量 w_offset (1,频数) + freq_seq
        import_meta.json
  ★ 也可以先【扫描目录…】：递归只读文件头 → 按变量名认 S0 / Z谱（不看文件名）
    → 嵌套目录自动配对 → 弹预览表勾选、改名、补频偏。

【掩模/ROI 提取】把别处已有的掩模与 ROI 收编成 ⑧ 的产物结构：
    输出（`<输出根>\\00_掩模\\<病例>\\`）：
        Mask.mat              变量 Mask      (X,Y,层)  ← 最终去噪掩模（下游只认这个名）
        <部位>ROIMask.mat     变量 <部位>ROIMask        ← 器官/组织掩模（部位名用户自己定）
        snrMask.mat / Thmask.mat                       ← 有就带过来
        ROI_total.mat                                  ← 原样拷（下游按名字取）
        extract_meta.json                              ← 来源留痕
  ★ 源目录只读，一个字节都不改。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from .base import Field, Module
from . import scan_helper as S

from detect import rules as R

S0_FILES = ('s0.mat', 'S0.mat')
S0_VARS = ('s0', 'S0')
W_FILES = ('w_offset.mat', 'w.mat', 'W.mat')
W_VARS = ('w_offset', 'w', 'freq_seq', 'freq', 'W')
B0_FILES = ('b0.mat', 'B0.mat')
B0_VARS = ('b0', 'B0')
Z_VARS = ('img_interp', 'zspect_dataavecorrect', 'zspect', 'zspec', 'norm_z', 'normZ',
          'ssat', 'zdata', 'z', 'Z')

MODE_DATA = '数据（S0 + Z 谱）'
MODE_MASK = '掩模 / ROI 提取'


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


def _as_layer3d(a, n_layer):
    """→ (X,Y,层)：2D 逐层复制；3D 层数不符时按前 n 层 / 循环取。"""
    a = np.asarray(a, dtype=float)
    if a.ndim == 4:
        a = np.squeeze(a)
    if a.ndim == 2:
        return np.repeat(a[:, :, None], n_layer, axis=2)
    if a.ndim == 3:
        if a.shape[2] == n_layer:
            return a
        if a.shape[2] > n_layer:
            return a[:, :, :n_layer]
        idx = [i % a.shape[2] for i in range(n_layer)]
        return a[:, :, idx]
    return a


def _max_layer(a):
    a = np.asarray(a)
    return a.shape[2] if a.ndim >= 3 else 1


class ImportModule(Module):
    id = 'm_import'
    badge = '⓪'
    title = '导入重建'
    input_label = '已有 .mat 数据'
    out_subdir = '01_recon'
    case_in_out = True
    accepts = ()
    produces = ('recon_mat',)

    schema = (
        Field('mode', '模式', 'choice', MODE_DATA, choices=(MODE_DATA, MODE_MASK),
              tip='数据 = 把已有 S0 / Z 谱组装成 01_重建；\n'
                  '掩模 = 把别处画好的掩模与 ROI 收编成 00_掩模（源目录只读）'),
        Field('src', '输入（总文件夹）', 'file',
              tip='把整个项目的总文件夹放进来：递归找 .mat、只读文件头、按变量名认角色。\n'
                  '点下面的【扫描目录…】可以先看清单再挑。'),
        Field('case', '病例名', 'text', tip='输出到 01_重建\\<病例>\\',
              modes=(MODE_DATA,)),
        Field('organ', '部位名（器官掩模）', 'text', '',
              tip='器官/组织掩模叫什么由你定：liver / brain / kidney …\n'
                  '留空 = 按文件里的变量名自动认（brainMask → brain，liverMask → liver），'
                  '都认不出就用 liver。\n'
                  '注意：最终掩模恒为 Mask.mat（下游只认这个名字），'
                  '部位名只影响另存的那份 <部位>ROIMask.mat。',
              modes=(MODE_MASK,)),
        Field('out_root', '输出根（留空 = 当前 PROJ）', 'dir',
              tip='想单独放一处（如 Desktop\\2026\\10\\数据重构）就填这里；留空写当前输出总目录'),
        Field('z_file', 'Z 谱文件（留空=自动）', 'text',
              tip='目录里有多个候选时才需要填；自动识别 img_interp / zspect_* / norm_z / ssat 等',
              modes=(MODE_DATA,)),
        Field('w_offset', '频偏（起始/步长/终点）', 'range',
              tip='数据里有 w（或整合文件里的 w）就用它；没有就点【设置…】填起始/步长/终点。'
                  '程序不会自动猜',
              modes=(MODE_DATA,)),
    )

    def __init__(self):
        self._plan = None      # 扫描表里勾选的行（None = 没扫，走老的自动定位）
        self._plan_mode = ''   # 这份勾选清单是哪种模式扫出来的：'data' / 'mask'

    # ================================================================ 参数
    def suggest(self, det, proj):
        p = {'case': '', 'out_root': '', 'z_file': '', 'w_offset': '', 'organ': ''}
        if det is not None:
            p['src'] = det.path
            p['case'] = (det.cases[0] if getattr(det, 'cases', None) else Path(det.path).name)
        return p

    @staticmethod
    def _is_mask_mode(p):
        return '掩模' in str(p.get('mode') or '')

    def _mask_now(self, p):
        """当前到底按哪种模式跑/预览。

        ★ 以「勾选清单是从哪种扫描来的」为准：否则点了 ② 又取消选择，
          模式被改到掩模、清单还是数据模式那批 → 预览凭空生成 00_掩模\\Mask.mat。
        """
        if self._plan:
            return self._plan_mode == 'mask'
        return self._is_mask_mode(p)

    # ================================================================ 定位
    def _locate(self, src: Path, p):
        """→ (s0 文件, z 文件, w 文件或 None, b0 文件或 None[, 多候选列表])。"""
        s0_given = None
        if src.is_file() and src.suffix.lower() == '.mat':
            vs = set(_mat_vars(src))
            if vs & set(Z_VARS):
                return (src if vs & set(S0_VARS) else None), src, \
                       (src if vs & set(W_VARS) else None), \
                       (src if vs & set(B0_VARS) else None)
            s0_given = src if vs & set(S0_VARS) else None
            d = src.parent
        else:
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
        if s0f is None:
            s0f = s0_given
        if s0f is None:
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
                if f in (wf, b0f):
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
            errs.append(f'⓪ 需要输入（总文件夹）：{src or "（空）"}')
            return errs
        if self._plan:
            bad = [r for r in self._plan if not r.get('ok', True)]
            if bad:
                errs.append(f'⓪ 勾选的条目里有 {len(bad)} 个缺前置（S0 / Z谱 / Mask / ROI_total），'
                            f'先补齐或取消勾选')
            return errs
        if self._mask_now(p):
            return errs                      # 掩模模式下目录里没有才算错，run 里逐条报
        if not str(p.get('case') or '').strip():
            errs.append('⓪ 需要病例名（输出到 01_重建\\<病例>\\）')
        got = self._locate(Path(src), p)
        s0f, zf = got[0], got[1]
        if zf is None:
            errs.append('⓪ 没找到 Z 谱文件（认的变量名：img_interp / zspect_* / norm_z / ssat…）；'
                        '请在「Z 谱文件」里指明，或点【扫描目录…】挑')
        if s0f is None:
            errs.append('⓪ 没找到 S0（认的变量名：s0 / S0）')
        return errs

    # ================================================================ 面板
    def out_dir_for(self, p, proj):
        root = str(p.get('out_root') or proj or '').strip()
        if not root:
            return None
        if self._mask_now(p):
            return Path(root) / R.cn_name('00_roi')
        # 勾了多行 → 输出根目录本身（每行一个 <病例> 子目录）
        if self._plan and len(self._plan) > 1:
            return Path(root) / R.cn_name('01_recon')
        case = str(p.get('case') or '').strip()
        if not case and self._plan:
            case = str(self._plan[0].get('case') or '').strip()
        d = Path(root) / R.cn_name('01_recon')
        return (d / case) if case else d

    def preview_files(self, p, proj):
        root = str(p.get('out_root') or proj or '').strip()
        if not root:
            return []
        base = Path(root) / R.cn_name('00_roi' if self._mask_now(p) else '01_recon')
        # 勾选的每一行各列一套（这就是「分开输出」的可视证据）
        if self._plan:
            cases = []
            for r in self._plan:
                c = self._safe_case(r.get('case'), Path(r.get('dir') or root))
                if c and c not in cases:
                    cases.append(c)
            if self._mask_now(p):
                organ = self._organ_of(p, self._plan)
                out = []
                for c in cases:
                    out += [str(base / c / x) for x in
                            ('Mask.mat', f'{organ}ROIMask.mat', 'Thmask.mat',
                             'ROI_total.mat', 'ROI1.mat', 'extract_meta.json')]
                return out
            out = []
            for c in cases:
                out += [str(base / c / x) for x in
                        ('s0.mat', 'normZ.mat', 'ssat.mat', 'w_offset.mat',
                         'import_meta.json')]
            return out
        case = str(p.get('case') or '').strip() or '<病例>'
        if self._mask_now(p):
            organ = self._organ_of(p, [])
            return [str(base / case / x) for x in
                    ('Mask.mat', f'{organ}ROIMask.mat', 'Thmask.mat',
                     'ROI_total.mat', 'ROI1.mat', 'extract_meta.json')]
        return [str(base / case / x) for x in ('s0.mat', 'normZ.mat', 'ssat.mat',
                                               'w_offset.mat', 'import_meta.json')]

    def preview_image(self, res):
        return None

    # ---------------------------------------------------------------- 面板
    def extra_panel(self, page):
        from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel,
                                       QPushButton, QVBoxLayout)
        pan = QGroupBox('导入 / 提取')
        v = QVBoxLayout(pan)
        v.setSpacing(4)
        tip = QLabel('递归只读文件头，按变量名认角色（不看文件名），嵌套目录自动配对，'
                     '弹表让你勾选。源目录只读。\n'
                     '数据 → 01_重建\\<病例>\\　掩模/ROI → 00_掩模\\<病例>\\')
        tip.setObjectName('muted')
        tip.setWordWrap(True)
        v.addWidget(tip)

        row = QVBoxLayout()
        row.setSpacing(4)
        pan.b_data = QPushButton('① 扫描数据目录…（S0 + Z 谱）')
        pan.b_data.setObjectName('primary')
        pan.b_data.setToolTip('把整个项目总文件夹放进来：找 S0 / Z 谱，弹表勾选后建 01_重建')
        pan.b_data.clicked.connect(lambda: self._scan(page, force='data'))
        pan.b_mask = QPushButton('② 扫描掩模目录…（掩膜提取）')
        pan.b_mask.setObjectName('primary')
        pan.b_mask.setToolTip('把含掩模的整个总文件夹放进来：找 Mask / 器官 mask / ROI_total，'
                             '收编成 00_掩模\\<病例>\\')
        pan.b_mask.clicked.connect(lambda: self._scan(page, force='mask'))
        row.addWidget(pan.b_data)
        row.addWidget(pan.b_mask)
        v.addLayout(row)

        row2 = QHBoxLayout()
        pan.b_clear = QPushButton('清空勾选')
        pan.b_clear.clicked.connect(lambda: self._clear_plan(page))
        row2.addWidget(pan.b_clear)
        row2.addStretch(1)
        v.addLayout(row2)

        pan.state = QLabel('还没扫描。点上面任一按钮开始（两个都能扫，看你要哪条产物）。')
        pan.state.setObjectName('muted')
        pan.state.setWordWrap(True)
        v.addWidget(pan.state)
        return pan

    def refresh_extra(self, panel, det, proj, params):
        if panel is None or not hasattr(panel, 'state'):
            return
        mask_mode = self._mask_now(params)
        try:
            panel.b_data.setStyleSheet('' if mask_mode else 'font-weight:bold;')
            panel.b_mask.setStyleSheet('font-weight:bold;' if mask_mode else '')
        except Exception:                                        # noqa: BLE001
            pass
        try:
            src = str(params.get('src') or '').strip()
            if self._plan:
                n = len(self._plan)
                cases = '、'.join(r.get('case', '') for r in self._plan[:3])
                more = '' if n <= 3 else f' …共 {n} 个'
                tail = (f'\n源目录只读 → 输出 00_掩模\\<病例>\\（Mask.mat + '
                        f'{self._organ_of(params, self._plan)}ROIMask.mat + ROI_total.mat）'
                        if mask_mode else
                        '\n→ 输出 01_重建\\<病例>\\（s0 / normZ / ssat / w_offset）')
                panel.state.setText(f'已勾选 {n} 个：{cases}{more}{tail}')
                return
            if not src or not Path(src).exists():
                panel.state.setText('填好「输入」后点【扫描目录…】，这里会显示清单摘要。')
                return
            got = self._locate(Path(src), params)
            s0f, zf, wf, b0f = got[:4]
            many = got[4] if len(got) > 4 else None

            def nm(f):
                return f.name if f else '—'
            txt = f'S0：{nm(s0f)}　Z 谱：{nm(zf)}　w：{nm(wf)}　b0：{nm(b0f)}'
            if many:
                txt += f'\n⚠ 目录里有多个 Z 谱候选：{many} → 请在上面「Z 谱文件」里选一个'
            panel.state.setText(txt)
        except Exception:                                        # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 扫描
    def _organ_of(self, p, rows):
        """部位名：用户填的 > 扫描出来的 > 默认 liver。"""
        v = str(p.get('organ') or '').strip()
        if v:
            return v
        for r in (rows or []):
            o = str(r.get('organ_name') or '').strip()
            if o and o not in ('snr', 'roi'):
                return o
        return 'liver'

    def _clear_plan(self, page):
        self._plan = None
        self._plan_mode = ''
        page.refresh_extra()
        page.refresh_preview(page.window.proj())

    def _scan(self, page, force=None):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from app.dialogs.import_scan_dialog import ScanDialog
        p = page.params()
        mask_mode = (force == 'mask') if force else self._is_mask_mode(p)
        start = str(p.get('src') or '') or page.window.proj() or ''
        d = QFileDialog.getExistingDirectory(
            page, ('选掩模总文件夹' if mask_mode else '选数据总文件夹')
            + '（递归找 .mat，只读文件头）', start)
        if not d:
            return                       # 取消 → 什么都不改（模式/清单都保持原样）
        page._set('src', d)
        page.window.log(f'⓪ 扫描 {d} …（{len(list(Path(d).rglob("*.mat")))} 个 .mat）')
        try:
            rows = (S.scan_masks(d, self._organ_of(p, [])) if mask_mode
                    else S.scan_data(d))
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(page, '扫描失败', f'{type(e).__name__}: {e}')
            return
        if not rows:
            QMessageBox.information(
                page, '没扫到可用的东西',
                f'{d}\n\n' + ('没找到掩模变量（含 mask 的 2D/3D 变量，或 ROI_total）。'
                              if mask_mode else
                              '没找到成对的 S0 + Z 谱（按变量名认，不看文件名；'
                              'V_as / V_exp 这类后处理产物不算 Z 谱）。'))
            return
        dlg = ScanDialog(page, rows, mode='掩模' if mask_mode else '数据')
        if not dlg.exec():
            return
        picked = dlg.picked()
        if not picked:
            return
        # 只有在真的拿到勾选清单之后，才把模式定下来
        self._plan = picked
        self._plan_mode = 'mask' if mask_mode else 'data'
        page._set('mode', MODE_MASK if mask_mode else MODE_DATA)
        if not mask_mode and len(picked) == 1:
            page._set('case', picked[0].get('case', ''))
        if mask_mode:
            org = self._organ_of(p, picked)
            if not str(p.get('organ') or '').strip():
                page._set('organ', org)
        page.window.log(f'⓪ 扫描：{d} → 勾选 {len(picked)} 个病例'
                        f'（{"掩模提取" if mask_mode else "数据重建"}）')
        page.refresh_extra()
        page.refresh_preview(page.window.proj())

    # ================================================================ 执行
    @staticmethod
    def _safe_case(case: str, src: Path) -> str:
        """病例名净化：只留最后一段、去掉路径分隔符与非法字符。"""
        c = str(case or '').strip().replace('\\', '/').rstrip('/')
        c = c.split('/')[-1].strip()
        c = c.strip(' .')
        for ch in '<>:"|?*':
            c = c.replace(ch, '_')
        if c in ('', '.', '..'):
            c = (src.stem or src.name or 'case').strip()
        return c

    def _root_of(self, p, ctx):
        out_root = str(p.get('out_root') or '').strip() or getattr(ctx, 'proj', None)
        if not out_root:
            raise RuntimeError('⓪ 没有输出根：请填「输出根」，或先选输出总目录 PROJ。')
        return Path(out_root).expanduser().resolve()

    def run(self, p, ctx):
        if self._mask_now(p):
            return self._run_masks(p, ctx)
        plan = self._plan
        if plan:
            return self._run_plan(p, ctx, plan)
        return self._import_one(p, ctx)

    # ---------------------------------------------------------------- 数据：批量
    def _run_plan(self, p, ctx, plan):
        root = self._root_of(p, ctx)
        res = []
        ctx.log(f'⓪ 扫描批量：{len(plan)} 个病例')
        for i, row in enumerate(plan, 1):
            ctx.check_cancel()
            case = self._safe_case(row.get('case'), Path(row.get('dir') or root))
            ctx.progress(i - 1, len(plan), f'⓪ {case}（{i}/{len(plan)}）')
            try:
                r = self._import_one(p, ctx, s0f=row.get('s0'), zf=row.get('z'),
                                     w_spec=row.get('w_spec'), case=case,
                                     root=root, wf=row.get('w'), quiet=True)
                res.append(r)
            except Exception as e:                               # noqa: BLE001
                ctx.log(f'   ✗ {case} 跳过：{e}')
        ctx.log(f'⓪ 完成：成功 {len(res)} / {len(plan)}')
        return dict(out_dir=str(root / R.cn_name('01_recon') / (res[0]['case'] if res else '')),
                    cases=[r['case'] for r in res], n=len(res),
                    files=[f for r in res for f in r.get('files', [])])

    # ---------------------------------------------------------------- 数据：单例
    def _import_one(self, p, ctx, *, s0f=None, zf=None, wf=None, b0f=None,
                    w_spec=None, case=None, root=None, quiet=False):
        from cest_io import load_first

        stamp = time.strftime('%Y%m%d_%H%M%S')
        src = Path(str(p.get('src') or '').strip())
        if root is None:
            root = self._root_of(p, ctx)
        raw_case = case or str(p.get('case') or '').strip() or src.stem or src.name
        case = self._safe_case(raw_case, src)
        out = (root / R.cn_name('01_recon') / case).resolve()
        try:
            out.relative_to(root)
        except ValueError:
            raise RuntimeError(f'⓪ 输出路径越界：{out} 不在输出根 {root} 之内，已拦下。'
                               f'（病例名 = {case!r}）')
        if case != str(raw_case).strip() and not quiet:
            ctx.log(f'   病例名已修正：{raw_case!r} → {case!r}')
        out.mkdir(parents=True, exist_ok=True)
        ctx.log(f'   输出目录：{out}')

        many = None
        if s0f is None and zf is None:
            got = self._locate(src, p)
            s0f, zf, wf, b0f = got[:4]
            many = got[4] if len(got) > 4 else None
        if zf is None:
            raise RuntimeError('⓪ 没找到 Z 谱文件（在「Z 谱文件」里指明，或先【扫描目录…】）')
        if s0f is None:
            raise RuntimeError('⓪ 没找到 S0')
        if many:
            raise RuntimeError(f'⓪ 目录里有多个 Z 谱候选：{many}；'
                               f'请在「Z 谱文件」里选一个，或点【扫描目录…】')
        s0f, zf = Path(s0f), Path(zf)
        wf = Path(wf) if wf else None
        b0f = Path(b0f) if b0f else None
        ctx.log(f'⓪ {case}：S0 ← {s0f.name}　Z 谱 ← {zf.name}　'
                f'w ← {wf.name if wf else "（参数/无）"}')

        # ---------------- 读 ----------------
        s0 = np.asarray(load_first(s0f, S0_VARS), float)
        z = np.asarray(load_first(zf, Z_VARS), float)
        b0 = None
        if b0f is not None:
            b0v = load_first(b0f, B0_VARS)
            b0 = None if b0v is None else np.asarray(b0v, float)
            if b0 is not None and (b0.size < 2 or not np.isfinite(b0).any()):
                ctx.log('   输入里的 b0 无效（空/单值/nan）→ 跳过，不写 b0.mat')
                b0 = None
        ctx.log(f'   S0 {s0.shape}　Z 谱 {z.shape}'
                + (f'　b0 {b0.shape}' if b0 is not None else '　（无 b0，跳过）'))

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

        if z.ndim == 3:
            z = z[:, :, None, :]
        elif z.ndim == 4:
            if z.shape[0] != s0.shape[0] or z.shape[1] != s0.shape[1]:
                raise RuntimeError(f'Z 谱 {z.shape} 与 S0 {s0.shape} 前两维不一致')
        else:
            raise RuntimeError(f'Z 谱形状 {z.shape} 不是 3D/4D')
        if z.shape[2] != n_layer and z.shape[3] == n_layer:
            z = np.swapaxes(z, 2, 3)
        nw = z.shape[3]
        ctx.log(f'   → s0 (X,Y,层)={s0.shape}　Z 谱 (X,Y,层,频)={z.shape}')

        # ---------------- 频偏 ----------------
        wtxt = str(w_spec or p.get('w_offset') or '').strip()
        wsrc = ''
        w = None
        for cand, label in ((wf, wf.name if wf else ''),
                            (zf if zf != wf else None, zf.name if zf else '')):
            if w is None and cand is not None:
                wv = load_first(cand, W_VARS)
                if wv is not None:
                    w = np.asarray(wv, float).ravel()
                    wsrc = label
        if w is None and wtxt:
            t = wtxt.replace('：', ':').replace('，', ',').replace(' ', '')
            if ':' in t:
                a, step, b = [float(x) for x in t.split(':')[:3]]
                w = np.arange(a, b + step / 2, step)
            else:
                w = np.asarray([float(x) for x in t.split(',') if x], float)
            wsrc = f'按参数 {wtxt}'
        if w is None:
            raise RuntimeError(
                f'⓪ 没找到频偏（w）。Z 谱有 {nw} 个频率点，请在「频偏」里点【设置…】\n'
                f'   填起始 / 步长 / 终点（例如 -12 / 0.1 / 12 → {nw} 个点）。\n'
                f'（这一项必须人工确认，程序不会自动猜）')
        if w.size != nw:
            raise RuntimeError(f'⓪ 频偏个数 {w.size} 与 Z 谱频率维 {nw} 不一致；'
                               f'请在「频偏」里点【设置…】把点数调成 {nw} 个')
        ctx.log(f'   w {nw} 点 {w.min():.2f}..{w.max():.2f}（{wsrc}）')

        # ---------------- 写 ----------------
        from cest_io import save_mat
        BIG = 2_000_000_000
        for fn in ('s0.mat', 'normZ.mat', 'ssat.mat', 'w_offset.mat', 'b0.mat'):
            _backup(out / fn, stamp, ctx.log)

        save_mat(out / 's0.mat', {'s0': s0})
        save_mat(out / 'w_offset.mat',
                 {'w_offset': w.reshape(1, -1), 'freq_seq': w.reshape(-1, 1)})
        if b0 is not None:
            save_mat(out / 'b0.mat', {'b0': b0})

        big = z.nbytes > BIG
        if big:
            ctx.log(f'   Z 谱 {z.nbytes/1073741824:.2f} GB → 写 v7.3(HDF5)')
            save_mat(out / 'normZ.mat', {'norm_z': z}, v73=True)
            import h5py
            with h5py.File(str(out / 'ssat.mat'), 'w') as f:
                f['ssat'] = h5py.ExternalLink('normZ.mat', 'norm_z')
                f.attrs['axis_order'] = 'layer_freq'
                f.attrs['note'] = 'ssat 与 normZ 同源，此处为指向 normZ.mat 的外部链接'
            ctx.log('   ssat.mat → 外部链接指向 normZ.mat（不重复占盘）')
        else:
            save_mat(out / 'normZ.mat', {'norm_z': z})
            save_mat(out / 'ssat.mat', {'ssat': z, 'axis_order': 'layer_freq'})

        meta = dict(case=case, out=str(out), src=str(src),
                    s0_file=s0f.name, z_file=zf.name,
                    w_file=(wf.name if wf else None), b0_file=(b0f.name if b0f else None),
                    w_source=wsrc, w=list(np.round(w, 6).astype(float)),
                    shape_s0=list(s0.shape), shape_z=list(z.shape),
                    z_is_normalized=True,
                    mat_format='v7.3 (HDF5)' if big else 'v7',
                    note='数据本身已是归一化 Z 谱（下游不要再除 S0）',
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

    # ---------------------------------------------------------------- 掩模提取
    def _run_masks(self, p, ctx):
        from cest_io import load_mat, load_first
        root = self._root_of(p, ctx)
        plan = self._plan
        if not plan:
            src = str(p.get('src') or '').strip()
            if not src or not Path(src).exists():
                raise RuntimeError('⓪ 掩模模式需要「输入」总文件夹')
            plan = S.scan_masks(src, self._organ_of(p, []))
            if not plan:
                raise RuntimeError(f'⓪ {src} 里没扫到掩模变量（含 mask 的 2D/3D，或 ROI_total）')
            ctx.log(f'⓪ 未扫描 → 自动识别到 {len(plan)} 个病例')

        base = root / R.cn_name('00_roi')
        base.mkdir(parents=True, exist_ok=True)
        organ_cli = str(p.get('organ') or '').strip()
        stamp = time.strftime('%Y%m%d_%H%M%S')
        done, skipped = [], []
        ctx.log(f'⓪ 掩模提取：{len(plan)} 个病例 → {base}')
        for i, row in enumerate(plan, 1):
            ctx.check_cancel()
            case = self._safe_case(row.get('case'), Path(row.get('dir') or root))
            ctx.progress(i - 1, len(plan), f'⓪ 掩模 {case}（{i}/{len(plan)}）')
            try:
                r = self._extract_one(ctx, row, case, base, organ_cli, stamp)
                done.append(r)
            except Exception as e:                               # noqa: BLE001
                ctx.log(f'   ✗ {case} 跳过：{e}')
                skipped.append((case, str(e)))
        ctx.progress(len(plan), len(plan), '⓪ 掩模提取完成')
        ctx.log(f'⓪ 掩模提取完成：成功 {len(done)}，跳过 {len(skipped)}')
        for c, why in skipped:
            ctx.log(f'   · {c}：{why}')
        return dict(out_dir=str(base), cases=[d['case'] for d in done],
                    n=len(done), skipped=[c for c, _w in skipped],
                    files=[f for d in done for f in d.get('files', [])])

    def _extract_one(self, ctx, row, case, base, organ_cli, stamp):
        from cest_io import load_mat
        out = (base / case)
        out.mkdir(parents=True, exist_ok=True)
        src_dir = Path(row.get('dir') or '')

        # 层数：同目录有 S0 就按它的层数（2D S0 → 1 层）
        n_layer = 1
        s0f = None
        for nm in ('S0.mat', 's0.mat'):
            f = src_dir / nm
            if f.exists():
                s0f = f
                break
        if s0f is not None:
            try:
                g = load_mat(str(s0f), ['S0', 's0'])
                s0 = g.get('S0', g.get('s0'))
                if s0 is not None:
                    s0 = np.squeeze(np.asarray(s0, dtype=float))
                    n_layer = int(s0.shape[2]) if s0.ndim >= 3 else 1
            except Exception:                                    # noqa: BLE001
                n_layer = 1

        written = []
        organ_name = ''
        # ---- 最终 Mask（下游只认这个名）----
        # ★ 口径：Mask = 器官 mask × snrMask。来源里没有显式 Mask 时：
        #   有器官 mask 就用它当主体（SNR 阈值掩模 Thmask 只是阈值图，含背景杂散，
        #   不能直接当 Mask）；两者都没有才退回 Thmask。
        mk = row.get('mask_file')
        mv = row.get('mask_var') or 'Mask'
        of = row.get('organ_file')
        ov = row.get('organ_var') or ''
        if of and (not mk or mv.lower() == 'thmask'):
            mk, mv = of, ov
            ctx.log(f'   最终 Mask 用器官 mask：{Path(mk).name}:{mv}'
                    f'（没有显式 Mask，Thmask 只是 SNR 阈值图，不当 Mask 用）')
        if not mk:
            raise RuntimeError('没有 Mask 候选（Mask / 器官 mask / Thmask 都没有）')
        got = load_mat(str(mk), [mv])
        arr = got.get(mv)
        if arr is None:
            got = load_mat(str(mk))
            arr = next((v for k, v in got.items() if k.lower() == mv.lower()), None)
        if arr is None:
            raise RuntimeError(f'{Path(mk).name} 里读不到 {mv}')
        mask3 = _as_layer3d(arr, n_layer)
        if n_layer == 1 and np.asarray(mask3).ndim == 3 and mask3.shape[2] == 1:
            pass
        _backup(out / 'Mask.mat', stamp, ctx.log)
        from cest_io import save_mat
        save_mat(out / 'Mask.mat', {'Mask': mask3, 'axis_order': 'layer_freq'})
        written.append('Mask.mat')
        ctx.log(f'   Mask.mat ← {Path(mk).name}:{mv}{np.asarray(arr).shape} → '
                f'{mask3.shape}，有效体素 {int((np.asarray(mask3) > 0).sum())}')

        # ---- 器官掩模 → <部位>ROIMask.mat ----
        if of:
            g2 = load_mat(str(of), [ov]) if ov else load_mat(str(of))
            a2 = g2.get(ov)
            if a2 is None:
                a2 = next(iter(g2.values()), None)
            if a2 is not None:
                organ_name = organ_cli or row.get('organ_name') or 'organ'
                key = f'{organ_name}ROIMask'
                a2 = np.asarray(a2, dtype=float)
                if a2.ndim == 3 and a2.shape[2] == 1:
                    a2 = a2[:, :, 0]
                _backup(out / f'{key}.mat', stamp, ctx.log)
                save_mat(out / f'{key}.mat', {key: a2})
                written.append(f'{key}.mat')
                ctx.log(f'   {key}.mat ← {Path(of).name}:{ov}{a2.shape}'
                        f'，有效体素 {int((a2 > 0).sum())}')

        # ---- snrMask / Thmask（有就带）----
        for src_key, var, dest in ((row.get('snr_file'), row.get('snr_var') or 'snrMask',
                                    'snrMask.mat'),
                                   (row.get('thmask_file'),
                                    row.get('thmask_var') or 'Thmask', 'Thmask.mat')):
            if not src_key:
                continue
            g3 = load_mat(str(src_key), [var])
            a3 = g3.get(var)
            if a3 is None:
                continue
            a3 = _as_layer3d(a3, n_layer)
            _backup(out / dest, stamp, ctx.log)
            save_mat(out / dest, {Path(dest).stem: a3})
            written.append(dest)
            ctx.log(f'   {dest} ← {Path(src_key).name}:{var}{np.asarray(a3).shape}')

        # ---- ROI_total 原样拷 + 拆出 ROI1.mat（与 ⑧ 的产物结构一致）----
        rf = row.get('roi_file')
        if rf and Path(rf).exists():
            _backup(out / 'ROI_total.mat', stamp, ctx.log)
            shutil.copy2(rf, out / 'ROI_total.mat')
            written.append('ROI_total.mat')
            ctx.log(f'   ROI_total.mat ← {Path(rf).name}（原样拷）')
            try:
                from scipy.io import loadmat
                cell = loadmat(str(rf)).get('ROI_total')
                if cell is not None and getattr(cell, 'size', 0) >= 1 and n_layer >= 1:
                    # ROI_total 存的是 1-based 列优先线性索引（MATLAB 口径），
                    # 拆成 2D 图得先知道 (Nx,Ny) —— 从同目录 S0 的 shape 拿。
                    shape2 = None
                    if s0f is not None:
                        try:
                            g4 = load_mat(str(s0f), ['S0', 's0'])
                            a4 = np.squeeze(np.asarray(g4.get('S0', g4.get('s0')), float))
                            shape2 = tuple(int(x) for x in a4.shape[:2])
                        except Exception:                            # noqa: BLE001
                            shape2 = None
                    if shape2:
                        for j in range(min(int(cell.size), 8)):
                            idx = np.asarray(cell.ravel()[j], dtype=float).ravel()
                            idx = idx[np.isfinite(idx) & (idx >= 1)].astype(int)
                            img2 = np.zeros(shape2, dtype=float)
                            if idx.size:
                                rr, cc = np.unravel_index(idx - 1, shape2, order='F')
                                img2[rr, cc] = 1.0
                            nm = f'ROI{j + 1}.mat'
                            _backup(out / nm, stamp, ctx.log)
                            save_mat(out / nm, {'ROI': img2})
                            written.append(nm)
                            ctx.log(f'   {nm} ← ROI_total 第 {j + 1} 个'
                                    f'（{idx.size} 体素）')
            except Exception as e:                               # noqa: BLE001
                ctx.log(f'   [提示] ROI1.mat 没拆出来（不影响）：{type(e).__name__}: {e}')
        else:
            ctx.log('   [提示] 没有 ROI_total → ③ 后处理会拦（需要先去 ⑧ 画 ROI）')

        meta = dict(case=case, out=str(out), src_dir=str(src_dir),
                    mask_from=dict(file=Path(mk).name, var=mv,
                                   shape=list(np.asarray(mask3).shape)),
                    organ=(f'{organ_name}ROIMask' if organ_name else None),
                    organ_from=(Path(of).name if of else None),
                    snr_from=(Path(row.get('snr_file')).name
                              if row.get('snr_file') else None),
                    roi_from=(Path(rf).name if rf else None),
                    n_layer=int(n_layer),
                    note='Mask.mat 恒为最终去噪掩模；器官掩模另存 <部位>ROIMask.mat',
                    time=time.strftime('%Y-%m-%d %H:%M:%S'))
        (out / 'extract_meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        written.append('extract_meta.json')
        return dict(case=case, out_dir=str(out), files=[str(out / x) for x in written])
