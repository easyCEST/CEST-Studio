# -*- coding: utf-8 -*-
"""app/modules/m3_post.py —— ③ CEST 后处理（Z谱插值 · B0校正 · 逐层落盘）。

底层：cest_post 的 normalize_spect / zspec_interp_b0corr / selfcorr_sft_map / norm_z
      —— 核心层一行不改；只把「逐层循环」放在壳里，为了**逐层进度 + 可取消 + 每层日志**。

依赖（门禁，缺了就拦下）：
    ★ PROJ\\01_重建\\<病例>\\    normZ.mat / w_offset.mat / s0.mat（B0 图谱校正另需 b0.mat）
    ★ PROJ\\00_掩模\\<病例>\\Mask.mat（= Thmask，来自 ⑧）
    可选：00_掩模\\<病例>\\liverMask.mat（V_*_mask 用它）、ROI_total.mat（拷一份到输出）

三个 B0 校正模式：不校正 No_corr / B0图谱校正 B0map_corr / 自校正 Self_corr（默认）。
不做：交互勾画掩模、自动生成掩模、4D 汇总、配准（配准单独一期）。

产物（PROJ\\03_CEST后处理\\<病例>\\{full|pos|neg}_interp_<后缀>\\，布局对齐 MATLAB）：
    sliceN\\                         S0 / S0m / S0_temp / Thmask / w_offset / x_as .mat
    sliceN\\<tag>\\                   V_exp / V_as / V_exp_mask / V_as_mask / sft_map .mat
    sliceN\\<tag>\\ROI_<时间戳>\\      ROI 定量曲线（Zspc / 插值 / LD；像 MATLAB 那样一步出）
    病例总目录\\                     w_offset.mat / x_as.mat / ROI_total.mat
    post_meta.json                  本次全部参数 + 每层统计
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from .base import Field, Module

from detect import rules as R

B0_TAGS = ('No_corr', 'B0map_corr', 'Self_corr')
B0_CHOICES = ('不校正', 'B0 图谱校正', '自校正')
SCOPE_ONE = '当前病例'
SCOPE_ALL = '全部病例（自动）'
NEG_CHOICES = ('全频偏', '正频偏', '负频偏')
NEG_MAP = {'全频偏': 2, '正频偏': 0, '负频偏': 1}   # 界面名 → cest_post 的 NegOrPos


def _f(p, k, d=None):
    v = p.get(k)
    if v is None or (isinstance(v, str) and not v.strip()):
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _backup(path, stamp, log=print):
    path = Path(path)
    if path.exists():
        bak = path.with_name(f'{path.stem}.bak_{stamp}{path.suffix}')
        try:
            shutil.copy2(path, bak)
            log(f'  已有 {path.name} → 备份为 {bak.name}')
        except OSError as e:                                     # noqa: BLE001
            log(f'  [提示] 备份失败（继续）：{e}')


class PostModule(Module):
    id = 'm3_post'
    badge = '③'
    title = 'CEST 后处理'
    out_subdir = '03_post'
    case_in_out = True
    accepts = ('recon_mat', 'recon_root', 'project_root')
    produces = ('post_out',)
    chain_proj = True               # 输入根默认 = PROJ（要 01_重建 + 00_掩模）
    chain_fill = ('case', 'src', 'mask_file')
    chain_batch_field = 'scope'     # 「全部病例（自动）」→ 放开数据链门禁
    chain_batch_values = ('全部',)
    schema = (
        # ---------------- 常用（主表单） ----------------
        Field('scope', '处理范围', 'choice', SCOPE_ONE, (SCOPE_ONE, SCOPE_ALL),
              tip='当前病例 = 只跑下拉框选的那个；\n'
                  '全部病例（自动）= 把 01_重建 下所有病例自动跑一遍；'
                  '缺前置条件的会先提醒、跳过，不用一个个点。'),
        Field('src', '输入（重建结果）', 'file',
              tip='① 的重建结果目录（含 normZ.mat / w_offset.mat / s0.mat）'),
        Field('case', '病例名', 'text', tip='输出到 03_CEST后处理\\<病例>\\'),
        Field('b0correct', 'B0 校正模式', 'choice', '自校正', B0_CHOICES,
              tip='自校正 = 逐体素 spline 找谷（默认，MATLAB 口径）；\n'
                  'B0 图谱校正 = 用 b0.mat 换算 ppm 后平移（需要 b0 比例/零点，见高级参数）；\n'
                  '不校正 = 原样插值'),
        Field('negorpos', '频偏', 'choice', '全频偏', NEG_CHOICES,
              tip='决定输出子目录前缀：全频偏→full / 正频偏→pos / 负频偏→neg'),
        Field('target_freq', '目标 ppm', 'float', 3.5, tip='算 ROI 底图/查找用的目标频偏'),
        Field('date_suffix', '输出版本后缀', 'text', '0803',
              tip='目录名 {前缀}_interp_{后缀}；默认 0803 以兼容已有 MATLAB 结果'),
        Field('mask_file', '掩模文件', 'file', '',
              tip='留空 = 自动找 00_掩模\\<病例>\\Mask.mat（必填门禁，缺了就拦下）'),
        Field('layers', '层', 'text', '', tip='留空 = 全部层；如 1,3,5（写盘层号仍是原层号）'),
        Field('save_full_vas', '写 V_as 全量', 'bool', True,
              tip='关掉只写 V_exp/V_*_mask（省地方）'),
        Field('save_uncorrect', '另存未校正版', 'bool', False,
              tip='写 V_as_uncorrect.mat（用于和校正后的对比）'),

        # ---------------- 高级：归一化 ----------------
        Field('norm_tol', '归一化判据 tol', 'float', 0.02, advanced=True, group='归一化'),
        Field('norm_plow', '归一化 低分位', 'float', 2.0, advanced=True, group='归一化'),
        Field('norm_phigh', '归一化 高分位', 'float', 98.0, advanced=True, group='归一化'),

        # ---------------- 高级：插值 ----------------
        Field('x_lw', '插值步长 ppm', 'float', 0.025, advanced=True, group='插值与B0',
              tip='x_as = min:步长:max；0.025 → 801 点（MATLAB 默认）'),

        # ---------------- 高级：B0 / 自校 ----------------
        Field('do_selfcorr_qc', '自校正 QC（写 sft_map）', 'bool', True, advanced=True,
              group='插值与B0'),
        Field('rotate_data', '数据旋转 90°', 'bool', False, advanced=True, group='插值与B0'),
        Field('b0_scale', 'B0 比例系数', 'float', '', advanced=True, group='插值与B0',
              tip='仅「B0 图谱校正」用：B0map(ppm) = (b0 - 零点) × 比例。\n'
                  '留空 → 该模式被拦下（b0.mat 是满量程整数图，必须先标定）'),
        Field('b0_offset', 'B0 零点偏置', 'float', 0.0, advanced=True, group='插值与B0'),

        # ---------------- 高级：层与体积 ----------------
        Field('total_slice_num', '总层数（留空=自动）', 'int', '', advanced=True,
              group='层与输出', tip='留空 = 按 normZ 的形状推断'),

        # ---------------- 高级：ROI & 掩模 ----------------
        Field('mask_layer', '掩模取第几层', 'int', 1, advanced=True, group='ROI与掩模',
              tip='Mask.mat 若是 3D（每层一张）就取这一层；2D 时忽略'),
        Field('snr', 'SNR 系数', 'float', 15.0, advanced=True, group='ROI与掩模',
              tip='仅当掩模文件里没有现成掩模、需要按 SNR 生成时才用（MATLAB 默认 15）'),
        Field('nROI', 'ROI 数量', 'int', 1, advanced=True, group='ROI与掩模'),
        Field('do_roi_curve', '顺带出 ROI 定量曲线（Zspc/LD）', 'bool', True,
              advanced=True, group='ROI与掩模',
              tip='像 MATLAB 那样，后处理时把 ⑧ 的 ROI 定量（Z 谱 / 插值 / LD 曲线）一起出到\n'
                  'sliceN\\<tag>\\ROI_<时间戳>\\；关掉就只在 ③ 页【ROI 定量曲线…】里单独出。'),
        Field('roi_display_mode', 'ROI 底图', 'choice', 'Z 原始', ('Z 原始', '1−Z'),
              advanced=True, group='ROI与掩模'),
        Field('save_single', '存单精度（float32）', 'bool', True, advanced=True,
              group='层与输出'),
    )

    def presets(self):
        return {
            '自校正（默认）': {'b0correct': '自校正'},
            '不校正': {'b0correct': '不校正'},
            'B0 图谱校正': {'b0correct': 'B0 图谱校正'},
            '细插值 0.025': {'x_lw': 0.025},
            '粗插值 0.1': {'x_lw': 0.1},
        }

    # ================================================================ 参数
    def suggest(self, det, proj):
        p = {'b0correct': '自校正', 'negorpos': '全频偏', 'target_freq': 3.5,
             'date_suffix': '0803', 'layers': '', 'mask_file': '',
             'save_full_vas': True, 'save_uncorrect': False,
             'norm_tol': 0.02, 'norm_plow': 2.0, 'norm_phigh': 98.0,
             'x_lw': 0.025, 'do_selfcorr_qc': True, 'rotate_data': False,
             'b0_scale': '', 'b0_offset': 0.0, 'total_slice_num': '',
             'snr': 15.0, 'nROI': 1, 'roi_display_mode': 'Z 原始',
             'do_roi_curve': True,
             'save_single': True}
        if det is None:
            return p
        if det.kind == 'project_root':
            _rc = R.find_subdir(det.path, '01_recon')
            cases = sorted(x.name for x in R.iter_cases(_rc)) if _rc else []
            p['src'] = str(_rc / cases[0]) if (_rc and cases) else ''
            p['case'] = cases[0] if cases else ''
        else:
            p['src'] = det.path
            p['case'] = (det.cases[0] if getattr(det, 'cases', None)
                         else Path(det.path).name)
            if det.kind == 'recon_root' and getattr(det, 'cases', None):
                p['src'] = str(Path(det.path) / det.cases[0])
        if proj and p.get('case'):
            _rd = R.find_subdir(proj, '00_roi')
            mf = (_rd / p['case'] / 'Mask.mat') if _rd else None
            if mf is not None and mf.exists():
                p['mask_file'] = str(mf)
        return p

    # ================================================================ 解析
    def _case_of(self, p, src_dir):
        return str(p.get('case') or '').strip() or Path(str(src_dir)).name

    def _src_dir(self, p, case):
        s = Path(str(p.get('src') or '').strip())
        if (s.is_dir() and not R.has_all(s, R.RECON_FILES) and case
                and (s / case / 's0.mat').exists()):
            return s / case
        return s

    def _find_mask(self, p, proj, case):
        cands = []
        mf = str(p.get('mask_file') or '').strip()
        if mf:
            cands.append((Path(mf), '参数指定'))
        if proj and case:
            _rd = R.find_subdir(proj, '00_roi')
            if _rd:
                for n in ('Mask.mat', 'mask.mat', 'Thmask.mat'):
                    cands.append((Path(_rd) / case / n, f'00_掩模\\{n}'))
        for f, src in cands:
            if f.exists():
                return str(f), src
        return None, ''

    def _is_batch(self, p) -> bool:
        return str(p.get('scope') or '').startswith('全部')

    def out_dir_for(self, p, proj):
        """批量模式输出到 03_CEST后处理\\（不带病例层）；单病例带病例层。"""
        if not proj:
            return None
        d = Path(proj) / R.cn_name(self.out_subdir)
        if self._is_batch(p):
            return d
        case = str(p.get('case') or '').strip()
        return (d / case) if case else d

    # ================================================================ 校验
    def validate(self, p, ctx=None):
        errs = []
        if str(p.get('scope') or '').startswith('全部'):
            proj = getattr(ctx, 'proj', None)
            if not proj:
                errs.append('「全部病例（自动）」需要先选「输出总目录（PROJ）」。')
            elif not self._cases(ctx):
                errs.append('PROJ\\01_重建 下没有病例；先跑 ① 重建。')
            return errs
        src = str(p.get('src') or '').strip()
        if not src:
            errs.append('③ 需要输入（① 的重建结果目录：含 normZ / w_offset / s0）')
        elif not Path(src).exists():
            errs.append(f'输入不存在：{src}')
        case = self._case_of(p, src)
        if src and Path(src).exists():
            sd = self._src_dir(p, case)
            need = ('normZ.mat', 'w_offset.mat', 's0.mat')
            miss = [n for n in need if not (sd / n).exists()]
            if miss:
                errs.append(f'输入目录缺 ① 的产物：{", ".join(miss)}（目录：{sd}）')
        mf, _ms = self._find_mask(p, getattr(ctx, 'proj', None), case)
        if mf is None:
            errs.append('③ 需要去噪掩膜：没找到 00_掩模\\<病例>\\Mask.mat\n'
                        '（请先在 ⑧ 去噪掩模 里画好/载入，或在「掩模文件」里指定）')
        bc = str(p.get('b0correct') or '自校正')
        if bc == 'B0 图谱校正' and _f(p, 'b0_scale') is None:
            errs.append('「B0 图谱校正」需要先定 b0 比例系数（高级参数里填 b0_scale）；\n'
                        'b0.mat 是满量程整数图，不能直接用。也可先用「自校正」。')
        if _f(p, 'x_lw', 0.025) is not None and (_f(p, 'x_lw', 0.025) or 0) <= 0:
            errs.append('插值步长必须 > 0')
        return errs

    # ================================================================ 面板
    def preview_files(self, p, proj):
        d = self.out_dir_for(p, proj)
        if not d:
            return []
        so = R.find_subdir(proj, '01_recon') if proj else None
        if self._is_batch(p):
            hint = (f'（批量：实际处理 {so} 下全部病例，上面「输入」被忽略）' if so
                    else '（批量：实际处理 PROJ\\01_重建 下全部病例，上面「输入」被忽略）')
            return [hint, str(d),
                    str(d / '<病例>\\full_interp_*\\sliceN\\<tag>\\V_exp_mask.mat'),
                    str(d / '<病例>\\full_interp_*\\sliceN\\<tag>\\ROI_<时间戳>\\')]
        pre = ('pos', 'neg', 'full')[NEG_MAP.get(str(p.get('negorpos') or '全频偏'), 2)]
        tag = B0_TAGS[B0_CHOICES.index(str(p.get('b0correct') or '自校正'))
                      if str(p.get('b0correct')) in B0_CHOICES else 2]
        suf = str(p.get('date_suffix') or '0803')
        base = Path(d) / f'{pre}_interp_{suf}'
        return [str(base / 'sliceN'), str(base / tag), str(Path(d) / 'post_meta.json')]

    def preview_image(self, res):
        return None

    def extra_panel(self, page):
        from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel,
                                       QPushButton, QVBoxLayout)
        pan = QGroupBox('CEST 后处理')
        v = QVBoxLayout(pan)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        b_adv = QPushButton('高级参数…')
        b_adv.setObjectName('primary')
        b_adv.setToolTip('归一化 / 插值步长 / B0 比例与零点 / 层数 / ROI / 输出…（带预设）')
        b_adv.clicked.connect(lambda: page._open_adv_params())
        row.addWidget(b_adv)
        b_curve = QPushButton('ROI 定量曲线…')
        b_curve.setToolTip('对⑧画的 ROI 出 Z 谱 / LD 曲线（需要先跑过本页，产出 V_exp_mask）')
        b_curve.clicked.connect(lambda: self._open_curve(page))
        row.addWidget(b_curve)
        row.addStretch(1)
        pan.state = QLabel('还没准备。')
        pan.state.setObjectName('muted')
        pan.state.setWordWrap(True)
        v.addLayout(row)
        v.addWidget(pan.state)
        return pan

    def _open_curve(self, page):
        from PySide6.QtWidgets import QMessageBox
        try:
            from app.dialogs.roi_curve_dialog import RoiCurveDialog
            RoiCurveDialog(page).show()
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(page, 'ROI 定量曲线',
                                f'打不开：{type(e).__name__}: {e}')

    def refresh_extra(self, panel, det, proj, params):
        if panel is None or not hasattr(panel, 'state'):
            return
        try:
            case = self._case_of(params, params.get('src'))
            mf, ms = self._find_mask(params, proj, case)
            txt = f'掩模：{ms or "未找到"}' if mf else '⚠ 没找到 00_掩模\\<病例>\\Mask.mat（⑧ 里先画）'
            panel.state.setText(txt)
        except Exception:                                        # noqa: BLE001
            pass

    # ================================================================ 执行
    def _cases(self, ctx):
        """PROJ\\01_重建 下的全部病例（跳过 `_` 开头的元数据目录）。"""
        d = R.find_subdir(ctx.proj, '01_recon') if getattr(ctx, 'proj', None) else None
        if d is None:
            return []
        return sorted(x.name for x in R.iter_cases(d))

    def _precheck(self, p, ctx, case, use_src=True):
        """返回 (src_dir, mask_file, 硬缺列表, 软提醒列表)。

        硬缺（会拦下/跳过）：① 重建产物 normZ/w_offset/s0 + ⑧ 去噪掩模 Mask.mat
                              + ROI_total.mat（按用户要求：后处理需要 ROI，当必须项）。
        软提醒：预留（目前恒为空）。
        """
        proj = getattr(ctx, 'proj', None)
        # src_dir：优先 p['src']（显式重建目录 / PROJ 下某病例）；否则 PROJ\01_重建\<病例>
        src = str(p.get('src') or '').strip()
        if use_src and src:
            src_dir = self._src_dir(p, case)
        else:
            d = R.find_subdir(proj, '01_recon') if proj else None
            src_dir = (Path(d) / case) if d is not None else None
        miss, warn = [], []
        # —— 硬前置：① 重建产物 + ⑧ 去噪掩模 + ROI_total（后处理需要；按用户要求当必须项）——
        if src_dir is None or not src_dir.is_dir():
            miss.append('① 重建目录')
        else:
            for n in ('normZ.mat', 'w_offset.mat', 's0.mat'):
                if not (src_dir / n).exists():
                    miss.append(n)
        mf, _ms = self._find_mask(p, proj, case)
        if mf is None:
            miss.append(f'掩模 00_掩模\\{case}\\Mask.mat')
        rd = R.find_subdir(proj, '00_roi') if proj else None
        roi_f = (Path(rd) / case / 'ROI_total.mat') if rd is not None else None
        if roi_f is None or not roi_f.exists():
            miss.append(f'ROI_total.mat（00_掩模\\{case}\\ROI_total.mat）—— 后处理需要，先去 ⑧ 画/载入')
        return src_dir, mf, miss, warn

    def run(self, p, ctx):
        if str(p.get('scope') or '').startswith('全部'):
            return self._run_all(p, ctx)
        src = str(p.get('src') or '').strip()
        if not src:
            raise RuntimeError('③ 需要输入（① 的重建结果目录）')
        case = self._case_of(p, src)
        src_dir, mask_file, miss, warn = self._precheck(p, ctx, case)
        if miss:
            raise RuntimeError(f'③ {case} 缺前置条件：' + '、'.join(miss)
                               + '\n（缺什么先去对应模块补：① 重建 / ⑧ 掩模。）')
        for w in warn:
            ctx.log(f'⚠ {case}：缺 {w}')
        return self._run_case(p, ctx, case, src_dir, mask_file)

    def _run_all(self, p, ctx):
        cases = self._cases(ctx)
        if not cases:
            raise RuntimeError('PROJ\\01_重建 下没有病例；先跑 ① 重建。')
        ready, missing = [], []
        for c in cases:
            src_dir, mf, miss, warn = self._precheck(p, ctx, c, use_src=False)
            (missing if miss else ready).append((c, src_dir, mf, miss, warn))
        ctx.log(f'③ 全部病例：共 {len(cases)} 个 → 可跑 {len(ready)}、缺前置 {len(missing)}')
        if missing:
            ctx.log('⚠ 以下病例缺前置条件（normZ/w_offset/s0、掩模 Mask.mat 或 ROI_total.mat），已跳过：')
            for c, _sd, _mf, miss, _w in missing:
                ctx.log(f'   · {c}：缺 {"、".join(miss)}')
        _warn = [(c, w) for c, _s, _m, _i, w in ready if w]
        if _warn:
            ctx.log('提醒：以下病例能跑，但缺 ROI_total.mat'
                    '（⑤ 的 ROI 曲线 / ⑥ 组内左右要用，建议先去 ⑧ 补）：')
            for c, w in _warn:
                ctx.log(f'   · {c}：缺 {w[0]}')
        if not ready:
            raise RuntimeError('所有病例都缺前置条件，没有可跑：\n'
                               + '\n'.join(f'· {c}：缺 {"、".join(m)}'
                                           for c, _s, _m, m, _w in missing))
        results, n_ok = [], 0
        N = len(ready)
        for i, (c, src_dir, mf, _m, _w) in enumerate(ready, 1):
            ctx.check_cancel()
            ctx.log(f'\n===== [{i}/{N}] {c} =====')

            def _p(v, text, _i=i, _N=N):
                ctx.progress(int(round(((_i - 1) + v / 100.0) * 100.0 / _N)), 100,
                             f'[{_i}/{N}] {text}')
            try:
                results.append(self._run_case(p, ctx, c, src_dir, mf, progress=_p))
                n_ok += 1
            except Exception as e:                               # noqa: BLE001
                ctx.log(f'   [{c}] 失败：{e}')
        ctx.progress(100, 100, f'③ 全部完成：成功 {n_ok}/{N}')
        out_base = ctx.out_dir(self.out_subdir)
        return dict(out_dir=str(out_base), case=f'全部（成功 {n_ok}/{N}）',
                    n_ok=n_ok, n_all=N, n_missing=len(missing),
                    missing=[{'case': c, 'miss': m} for c, _s, _m, m, _w in missing],
                    n_no_roi=len(_warn),
                    no_roi=[{'case': c, 'warn': w} for c, w in _warn],
                    results=results, files=[str(out_base)])

    def _run_case(self, p, ctx, case, src_dir, mask_file, progress=None):
        import cest_post as C
        from cest_io import load_first

        stamp = time.strftime('%Y%m%d_%H%M%S')
        src_dir = Path(src_dir)

        bc = str(p.get('b0correct') or '自校正')
        b0correct = B0_CHOICES.index(bc) if bc in B0_CHOICES else 2
        neg = str(p.get('negorpos') or '全频偏')
        negorpos = NEG_MAP.get(neg, 2)
        prefix = ('pos', 'neg', 'full')[negorpos]
        tag = B0_TAGS[b0correct]
        x_lw = _f(p, 'x_lw', 0.025)
        tol = _f(p, 'norm_tol', 0.02)
        plow = _f(p, 'norm_plow', 2.0)
        phigh = _f(p, 'norm_phigh', 98.0)
        target = _f(p, 'target_freq', 3.5)
        snr = _f(p, 'snr', 15.0)
        do_qc = bool(p.get('do_selfcorr_qc', True))
        rotate = bool(p.get('rotate_data', False))
        b0_scale = _f(p, 'b0_scale')
        b0_offset = _f(p, 'b0_offset', 0.0) or 0.0
        save_single = bool(p.get('save_single', True))
        save_vas = bool(p.get('save_full_vas', True))
        save_unc = bool(p.get('save_uncorrect', False))
        suf = str(p.get('date_suffix') or '0803').strip() or '0803'
        if b0correct == 1 and b0_scale is None:
            raise RuntimeError('「B0 图谱校正」需要 b0_scale（高级参数），或改用「自校正」。')

        out_root = ctx.out_dir(self.out_subdir) / case
        save_root = out_root / f'{prefix}_interp_{suf}'
        save_root.mkdir(parents=True, exist_ok=True)

        T0 = time.time()

        def _p(v, text):
            txt = f'{text}　（已用 {time.time() - T0:.0f}s）'
            if progress is None:
                ctx.progress(int(round(v)), 100, txt)
            else:
                progress(v, txt)

        # ---------------- 读数据 ----------------
        _p(2, f'读数据：{case}\\ normZ / w_offset / s0…')
        Mz_all = np.asarray(load_first(src_dir / 'normZ.mat', ('norm_z', 'normZ')), float)
        w_offset = np.asarray(load_first(src_dir / 'w_offset.mat', ('w_offset', 'w')),
                              float).ravel()
        S0_all = np.asarray(load_first(src_dir / 's0.mat', ('s0', 'S0')), float)
        ctx.log(f'③ {case}：normZ {Mz_all.shape} / s0 {S0_all.shape} / {w_offset.size} 个频偏')
        ctx.log(f'   校正模式 {bc}（{tag}）　频偏 {neg}（前缀 {prefix}）　'
                f'x_lw {x_lw}　目标 {target} ppm')

        # 轴序统一为「频优先」[X,Y,频,层]（与 cest_post 后续算法一致）
        if Mz_all.ndim == 3:
            Mz_all = Mz_all[:, :, :, None]
        if Mz_all.ndim != 4:
            raise RuntimeError(f'normZ 维度 {Mz_all.shape} 不是 3D/4D')
        if Mz_all.shape[2] != w_offset.size and Mz_all.shape[3] == w_offset.size:
            Mz_all = np.swapaxes(Mz_all, 2, 3)          # 层优先 → 频优先（视图）
        n_nan = int((~np.isfinite(Mz_all)).sum())
        if n_nan:
            ctx.log(f'   非有限值 {n_nan} 个 → 置 0')
            Mz_all = Mz_all.copy()
            Mz_all[~np.isfinite(Mz_all)] = 0
        Nx, Ny = Mz_all.shape[0], Mz_all.shape[1]
        Nfreq = Mz_all.shape[2]
        n_layer = Mz_all.shape[3]
        if Nfreq != w_offset.size:
            raise RuntimeError(f'normZ 频偏维 {Nfreq} ≠ w_offset {w_offset.size}')
        tsn = p.get('total_slice_num')
        tsn = int(tsn) if str(tsn or '').strip() not in ('', 'None') else n_layer
        if tsn != n_layer:
            raise RuntimeError(f'总层数 {tsn} 与 normZ 层数 {n_layer} 不一致（'
                               f'total_slice_num 留空即自动）')

        # S0 → (X, Y, 层)
        S0_all = np.asarray(S0_all, float)
        if S0_all.ndim == 4 and S0_all.shape[3] == 1:
            S0_all = S0_all[:, :, :, 0]
        elif S0_all.ndim == 4 and S0_all.shape[2] == 1:
            S0_all = S0_all[:, :, 0, :]
        elif S0_all.ndim == 3 and S0_all.shape[2] != n_layer:
            S0_all = S0_all[:, :, None]
        elif S0_all.ndim == 2:
            S0_all = S0_all[:, :, None]
        b0_all = None
        if b0correct == 1:
            b0f = src_dir / 'b0.mat'
            if not b0f.exists():
                raise RuntimeError('B0 图谱校正需要 01_重建\\<病例>\\b0.mat')
            b0_all = np.asarray(load_first(b0f, ('b0', 'B0')), float)
            if b0_all.ndim == 4 and b0_all.shape[3] == 1:
                b0_all = b0_all[:, :, :, 0]
            elif b0_all.ndim == 4 and b0_all.shape[2] == 1:
                b0_all = b0_all[:, :, 0, :]
        if rotate:
            Mz_all = np.rot90(Mz_all, 1)
            S0_all = np.rot90(S0_all, 1)
            if b0_all is not None:
                b0_all = np.rot90(b0_all, 1)

        bg = int(np.argmin(np.abs(w_offset - target)))
        _p(8, '读数据完成')

        # ---------------- 逐层归一化 ----------------
        _p(10, f'归一化：tol={tol} p{plow:g}/p{phigh:g}')
        Mz = np.zeros_like(Mz_all)
        for ns in range(n_layer):
            Mz[:, :, :, ns] = C.normalize_spect(Mz_all[:, :, :, ns], tol, plow, phigh)
        w_offset_ori = w_offset.copy()
        ctx.log(f'   归一化完成 → Mz {Mz.shape}')

        # ---------------- 掩模（复用 ⑧ 的产物，不交互） ----------------
        _p(14, f'掩模：{Path(mask_file).name}')
        Thmask = np.asarray(load_first(Path(mask_file), ('Mask', 'Thmask', 'liverMask')), float)
        if Thmask.ndim == 4:
            Thmask = np.squeeze(Thmask)
        ml = int(_f(p, 'mask_layer', 1) or 1)
        if Thmask.ndim == 3:                      # 每层一张 → 取第 ml 层
            ml = min(max(ml, 1), Thmask.shape[2])
            Thmask = Thmask[:, :, ml - 1]
            ctx.log(f'   掩模是 3D（每层一张）→ 取第 {ml} 层')
        elif Thmask.ndim != 2:
            Thmask = np.squeeze(Thmask)
        if Thmask.shape != (Nx, Ny):
            raise RuntimeError(f'掩模形状 {Thmask.shape} 与数据 {(Nx, Ny)} 不一致')
        # liverMask：同目录里的 liverMask.mat；没有就用 Thmask
        _rd = R.find_subdir(ctx.proj, '00_roi') if ctx.proj else None
        lf = (_rd / case / 'liverMask.mat') if _rd else None
        if lf is not None and lf.exists():
            liverMask = np.asarray(load_first(lf, ('liverMask', 'Mask', 'Thmask')), float)
            if liverMask.ndim == 4:
                liverMask = np.squeeze(liverMask)
            if liverMask.ndim == 3:
                liverMask = liverMask[:, :, min(max(ml, 1), liverMask.shape[2]) - 1]
            elif liverMask.ndim != 2:
                liverMask = np.squeeze(liverMask)
            if liverMask.shape != (Nx, Ny):
                liverMask = Thmask
        else:
            liverMask = Thmask
        ctx.log(f'   掩模 {mask_file} → {Thmask.shape}，有效体素 {int(np.count_nonzero(Thmask))}'
                f'（liverMask：{"独立文件" if lf and lf.exists() else "同 Thmask"}）')

        # ---------------- ROI_total（拷一份到输出；有就顺带算 ROI 定量曲线） ----------------
        roi_total_src = (_rd / case / 'ROI_total.mat') if _rd else None
        roi_file = None
        if roi_total_src is not None and roi_total_src.exists():
            roi_file = str(roi_total_src)
            try:
                shutil.copy2(roi_total_src, save_root / 'ROI_total.mat')
                ctx.log('   已拷 ROI_total.mat 到输出目录')
            except OSError as e:                                  # noqa: BLE001
                ctx.log(f'   [提示] 拷 ROI_total 失败：{e}')
        else:
            ctx.log('   [提示] 没有 ROI_total.mat（⑧ 里画了 ROI 才有）；跳过 ROI 定量曲线')

        # ---------------- 逐层：插值 + B0 校正 ----------------
        layer_list = [int(v) for v in str(p.get('layers') or '').replace('，', ',')
                      .split(',') if v.strip()] or list(range(1, n_layer + 1))
        bad = [s for s in layer_list if not (1 <= s <= n_layer)]
        if bad:
            raise RuntimeError(f'层号超出范围 1~{n_layer}：{bad}')
        ctx.log(f'   ▶ 开始逐层（{len(layer_list)} 层，{tag}）：{layer_list}')

        x_as = None
        per_layer = []
        roi_curve_dirs = []
        n = len(layer_list)
        for idx, s in enumerate(layer_list, 1):
            ctx.check_cancel()
            t0 = time.time()
            _p(14 + 82 * (idx - 1) / n, f'后处理：第 {s}/{n_layer} 层 开始…')
            sp = save_root / f'slice{s}' / tag
            sp.mkdir(parents=True, exist_ok=True)
            try:
                Zdata = np.squeeze(Mz[:, :, :, s - 1])
                S0 = S0_all[:, :, min(s - 1, S0_all.shape[2] - 1)]
                S0m, S0_temp = S0, np.ones((Nx, Ny))
                if b0correct == 1:
                    B0map = (b0_all[:, :, min(s - 1, b0_all.shape[2] - 1)] - b0_offset) * b0_scale
                    B0map[~(Thmask > 0)] = 0
                else:
                    B0map = np.zeros((Nx, Ny))
                x_as, V_as, V_as_unc, w_out, V_exp = C.zspec_interp_b0corr(
                    Zdata, w_offset_ori, Thmask, B0map, b0correct, negorpos, x_lw)
                if do_qc and b0correct == 2:
                    try:
                        sft = C.selfcorr_sft_map(Zdata, w_offset_ori, x_as, Thmask)
                        sel = Thmask > 0
                        ctx.log(f'   slice {s:2d} | 自校正 median {np.median(sft[sel]):+.2f} ppm'
                                f' | |sft|>1 占 {100 * np.mean(np.abs(sft[sel]) > 1):.1f}%')
                        C.savemat(str(sp / 'sft_map.mat'), {'sft_map': sft})
                    except Exception as eqc:                          # noqa: BLE001
                        ctx.log(f'   slice {s:2d} | QC 失败（跳过）：{eqc}')
                if save_single:
                    V_as = V_as.astype(np.float32)
                    V_exp = V_exp.astype(np.float32)
                    V_as_unc = V_as_unc.astype(np.float32)
                for nm, val in (('S0', S0), ('S0m', S0m), ('S0_temp', S0_temp),
                                ('Thmask', Thmask), ('w_offset', w_out), ('x_as', x_as)):
                    C.savemat(str(save_root / f'slice{s}' / f'{nm}.mat'), {nm: val})
                C.savemat(str(sp / 'V_exp.mat'), {'V_exp': V_exp})
                if save_vas:
                    C.savemat(str(sp / 'V_as.mat'), {'V_as': V_as})
                if save_unc:
                    C.savemat(str(sp / 'V_as_uncorrect.mat'), {'V_as_uncorrect': V_as_unc})
                V_exp_mask = C.norm_z(S0_temp, V_exp, liverMask)
                V_as_mask = C.norm_z(S0_temp, V_as, liverMask)
                if save_single:
                    V_exp_mask = V_exp_mask.astype(np.float32)
                    V_as_mask = V_as_mask.astype(np.float32)
                C.savemat(str(sp / 'V_exp_mask.mat'), {'V_exp_mask': V_exp_mask})
                C.savemat(str(sp / 'V_as_mask.mat'), {'V_as_mask': V_as_mask})

                # ---- 顺带出 ROI 定量曲线（Zspc / 插值 / LD）----
                if bool(p.get('do_roi_curve', True)) and roi_file:
                    try:
                        import cest_roi as CR
                        roi_out = sp / f'ROI_{stamp}'
                        CR.run_curves(np.asarray(V_exp_mask, float),
                                      (np.asarray(V_as_mask, float) if save_vas else None),
                                      w_out, x_as, roi_file, roi_out,
                                      cfg=dict(trim=True, negorpos=negorpos, dpi=120),
                                      log=lambda _s: None)
                        roi_curve_dirs.append(str(roi_out))
                        ctx.log(f'   slice {s:2d} | ROI 定量曲线 → {roi_out.name}/')
                    except Exception as er:                            # noqa: BLE001
                        ctx.log(f'   slice {s:2d} | ROI 定量曲线失败（不影响主产物）：{er}')
                dt = time.time() - t0
                per_layer.append(dict(layer=s, sec=round(dt, 2),
                                      n_valid=int(np.count_nonzero(Thmask))))
                ctx.log(f'   slice {s:2d}/{n_layer} | OK（{dt:.1f}s，有效体素 '
                        f'{int(np.count_nonzero(Thmask))}）')
            except Exception as es:                               # noqa: BLE001
                per_layer.append(dict(layer=s, sec=None, error=str(es)))
                ctx.log(f'   slice {s:2d} | 失败：{es}')
            _p(14 + 82 * idx / n, f'后处理：第 {idx}/{n} 层完成')

        # ---------------- 收尾 ----------------
        _p(97, '写病例总目录的坐标轴…')
        if x_as is not None:
            C.savemat(str(save_root / 'w_offset.mat'), {'w_offset': w_offset})
            C.savemat(str(save_root / 'x_as.mat'), {'x_as': x_as})
        meta = dict(case=case, src=str(src_dir), mask_file=str(mask_file),
                    b0correct=bc, b0_tag=tag, negorpos=neg, prefix=prefix,
                    target_freq=target, x_lw=x_lw, date_suffix=suf,
                    norm=(tol, plow, phigh), rotate=rotate,
                    b0_scale=b0_scale, b0_offset=b0_offset,
                    layers=layer_list, n_layer=n_layer, shape=[int(Nx), int(Ny)],
                    per_layer=per_layer, roi_curve_dirs=roi_curve_dirs,
                    sec=round(time.time() - T0, 1))
        (out_root / 'post_meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        ctx.log(f'   已写 {out_root / "post_meta.json"}')
        _p(100, f'③ 完成（共 {time.time() - T0:.0f}s）')
        n_ok = sum(1 for x in per_layer if x.get('sec') is not None)
        return dict(out_dir=str(save_root), case=case, n_ok=n_ok, n_layer=len(layer_list),
                    meta=meta, roi_curves=roi_curve_dirs,
                    files=[str(save_root / 'w_offset.mat'),
                                      str(save_root / 'x_as.mat'),
                                      str(out_root / 'post_meta.json')])
