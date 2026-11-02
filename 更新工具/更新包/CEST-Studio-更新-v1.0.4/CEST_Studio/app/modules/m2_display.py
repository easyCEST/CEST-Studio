# -*- coding: utf-8 -*-
"""app/modules/m2_display.py —— ② 多层图谱展示（raw / ST / APTW）。

底层：cest_display.display_single / build_mode_data / gen_mosaic（算法一行不改）。
★ 依赖：PROJ\\00_掩模\\<病例>\\Mask.mat（去噪掩膜，**必填门禁**）；**不读 ROI_total**。
★ 输入若是「重建结果」（只有 s0/ssat/w_offset）→ 生成 **一次** 0.7ut_img_interp.mat 到
  02_展示\\<病例>\\_cache\\，之后复用（原来每跑一个 mode 都重算重写 280MB）。
★ 命名与以前逐字一致：`{MODE} {w}ppm_combined.png/.mat/.json`；同名重跑先备份 .bak_<时间戳>。
★ LD 模式暂不开放（等 ④ 逐体素LD 接入后打开）。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np

from .base import Field, Module

from detect import rules as R          # PROJ 子目录名（中文优先，兼容旧英文名）

# MATLAB 口径的常用色标（caxis_mode='manual' 且没填时的兜底）
DEFAULT_CAXIS = {'raw': None, 'ST': (0.15, 0.35), 'APTW': (-0.05, 0.03), 'LD': (0.03, 0.10)}

# 展示类型
AXIS_AUTO = '自动'
AXIS_LAYER = '固定频偏（逐层）'
AXIS_FREQ = '固定层（全频率）'
AXIS_PICK = '特定频率'
AXIS_CHOICES = (AXIS_AUTO, AXIS_LAYER, AXIS_FREQ, AXIS_PICK)

INTERP_FILES = ('0.7ut_img_interp.mat', '0p7P_interp.mat',
                '0p7N_interp.mat', '2APT_interp.mat')

CACHE_DIR = '_cache'
CACHE_FILE = '0.7ut_img_interp.mat'
OUT_EXTS = ('.png', '.mat', '.json')


def _has_interp(d: Path) -> bool:
    return any((d / f).exists() for f in INTERP_FILES)


def is_recon_dir(d: Path) -> bool:
    """是不是 ① 的重建结果目录（只有 s0/ssat/w_offset，没有 img_interp）。"""
    return R.has_all(d, R.RECON_FILES)


def combined_name(mode, w) -> str:
    """与 cest_display.display_single 的命名**逐字一致**：`{MODE} {w}ppm_combined`。

    （只做「小数点→下划线」，与核心层 `_matlab_name` 同规则；不改任何前后缀。）
    """
    return (f'{mode} {w}ppm').replace('.', '_') + '_combined'


def _backup_combined(out_dir: Path, name: str, log=print) -> None:
    """同名三件套覆盖前先备份 <名>.bak_<时间戳>.<ext>（照 ⑧ 的做法）。"""
    out_dir = Path(out_dir)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    for ext in ('.png', '.mat', '.json'):
        f = out_dir / f'{name}{ext}'
        if f.exists():
            bak = out_dir / f'{name}.bak_{stamp}{ext}'
            try:
                shutil.copy2(f, bak)
                log(f'  已有 {f.name} → 备份为 {bak.name}')
            except OSError as e:                                 # noqa: BLE001
                log(f'  [提示] 备份失败（继续）：{e}')


def save_mosaic(img3d, name, mode, w, caxis, cols, dpi, out_dir, source,
                layers=None, cmap_n_black=1, log=print,
                title_values=None, title_prefix=None, title_fmt=None):
    """落盘（② 的【开始】与图谱预览的【导出】**走同一条路径**，保证命名/数值一致）。

    title_values/title_fmt：按频率展示时逐子图标题用 ppm（核心 gen_mosaic 支持）。
    """
    import cest_display as D

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _backup_combined(out_dir, name, log)
    opts = dict(
        title_prefix=f'{mode} {w}ppm',
        layers=list(layers or range(1, int(img3d.shape[2]) + 1)),
        cols=int(cols), caxis=caxis, cmap_n_black=int(cmap_n_black),
        cbar_pos='bottom', dpi=int(dpi), save_dir=str(out_dir), name=name,
        mode=mode, w=w, source=str(source))
    if title_values is not None:
        opts['title_prefix'] = title_prefix or f'{mode}'
        opts['title_values'] = [float(v) for v in title_values]
        opts['title_fmt'] = title_fmt or '{prefix}  {val:.2f} ppm'
    fig = D.gen_mosaic(img3d, opts)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return [out_dir / f'{name}{ext}' for ext in ('.png', '.mat', '.json')]


def build_mode_and_caxis(d, mode, w, use_mask=True, caxis_mode='manual',
                         caxis=None, layers=None, ld_mode='POS', verbose=True):
    """取要画的 (X,Y,层) + 定色标（manual 与 display_single 同口径；自适应按 MATLAB 口径取数）。

    ★ 直接复用核心层的 build_mode_data / layer_quantile（经 build_mode_data）。
    （不用 display_single，是为了避免把 160MB 的 img_interp 重复读两遍，
      且把「保存」收敛到 save_mosaic 一处。）

    返回 (img3d, caxis, qsrc)：
      img3d —— 要画的数据（用掩模时背景 = -1，与 MATLAB 画图口径一致）
      qsrc  —— 自适应色标的取数源（用掩模时 = img .* Mask，背景 = 0）
    ★ 自适应色标必须在 qsrc 上统计：MATLAB 的 plotLayerQuantileCurve 是
      `valid = (img .* Mask); valid = valid(valid ~= 0)` —— 分母是「背景=0」那张，
      而画图那张背景是 -1；两者混用会把 -1 当有效值、把色标算歪。
    """
    import cest_display as D

    img3d, mask = D.build_mode_data(d, mode=mode, w_target=w, w_mirror=-w,
                                     ld_mode=ld_mode, use_mask=use_mask,
                                     verbose=verbose)
    qsrc = np.asarray(img3d, dtype=float)
    if use_mask and mask is not None and np.size(mask):
        plain, _m = D.build_mode_data(d, mode=mode, w_target=w, w_mirror=-w,
                                       ld_mode=ld_mode, use_mask=False,
                                       verbose=False)
        m3 = np.asarray(mask, dtype=float)
        qsrc = (plain * (m3 > 0)) if (m3.ndim == 3 and m3.shape[:2] == plain.shape[:2]) \
            else np.asarray(plain, dtype=float)
    lay = list(layers or range(1, int(img3d.shape[2]) + 1))
    cax = caxis
    if caxis_mode == 'per_layer_5_95':
        lo, hi = D.layer_quantile(qsrc, lay)          # 内部已按「非 0」筛
        cax = (float(np.min(lo)), float(np.max(hi)))
    elif caxis_mode == 'global_5_95':
        v = np.asarray(qsrc)[:, :, [int(x) - 1 for x in lay]]
        v = v[np.isfinite(v) & (v != 0)]
        cax = ((float(np.percentile(v, 5)), float(np.percentile(v, 95)))
               if v.size else (0.0, 1.0))
    return img3d, cax, qsrc


def build_freq_stack(d, mode, layer, use_mask=True, ld_mode='POS'):
    """按「频率」铺子图：取固定层，把整条频偏轴展开成 (X,Y,F)。

    只支持 raw / ST / LD（APTW 是 ± 差值，没有逐频点概念，调用前先拦）。
    """
    import cest_display as D

    img = np.asarray(d['img_interp'], float)
    if img.ndim != 4:
        raise RuntimeError(f'数据不是 4D (X,Y,层,频)：{img.shape}')
    if mode == 'ST':
        src = D.process_img_interp(img)
    elif mode == 'LD':
        if 'voxel_ld' not in d:
            raise RuntimeError('LD 展示需要 ④ 逐体素LD 的 voxel_LD.mat（先跑 ④）')
        src = np.asarray(d['voxel_ld'], float)
    else:
        src = img
    L = int(src.shape[2])
    lay = int(np.clip(int(layer), 1, L))
    stack = np.asarray(src, float)[:, :, lay - 1, :]                 # (X,Y,F)
    if use_mask and d.get('mask') is not None:
        m = np.asarray(d['mask'], float)
        ms = m if m.ndim == 2 else m[:, :, min(lay - 1, m.shape[2] - 1)]
        m3 = np.repeat(ms[:, :, None], int(stack.shape[2]), axis=2)
        stack = D.apply_mask(stack, m3)
    return stack


class DisplayModule(Module):
    id = 'm2_display'
    badge = '②'
    title = '多层图谱展示'
    out_subdir = '02_display'
    case_in_out = True
    accepts = ('recon_mat', 'recon_root', 'img_mat', 'project_root')
    produces = ('display_out',)
    chain_proj = True               # 输入根默认 = PROJ（要 01_重建 + 00_掩模）
    chain_fill = ('case', 'src', 'mask_file')
    schema = (
        Field('src', '输入', 'file', tip='重建结果目录，或含 img_interp 的目录/.mat'),
        Field('case', '病例名', 'text', tip='输出到 02_展示\\<病例名>\\'),
        Field('mode', '图类型', 'choice', 'APTW', ('raw', 'ST', 'APTW', 'LD'),
              tip='raw / ST / APTW；LD 需要先跑 ④ 逐体素LD（有 voxel_LD.mat 才能出图）'),
        Field('w', '目标频偏 ppm', 'float', 3.5),
        Field('axis', '展示类型', 'choice', AXIS_AUTO, AXIS_CHOICES,
              tip=f'{AXIS_AUTO}：单层→全频率；多层→固定频偏逐层 / '
                  f'{AXIS_LAYER}：固定 w，逐层 / {AXIS_FREQ}：固定层，逐频偏 / '
                  f'{AXIS_PICK}：你指定的几个频率（5 个输入框，留空=跳过）'),
        Field('layer', '指定层（固定层全频率用）', 'int', 1,
              show_if=('axis', (AXIS_FREQ,)), tip='单层数据自动=1；多层手选看哪层'),
        Field('freq1', '特定频率 1 ppm', 'float', -3.5, show_if=('axis', (AXIS_PICK,)),
              tip='留空=跳过该格；无精确帧时自动取最近帧'),
        Field('freq2', '特定频率 2 ppm', 'float', -2.0, show_if=('axis', (AXIS_PICK,))),
        Field('freq3', '特定频率 3 ppm', 'float', 2.0, show_if=('axis', (AXIS_PICK,))),
        Field('freq4', '特定频率 4 ppm', 'float', 2.6, show_if=('axis', (AXIS_PICK,))),
        Field('freq5', '特定频率 5 ppm', 'float', 3.5, show_if=('axis', (AXIS_PICK,))),
        Field('cols', '每行子图数', 'int', 5),
        Field('caxis_mode', '色标策略', 'choice', 'manual',
              ('manual', 'per_layer_5_95', 'global_5_95'),
              tip='manual=照 MATLAB 手输；两种 5~95% 只作参考'),
        Field('caxis_lo', '色标下限', 'float', '', tip='留空=按图类型用常用口径'),
        Field('caxis_hi', '色标上限', 'float', '', tip='留空=按图类型用常用口径'),
        Field('mask_file', '掩模文件', 'file', '',
              tip='留空=自动找 00_掩模\\<病例>\\Mask.mat（必填门禁），再找输入目录'),
        Field('use_mask', '用掩模压背景', 'bool', True, tip='口径 .*Mask - (~Mask)，背景强制 -1'),
        Field('ld_mode', 'LD 正负', 'choice', 'POS', ('POS', 'NEG'), modes=('LD',),
              tip='LD 取 w_target（POS，看 NOE/-1.2 ppm 一带）/ -w_target（NEG）'),
        Field('layers', '层', 'text', '', tip='留空=全部层；如 1,3,5'),
        Field('dpi', '保存分辨率', 'int', 300),
    )

    # ================================================================ 参数
    def suggest(self, det, proj):
        p = {'mode': 'APTW', 'w': 3.5, 'axis': AXIS_AUTO, 'layer': 1,
             'freq1': -3.5, 'freq2': -2.0, 'freq3': 2.0, 'freq4': 2.6, 'freq5': 3.5,
             'cols': 5, 'caxis_mode': 'manual',
             'use_mask': True, 'ld_mode': 'POS', 'dpi': 300, 'layers': '',
             'caxis_lo': '', 'caxis_hi': '', 'mask_file': ''}
        if det is None:
            return p
        p['src'] = det.path
        if det.kind == 'project_root':
            p['src'] = ''
            p['case'] = ''
            return p
        p['case'] = (det.cases[0] if det.cases else Path(det.path).name)
        if det.kind == 'recon_root' and det.cases:
            # 「多病例的已重建目录」→ 默认取第一个病例的子目录
            p['src'] = str(Path(det.path) / det.cases[0])
        if proj and p['case']:
            _rd = R.find_subdir(proj, '00_roi')
            mf = (_rd / p['case'] / 'Mask.mat') if _rd else None
            if mf is not None and mf.exists():
                p['mask_file'] = str(mf)
        return p

    # ================================================================ 输入解析
    def _src_dir(self, p, case) -> Path:
        """src 指向「多病例父目录」时自动下钻到该病例子目录。"""
        src = str(p.get('src') or '').strip()
        s = Path(src)
        if (s.is_dir() and not _has_interp(s) and not is_recon_dir(s)
                and case and (s / case / 's0.mat').exists()):
            return s / case
        return s

    def _find_mask(self, p, proj, case, src_dir=None):
        """返回 (掩模路径, 来源说明)。优先级：参数 → 00_掩模\\<病例>\\ → 输入目录。"""
        cands = []
        mf = str(p.get('mask_file') or '').strip()
        if mf:
            cands.append((Path(mf), '参数指定'))
        if proj and case:
            _rd = R.find_subdir(proj, '00_roi')
            if _rd:
                cands.append((Path(_rd) / case / 'Mask.mat', '00_掩模\\Mask.mat'))
                cands.append((Path(_rd) / case / 'mask.mat', '00_掩模\\mask.mat'))
                cands.append((Path(_rd) / case / 'liverMask.mat', '00_掩模\\liverMask.mat'))
        for d in (src_dir,):
            if d:
                cands.append((Path(d) / 'Mask.mat', '输入目录\\Mask.mat'))
                cands.append((Path(d) / 'liverMask.mat', '输入目录\\liverMask.mat'))
        for f, src in cands:
            if f.exists():
                return str(f), src
        return None, ''

    def _resolve_data(self, p, proj, case, src_dir):
        """找可用的展示数据（只读，不生成）。返回 (路径, 说明) 或 (None, '')。"""
        s = Path(str(p.get('src') or '').strip())
        if s.is_file():
            return str(s), '参数指定的 .mat'
        if _has_interp(s):
            f = next((s / n for n in INTERP_FILES if (s / n).exists()), None)
            return str(f), '输入目录里的 interp'
        if proj and case:
            _ld = R.find_subdir(proj, '04_ld')
            f = (_ld / case / CACHE_FILE) if _ld else None
            if f is not None and f.exists():
                return str(f), '④ 逐体素LD 的产物（复用）'
            _out = self.out_dir_for(p, proj)
            f = (Path(_out) / CACHE_DIR / CACHE_FILE) if _out else None
            if f is not None and f.exists():
                return str(f), f'{CACHE_DIR}\\{CACHE_FILE}（复用）'
        return None, ''

    def _ensure_data(self, p, proj, case, src_dir, mask_file, log=print):
        """要用的数据：能复用就复用；否则（输入是重建结果）生成一次到 _cache。"""
        import cest_ld as L

        dp, dsrc = self._resolve_data(p, proj, case, src_dir)
        if dp is not None:
            log(f'  数据：{Path(dp).name}（{dsrc}）')
            return dp, dsrc
        if is_recon_dir(src_dir):
            out = self.out_dir_for(p, proj)
            if not out:
                raise RuntimeError('需要先选输出总目录（PROJ）')
            cache = Path(out) / CACHE_DIR
            log(f'  输入是重建结果（无 img_interp）→ 生成一次到 {CACHE_DIR}\\（后续复用，不再重算）')
            L.make_img_interp(str(src_dir), str(cache), mask_file=mask_file, verbose=True)
            return str(cache / CACHE_FILE), f'{CACHE_DIR}（本次生成）'
        raise RuntimeError(
            '输入里没有 img_interp，也不是 ① 的重建结果目录\n'
            '（要 0.7ut_img_interp.mat / 0p7P_interp.mat …，或含 s0+ssat+w_offset 的重建结果）')

    # ================================================================ 校验
    def validate(self, p, ctx=None):
        errs = []
        proj = getattr(ctx, 'proj', None)
        src = str(p.get('src') or '').strip()
        if not src:
            errs.append('② 需要输入（重建结果目录，或含 img_interp 的目录/.mat）')
        elif not Path(src).exists():
            errs.append(f'输入不存在：{src}')
        case = str(p.get('case') or '').strip() or (Path(src).name if src else '')
        mode = str(p.get('mode') or '').upper()
        # ---- APTW 是按频率展示的例外（± 差值，无逐频点概念）----
        if mode == 'APTW' and str(p.get('axis') or AXIS_AUTO) in (AXIS_FREQ, AXIS_PICK):
            errs.append('APTW 是 ± 差值（没有逐频点概念）：'
                        '「固定层(全频率)/特定频率」请改用 ST / raw / LD')
        # ---- 去噪掩膜门禁（② 必须压背景）----
        if not errs:
            src_dir = self._src_dir(p, case)
            mf, _msrc = self._find_mask(p, proj, case, src_dir)
            if mf is None:
                errs.append('② 需要去噪掩膜：没找到 00_掩模\\<病例>\\Mask.mat\n'
                            '（请先在 ⑧ 去噪掩模 里画好/载入，或在「掩模文件」里指定）')
            dp, _dsrc = self._resolve_data(p, proj, case, src_dir)
            if dp is None and not is_recon_dir(src_dir):
                errs.append('输入里没有 img_interp，也不是重建结果目录 → 没数据可出图')
        return errs

    # ================================================================ 预览
    def preview_files(self, p, proj):
        d = self.out_dir_for(p, proj)
        if not d:
            return []
        mode = str(p.get('mode') or 'APTW').upper()
        axis = str(p.get('axis') or AXIS_AUTO)
        try:
            _w = float(p.get('w') or 3.5)
        except (TypeError, ValueError):
            _w = 3.5
        if axis == AXIS_PICK:
            sub = Path(d) / '特定频率'
            return [str(sub), str(sub / f'{mode} 特定频率_combined.png'),
                    str(sub / f'{mode} 特定频率_combined.json')]
        if axis == AXIS_FREQ:
            name = f'{mode} slice{int(p.get("layer") or 1)}_allfreq_combined'
            return [str(Path(d) / f'{name}{ext}') for ext in ('.png', '.mat', '.json')]
        name = combined_name(mode, _w)
        return [str(Path(d) / f'{name}{ext}') for ext in ('.png', '.mat', '.json')]

    def preview_image(self, res):
        p = (res or {}).get('png')
        return p if (p and Path(p).exists()) else None

    # ================================================================ 自定义面板
    def extra_panel(self, page):
        from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel,
                                       QPushButton, QVBoxLayout)
        pan = QGroupBox('多层图谱展示（raw / ST / APTW）')
        v = QVBoxLayout(pan)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        b_prev = QPushButton('打开图谱预览…')
        b_prev.setObjectName('primary')
        b_prev.setToolTip('交互式看：层览翻层 / 缩放平移 / 实时改色标 / 导出（与【开始】同一条保存路径）')
        b_prev.clicked.connect(lambda: self._open_preview(page))
        row.addWidget(b_prev)
        row.addStretch(1)
        v.addLayout(row)
        pan.state = QLabel('还没准备。')
        pan.state.setObjectName('muted')
        pan.state.setWordWrap(True)
        v.addWidget(pan.state)
        return pan

    def refresh_extra(self, panel, det, proj, params):
        if panel is None or not hasattr(panel, 'state'):
            return
        lines = []
        src = str(params.get('src') or '').strip()
        case = str(params.get('case') or '').strip() or (Path(src).name if src else '')
        src_dir = self._src_dir(params, case) if src else Path('.')
        try:
            mf, msrc = self._find_mask(params, proj, case, src_dir)
            lines.append(f'掩模：{Path(mf).name}（{msrc}）' if mf else
                         '掩模：× 没找到 —— 请先在 ⑧ 去噪掩模 里画好/载入')
            dp, dsrc = self._resolve_data(params, proj, case, src_dir)
            if dp:
                lines.append(f'数据：{Path(dp).name}（{dsrc}）')
            elif src and is_recon_dir(src_dir):
                lines.append(f'数据：待生成（首次出图会写 {CACHE_DIR}\\{CACHE_FILE}，之后复用）')
            else:
                lines.append('数据：× 没找到 img_interp')
        except Exception as e:                                   # noqa: BLE001
            lines.append(f'× {e}')
        from app.widgets.autosize import fit_label
        fit_label(panel.state, '\n'.join(lines))   # 可滚动区里 QLabel 会被截一行

    # ================================================================ 执行
    def _find_ld(self, proj, case, data_path):
        """找 ④ 的 voxel_LD.mat（② 的 LD 模式用）。"""
        cands = []
        if proj and case:
            _ld = R.find_subdir(proj, '04_ld')
            if _ld:
                cands.append(Path(_ld) / case / 'voxel_LD.mat')
        if data_path:
            cands.append(Path(data_path).parent / 'voxel_LD.mat')
        for f in cands:
            if f.exists():
                return str(f)
        return None

    def _prepare(self, p, proj, log=print):
        """解析 → 门禁 → 数据（必要时生成）→ load_display_input。返回 (case, d, data_path, out)。"""
        import cest_display as D

        src = str(p.get('src') or '').strip()
        if not src:
            raise RuntimeError('② 需要输入（重建结果目录，或含 img_interp 的目录/.mat）')
        case = str(p.get('case') or '').strip() or Path(src).name
        src_dir = self._src_dir(p, case)
        mask_file, msrc = self._find_mask(p, proj, case, src_dir)
        if mask_file is None:
            raise RuntimeError(
                '② 需要去噪掩膜：没找到 00_掩模\\<病例>\\Mask.mat\n'
                '请先在 ⑧ 去噪掩模 里画好/载入，或在「掩模文件」里指定。')
        log(f'  掩模：{mask_file}（{msrc}）')
        data_path, dsrc = self._ensure_data(p, proj, case, src_dir, mask_file, log=log)
        ld_file = self._find_ld(proj, case, data_path)
        if ld_file:
            log(f'  LD 数据：{Path(ld_file).name}（④ 逐体素LD 的产物）')
        d = D.load_display_input(data_path, verbose=True, mask_file=mask_file,
                                 ld_file=ld_file)
        out = self.out_dir_for(p, proj)
        if not out:
            raise RuntimeError('需要先选输出总目录（PROJ）')
        return case, d, data_path, Path(out)

    @staticmethod
    def _pick_caxis(img, caxis_mode, caxis, log=print):
        """按频率铺的子图定色标：manual 直接用；两种 5~95% 在非 0 有效值上取。"""
        v = np.asarray(img, float)
        if caxis_mode == 'manual':
            return caxis
        if caxis_mode == 'per_layer_5_95':
            lo, hi = [], []
            for k in range(int(v.shape[2])):
                c = v[:, :, k]
                c = c[np.isfinite(c) & (c != 0)]
                if c.size:
                    lo.append(float(np.percentile(c, 5)))
                    hi.append(float(np.percentile(c, 95)))
            cax = (float(min(lo)), float(max(hi))) if lo else (0.0, 1.0)
        else:   # global_5_95
            g = v[np.isfinite(v) & (v != 0)]
            cax = ((float(np.percentile(g, 5)), float(np.percentile(g, 95)))
                   if g.size else (0.0, 1.0))
        log(f'  自适应色标（{caxis_mode}）= {cax}')
        return cax

    def run(self, p, ctx):
        import matplotlib
        matplotlib.use('Agg')

        mode = str(p.get('mode') or 'APTW').upper()
        w = float(p.get('w') or 3.5)
        case, d, data_path, out = self._prepare(p, ctx.proj, log=ctx.log)
        out.mkdir(parents=True, exist_ok=True)

        caxis_mode = str(p.get('caxis_mode') or 'manual')
        caxis = None
        if caxis_mode == 'manual':
            lo = str(p.get('caxis_lo') or '').strip()
            hi = str(p.get('caxis_hi') or '').strip()
            caxis = (float(lo), float(hi)) if (lo and hi) else DEFAULT_CAXIS.get(mode)
        layers = [int(v) for v in str(p.get('layers') or '').replace('，', ',')
                  .split(',') if v.strip()] or None
        cols = int(p.get('cols') or 5)
        dpi = int(p.get('dpi') or 300)

        axis = str(p.get('axis') or AXIS_AUTO)
        n_layer = int(np.asarray(d['img_interp']).shape[2])
        n_freq = int(np.asarray(d['img_interp']).shape[3])
        eff = (AXIS_FREQ if (axis == AXIS_AUTO and n_layer == 1) else
               (AXIS_LAYER if axis == AXIS_AUTO else axis))
        ctx.log(f'② 出图：{case}  mode={mode}  展示={eff}  '
                f'层数 {n_layer} / 频点 {n_freq}  →  {out}')
        if eff in (AXIS_FREQ, AXIS_PICK) and mode == 'APTW':
            raise RuntimeError('APTW 是 ± 差值（没有逐频点概念）：'
                               '「固定层(全频率)/特定频率」请改用 ST / raw / LD')

        if eff == AXIS_LAYER:
            img3d, cax, _q = build_mode_and_caxis(
                d, mode, w, use_mask=bool(p.get('use_mask', True)),
                caxis_mode=caxis_mode, caxis=caxis, layers=layers,
                ld_mode=str(p.get('ld_mode') or 'POS'), verbose=True)
            if caxis_mode != 'manual':
                ctx.log(f'  自适应色标（{caxis_mode}）= {cax}')
            name = combined_name(mode, w)
            paths = save_mosaic(img3d, name, mode, w, cax, cols, dpi,
                                out, data_path, layers=layers, log=ctx.log)
            ctx.log(f'  data 形状 {tuple(img3d.shape)}  色标 {cax}')
        elif eff == AXIS_FREQ:
            lay = int(np.clip(int(p.get('layer') or 1), 1, n_layer))
            img = build_freq_stack(d, mode, lay,
                                   use_mask=bool(p.get('use_mask', True)),
                                   ld_mode=str(p.get('ld_mode') or 'POS'))
            cax = self._pick_caxis(img, caxis_mode, caxis, ctx.log)
            name = f'{mode} slice{lay}_allfreq_combined'
            paths = save_mosaic(img, name, mode, lay, cax, cols, dpi,
                                out, data_path, title_values=list(d['w']),
                                title_prefix=f'{mode}', log=ctx.log)
            ctx.log(f'  data 形状 {tuple(img.shape)}  色标 {cax}  '
                    f'子图 {int(img.shape[2])} 张（按频偏）')
        else:   # AXIS_PICK
            import cest_display as D
            freqs = [float(p[f'freq{k}']) for k in range(1, 6)
                     if str(p.get(f'freq{k}') or '').strip()]
            if not freqs:
                raise RuntimeError('「特定频率」至少填一个（频率1~频率5）')
            w_arr = np.asarray(d['w'], float).ravel()
            matched = [float(w_arr[int(D.nearest_freq_index(w_arr, f))]) for f in freqs]
            ctx.log('  特定频率 → 实际匹配帧：' + '；'.join(
                f'{f:g} → {m:.2f} ppm' for f, m in zip(freqs, matched)))
            sub = out / '特定频率'
            sub.mkdir(parents=True, exist_ok=True)
            if n_layer == 1:
                stack = np.stack([
                    build_mode_and_caxis(
                        d, mode, m, use_mask=bool(p.get('use_mask', True)),
                        caxis_mode='manual', caxis=None, layers=layers,
                        ld_mode=str(p.get('ld_mode') or 'POS'),
                        verbose=False)[0][:, :, 0]
                    for m in matched], axis=2)
                cax = self._pick_caxis(stack, caxis_mode, caxis, ctx.log)
                name = f'{mode} 特定频率_combined'
                paths = save_mosaic(stack, name, mode, 0, cax,
                                    min(cols, max(1, len(freqs))), dpi,
                                    sub, data_path, title_values=matched,
                                    title_prefix=f'{mode}', log=ctx.log)
                ctx.log(f'  data 形状 {tuple(stack.shape)}  色标 {cax}  '
                        f'子图 {int(stack.shape[2])} 张（你指定的 {len(freqs)} 个频率）')
            else:
                paths = []
                for f, m in zip(freqs, matched):
                    img3d, cax, _q = build_mode_and_caxis(
                        d, mode, m, use_mask=bool(p.get('use_mask', True)),
                        caxis_mode=caxis_mode, caxis=caxis, layers=layers,
                        ld_mode=str(p.get('ld_mode') or 'POS'), verbose=False)
                    if caxis_mode != 'manual':
                        cax = self._pick_caxis(img3d, caxis_mode, None, ctx.log)
                    nm = combined_name(mode, m)
                    paths += save_mosaic(img3d, nm, mode, m, cax, cols, dpi,
                                         sub, data_path, layers=layers, log=ctx.log)
                    ctx.log(f'  {f:g} ppm → 实际 {m:.2f} ppm：{nm}.png  '
                            f'形状 {tuple(img3d.shape)}')

        for x in paths:
            ctx.log(f'  已写 {x}')
        png = next((str(x) for x in paths if x.suffix == '.png'), str(out))
        return dict(png=png, name=locals().get('name', ''), out_dir=str(out),
                    caxis=cax, case=case, files=[str(x) for x in paths])

    # ================================================================ 交互式预览
    def _open_preview(self, page):
        from PySide6.QtWidgets import QMessageBox
        from app.dialogs.mosaic_view_dialog import MosaicViewDialog

        p = page.params()
        proj = page.window.proj()
        log = page.window.log
        try:
            case, d, data_path, out = self._prepare(p, proj, log=log)
        except Exception as e:                                   # noqa: BLE001
            log(f'② 打不开图谱预览：{e}')
            QMessageBox.warning(page, '打不开图谱预览', str(e))
            return
        log(f'② 打开图谱预览：{case}　数据 {Path(data_path).name}  →  {out}')
        _ax = str(p.get('axis') or AXIS_AUTO)
        _nl = int(np.asarray(d['img_interp']).shape[2])
        _eff = (AXIS_FREQ if (_ax == AXIS_AUTO and _nl == 1) else
                (AXIS_LAYER if _ax == AXIS_AUTO else _ax))
        if _eff != AXIS_LAYER:
            log(f'② 交互式预览暂支持「{AXIS_LAYER}」；{_eff} 请直接点【开始】出图')
            QMessageBox.information(page, '提示',
                                    f'交互式图谱预览暂支持「{AXIS_LAYER}」；\n'
                                    f'{_eff} 请直接点【开始】出图（落盘格式一致）。')
            return

        def _export(img3d, mode, w, caxis, cols, dpi):
            name = combined_name(mode, w)
            _backup_combined(out, name, log)
            paths = save_mosaic(img3d, name, mode, w, caxis, cols, dpi, out,
                                data_path, log=log)
            for x in paths:
                log(f'  已写 {x}')
            return [str(x) for x in paths]

        dlg = MosaicViewDialog(page, d, case=case, params=p, out_dir=out,
                               on_export=_export)
        dlg.exec()
        page.refresh_extra()
        page.refresh_preview(proj)
