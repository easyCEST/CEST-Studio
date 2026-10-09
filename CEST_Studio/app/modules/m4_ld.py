# -*- coding: utf-8 -*-
"""app/modules/m4_ld.py —— ④ 逐体素LD（洛伦兹拟合）。

底层：cest_ld 的 is_already_norm / normalize_spect0916 / voxel_ld_filter_fast|voxel_ld_filter
      —— 核心层一行不改；只把「逐层循环」放在壳里，为了**逐层进度 + 可取消 + 每层日志**。

依赖：
    ★ PROJ\\00_掩模\\<病例>\\Mask.mat（去噪掩膜，**必填门禁**，缺了就拦下）
    数据 = ① 的重建结果（w_offset.mat / ssat.mat / s0.mat）
    ★ 不需要器官 ROI（拟合只要掩模）
    归一化：除 S0 → normalize_spect0916 只裁剪（MATLAB 口径）

产物（PROJ\\04_逐体素LD\\<病例>\\，命名对齐 MATLAB）：
    0.7ut_img_interp.mat   s0 / b0 / norm_z=img_interp / w      ← ② 可直接复用
    voxel_LD.mat           voxel_ld / Voxel_LD_all   [X,Y,层,频]
    voxel_LD_interp.mat    voxel_ld_interp / ..._all + w_offsetLD_interp
    _thmask_used.mat       本次实际用的 2D 掩模（第 k 层）留痕
    ld_check.png           质检图（LD 切片 / 线宽 / 每层线宽中位数）
    ld_meta.json           全部参数 + 每层耗时/有效体素/线宽中位数
★ 三区间 / lb / ub / par0 / 对称性阈值 / 网格 / 插值 等**全部在 GUI 里改**（见「高级参数…」）。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from .base import Field, Module

from detect import rules as R

LD_FILES = ('voxel_LD.mat', 'voxel_LD_interp.mat', '0.7ut_img_interp.mat')
INF = float('inf')


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


class LDModule(Module):
    id = 'm4_ld'
    badge = '④'
    title = '逐体素LD（洛伦兹拟合）'
    out_subdir = '04_ld'
    case_in_out = True
    accepts = ('recon_mat', 'recon_root', 'project_root')
    produces = ('ld_out',)
    chain_proj = True               # 输入根默认 = PROJ（要 01_重建 + 00_掩模）
    chain_fill = ('case', 'src', 'mask_file')
    schema = (
        # ---------------- 常用（主表单） ----------------
        Field('src', '输入（重建结果）', 'file',
              tip='① 的重建结果目录（含 w_offset.mat / ssat.mat / s0.mat）'),
        Field('case', '病例名', 'text', tip='输出到 04_逐体素LD\\<病例>\\；取掩模也按它'),
        Field('mask_file', '掩模文件', 'file', '',
              tip='留空 = 自动找 00_掩模\\<病例>\\Mask.mat（必填门禁，缺了就拦下）'),
        Field('mask_layer', '掩模取第几层', 'int', 1,
              tip='Lorentz 拟合只吃一张 2D 掩模（MATLAB 用 Mask(:,:,1)）；'
                  '这里选第几层，默认 1'),
        Field('fit', '拟合算法', 'choice', 'fast', ('fast', 'exact'),
              tip='fast = (中心,线宽) 网格 + 幅度/基线闭式解（≈1.7 s/层，默认，'
                  '全局最小二乘解）；exact = 逐体素 lsqcurvefit 等价（分钟级/层，'
                  'MATLAB LM 局部解）'),
        Field('layers', '层', 'text', '', tip='留空 = 全部层；如 1,3,5（写盘层号仍是原层号）'),

        # ---------------- 高级：三个拟合区间 ----------------
        Field('l3', '低端段·起点 ppm', 'float', -10.0, advanced=True, group='三个拟合区间'),
        Field('l2', '低端段·终点 ppm', 'float', -6.0, advanced=True, group='三个拟合区间'),
        Field('l1', '中段·左端 ppm', 'float', -0.6, advanced=True, group='三个拟合区间'),
        Field('h1', '中段·右端 ppm', 'float', 0.6, advanced=True, group='三个拟合区间'),
        Field('h2', '高端段·起点 ppm', 'float', 6.0, advanced=True, group='三个拟合区间'),
        Field('h3', '高端段·终点 ppm', 'float', 10.0, advanced=True, group='三个拟合区间'),

        # ---------------- 高级：洛伦兹拟合 ----------------
        Field('lb1', 'lb₁ 中心下界', 'float', '-inf', advanced=True, group='洛伦兹拟合',
              tip='MATLAB 新版 lb=[] → 无下界；processLD_0909 用 lb=[0,-Inf,0,1]（可点预设一键切）'),
        Field('lb2', 'lb₂ 幅度下界', 'float', '-inf', advanced=True, group='洛伦兹拟合'),
        Field('lb3', 'lb₃ 线宽下界', 'float', 0.0, advanced=True, group='洛伦兹拟合'),
        Field('lb4', 'lb₄ 基线下界', 'float', '-inf', advanced=True, group='洛伦兹拟合'),
        Field('ub1', 'ub₁ 中心上界', 'float', 0.0, advanced=True, group='洛伦兹拟合',
              tip='中心 ≤ 0（本项目峰在负频偏侧）'),
        Field('ub2', 'ub₂ 幅度上界', 'float', 'inf', advanced=True, group='洛伦兹拟合'),
        Field('ub3', 'ub₃ 线宽上界', 'float', 1.8, advanced=True, group='洛伦兹拟合',
              tip='processLD_0909 / Python 默认 1.8；generate_Voxel_LD_all 用 1.7'),
        Field('ub4', 'ub₄ 基线上界', 'float', 'inf', advanced=True, group='洛伦兹拟合'),
        Field('par01', 'par0₁ 中心初值', 'float', 0.0, advanced=True, group='洛伦兹拟合'),
        Field('par02', 'par0₂ 幅度初值', 'float', -50.0, advanced=True, group='洛伦兹拟合'),
        Field('par03', 'par0₃ 线宽初值', 'float', 20.0, advanced=True, group='洛伦兹拟合',
              tip='MATLAB par0=[0,-50,20,1]；20 超过线宽上界时会显示为夹到边界的值'),
        Field('par04', 'par0₄ 基线初值', 'float', 1.0, advanced=True, group='洛伦兹拟合'),
        Field('center_fix0', '中心固定为 0（MATLAB 口径）', 'bool', False,
              advanced=True, group='洛伦兹拟合',
              tip='勾上 = lb₁=ub₁=0（MATLAB 多版本如此）；不勾 = 中心 ≤ 0（Python 现状）'),
        Field('sym_threshold', '对称性剔除阈值 ppm', 'float', 5.0,
              advanced=True, group='洛伦兹拟合',
              tip='对 |ppm|>阈值 的端点做三连升/降判断，剔除对称异常（processLD_0909 取 5）'),

        # ---------------- 高级：fast 的搜索网格 ----------------
        Field('c_grid_lo', '中心网格 起点', 'float', -2.0, advanced=True,
              group='拟合网格（fast）', tip='默认 -2.0 ~ ub₁，步长见下（核心层默认 -2.0/0.05）'),
        Field('c_grid_step', '中心网格 步长', 'float', 0.05, advanced=True,
              group='拟合网格（fast）'),
        Field('g_grid_lo', '线宽网格 起点', 'float', 0.2, advanced=True,
              group='拟合网格（fast）', tip='默认 0.2 ~ ub₃，步长见下（核心层默认 0.2/0.05）'),
        Field('g_grid_step', '线宽网格 步长', 'float', 0.05, advanced=True,
              group='拟合网格（fast）'),

        # ---------------- 高级：归一化 ----------------
        Field('tol', '归一化判据 tol', 'float', 0.05, advanced=True, group='归一化',
              tip='已在 [0,1]±tol 内 → 跳过「除 S0」。'
                  '（核心层固定：分位 1/99、只裁剪不重标定 —— 与 MATLAB '
                  'normalizeSpect0916(z,tol,1,99,false) 一致）'),

        # ---------------- 高级：输出与插值 ----------------
        Field('step_interp', '拟合后插值步长 ppm', 'float', 0.1, advanced=True,
              group='输出与插值'),
        Field('interp_method', '插值方法（exact）', 'choice', 'spline',
              ('spline', 'linear', 'cubic', 'nearest'), advanced=True,
              group='输出与插值'),
        Field('save_interp', '写 voxel_LD_interp.mat', 'bool', True, advanced=True,
              group='输出与插值',
              tip='密网格版（≈300 MB）；关掉只出 voxel_LD.mat'),
        Field('max_voxels', '调试·每层最多体素数', 'int', '', advanced=True,
              group='输出与插值', tip='留空 = 不限；试跑时填小值（如 200）很快看到效果'),
    )

    def presets(self):
        return {
            'MATLAB 默认': {},
            '无下界 lb=[]': {'lb1': '-inf', 'lb2': '-inf', 'lb3': '0', 'lb4': '-inf'},
            'processLD_0909 版': {'lb1': '0', 'lb2': '-inf', 'lb3': '0', 'lb4': '1'},
            'GenAll 线宽 1.7': {'ub3': '1.7'},
            '区间·NEG': {'l3': '-10', 'l2': '-6', 'l1': '-0.4',
                         'h1': '0.6', 'h2': '0.7', 'h3': '1.5'},
            '区间·POS': {'l3': '-0.6', 'l2': '-0.4', 'l1': '-0.3',
                         'h1': '1.1', 'h2': '6', 'h3': '10'},
            '中心固定 0': {'center_fix0': True},
        }

    # ================================================================ 参数
    def suggest(self, det, proj):
        p = {'fit': 'fast', 'mask_layer': 1, 'layers': '', 'tol': 0.05,
             'step_interp': 0.1, 'interp_method': 'spline', 'save_interp': True,
             'l3': -10.0, 'l2': -6.0, 'l1': -0.6, 'h1': 0.6, 'h2': 6.0, 'h3': 10.0,
             'lb1': '-inf', 'lb2': '-inf', 'lb3': 0.0, 'lb4': '-inf',
             'ub1': 0.0, 'ub2': 'inf', 'ub3': 1.8, 'ub4': 'inf',
             'par01': 0.0, 'par02': -50.0, 'par03': 20.0, 'par04': 1.0,
             'center_fix0': False, 'sym_threshold': 5.0,
             'c_grid_lo': -2.0, 'c_grid_step': 0.05,
             'g_grid_lo': 0.2, 'g_grid_step': 0.05, 'max_voxels': ''}
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
                for n in ('Mask.mat', 'mask.mat', 'Thmask.mat', 'liverMask.mat'):
                    cands.append((Path(_rd) / case / n, f'00_掩模\\{n}'))
        for f, src in cands:
            if f.exists():
                return str(f), src
        return None, ''

    # ================================================================ 校验
    def validate(self, p, ctx=None):
        errs = []
        proj = getattr(ctx, 'proj', None)
        src = str(p.get('src') or '').strip()
        if not src:
            errs.append('④ 需要输入（① 的重建结果目录：含 w_offset / ssat / s0）')
        elif not Path(src).exists():
            errs.append(f'输入不存在：{src}')
        case = self._case_of(p, src)
        if src and Path(src).exists():
            sd = self._src_dir(p, case)
            if not R.has_all(sd, R.RECON_FILES):
                errs.append(f'输入目录里没有 ① 的产物（需要 s0.mat / ssat.mat / w_offset.mat）：{sd}')
        mf, _ms = self._find_mask(p, proj, case)
        if mf is None:
            errs.append('④ 需要去噪掩膜：没找到 00_掩模\\<病例>\\Mask.mat\n'
                        '（请先在 ⑧ 去噪掩模 里画好/载入，或在「掩模文件」里指定）')
        # ---- 拟合区间 ----
        try:
            l3, l2, l1 = _f(p, 'l3'), _f(p, 'l2'), _f(p, 'l1')
            h1, h2, h3 = _f(p, 'h1'), _f(p, 'h2'), _f(p, 'h3')
            if None not in (l3, l2, l1, h1, h2, h3):
                if not (l3 < l2 < l1 < h1 < h2 < h3):
                    errs.append(f'三个拟合区间要「低→高」递增：'
                                f'{l3} < {l2} < {l1} < {h1} < {h2} < {h3} 不成立')
        except Exception:                                        # noqa: BLE001
            pass
        # ---- lb / ub / par0 ----
        try:
            lb = [_f(p, f'lb{i}') for i in (1, 2, 3, 4)]
            ub = [_f(p, f'ub{i}') for i in (1, 2, 3, 4)]
            for i, (a, b) in enumerate(zip(lb, ub), 1):
                if a is not None and b is not None and a > b:
                    errs.append(f'lb{i}（{a}）> ub{i}（{b}）')
            if p.get('center_fix0'):
                pass          # 中心固定 0 时 lb1/ub1 会被强制成 0，不报
            if ub[2] == 0 or (lb[2] is not None and ub[2] is not None and ub[2] <= 0):
                errs.append('线宽上界 ub₃ 必须 > 0')
        except Exception:                                        # noqa: BLE001
            pass
        # ---- 网格（fast）----
        if str(p.get('fit') or 'fast') == 'fast':
            try:
                c_lo, c_st = _f(p, 'c_grid_lo', -2.0), _f(p, 'c_grid_step', 0.05)
                g_lo, g_st = _f(p, 'g_grid_lo', 0.2), _f(p, 'g_grid_step', 0.05)
                if c_st is None or c_st <= 0 or g_st is None or g_st <= 0:
                    errs.append('网格步长必须 > 0')
                ub1, ub3 = _f(p, 'ub1', 0.0), _f(p, 'ub3', 1.8)
                if None not in (c_lo, ub1) and c_lo >= ub1:
                    errs.append(f'中心网格起点（{c_lo}）≥ ub₁（{ub1}）→ 网格是空的')
                if None not in (g_lo, ub3) and g_lo >= ub3:
                    errs.append(f'线宽网格起点（{g_lo}）≥ ub₃（{ub3}）→ 网格是空的')
            except Exception:                                    # noqa: BLE001
                pass
        if _f(p, 'tol', 0.05) is not None and (_f(p, 'tol', 0.05) or 0) <= 0:
            errs.append('归一化判据 tol 必须 > 0')
        if _f(p, 'step_interp', 0.1) is not None and (_f(p, 'step_interp', 0.1) or 0) <= 0:
            errs.append('插值步长必须 > 0')
        return errs

    # ================================================================ 面板
    def preview_files(self, p, proj):
        d = self.out_dir_for(p, proj)
        if not d:
            return []
        return [str(Path(d) / x) for x in
                ('0.7ut_img_interp.mat', 'voxel_LD.mat', 'voxel_LD_interp.mat',
                 '_thmask_used.mat', 'ld_check.png', 'ld_meta.json')]

    def preview_image(self, res):
        p = (res or {}).get('png')
        return p if (p and Path(p).exists()) else None

    def extra_panel(self, page):
        from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel,
                                       QPushButton, QVBoxLayout)
        pan = QGroupBox('逐体素 LD（洛伦兹拟合）')
        v = QVBoxLayout(pan)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        b_adv = QPushButton('高级参数…')
        b_adv.setObjectName('primary')
        b_adv.setToolTip('三区间 / lb / ub / par0 / 对称性阈值 / 网格 / 插值…（带预设）')
        b_adv.clicked.connect(lambda: page._open_adv_params())
        row.addWidget(b_adv)
        b_clean = QPushButton('清空旧结果…')
        b_clean.setToolTip('一键清掉本模块输出目录里的旧备份（*.bak_<时间戳>），保留最新一组产物；\n'
                           '也可以选「清空整个病例子目录」（连最新产物一起删，会再确认）。\n'
                           '只动 04_逐体素LD\\<病例>\\，不碰 ① 重建数据 / ⑧ 掩模。')
        b_clean.clicked.connect(lambda: self._clean_results(page))
        row.addWidget(b_clean)
        pan.state = QLabel('还没准备。')
        pan.state.setObjectName('muted')
        pan.state.setWordWrap(True)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(pan.state)
        return pan

    def _clean_results(self, page):
        """【清空旧结果…】——清本模块输出目录里的旧备份 / 整个病例子目录。"""
        from PySide6.QtWidgets import QMessageBox

        from app.dialogs.clean_dialog import CleanDialog, human_size

        proj = page.window.proj()
        if not proj:
            QMessageBox.information(page, '还没有输出目录', '请先选输出总目录（PROJ）。')
            return
        p = page.params()
        case = self._case_of(p, p.get('src'))
        base = R.find_subdir(proj, self.out_subdir) or \
            (Path(proj) / R.cn_name(self.out_subdir))
        if not Path(base).exists():
            QMessageBox.information(page, '还没有结果',
                                    f'这个模块还没有输出目录：\n{base}')
            return
        dlg = CleanDialog(page, base, case, f'{self.badge} {self.title}',
                          log=page.window.log)
        if dlg.exec():
            page.window.log(f'{self.badge} 清空旧结果：删除 {dlg.n_del} 个文件，'
                            f'释放 {human_size(dlg.freed)}')
            page.refresh_preview(proj)

    def refresh_extra(self, panel, det, proj, params):
        if panel is None or not hasattr(panel, 'state'):
            return
        src = str(params.get('src') or '').strip()
        case = self._case_of(params, src)
        sd = self._src_dir(params, case) if src else Path('.')
        # ★ 只留两行：左栏纵向空间有限，4 行会被容器压掉最后一行（状态行被截）
        L1, L2 = [], []
        try:
            mf, ms = self._find_mask(params, proj, case)
            L1.append(f'掩模：{Path(mf).name}（{ms}，取第 {params.get("mask_layer") or 1} 层）'
                      if mf else '掩模：× 没找到 —— 请先在 ⑧ 去噪掩模 里画好/载入')
        except Exception as e:                                   # noqa: BLE001
            L1.append(f'掩模：× {e}')
        if src and Path(src).exists() and not R.has_all(sd, R.RECON_FILES):
            L1.append(f'数据：× 不是重建结果（缺 s0/ssat/w_offset）：{sd}')
        elif src:
            L1.append('数据：① 重建结果 ✓')
        L2.append(f'区间 {params.get("l3")}~{params.get("l2")} ∪ '
                  f'{params.get("l1")}~{params.get("h1")} ∪ '
                  f'{params.get("h2")}~{params.get("h3")}　'
                  f'算法 {params.get("fit") or "fast"}　'
                  f'lb/ub/par0 见【高级参数…】')
        from app.widgets.autosize import fit_label
        fit_label(panel.state, '　'.join(L1) + '\n' + '　'.join(L2))

    # ================================================================ 执行
    def run(self, p, ctx):
        import matplotlib
        matplotlib.use('Agg')
        import cest_ld as L
        from cest_io import AXIS_LAYER_FIRST, load_first
        from scipy.io import savemat

        stamp = time.strftime('%Y%m%d_%H%M%S')
        src = str(p.get('src') or '').strip()
        if not src:
            raise RuntimeError('④ 需要输入（① 的重建结果目录）')
        case = self._case_of(p, src)
        src_dir = self._src_dir(p, case)
        if not R.has_all(src_dir, R.RECON_FILES):
            raise RuntimeError(f'输入目录里没有 ① 的产物（s0/ssat/w_offset）：{src_dir}')
        mask_file, ms = self._find_mask(p, ctx.proj, case)
        if mask_file is None:
            raise RuntimeError('④ 需要去噪掩膜：没找到 00_掩模\\<病例>\\Mask.mat\n'
                               '请先在 ⑧ 去噪掩模 里画好/载入，或在「掩模文件」里指定。')
        out = ctx.out_dir(self.out_subdir) / case
        out.mkdir(parents=True, exist_ok=True)

        # ---------------- 进度条：整条流水线折算成 0~100 ----------------
        #   读数据 0~6 / 归一化 6~14 / 掩模 14~20 / 写 0.7ut 20~22
        #   逐层 LD 拟合 22~96 / 保存 96~98 / 质检图 98~100
        T0 = time.time()

        def _p(v, text):
            ctx.progress(int(round(v)), 100,
                         f'{text}　（已用 {time.time() - T0:.0f}s）')

        _p(2, f'读数据：{case}\w_offset / ssat / s0 / b0…')

        # ---------------- 参数组装（全部来自 GUI） ----------------
        ppm = dict(l1=_f(p, 'l1', -0.6), l2=_f(p, 'l2', -6.0), l3=_f(p, 'l3', -10.0),
                   h1=_f(p, 'h1', 0.6), h2=_f(p, 'h2', 6.0), h3=_f(p, 'h3', 10.0))
        lb = [_f(p, f'lb{i}', [-INF, -INF, 0.0, -INF][i - 1]) for i in (1, 2, 3, 4)]
        ub = [_f(p, f'ub{i}', [0.0, INF, 1.8, INF][i - 1]) for i in (1, 2, 3, 4)]
        par0 = [_f(p, f'par0{i}', [0.0, -50.0, 20.0, 1.0][i - 1]) for i in (1, 2, 3, 4)]
        if p.get('center_fix0'):
            lb[0] = ub[0] = 0.0
        bounds = (lb, ub)
        tol = _f(p, 'tol', 0.05)
        step_interp = _f(p, 'step_interp', 0.1)
        method = str(p.get('interp_method') or 'spline')
        sym = _f(p, 'sym_threshold', 5.0)
        fast = str(p.get('fit') or 'fast') != 'exact'
        save_interp = bool(p.get('save_interp', True))
        max_voxels = p.get('max_voxels')
        max_voxels = int(max_voxels) if str(max_voxels or '').strip() not in ('', 'None') else None
        c_grid = g_grid = None
        if fast:
            c_lo = _f(p, 'c_grid_lo', -2.0)
            c_st = _f(p, 'c_grid_step', 0.05)
            g_lo = _f(p, 'g_grid_lo', 0.2)
            g_st = _f(p, 'g_grid_step', 0.05)
            c_grid = np.arange(c_lo, float(ub[0]) + 1e-9, c_st)
            g_grid = np.arange(g_lo, float(ub[2]) + 1e-9, g_st)
            if c_grid.size == 0 or g_grid.size == 0:
                raise RuntimeError(f'搜索网格是空的：中心 {c_lo}~{ub[0]} 步 {c_st}；'
                                   f'线宽 {g_lo}~{ub[2]} 步 {g_st}')

        ctx.log(f'④ {case}：拟合算法 {"fast（网格+闭式解）" if fast else "exact（逐体素）"}'
                f'　区间 {ppm["l3"]}~{ppm["l2"]} ∪ {ppm["l1"]}~{ppm["h1"]} ∪ '
                f'{ppm["h2"]}~{ppm["h3"]}')
        ctx.log(f'   lb {lb}')
        ctx.log(f'   ub {ub}')
        ctx.log(f'   par0 {par0}' + ('（中心固定 0）' if p.get('center_fix0') else ''))
        ctx.log(f'   网格 中心 {c_grid[0]:.3g}~{c_grid[-1]:.3g}/{c_st} '
                f'× 线宽 {g_grid[0]:.3g}~{g_grid[-1]:.3g}/{g_st}'
                if fast else '   exact：逐体素 lsqcurvefit 等价')

        # ---------------- 读数据 + 归一化（与 make_voxel_ld 同口径） ----------------
        w = np.asarray(load_first(src_dir / 'w_offset.mat', ('w_offset', 'w')),
                       float).ravel()
        ssat = np.asarray(load_first(src_dir / 'ssat.mat', ('zdata', 'ssat')), float)
        s0 = np.asarray(load_first(src_dir / 's0.mat', ('s0', 'S0')), float)
        _bf = src_dir / 'b0.mat'
        _b0 = load_first(_bf, ('b0', 'B0')) if _bf.exists() else None
        b0 = np.asarray(_b0, float) if _b0 is not None else np.empty(0)
        _p(6, f'读数据完成：ssat {ssat.shape} / s0 {s0.shape} / {w.size} 个频偏')

        if ssat.ndim == 3:
            ssat = ssat[:, :, :, None]
        elif ssat.ndim == 4 and ssat.shape[3] != w.size and ssat.shape[2] == w.size:
            ssat = np.swapaxes(ssat, 2, 3)
        if ssat.ndim != 4 or ssat.shape[3] != w.size:
            raise RuntimeError(f'ssat 形状 {ssat.shape} 与 {w.size} 个频偏不匹配'
                               f'（应为 [X,Y,层,频]）')
        s0 = np.asarray(s0, dtype=float)
        if s0.ndim == 3:
            s0_3d = s0
        elif s0.ndim == 4 and s0.shape[2] == 1:
            s0_3d = s0[:, :, 0, :]
        elif s0.ndim == 4 and s0.shape[3] == 1:
            s0_3d = s0[:, :, :, 0]
        else:
            s0_3d = np.squeeze(s0)
        if s0_3d.shape[0] != ssat.shape[0] or s0_3d.shape[1] != ssat.shape[1]:
            raise RuntimeError(f's0 {s0_3d.shape} 与数据 {ssat.shape[:2]} 尺寸不一致')
        if np.size(b0):
            if b0.ndim == 4 and b0.shape[2] == 1:
                b0 = b0[:, :, 0, :]
            elif b0.ndim == 4 and b0.shape[3] == 1:
                b0 = b0[:, :, :, 0]

        _p(8, '归一化：除 S0 → 裁剪到 1~99 分位…')
        if L.is_already_norm(ssat, tol):
            ctx.log('   [检查] 已在 [0,1] 附近 → 跳过除 S0')
            z = ssat
        else:
            den = s0_3d.copy()
            den[den <= 0] = np.nan
            z = ssat / den[:, :, :, None]
            fin = z[np.isfinite(z)]
            ctx.log(f'   除 S0 后 median = {np.median(fin):.4f}'
                    if fin.size else '   除 S0 后无有效值')
        img_interp, info = L.normalize_spect0916(z, tol, 1, 99, False)
        _p(14, '归一化完成')
        ctx.log(f'   归一化后 min {img_interp.min():.4f} / max {img_interp.max():.4f}'
                f'　裁剪 <0 {info["nClipLow"]} / >1 {info["nClipHigh"]}')
        mz = img_interp
        nx, ny, n_layer, nw = mz.shape

        # ---------------- 掩模（取第 k 层 2D，留痕） ----------------
        mask = np.asarray(load_first(Path(mask_file), ('Mask', 'Thmask', 'liverMask')),
                          float)
        _p(16, f'掩模：{Path(mask_file).name}')
        if mask.ndim == 4 and mask.shape[2] == 1:
            mask = mask[:, :, 0, :]
        elif mask.ndim == 4 and mask.shape[3] == 1:
            mask = mask[:, :, :, 0]
        k = int(_f(p, 'mask_layer', 1) or 1)
        if mask.ndim == 3:
            k = min(max(k, 1), mask.shape[2])
            thmask = mask[:, :, k - 1]
        else:
            thmask = mask
        thmask = np.asarray(thmask, float)
        if thmask.shape != (nx, ny):
            raise RuntimeError(f'掩模形状 {thmask.shape} 与数据 {(nx, ny)} 不一致')
        ctx.log(f'   掩模 {mask_file} → 取第 {k} 层，形状 {thmask.shape}，'
                f'覆盖 {int(np.count_nonzero(thmask))} 个体素')
        _backup(out / '_thmask_used.mat', stamp, ctx.log)
        savemat(str(out / '_thmask_used.mat'), {'Thmask': thmask}, do_compression=True)
        _p(20, f'掩模就绪（第 {k} 层，{int(np.count_nonzero(thmask))} 个体素）')

        # ---------------- 存 0.7ut_img_interp.mat（② 可直接复用） ----------------
        _backup(out / '0.7ut_img_interp.mat', stamp, ctx.log)
        _p(22, '写 0.7ut_img_interp.mat…')
        savemat(str(out / '0.7ut_img_interp.mat'),
                {'s0': s0_3d, 'b0': b0, 'norm_z': img_interp,
                 'img_interp': img_interp, 'w': w.reshape(1, -1),
                 'axis_order': AXIS_LAYER_FIRST,
                 'S0': s0_3d, 'w_offset': w.reshape(1, -1)},
                do_compression=True)
        ctx.log(f'   已写 {out / "0.7ut_img_interp.mat"}（s0 {s0_3d.shape} / '
                f'img_interp {img_interp.shape}）')

        # ---------------- 逐层拟合 ----------------
        nwi = int(round((w.max() - w.min()) / step_interp)) + 1
        ld_all = np.zeros((nx, ny, n_layer, nw))
        ld_interp_all = np.full((nx, ny, n_layer, nwi), np.nan)
        w_interp = np.arange(w.min(), w.max() + step_interp / 2, step_interp).reshape(-1, 1)
        layer_list = [int(v) for v in str(p.get('layers') or '').replace('，', ',')
                      .split(',') if v.strip()] or list(range(1, n_layer + 1))
        bad = [s for s in layer_list if not (1 <= s <= n_layer)]
        if bad:
            raise RuntimeError(f'层号超出范围 1~{n_layer}：{bad}')
        _filt = L.voxel_ld_filter_fast if fast else L.voxel_ld_filter
        per_layer = []
        t_all = time.time()
        t_fit0 = t_all
        n_lyr = len(layer_list)
        ctx.log(f'   ▶ 开始逐层 LD 拟合：{n_lyr} 层 × {nw} 个频偏（{method}）')
        for idx, s in enumerate(layer_list, 1):
            ctx.check_cancel()
            t0 = time.time()
            ctx.progress(int(round(22 + 74 * (idx - 1) / n_lyr)), 100,
                         f'LD 拟合：第 {s}/{n_layer} 层 开始…（已用 {time.time() - T0:.0f}s）')
            v_slice = np.squeeze(mz[:, :, s - 1, :])
            v_mask = v_slice * np.asarray(thmask)[:, :, None]
            kw = dict(ppm=ppm, par0=par0, bounds=bounds, sym_threshold=sym,
                      c_grid=c_grid, g_grid=g_grid, verbose=False, max_voxels=max_voxels)
            if not fast:
                kw['interp_method'] = method
            ld, _w2, ld_i, _a, lw, _wi = _filt(v_mask, w, step_interp, **kw)
            ld_all[:, :, s - 1, :] = ld
            if ld_i.shape[2] == ld_interp_all.shape[3]:
                ld_interp_all[:, :, s - 1, :] = ld_i
            dt = time.time() - t0
            n_ok = int(np.sum(np.isfinite(lw)))
            lw_med = float(np.nanmedian(lw)) if n_ok else float('nan')
            per_layer.append(dict(layer=s, sec=round(dt, 2), n_valid=n_ok,
                                  lw_median=None if n_ok == 0 else round(lw_med, 4)))
            ctx.log(f'   第 {s}/{n_layer} 层完成（{dt:.1f}s，有效体素 {n_ok}，'
                    f'线宽中位数 {lw_med:.3f}）')
            _avg = (time.time() - t_fit0) / idx
            _eta = _avg * (n_lyr - idx)
            ctx.progress(int(round(22 + 74 * idx / n_lyr)), 100,
                         f'LD 拟合 第 {idx}/{n_lyr} 层 · 本层 {dt:.1f}s · 均 {_avg:.1f}s · 剩 {_eta:.0f}s')

        # ---------------- 保存 ----------------
        _p(96, '保存 voxel_LD.mat…')
        _backup(out / 'voxel_LD.mat', stamp, ctx.log)
        savemat(str(out / 'voxel_LD.mat'),
                {'voxel_ld': ld_all, 'Voxel_LD_all': ld_all,
                 'axis_order': AXIS_LAYER_FIRST}, do_compression=True)
        ctx.log(f'   已写 {out / "voxel_LD.mat"}（voxel_ld {ld_all.shape} = [X,Y,层,频]）')
        files = [out / '0.7ut_img_interp.mat', out / 'voxel_LD.mat']
        if save_interp:
            _backup(out / 'voxel_LD_interp.mat', stamp, ctx.log)
            savemat(str(out / 'voxel_LD_interp.mat'),
                    {'voxel_ld_interp': ld_interp_all,
                     'Voxel_LD_interp_all': ld_interp_all,
                     'axis_order': AXIS_LAYER_FIRST,
                     'w_offset_ld_interp': w_interp,
                     'w_offsetLD_interp': w_interp}, do_compression=True)
            ctx.log(f'   已写 {out / "voxel_LD_interp.mat"}（{ld_interp_all.shape}）')
            files.append(out / 'voxel_LD_interp.mat')

        # ---------------- 质检图 ----------------
        _p(98, '出质检图…')
        png = None
        try:
            png = self._qc_png(out, ld_all, w, per_layer, case, int(p.get('dpi') or 150))
            ctx.log(f'   已写 {png}')
            files.append(png)
        except Exception as e:                                   # noqa: BLE001
            ctx.log(f'   [提示] 质检图生成失败：{type(e).__name__}: {e}')

        # ---------------- meta ----------------
        meta = dict(case=case, src=str(src_dir), mask_file=str(mask_file),
                    mask_layer=int(k), fit=('fast' if fast else 'exact'),
                    ppm=ppm, lb=lb, ub=ub, par0=par0,
                    center_fix0=bool(p.get('center_fix0')),
                    sym_threshold=sym, tol=tol, step_interp=step_interp,
                    interp_method=method, save_interp=bool(save_interp),
                    max_voxels=max_voxels,
                    c_grid=[float(c_grid[0]), float(c_grid[-1]), float(_f(p, 'c_grid_step', 0.05))]
                    if fast else None,
                    g_grid=[float(g_grid[0]), float(g_grid[-1]), float(_f(p, 'g_grid_step', 0.05))]
                    if fast else None,
                    layers=layer_list, n_layer=int(n_layer), n_freq=int(nw),
                    shape=[int(nx), int(ny)],
                    total_sec=round(time.time() - t_all, 1), per_layer=per_layer,
                    note='归一化：除 S0 → normalizeSpect0916(tol,1,99,False) 只裁剪'
                         '（核心层口径）；分位 1/99 固定')
        (out / 'ld_meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        ctx.log(f'   已写 {out / "ld_meta.json"}')
        files.append(out / 'ld_meta.json')
        _p(100, f'④ 完成（共 {time.time() - T0:.0f}s）')

        return dict(png=str(png) if png else None, out_dir=str(out), case=case,
                    files=[str(x) for x in files], meta=meta)

    # ---------------------------------------------------------------- 质检图
    def _qc_png(self, out, ld_all, w, per_layer, case, dpi=150):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
        plt.rcParams['axes.unicode_minus'] = False
        cmap = 'jet'
        n_layer = ld_all.shape[2]
        mid = n_layer // 2
        kf = int(np.argmin(np.abs(np.asarray(w) - 3.5)))
        fig, ax = plt.subplots(1, 3, figsize=(14.4, 5.0), dpi=int(dpi))
        a = np.asarray(ld_all[:, :, mid, kf], float)
        ax[0].imshow(a, cmap=cmap)
        ax[0].set_title(f'LD 中层 {mid + 1}  @ {np.asarray(w).ravel()[kf]:.1f} ppm', fontsize=10)
        ax[1].imshow(np.abs(a), cmap='gray')
        ax[1].set_title('|LD| 同层（看结构）', fontsize=10)
        for k in range(3):
            ax[k].set_axis_off()
        yy = [x['layer'] for x in per_layer]
        vv = [x['lw_median'] if x['lw_median'] is not None else np.nan for x in per_layer]
        ax[2].plot(yy, vv, 'o-', color='#06b6d4')
        ax[2].set_xlabel('层')
        ax[2].set_ylabel('线宽中位数 (ppm)')
        ax[2].set_title('每层线宽中位数', fontsize=10)
        ax[2].grid(alpha=0.3)
        fig.suptitle(f'{case}　逐体素 LD（④）　{len(per_layer)} 层', fontsize=12)
        try:
            fig.tight_layout()
        except Exception:                                        # noqa: BLE001
            pass
        png = Path(out) / 'ld_check.png'
        fig.savefig(str(png))
        plt.close(fig)
        return png
