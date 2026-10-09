# -*- coding: utf-8 -*-
"""app/modules/m_roi.py —— ⑧ 去噪掩模（Mask = 组织 ROI × 噪声 SNR 掩模）。

对应 MATLAB 那条线：`denoise_Mask.m` / `ThreMask_SNR.m` / `GenerateThmask.m` /
`GenerateliverMask.m` + `runCESTpost.m` 的 4.1 节：

    img_mask_base = mean(S0, 3)                      ← 画掩模的底图（= S0 层间平均）
    snrMask       = img_mask_base > snr × std(img_mask_base[噪声ROI])   （snr 默认 15）
    liverMask     = 手画的组织/肝脏多边形（roipoly）
    Mask          = liverMask .* snrMask             ← 最终去噪掩模

★ 本模块做两件事：① 去噪掩模（Mask = 组织 ROI × 噪声 SNR 掩模）；
  ② 器官 ROI（ROI1…ROIn → ROI_total.mat，= MATLAB ROIplotSong_0708.m 那一套）。
★ 产物统一落 `PROJ\\00_roi\\<病例>\\`，② 多层展示 / ④ 逐体素LD 会优先从这里取。
★ 核心层一行都不改：算法全部复用 cest_post（points_to_mask / thre_mask_snr /
  _cell_of_arrays / savemat）与 CEST_Studio 自己的画布控件。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from .base import Field, Module

from detect import rules as R          # PROJ 子目录名（中文优先，兼容旧英文名）

S0_FILES = ('s0.mat', 'S0.mat')
INTERP_FILES = ('0.7ut_img_interp.mat', '0p7P_interp.mat',
                '0p7N_interp.mat', '2APT_interp.mat', '0.7ut_img_raw.mat')
MASK_NAMES = ('Mask', 'mask', 'Thmask', 'liverMask', 'snrMask', 'SnrMask')


class ROIModule(Module):
    id = 'm_roi'
    badge = '⑧'
    title = '去噪掩模'
    out_subdir = '00_roi'
    case_in_out = True
    accepts = ('recon_mat', 'recon_root', 'img_mat', 'roi_mat',
               'project_root', 'post_mat')
    produces = ('roi_mat',)
    chain_proj = True               # 输入根默认 = PROJ（⑧ 的底图 = 01_重建\<病例>）
    chain_fill = ('case', 'src')
    schema = (
        Field('src', '底图（重建结果目录 / s0.mat）', 'file',
              tip='要有 s0.mat（或 0.7ut_img_interp.mat）；留空=自动取 PROJ\\01_重建\\<病例>'),
        Field('case', '病例名', 'text', tip='输出到 00_掩模\\<病例>\\，②④ 按这个名字取'),
        # ---- 器官 ROI（ROI_total，= MATLAB ROIplotSong_0708.m）先列，不用往下滚 ----
        Field('nroi', 'ROI 数量（器官 ROI）', 'int', 1,
              tip='MATLAB 的 nROI（默认 1）。画布会生成 ROI1…ROIn，逐个画；'
                  '画布里也有一个同名的快速入口'),
        Field('roi_ppm', 'ROI 目标 ppm', 'float', 3.5,
              tip='ROI 底图 = 归一化 Z 谱 @ 该 ppm 的 20 层平均 × 去噪掩模（MATLAB target_freq=3.5）'),
        Field('roi_display', 'ROI 显示', 'choice', 'Z 原始（APT）',
              choices=('Z 原始（APT）', '1−Z（ST）'),
              tip='ROI 底图显示模式：Z 原始（= MATLAB roi_display_mode 1）/ '
                  '1−Z（ST，= roi_display_mode 2）。只影响观感，'
                  '不改 ROI 几何与 ROI_total 索引；画布上也能随时切'),
        Field('roi_caxis_lo', 'ROI 色标下限（留空=自动）', 'float', '',
              tip='留空 = 0；只影响观感，画布上可随时改'),
        Field('roi_caxis_hi', 'ROI 色标上限（留空=自动）', 'float', '',
              tip='留空 = 有效值 95% 分位（同 MATLAB）；只影响观感'),
        # ---- 掩模（SNR / 噪声区）----
        Field('snr', 'SNR 系数', 'float', 15.0, tip='MATLAB 默认 15（信号 > 15σ）'),
        Field('noise_sigma', '噪声 σ（0=用画的噪声区）', 'float', 0.0,
              tip='画布里画了「噪声区」就填 0；要复现 MATLAB 旧结果可直接填它算出的 σ'),
        Field('mask_file', '载入已有掩模 .mat', 'file', '',
              tip='填了就走「拷现成掩模」，不再用画布；可含 Mask / Thmask / liverMask / snrMask'),
        Field('dpi', '确认图分辨率', 'int', 150),
    )

    def __init__(self):
        self._draw = None      # 画布返回的结果（含 liver/noise/snr 数组 + 元信息）
        self._loaded = None    # 载入的现成掩模（dict: 变量名 → 数组）
        self._roi_draw = None   # 画布返回的器官 ROI（含 rois）
        self._roi_dirty = False  # 本轮在画布上动过 ROI → 覆盖重画；否则默认沿用 ROI_total.mat

    # ================================================================ 参数
    def suggest(self, det, proj):
        p = {'src': '', 'case': '', 'snr': 15.0, 'noise_sigma': 0.0,
             'mask_file': '', 'dpi': 150,
             'nroi': 1, 'roi_ppm': 3.5, 'roi_caxis_lo': '', 'roi_caxis_hi': '',
             'roi_display': 'Z 原始（APT）'}
        if det is not None:
            case = (det.cases[0] if getattr(det, 'cases', None) else Path(det.path).name)
            p['case'] = case
            _rd = R.find_subdir(proj, '01_recon') if proj else None
            if _rd and (_rd / case / 's0.mat').exists():
                p['src'] = str(_rd / case)
            elif det.kind in ('recon_mat', 'img_mat', 'roi_mat', 'post_mat'):
                p['src'] = det.path
            elif det.kind == 'recon_root':
                d = Path(det.path) / case
                p['src'] = str(d if d.exists() else det.path)
        return p

    # ================================================================ 底图
    def _case_of(self, p):
        return str(p.get('case') or '').strip()

    def _find_base(self, p, proj):
        """返回 (底图目录或 .mat, 病例名)。"""
        src = str(p.get('src') or '').strip()
        case = self._case_of(p)
        if src:
            s = Path(src)
            return (s, case or s.name)
        if proj and case:
            d = (R.find_subdir(proj, '01_recon') or Path(proj) / '01_重建') / case
            if d.exists():
                return (d, case)
        return (None, case)

    def _load_base(self, p, proj, log=print):
        """读 s0（X,Y,层）；有 img_interp/w 也一并读出来（给 Z 底图用）。"""
        from cest_io import load_mat

        src, case = self._find_base(p, proj)
        s0 = img = w = None
        base_file = None
        if src is not None:
            s = Path(src)
            cand_dir = s if s.is_dir() else s.parent
            # 1) s0.mat / S0.mat
            for f in S0_FILES:
                f = cand_dir / f
                if f.exists():
                    got = load_mat(f, ['s0', 'S0'])
                    if got:
                        s0 = np.asarray(list(got.values())[0], dtype=float)
                        base_file = str(f)
                        break
            if s0 is None and s.is_file():
                got = load_mat(s, ['s0', 'S0', 'img_interp', 'norm_z', 'normZ'])
                if 's0' in got or 'S0' in got:
                    s0 = np.asarray(got.get('s0', got.get('S0')), dtype=float)
                    base_file = str(s)
            # 2) 展示输入里的 s0 + img_interp
            if s0 is None:
                for f in INTERP_FILES:
                    f = cand_dir / f
                    if not f.exists():
                        continue
                    got = load_mat(f, ['s0', 'S0', 'img_interp', 'norm_z', 'normZ',
                                       'w', 'w_offset'])
                    s0 = np.asarray(got.get('s0', got.get('S0')), dtype=float) \
                        if (got.get('s0') is not None or got.get('S0') is not None) else None
                    if s0 is None:
                        continue
                    img = got.get('img_interp', got.get('norm_z', got.get('normZ')))
                    w = got.get('w', got.get('w_offset'))
                    base_file = str(f)
                    break
            if img is None and s0 is not None:
                got = load_mat(base_file, ['img_interp', 'norm_z', 'normZ', 'w', 'w_offset'])
                img = got.get('img_interp', got.get('norm_z', got.get('normZ')))
                w = got.get('w', got.get('w_offset'))
        if s0 is None:
            raise RuntimeError(
                '读不到底图 s0。⑧ 需要一个含 s0.mat 的目录（如 PROJ\\01_重建\\<病例>）\n'
                '或含 0.7ut_img_interp.mat 的展示输入目录；请先在「底图」里选对。')
        s0 = np.asarray(s0, dtype=float)
        while s0.ndim > 3 and s0.shape[-1] == 1:
            s0 = s0[..., 0]
        if s0.ndim == 2:
            s0 = s0[:, :, None]
        if s0.ndim != 3:
            raise RuntimeError(f's0 形状不认识：{s0.shape}')
        log(f'  底图：{base_file}  s0{tuple(s0.shape)}'
            + (f'  img_interp{np.asarray(img).shape} w{np.asarray(w).size} 个'
               if img is not None else ''))
        return s0, (None if img is None else np.asarray(img, dtype=float)), \
            (None if w is None else np.asarray(w, dtype=float).ravel()), \
            (case or Path(base_file).parent.name), base_file

    # ================================================================ ROI 底图
    def _load_roi_z(self, p, proj, log=print):
        """读 normZ.mat → 归一化（normalize_spect, tol=.02, p2/p98）→ [X,Y,频,层]。

        与 ③ 后处理 run_post 里的 Mz 完全同口径；ROI 底图 = 该 Z 谱 @ 目标 ppm 的层间平均。
        """
        from cest_io import load_mat
        import cest_post as P
        case = self._case_of(p)
        cands = []
        if proj and case:
            d = R.find_subdir(proj, '01_recon') or Path(proj) / '01_重建'
            cands.append(Path(d) / case / 'normZ.mat')
        src = str(p.get('src') or '').strip()
        if src:
            s = Path(src)
            d0 = s if s.is_dir() else s.parent
            cands += [d0 / 'normZ.mat', d0.parent / 'normZ.mat']
        f = next((x for x in cands if x.exists()), None)
        if f is None:
            raise RuntimeError('找不到 normZ.mat（应在 PROJ\\01_重建\\<病例>\\）'
                               '—— ROI 底图需要归一化 Z 谱')
        got = load_mat(f, ['norm_z', 'normZ', 'w_offset'])
        nz = got.get('norm_z', got.get('normZ'))
        if nz is None:
            raise RuntimeError(f'{f} 里没有 norm_z / normZ')
        w = got.get('w_offset')
        if w is None:
            wf = Path(f).parent / 'w_offset.mat'
            if wf.exists():
                w = load_mat(wf, ['w_offset']).get('w_offset')
        if w is None:
            raise RuntimeError(f'读不到 w_offset（应与 {Path(f).name} 同目录）')
        nz = np.asarray(nz, dtype=float)
        w = np.asarray(w, dtype=float).ravel()
        while nz.ndim > 4 and nz.shape[-1] == 1:
            nz = nz[..., 0]
        if nz.ndim != 4:
            raise RuntimeError(f'normZ 形状不认识：{nz.shape}')
        if nz.shape[2] != w.size and nz.shape[3] == w.size:
            nz = np.swapaxes(nz, 2, 3)          # → [X, Y, 频, 层]（与 run_post 一致）
        if nz.shape[2] != w.size:
            raise RuntimeError(f'normZ 频点数 {nz.shape[2]} ≠ w_offset {w.size}')
        nz[~np.isfinite(nz)] = 0.0
        mz = np.empty(nz.shape, dtype=np.float32)
        for ns in range(nz.shape[3]):
            mz[:, :, :, ns] = P.normalize_spect(nz[:, :, :, ns], 0.02, 2, 98)
        log(f'  ROI 底图：{Path(f).name}  normZ{tuple(nz.shape)} → 归一化 '
            f'[X,Y,频,层]{tuple(mz.shape)}  w {w.size} 个（{w.min():g}~{w.max():g} ppm）')
        return mz, w, str(f)

    def _current_mask2d(self, p, proj):
        """ROI 底图要乘的那张去噪掩模（2D）：本轮画的 → 载入的 → 00_掩模\\<病例>\\Mask.mat。"""
        from cest_io import load_mat
        if self._draw:
            liver = self._draw.get('liver')
            snr = self._draw.get('snr')
            if liver is not None:
                Lm = _flat2d(np.asarray(liver)) > 0
                if snr is None:
                    return Lm
                return Lm & (_flat2d(np.asarray(snr)) > 0)
        if self._loaded:
            for k in ('Mask', 'mask', 'Thmask', 'liverMask'):
                v = self._loaded.get(k)
                if v is not None:
                    return _flat2d(np.asarray(v)) > 0
        case = self._case_of(p)
        if proj and case:
            d = R.find_subdir(proj, '00_roi') or Path(proj) / '00_掩模'
            f = Path(d) / case / 'Mask.mat'
            if f.exists():
                got = load_mat(f, ['Mask', 'mask', 'Thmask', 'liverMask'])
                if got:
                    return _flat2d(np.asarray(list(got.values())[0])) > 0
        return None

    def _seed_targets_from_disk(self, p, proj):
        """从 00_掩模\\<病例>\\ 把现成掩模读出来当画布的「种子」。

        没有它的话：只打开画布画个 ROI、liverMask 是空的 → 确定后会走「只有 SNR 掩模」
        那条路把原来的 Mask 给覆盖了。先种上就无损（liver×snr = 原 Mask）。
        """
        from cest_io import load_mat
        case = self._case_of(p)
        if not (proj and case):
            return {}
        d = R.find_subdir(proj, '00_roi') or Path(proj) / '00_掩模'
        d = Path(d) / case
        out = {}
        fl = d / 'liverMask.mat'
        fm = d / 'Mask.mat'
        if fl.exists():
            got = load_mat(fl, ['liverMask'])
            if got.get('liverMask') is not None:
                out['liverMask'] = np.asarray(got['liverMask'], dtype=float)
        elif fm.exists():
            got = load_mat(fm, ['Mask', 'mask', 'Thmask'])
            v = got.get('Mask', got.get('mask', got.get('Thmask')))
            if v is not None:
                out['liverMask'] = np.asarray(v, dtype=float)
        fs = d / 'snrMask.mat'
        if fs.exists():
            got = load_mat(fs, ['snrMask'])
            if got.get('snrMask') is not None:
                out['snrMask'] = np.asarray(got['snrMask'], dtype=float)
        return out

    # ================================================================ 面板
    def extra_panel(self, page):
        from app.widgets.mask_canvas import MaskCanvasPanel
        pan = MaskCanvasPanel()
        pan.on_open = lambda: self._open_canvas(page)
        pan.on_load = lambda: self._load_masks(page)
        pan.on_scan = lambda: self._scan_input(page)
        pan.on_redraw_roi = lambda: self._redraw_roi(page)
        return pan

    def refresh_extra(self, panel, det, proj, params):
        lines = []
        try:
            s0, img, w, case, base_file = self._load_base(params, proj, log=lambda *_: None)
            lines.append(f'底图：{Path(base_file).name}　s0{tuple(s0.shape)}'
                         f'（{s0.shape[2]} 层）')
        except Exception as e:                                   # noqa: BLE001
            lines.append(f'底图：× {e}')
        if self._draw:
            d = self._draw
            lines.append(f'画布：{d["base"]}　liverMask {d["n_liver"]} 体素　'
                         f'噪声区 {d["n_noise"]} 体素　SNR掩模 {d["n_snr"]} 体素'
                         f'（σ={d["snr_sigma"]:.4g}，{d["snr_factor"]:g}σ）'
                         + (f'　单独层 {len(d["per_layer"])}' if d.get('per_layer') else ''))
        elif self._loaded:
            lines.append('载入：' + '、'.join(f'{k}{np.asarray(v).shape}'
                                             for k, v in self._loaded.items() if v is not None))
        else:
            lines.append('还没画/没载入 —— 点【打开掩模画布…】或【载入已有掩模…】')
        nroi = int(params.get('nroi') or 0)
        if nroi >= 1:
            drawn = [(n, m) for n, m in ((self._roi_draw or {}).get('rois') or [])
                     if m is not None and np.asarray(m).any()]
            if drawn:
                lines.append('器官 ROI：画布上 ' + '、'.join(
                    f'{n} {int(np.asarray(m).sum())} 体素' for n, m in drawn)
                    + ('（本轮覆盖重画）' if self._roi_dirty else ''))
            else:
                ex = None
                try:
                    d = self.out_dir_for(params, proj)
                    if d and (Path(d) / 'ROI_total.mat').exists():
                        ex = Path(d) / 'ROI_total.mat'
                except Exception:                                # noqa: BLE001
                    ex = None
                lines.append(f'器官 ROI（nROI={nroi}）：'
                             + (f'沿用 {ex.name}' if ex else '还没画')
                             + '；数量在下面「参数 → ROI 数量」或画布里改；画/重画点【重新画 ROI…】')
        panel.set_state(lines)

    # ================================================================ 画布
    def _open_canvas(self, page, focus_roi=False):
        from app.dialogs.mask_canvas_dialog import MaskCanvasDialog
        p = page.params()
        proj = page.window.proj()
        log = page.window.log
        try:
            s0, img, w, case, base_file = self._load_base(p, proj, log=log)
        except Exception as e:                                   # noqa: BLE001
            log(f'⑧ 打不开画布：{e}')
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(page, '打不开画布', str(e))
            return
        if not self._case_of(p):
            p['case'] = case
            page._set('case', case)
        shape2 = tuple(int(v) for v in s0.shape[:2])
        nroi = int(p.get('nroi') or 0)
        # ---- 器官 ROI 的底图：Z@ppm 层间平均 × 掩模；没有掩模就提醒（仍可先画掩模）----
        mask2d = self._current_mask2d(p, proj)
        roi_z = roi_w = None
        if nroi >= 1:
            try:
                roi_z, roi_w, _zsrc = self._load_roi_z(p, proj, log=log)
            except Exception as e:                               # noqa: BLE001
                log(f'⑧ ROI 底图读不到：{e}（ROI 底图将退回 S0 平均图）')
            if mask2d is None:
                log('⑧ ⚠ 还没有去噪掩模：ROI 底图暂时不乘掩模；'
                    '请先在本页画好/载入掩模，再画器官 ROI')
        # ---- 续画：先把已画的掩模/ROI（本轮内存 / 磁盘）当种子 ----
        init = dict(self._draw or {})
        if not init.get('targets'):
            if self._loaded:
                init['targets'] = {k: v for k, v in self._loaded.items()
                                   if k != '_file' and v is not None}
            else:
                seeds = self._seed_targets_from_disk(p, proj)
                if seeds:
                    init['targets'] = seeds
                    log('⑧ 画布已带出现成掩模（' + '、'.join(seeds) + '）作底稿')
        if not init.get('rois') and nroi >= 1:
            dg = self.out_dir_for(p, proj)
            if dg and Path(dg).exists():
                try:
                    ro = load_existing_rois(dg, nroi, shape2)
                    if ro:
                        init['rois'] = ro
                except Exception:                                # noqa: BLE001
                    pass
        log(f'⑧ 打开画布：{case}　底图 {Path(base_file).name} s0{tuple(s0.shape)}'
            + (f'　nROI={nroi}' if nroi >= 1 else '')
            + ('　掩模已就位' if mask2d is not None else ''))
        dlg = MaskCanvasDialog(page, s0, case=case, img_interp=img, w=w,
                               init=init, snr=float(p.get('snr') or 15),
                               nroi=nroi, roi_ppm=float(p.get('roi_ppm') or 3.5),
                               roi_caxis=(p.get('roi_caxis_lo'), p.get('roi_caxis_hi')),
                               mask2d=mask2d, roi_z=roi_z, roi_w=roi_w,
                               roi_display=p.get('roi_display'),
                               on_nroi=lambda v: (page._set('nroi', v),
                                                  page.refresh_extra()))
        if focus_roi and nroi >= 1 and 'ROI1' in dlg.targets:
            try:
                dlg.lst.setCurrentRow(list(dlg.targets).index('ROI1'))
            except Exception:                                    # noqa: BLE001
                pass
        if dlg.exec():
            self._draw = dlg.result_data
            self._roi_draw = dlg.result_data
            self._roi_dirty = True          # 本轮动过画布 → ROI 覆盖重画（否则默认沿用）
            d = self._draw
            n_roi = len([1 for _n, _m in (d.get('rois') or [])
                         if _m is not None and np.asarray(_m).any()])
            log(f'⑧ 画布确定：liverMask {d["n_liver"]} 体素、'
                f'噪声区 {d["n_noise"]} 体素、SNR掩模 {d["n_snr"]} 体素'
                f'（σ={d["snr_sigma"]:.4g}，{d["snr_factor"]:g}σ，'
                f'阈值 {d["snr_thr"]:.4g}）；底图 {d["base"]}'
                + (f'；器官 ROI {n_roi} 个' if d.get('nroi') else ''))
            self._loaded = None
            page.refresh_extra()
            page.refresh_preview(page.window.proj())

    def _redraw_roi(self, page):
        """【重新画 ROI】= 打开画布并直接到 ROI1（确定后强制覆盖重画；默认是沿用）。"""
        self._open_canvas(page, focus_roi=True)

    # ================================================================ 载入
    def _load_masks(self, page):
        from PySide6.QtWidgets import QFileDialog
        from cest_io import load_mat
        p = page.params()
        start = str(p.get('src') or '') or page.window.proj() or ''
        f, _ = QFileDialog.getOpenFileName(page, '选择掩模 .mat（Mask / Thmask / liverMask / snrMask）',
                                          start, 'MAT (*.mat);;所有文件 (*)')
        if not f:
            return
        got = load_mat(f, list(MASK_NAMES))
        if not got:
            page.window.log(f'⑧ 载入失败：{Path(f).name} 里没有 Mask/Thmask/liverMask/snrMask')
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(page, '载入失败',
                                f'{Path(f).name} 里没找到 Mask / Thmask / liverMask / snrMask。')
            return
        self._loaded = {k: np.asarray(v, dtype=float) for k, v in got.items()}
        self._loaded['_file'] = f
        self._draw = None
        page._set('mask_file', f)
        page.window.log(f'⑧ 载入掩模：{f} → '
                        + '、'.join(f'{k}{np.asarray(v).shape}'
                                    for k, v in self._loaded.items() if k != '_file'))
        page.refresh_extra()
        page.refresh_preview(page.window.proj())

    def _scan_input(self, page):
        """在输入目录 / PROJ 里自动找现成掩模。"""
        from cest_io import load_mat
        p = page.params()
        proj = page.window.proj()
        cands = []
        src = str(p.get('src') or '').strip()
        if src:
            s = Path(src)
            d0 = s if s.is_dir() else s.parent
            cands += [d0 / 'Mask.mat', d0 / 'liverMask.mat', d0 / 'Thmask.mat',
                      d0.parent / 'Mask.mat', d0.parent / 'liverMask.mat',
                      d0.parent / 'Thmask.mat']
        case = self._case_of(p)
        if proj and case:
            _rd = R.find_subdir(proj, '00_roi') or Path(proj) / '00_掩模'
            cands += [_rd / case / 'Mask.mat', _rd / case / 'liverMask.mat',
                      _rd / case / 'Thmask.mat']
        found = [c for c in cands if c.exists()]
        if not found:
            page.window.log('⑧ 输入目录和 PROJ 里都没找到 Mask.mat / liverMask.mat / Thmask.mat。')
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(page, '没找到',
                                    '输入目录和 PROJ 里都没有掩模文件，\n'
                                    '用【载入已有掩模…】手动指定，或直接画。')
            return
        merged: dict = {}
        for c in found:
            got = load_mat(c, list(MASK_NAMES))
            for k, v in got.items():
                merged.setdefault(k, np.asarray(v, dtype=float))
        merged['_file'] = str(found[0])
        self._loaded = merged
        self._draw = None
        page._set('mask_file', str(found[0]))
        page.window.log('⑧ 找到掩模：' + '、'.join(str(x) for x in found)
                        + ' → ' + '、'.join(f'{k}{np.asarray(v).shape}'
                                            for k, v in merged.items() if k != '_file'))
        page.refresh_extra()

    # ================================================================ 校验
    def validate(self, p, ctx=None):
        errs = []
        proj = getattr(ctx, 'proj', None)
        if not proj:
            errs.append('需要先选输出总目录（PROJ）——掩模要存到「00_掩模」')
        mf = str(p.get('mask_file') or '').strip()
        if mf:
            f = Path(mf)
            if not f.exists():
                errs.append(f'载入的掩模文件不存在：{f}')
        elif self._draw is None and self._loaded is None:
            errs.append('还没有掩模：点【打开掩模画布…】画一张，'
                        '或【载入已有掩模…】/【从输入目录找】')
        else:
            try:
                self._load_base(p, proj, log=lambda *_: None)
            except Exception as e:                               # noqa: BLE001
                errs.append(str(e))
        # ---- 器官 ROI 的前置门禁：画 ROI 必须先有去噪掩模 ----
        if int(p.get('nroi') or 0) >= 1:
            try:
                mask2d = self._current_mask2d(p, proj)
            except Exception:                                    # noqa: BLE001
                mask2d = None
            if mask2d is None and not mf:
                errs.append('ROI 需要先有去噪掩模：请在 ⑧ 页先完成掩模（画好或载入 '
                            'Mask.mat），再画器官 ROI')
        return errs

    def preview_files(self, p, proj):
        d = self.out_dir_for(p, proj)
        if not d:
            return []
        files = [str(d / x) for x in ('Mask.mat', 'Thmask.mat', 'liverMask.mat',
                                      'snrMask.mat', 'roi_meta.json', 'mask_check.png')]
        nroi = int(p.get('nroi') or 0)
        if nroi >= 1:
            files += [str(d / f'ROI{k}.mat') for k in range(1, nroi + 1)]
            files.append(str(d / 'ROI_total.mat'))
        return files

    def preview_image(self, res):
        p = (res or {}).get('png')
        return p if (p and Path(p).exists()) else None

    # ================================================================ 执行
    def run(self, p, ctx):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import cest_post as P
        from cest_io import load_mat

        # 确认图里的中文（与 cest_display 同一套字体口径）
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
        plt.rcParams['axes.unicode_minus'] = False

        s0, img, w, case, base_file = self._load_base(p, ctx.proj, log=ctx.log)
        case = self._case_of(p) or case
        out = Path(ctx.out_dir(self.out_subdir)) / case
        out.mkdir(parents=True, exist_ok=True)
        n_layer = int(s0.shape[2])
        shape2 = tuple(int(v) for v in s0.shape[:2])
        base2 = s0.mean(axis=2)
        snr_f = float(p.get('snr') or 15.0)

        # ---------------- 1) 收集三张掩模 ----------------
        draw = self._draw or {}
        loaded = dict(self._loaded or {})
        loaded.pop('_file', None)

        liver = draw.get('liver')
        noise = draw.get('noise')
        snr_m = draw.get('snr')
        final = None
        sigma = float(p.get('noise_sigma') or 0) or float(draw.get('snr_sigma') or 0)

        if loaded:
            ctx.log(f'  用载入的掩模：{dict((k, np.asarray(v).shape) for k, v in loaded.items())}')
            final = loaded.get('Mask', loaded.get('mask'))
            if loaded.get('liverMask') is not None:
                liver = loaded['liverMask']
            if loaded.get('snrMask') is not None:
                snr_m = loaded['snrMask']
            elif loaded.get('Thmask') is not None and final is None:
                snr_m = loaded['Thmask']       # MATLAB 的 Thmask.mat 存的就是 SNR 掩模
                ctx.log('  提示：载入的 Thmask 按 MATLAB 口径当作「SNR 掩模」处理')

        if final is None and liver is None and snr_m is None and self._draw is None:
            raise RuntimeError('没有可用的掩模：先在 ⑧ 页画一张，或载入现成掩模。')

        def _chk(a, tag):
            if a is None:
                return None
            a = np.asarray(a, dtype=float)
            if a.ndim == 2:
                if a.shape != shape2:
                    raise RuntimeError(f'{tag} 形状 {a.shape} ≠ 底图 {shape2}')
            elif a.ndim == 3:
                if a.shape[:2] != shape2:
                    raise RuntimeError(f'{tag} 形状 {a.shape} 前两维 ≠ 底图 {shape2}')
                if a.shape[2] != n_layer:
                    ctx.log(f'  [提示] {tag} 层数 {a.shape[2]} ≠ S0 层数 {n_layer}，'
                            f'按前 {n_layer} 层用')
            return a

        liver = _chk(liver, 'liverMask')
        noise = _chk(noise, 'noiseROI')
        snr_m = _chk(snr_m, 'snrMask')
        final = _chk(final, 'Mask')

        # ---------------- 2) SNR 掩模 ----------------
        if snr_m is None:
            if sigma > 0:
                thr = snr_f * sigma
                snr_m = (base2 > thr).astype(float)
                ctx.log(f'  SNR 掩模：σ={sigma:.4g}（手输）→ 阈值 {snr_f:g}σ={thr:.6g}，'
                        f'信号体素 {int(snr_m.sum())}')
            elif noise is not None:
                py = np.asarray(noise, dtype=bool)
                if py.ndim == 3:
                    py = py.any(axis=2)
                if not py.any():
                    raise RuntimeError('噪声区是空的：请在画布里圈一块纯噪声，或手输 σ')
                sigma = float(np.std(base2[py]))
                thr = snr_f * sigma
                snr_m = (base2 > thr).astype(float)
                ctx.log(f'  SNR 掩模：σ=std(噪声区{int(py.sum())}体素)={sigma:.4g} '
                        f'→ 阈值 {snr_f:g}σ={thr:.6g}，信号体素 {int(snr_m.sum())}')
            else:
                snr_m = np.ones(shape2, dtype=float)
                ctx.log('  [提示] 既没有噪声区也没有 σ → snrMask 置全 1'
                        '（等价于不做 SNR 过滤；MATLAB 在这步会让你画噪声 ROI）')

        # ---------------- 3) 最终 Mask ----------------
        if final is None and liver is None:
            # 只给了 SNR 掩模（MATLAB 的 Thmask.mat 就是这种）→ 直接当最终掩模用，
            # 否则「拿旧掩模来去背景」这条最省事的路永远走不通。
            ctx.log('  [提示] 载入的掩模里没有 liverMask / Mask，只有 SNR 掩模 → '
                    '直接把它当作最终去噪掩模。')
            ctx.log('        注意：MATLAB 的 Thmask.mat 是「SNR 阈值掩模」，含背景杂散信号；'
                    '要用它那套组合掩模请载入 mask.mat（里面带 liverMask）。')
            final = snr_m
        if final is None:
            if liver is None:
                raise RuntimeError('没有 liverMask：去噪掩模 = liverMask × snrMask，'
                                   '组织/肝脏那张必须有。')
            final = _combine(liver, snr_m, n_layer)
            ctx.log(f'  最终掩模 = liverMask{np.asarray(liver).shape} × '
                    f'snrMask{np.asarray(snr_m).shape} → {final.shape}，'
                    f'有效体素 {int((final > 0).sum())}')
        else:
            ctx.log(f'  最终掩模：直接用载入的 Mask{np.asarray(final).shape}，'
                    f'有效体素 {int((np.asarray(final) > 0).sum())}')

        # ---------------- 4) 落盘 ----------------
        # ★ Mask / Thmask 一律存 3D (X,Y,层)：2D 画的结果逐层复制。
        #   原因：② 多层展示的 apply_mask 是 MATLAB 口径 `img .* Mask - (~Mask)`，
        #   MATLAB 有隐式扩展（2D 掩模自动按第 3 维展开），numpy 没有 → 2D 会广播报错。
        #   ④ 逐体素LD 读 3D 时取第 1 层（与 2D 完全等价），③ 后处理用的是 liverMask 那张。
        mask3 = _as_layer3d(final, n_layer)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        written = []
        for name, payload in (('Mask.mat', {'Mask': mask3, 'axis_order': 'layer_freq'}),
                              ('Thmask.mat', {'Thmask': mask3}),
                              ('liverMask.mat', {'liverMask': liver}
                               if liver is not None else None),
                              ('snrMask.mat', {'snrMask': snr_m}
                               if snr_m is not None else None)):
            if payload is None:
                continue
            f = out / name
            if f.exists():
                bak = out / f'{f.stem}.bak_{stamp}.mat'
                shutil.copy2(f, bak)
                ctx.log(f'  已有 {name} → 备份为 {bak.name}')
            P.savemat(str(f), payload)
            written.append(name)
            ctx.log(f'  已写 {f}')

        # ---------------- 4.5 器官 ROI（ROI1…ROIn → ROI_total.mat） ----------------
        # ROI 数量：画布里可能改过（画布的「ROI 数量」+【应用】）→ 以画布返回的为准
        nroi = int((self._roi_draw or {}).get('nroi') or p.get('nroi') or 0)
        roi_meta = None
        roi_show = []
        if nroi >= 1:
            th2 = _flat2d(mask3) > 0
            drawn = [(n, _flat2d(np.asarray(m)) > 0)
                     for n, m in ((self._roi_draw or {}).get('rois') or [])
                     if m is not None and np.asarray(m).any()]
            existing = (out / 'ROI_total.mat').exists()
            if self._roi_dirty and drawn:
                ctx.log(f'  器官 ROI：覆盖重画 {len(drawn)} 个（∩ 去噪掩模）')
                if len(drawn) < nroi:
                    ctx.log(f'  [提示] nROI={nroi}，但只画了 {len(drawn)} 个 → ROI_total '
                            f'只含这 {len(drawn)} 个（MATLAB 这边会提示「太小，请重画」）')
                lists = build_roi_total([m for _n, m in drawn], th2)
                for (name, m), v in zip(drawn, lists):
                    f = out / f'{name}.mat'
                    _backup(f, stamp, ctx.log)
                    P.savemat(str(f), {'ROI': m.astype(float)})
                    ctx.log(f'  已写 {f}（勾画 {int(m.sum())} 体素，∩ 掩模后 {v.size}）')
                ft = out / 'ROI_total.mat'
                _backup(ft, stamp, ctx.log)
                P.savemat(str(ft), {'ROI_total': P._cell_of_arrays(lists)})
                ctx.log(f'  已写 {ft}（ROI_total cell({len(lists)},1)，1-based 列优先，'
                        + '、'.join(f'{v.size} 体素' for v in lists) + '）')
                roi_meta = dict(nROI=len(drawn), mode='redraw',
                                names=[n for n, _m in drawn],
                                ppm=float(p.get('roi_ppm') or 3.5),
                                caxis=[p.get('roi_caxis_lo'), p.get('roi_caxis_hi')],
                                display=str((self._roi_draw or {}).get('roi_display')
                                            or p.get('roi_display') or ''),
                                base='Z@ppm 层间平均 × Mask', mask_intersect=True,
                                voxels_drawn=[int(m.sum()) for _n, m in drawn],
                                voxels=[int(v.size) for v in lists],
                                first=[int(v[0]) if v.size else None for v in lists],
                                max=[int(v.max()) if v.size else None for v in lists])
                roi_show = drawn
            elif existing:
                ctx.log('  器官 ROI：沿用已有 ROI_total.mat（照 MATLAB 的 try/load；'
                        '要重画点【重新画 ROI…】）')
                try:
                    # 复用核心层 ROIplotSong：有 ROI_total.mat 就直接读回来（不再重画）
                    got = P.roi_plot_song(nroi, [f'ROI{k + 1}' for k in range(nroi)],
                                           th2, out, th2.astype(float), verbose=True)
                    vox = [int(np.asarray(v).size) for v in got]
                    roi_meta = dict(nROI=len(vox), mode='reuse', voxels=vox,
                                    display=str(p.get('roi_display') or ''))
                except Exception as e:                           # noqa: BLE001
                    ctx.log(f'  [提示] 读取已有 ROI_total.mat 时出错：{e}')
                    roi_meta = dict(nROI=nroi, mode='reuse')
                try:
                    roi_show = list(load_existing_rois(out, nroi, shape2).items())
                except Exception:                                # noqa: BLE001
                    roi_show = []
            else:
                ctx.log('  器官 ROI：还没画、也没有现成的 ROI_total.mat → 本次不出 ROI'
                        '（③ 后处理需要它；要画就点【重新画 ROI…】，'
                        '在「掩模对象」里选 ROI1…）')
                roi_meta = dict(nROI=nroi, mode='none', ppm=float(p.get('roi_ppm') or 3.5),
                                display=str(p.get('roi_display') or ''))
            self._roi_dirty = False

        meta = dict(
            case=case, layer_count=n_layer, shape=list(shape2),
            base_image=str(base_file), base_kind=str(draw.get('base') or ''),
            display_cmap=str(draw.get('cmap') or 'gray'),
            base_ppm=(float(draw['ppm']) if draw else None),
            snr_factor=snr_f, noise_sigma=float(sigma or 0.0),
            snr_thr=float(snr_f * sigma) if sigma else None,
            voxels=dict(
                liverMask=(None if liver is None else int((np.asarray(liver) > 0).sum())),
                snrMask=(None if snr_m is None else int((np.asarray(snr_m) > 0).sum())),
                Mask=int((np.asarray(final) > 0).sum())),
            mask_ndim=int(np.asarray(final).ndim),
            mask_stored_ndim=int(np.asarray(mask3).ndim),
            mask_uniform_layers=bool(np.asarray(mask3).ndim == 3 and
                                     np.allclose(np.asarray(mask3), np.asarray(mask3)[:, :, :1])),
            per_layer_layers=(draw.get('per_layer') or None),
            source=('画布' if (self._draw and not loaded) else
                    (f'载入 {loaded.get("_file", "")}' if loaded else '画布')),
            loaded_vars=sorted(k for k in loaded if k != '_file') or None,
            written=written, time=time.strftime('%Y-%m-%d %H:%M:%S'),
            note='Mask = liverMask × snrMask；口径同 MATLAB runCESTpost 4.1 / GenerateThmask',
            roi=roi_meta)
        (out / 'roi_meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        ctx.log(f'  已写 {out / "roi_meta.json"}')

        # ---------------- 5) 确认图 ----------------
        png = out / 'mask_check.png'
        ctx.progress(0, 1, '出确认图')

        def _flat(a):
            a = np.asarray(a, dtype=float)
            return a.any(axis=2) if a.ndim == 3 else a

        L2, N2, S2, F2 = (_flat(liver) if liver is not None else None,
                          _flat(noise) if noise is not None else None,
                          _flat(snr_m), _flat(final))
        n_lv, n_sn, n_mk = (int((L2 > 0).sum()) if L2 is not None else 0,
                            int((S2 > 0).sum()), int((F2 > 0).sum()))
        npanel = 4 if roi_show else 3
        fig, ax = plt.subplots(1, npanel, figsize=(4.8 * npanel, 5.2),
                               dpi=int(p.get('dpi') or 150))
        disp_cmap = _mpl_cmap((draw or {}).get('cmap'))
        titles = [f'① 底图（S0 层间平均）+ 组织/噪声轮廓\n'
                  f'组织 {n_lv} 体素（绿）' +
                  (f'　噪声区 {int((N2 > 0).sum())} 体素（橙）' if N2 is not None else ''),
                  f'② SNR 掩模轮廓（{snr_f:g}σ，σ={sigma:.4g}）\n'
                  f'有效 {n_sn} 体素 = {100.0 * n_sn / max(1, S2.size):.1f}%（青）',
                  f'③ 最终去噪掩模 Mask = 组织 × SNR\n'
                  f'有效 {n_mk} 体素（红）']
        for k in range(3):
            ax[k].imshow(base2, cmap=disp_cmap)
            ax[k].set_title(titles[k], fontsize=10)
            ax[k].set_axis_off()
        if L2 is not None:
            ax[0].contour(L2, [0.5], colors='#22c55e', linewidths=1.6)
        if N2 is not None:
            ax[0].contour(N2, [0.5], colors='#f59e0b', linewidths=1.6)
        ax[1].contour(S2, [0.5], colors='#06b6d4', linewidths=1.4)
        ax[2].imshow(np.ma.masked_where(~(F2 > 0), F2), cmap='autumn', alpha=0.40)
        ax[2].contour(F2, [0.5], colors='#ef4444', linewidths=1.4)
        if npanel == 4:
            from app.widgets.mask_canvas import ROI_PALETTE
            ax[3].imshow(base2, cmap='gray')
            ax[3].set_title('④ 器官 ROI（ROI_total）\n'
                            + '　'.join(f'{n} {int(np.asarray(m).sum())} 体素'
                                         for n, m in roi_show), fontsize=10)
            ax[3].set_axis_off()
            for j, (n, m) in enumerate(roi_show):
                ax[3].contour(np.asarray(m).astype(bool), [0.5],
                              colors=ROI_PALETTE[j % len(ROI_PALETTE)], linewidths=1.6)
        fig.suptitle(f'{case}　去噪掩模（⑧）　底图 {Path(str(base_file)).name}'
                     f'　snr={snr_f:g}　σ={sigma:.4g}'
                     + (f'　器官 ROI {len(roi_show)} 个' if roi_show else ''), fontsize=12)
        fig.tight_layout()
        fig.savefig(str(png), bbox_inches='tight')
        plt.close(fig)
        ctx.log(f'  确认图：{png}')

        return dict(png=str(png), out_dir=str(out), case=case,
                    voxels=meta['voxels'], written=written, meta=meta)


def _combine(liver, snr_m, n_layer):
    """Mask = liverMask × snrMask，四种维度组合都能对上。

    2D × 2D → 2D；只要有一边是 3D → 结果 3D (X,Y,层)，2D 那一边按层广播。
    """
    L = np.asarray(liver, dtype=float)
    S = np.asarray(snr_m, dtype=float)
    if L.ndim == 2 and S.ndim == 2:
        return L * S
    if L.ndim == 3 and S.ndim == 3:
        n = min(L.shape[2], S.shape[2], int(n_layer))
        return L[:, :, :n] * S[:, :, :n]
    if L.ndim == 3:
        n = min(L.shape[2], int(n_layer))
        return L[:, :, :n] * S[:, :, None]
    n = min(S.shape[2], int(n_layer))
    return L[:, :, None] * S[:, :, :n]


def _mpl_cmap(key):
    """画布色图名 → matplotlib 色图对象（jet_black = jet 前 2 行置黑，MATLAB 口径）。"""
    key = str(key or 'gray')
    try:
        import matplotlib
        if key == 'jet_black':
            import numpy as _np
            from matplotlib.colors import ListedColormap
            cols = _np.asarray(matplotlib.colormaps['jet'](_np.linspace(0, 1, 256)))
            cols[:2] = (0.0, 0.0, 0.0, 1.0)
            return ListedColormap(cols)
        return matplotlib.colormaps[key]
    except Exception:                                            # noqa: BLE001
        return 'gray'


def _as_layer3d(a, n_layer):
    """2D → 逐层复制的 3D (X,Y,层)；已是 3D 就补齐/截断到 n_layer 层。"""
    a = np.asarray(a, dtype=float)
    n = int(n_layer)
    if a.ndim == 2:
        return np.repeat(a[:, :, None], n, axis=2)
    if a.ndim == 3:
        if a.shape[2] == n:
            return a
        out = np.zeros((a.shape[0], a.shape[1], n), dtype=float)
        k = min(n, a.shape[2])
        out[:, :, :k] = a[:, :, :k]
        return out
    raise RuntimeError(f'掩模维度不认识：{a.shape}')


def _squeeze_to(ref, other):
    """把 other 降成与 ref 同维（2D 广播到 3D 时用）。"""
    o = np.asarray(other, dtype=float)
    r = np.asarray(ref)
    if o.ndim == 3 and r.ndim == 2:
        return o[:, :, 0]
    return o


# ===========================================================================
# 器官 ROI（ROI_total）—— 与 cest_post.roi_plot_song / _cell_of_arrays 同口径
# ===========================================================================
def _flat2d(a):
    """3D (X,Y,层) → 取第 1 层（掩模逐层相同时与 2D 等价）；2D 原样。"""
    a = np.asarray(a)
    return a[:, :, 0] if a.ndim == 3 else a


def _backup(path, stamp, log):
    """覆盖前备份 <名>.bak_<时间戳>.mat。"""
    path = Path(path)
    if path.exists():
        bak = path.with_name(f'{path.stem}.bak_{stamp}.mat')
        shutil.copy2(path, bak)
        log(f'  已有 {path.name} → 备份为 {bak.name}')


def build_roi_total(roi_masks, thmask):
    """每个 ROI（2D 掩模）∩ 去噪掩模 → 1-based 列优先线性索引（升序 = MATLAB find 的顺序）。

    完全复刻 cest_post.roi_plot_song 的算法（points_to_mask → 求交 →
    ``np.ravel_multi_index(..., order='F')`` → +1）；只额外把每个索引按升序排一下：
    MATLAB 的 ``find`` 返回的就是列优先升序，排一下就与 MATLAB 的 ROI_total **逐位一致**。
    打包用 cest_post._cell_of_arrays（cell(nROI,1)、元素为列向量）。
    """
    th = _flat2d(thmask).astype(bool)
    shape = tuple(int(v) for v in th.shape)
    out = []
    for k, m in enumerate(roi_masks):
        mm = _flat2d(m).astype(bool)
        if mm.shape != shape:
            raise RuntimeError(f'ROI{k + 1} 形状 {mm.shape} ≠ 去噪掩模 {shape}')
        idx = np.nonzero(mm & th)
        if idx[0].size == 0:
            raise ValueError(f'ROI{k + 1} 与去噪掩模没有交集，请重新勾画')
        flat0 = np.ravel_multi_index(idx, shape, order='F')   # 列优先线性索引（0-based）
        out.append(np.sort(flat0 + 1).astype(np.int64))       # 1-based、升序（= find）
    return out


def load_existing_rois(roi_dir, n, shape):
    """从 ROI<k>.mat / ROI_total.mat 还原 ROI1..ROIn 的 2D 掩模（画布续画用）。"""
    from cest_io import load_mat
    roi_dir = Path(roi_dir)
    shape = tuple(int(v) for v in shape)
    out = {}
    total = None
    tf = roi_dir / 'ROI_total.mat'
    if tf.exists():
        got = load_mat(tf, ['ROI_total'])
        raw = got.get('ROI_total')
        if raw is not None:
            raw = np.asarray(raw, dtype=object).ravel()
            total = [np.asarray(raw[i]).ravel() for i in range(raw.size)]
    for k in range(1, int(n) + 1):
        f = roi_dir / f'ROI{k}.mat'
        if f.exists():
            got = load_mat(f, ['ROI'])
            if 'ROI' in got:
                out[f'ROI{k}'] = _flat2d(np.asarray(got['ROI'])) > 0
                continue
        if total is not None and k - 1 < len(total):
            try:
                idx = np.asarray(total[k - 1]).astype(np.int64) - 1   # 1-based → 0-based
                m = np.zeros(shape, bool)
                rr, cc = np.unravel_index(idx, shape, order='F')
                m[rr, cc] = True
                out[f'ROI{k}'] = m
            except Exception:                                    # noqa: BLE001
                continue
    return out
