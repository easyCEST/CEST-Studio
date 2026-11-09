# -*- coding: utf-8 -*-
"""app/modules/m1_recon.py —— ① DICOM 重建（UIH 三序列 / Philips 单序列）。

底层：cest_recon.run_recon（自动按前缀找）／ detect.manual_recon.recon_with_dirs（手动指定的文件夹）。
本模块负责：
  * **序列匹配 + 3D/4D 判定**（detect/series.py，读 DICOM 标签）；
  * **目录预览**（app/dialogs/browse_dialog.py）——可手动挑文件夹，挑完自动找同组；
  * **频率表门禁**：4D 序列没有"已确认的频率表"就不许跑（detect/freq_doc.py 管来源与存档）。
"""
from __future__ import annotations

from pathlib import Path

from .base import Field, Module

TAGS = ('S0', 'Ssat', 'B0Ma')
NO_FREQ_TAGS = ('S0', 'B0Ma')          # 天然 3D，不需要频率


def _split(s) -> list:
    return [x.strip() for x in str(s or '').replace('，', ',').replace(';', ',')
            .split(',') if x.strip()]


class ReconModule(Module):
    id = 'm1_recon'
    badge = '①'
    title = 'DICOM 重建'
    out_subdir = '01_recon'
    accepts = ('dicom_uih', 'dicom_philips')
    produces = ('recon_mat',)
    schema = (
        Field('src', '输入目录（DICOM）', 'dir'),
        Field('cases', '病例（UIH）', 'cases', tip='逗号分隔，如 c1,c2,c3,h1,h2,h3,hp1,hp2,hp3',
              kinds=('dicom_uih',)),
        Field('case', '病例名（Philips）', 'text', tip='单序列没有病例名，默认取目录名，可改',
              kinds=('dicom_philips',)),
        Field('seq_base', '序列前缀', 'text', 'cest_tra_fast_10slice_0.7ut',
              tip='必须写全，否则类型解析会错', kinds=('dicom_uih',)),
        Field('slice_max', '层数（UIH）', 'int', 20, tip='不是协议里的 10，本批为 20',
              kinds=('dicom_uih',)),
        Field('dicom_order', '原始 DICOM 文件序', 'choice', 'auto',
              ('auto', 'layer_freq', 'freq_layer'),
              tip='auto=读 DICOM 标签自动判定（推荐）', kinds=('dicom_uih',)),
        Field('n_layer', '层数（Philips）', 'int', 10, kinds=('dicom_philips',)),
        Field('m0_tpi', 'M0 时相', 'text', '1,2,33',
              tip='Philips 单序列里哪几帧是 M0，逗号分隔', kinds=('dicom_philips',)),
        Field('freq_list', '频偏列表', 'text', '',
              tip='Philips 用；留空=从【频率表】取（推荐）', kinds=('dicom_philips',)),
        Field('do_normalize', '顺带做归一化', 'bool', True, tip='生成 normZ = ssat ./ s0'),
    )

    def __init__(self):
        self._plan_case = None      # 面板上选中的病例
        self._need: dict = {}       # {病例: {tag: 需要的频率个数}}
        self._manual: dict = {}     # {病例: 目录预览里手动选的结果}

    # ================================================================ 参数
    def suggest(self, det, proj):
        if det is None:
            return {}
        p = {'src': det.path, 'seq_base': 'cest_tra_fast_10slice_0.7ut',
             'slice_max': 20, 'dicom_order': 'auto', 'n_layer': 10,
             'm0_tpi': '1,2,33', 'freq_list': '', 'do_normalize': True,
             'cases': '', 'case': ''}
        if det.kind == 'dicom_uih':
            p['cases'] = ','.join(det.cases)
            p['n_layer'] = 20
        elif det.kind == 'dicom_philips':
            p['case'] = Path(det.path).name
        return p

    # ================================================================ 计划（面板数据）
    def plan(self, det, proj, params) -> dict:
        from detect import freq_doc as FD
        from detect import series

        kind = getattr(det, 'kind', '') or 'dicom_philips'
        src = str(params.get('src') or '').strip()
        out = dict(kind=kind, src=src, proj=proj or '', case='', cases=[],
                   rows=[], messages=[], blocking=False, has_4d=False,
                   scanned=False, manual=False)
        if not src:
            out['messages'].append('还没选输入目录。')
            return out
        if kind not in ('dicom_uih', 'dicom_philips'):
            out['messages'].append(
                f'当前识别为「{getattr(det, "label", "?")}」——不是 DICOM 数据。'
                f'① 需要含 .dcm 的目录；若这是上级目录，'
                f'请在顶部「下级数据集」里往下选一个。')
            return out

        if kind == 'dicom_uih':
            cases = _split(params.get('cases'))
            out['cases'] = cases
            case = (self._plan_case if self._plan_case in cases
                    else (cases[0] if cases else ''))
            self._plan_case = case
            out['case'] = case
            if not case:
                out['messages'].append('病例列表为空。')
                return out
            cdir = Path(src) / case

            man = self._manual.get(case)
            paths, miss, prefix = {}, [], ''
            singles = []

            if man:
                out['manual'] = True
                paths = dict(man['seqs'])
                miss = list(man['missing'])
                prefix = man['prefix']
                singles = list(man.get('single') or [])
            else:
                g = series.match_group(cdir, seq_base=str(params.get('seq_base') or ''),
                                       tags=TAGS, slice_max=params.get('slice_max'),
                                       scan=True)
                out['scanned'] = True
                if g.error:
                    out['messages'].append(g.error)
                paths = {t: s.path for t, s in g.seqs.items()}
                miss = list(g.missing)
                prefix = g.prefix
                if g.candidates:
                    out['messages'].append('还有其它同样相邻的候选组合（当前取编号最大的那组）：'
                                           + '、'.join(str(list(c)) for c in g.candidates[:4]))
                if g.others:
                    out['messages'].append(f'另有 {len(g.others)} 个文件夹不参与匹配'
                                           f'（如 {"、".join(g.others[:3])} …）')

            dims = {}
            for t, pth in paths.items():
                dims[t] = series.scan_dims(pth, slice_max=params.get('slice_max'))
            out['scanned'] = True
            self._need.setdefault(case, {})

            if man:
                if paths:
                    names = '、'.join(f'{t}={Path(p).name}' for t, p in paths.items())
                    out['messages'].append(f'已手动指定：{names}' +
                                           (f'；缺 {"、".join(miss)}' if miss else '')
                                           + '（点【浏览 / 选择序列…】可改）')
            if miss and paths:
                out['messages'].append('缺少：' + '、'.join(miss) +
                                       '（只重建已有的；缺 S0 无法归一化、缺 Ssat 没有频率维）')
            if singles:
                out['messages'].append('另按【单序列】重建 3D：'
                                       + '、'.join(Path(x).name for x in singles)
                                       + '（只出一个 3D，不需要频率表）')
                for x in singles:
                    out['rows'].append(dict(tag='单序列', name=Path(x).name,
                                            size_txt='—', ndim='3D(单序列)', need=0,
                                            freq_txt='—（不需要）', path=x, order=''))

            for t in TAGS:
                if t not in paths:
                    out['rows'].append(dict(tag=t, name='—', size_txt='—', ndim='—',
                                            need=0, freq_txt='—'))
                    continue
                d = dims[t]
                need = d.fps
                self._need[case][t] = need
                r = dict(tag=t, name=Path(paths[t]).name,
                         size_txt=f'{d.n_frames}/{d.n_slices}', ndim=d.ndim,
                         need=need, path=paths[t], order=d.order, allow_extra=False)
                if t in NO_FREQ_TAGS or d.ndim != '4D':
                    r['freq_txt'] = '—（不需要）'
                else:
                    r['allow_extra'] = True          # UIH：允许多出帧（重建按谱线自动识别并丢弃）
                    saved = FD.load_saved(proj, case) if proj else None
                    u, _ = FD.dedup(saved.values) if saved else ([], None)
                    if saved and u and (not need or len(u) <= need):
                        extra = (need - len(u)) if need else 0
                        r['freq_txt'] = (f'√ 已确认 {len(u)} 个频偏'
                                         + (f'（每层 {need} 帧 → 多出的 {extra} 帧自动丢弃）'
                                            if extra else '（== 每层帧数）'))
                    else:
                        r['freq_txt'] = f'× 未设定（需 {need} 个频偏）'
                        out['blocking'] = True
                if r['ndim'] == '4D':
                    out['has_4d'] = True
                out['rows'].append(r)

            if man and not any(str(r.get('ndim', '')).startswith('3D') for r in out['rows']) \
                    and not any(r.get('ndim') == '4D' for r in out['rows']):
                out['messages'].append('无法判定维度：手动指定的文件夹读不出 DICOM 标签。')
            return out

        # ---- Philips 单序列 ----
        case = str(params.get('case') or '').strip() or Path(src).name
        out['case'] = case
        out['cases'] = [case]
        d = series.scan_dims(src)
        out['scanned'] = True
        m0 = _split(params.get('m0_tpi'))
        need = max(0, d.fps - len(m0)) if d.fps else 0
        self._need.setdefault(case, {})['单序列'] = need
        r = dict(tag='单序列', name=Path(src).name,
                 size_txt=f'{d.n_frames}/{d.n_slices}（M0 {len(m0)} 帧）',
                 ndim=d.ndim, need=need, path=src, order=d.order, allow_extra=False)
        if d.ndim != '4D':
            r['freq_txt'] = '—（不需要）'
        else:
            saved = FD.load_saved(proj, case) if proj else None
            if saved and len(saved.values) == need:
                u, _ = FD.dedup(saved.values)
                r['freq_txt'] = f'√ 已确认 {len(saved.values)}→{len(u)} 个'
            else:
                r['freq_txt'] = f'× 未设定（需 {need} 个 = 每层 {d.fps} 帧 − M0 {len(m0)} 帧）'
                out['blocking'] = True
            out['has_4d'] = True
        if d.error:
            out['messages'].append(f'读 DICOM 标签失败：{d.error}')
        out['rows'].append(r)
        return out

    # ================================================================ 界面钩子
    def extra_panel(self, page):
        from app.widgets.recon_plan import ReconPlanPanel
        p = ReconPlanPanel()
        p.on_case_change = lambda c: (setattr(self, '_plan_case', c), page.refresh_extra())
        p.on_rescan = page.refresh_extra
        p.on_edit_freq = lambda plan: self._edit_freq(plan, page)
        p.on_browse = lambda plan: self._browse(plan, page)
        return p

    def refresh_extra(self, panel, det, proj, params):
        panel.render(self.plan(det, proj, params))

    # ================================================================ 目录预览
    def _browse(self, plan, page):
        from app.dialogs.browse_dialog import BrowseDialog
        if plan.get('kind') != 'dicom_uih':
            page.window.log('目录预览目前用于 UIH 多病例目录；Philips 单序列直接把它的目录当输入即可。')
            return
        case = plan.get('case') or ''
        cur = self._manual.get(case) or {}
        pre = {t for t in (cur.get('seqs') or {})}
        dlg = BrowseDialog(page, data_root=plan.get('src') or '', case=case,
                           seq_base=str(plan.get('seq_base') or ''),
                           slice_max=None, sel_tags=pre)
        # 直接进到该病例目录
        cdir = Path(plan['src']) / case
        if cdir.is_dir():
            dlg._go(cdir)
        if dlg.exec():
            sel = dlg.selection()
            if sel:
                self._manual[sel['case']] = sel
                page.window.log(f'目录预览：{sel["case"]} 手动指定 '
                                + '、'.join(f'{k}={Path(v).name}' for k, v in sel['seqs'].items())
                                + (f'（缺 {sel["missing"]}）' if sel['missing'] else ''))
                page.refresh_extra()

    # ================================================================ 频率表
    def _edit_freq(self, plan, page):
        from app.dialogs.freq_dialog import FreqDialog
        from detect import freq_doc as FD

        row = next((r for r in plan.get('rows') or []
                    if r.get('ndim') == '4D' and r.get('need')), None)
        if row is None:
            return
        case = plan.get('case') or ''
        man = self._manual.get(case) or {}
        case_dir = man.get('case_dir') or (
            str(Path(plan['src']) / case) if plan.get('kind') == 'dicom_uih'
            else plan.get('src') or '')
        dlg = FreqDialog(page, seq_name=row['name'], need=row['need'], case=case,
                         case_dir=case_dir, data_root=plan.get('src') or '',
                         proj=plan.get('proj') or '',
                         allow_extra=bool(row.get('allow_extra', True)))
        if dlg.exec():
            p = FD.save(plan['proj'], case, dlg.payload['values'],
                        source=dlg.payload['source'])
            page.window.log(f'频率表已确认并保存：{p}（{len(dlg.payload["values"])} 个）')
            page.refresh_extra()

    # ================================================================ 校验
    def _case_plan(self, p, c, case_dir=None):
        """返回 (paths, missing, names)，优先用手动指定。"""
        from detect import series
        man = self._manual.get(c)
        if man:
            return dict(man['seqs']), list(man['missing']), \
                {t: Path(x).name for t, x in man['seqs'].items()}
        case_dir = case_dir or (Path(str(p.get('src'))) / c)
        g = series.match_group(case_dir,
                               seq_base=str(p.get('seq_base') or ''),
                               tags=TAGS, slice_max=p.get('slice_max'), scan=False)
        return ({t: s.path for t, s in g.seqs.items()}, list(g.missing),
                {t: s.name for t, s in g.seqs.items()})

    def validate(self, p, ctx=None):
        from detect import freq_doc as FD

        errs = []
        src = str(p.get('src') or '').strip()
        if not src:
            return ['① 需要输入总目录（DICOM 位置）']
        if not Path(src).exists():
            return [f'输入目录不存在：{src}']
        if not (ctx and getattr(ctx, 'det', None)):
            return errs
        if ctx.det.kind not in ('dicom_uih', 'dicom_philips'):
            return [f'当前识别为「{ctx.det.label}」，① 需要 DICOM 目录'
                    f'（若是上级目录，用顶部「下级数据集」往下选）']
        proj = getattr(ctx, 'proj', None)
        kind = ctx.det.kind

        if kind == 'dicom_uih':
            cases = _split(p.get('cases'))
            if not cases:
                return ['UIH 模式需要病例列表（自动识别会填好）']
            if not proj:
                return ['需要先选输出总目录（PROJ）——4D 的频率表要存到那里']
            for c in cases:
                cdir = Path(src) / c
                paths, missing, names = self._case_plan(p, c, cdir)
                if not paths and not (self._manual.get(c) or {}).get('single'):
                    errs.append(f'{c}：一个序列都没匹配到（可用【浏览 / 选择序列…】手动指定）')
                    continue
                if 'Ssat' not in paths and 'S0' not in paths \
                        and not (self._manual.get(c) or {}).get('single'):
                    errs.append(f'{c}：既没有 S0 也没有 Ssat，无法重建')
                    continue
                for t, path in paths.items():
                    if t in NO_FREQ_TAGS:
                        continue
                    need = (self._need.get(c) or {}).get(t)
                    saved = FD.load_saved(proj, c)
                    if saved is None:
                        errs.append(f'{c}：4D 序列 {names.get(t, path)} 缺少已确认的频率表'
                                    + (f'（每层 {need} 帧）' if need else '')
                                    + ' → 点「频率表…」生成并确认（4D 数据没有频率不能跑）')
                    elif need and len(FD.dedup(saved.values)[0]) > need:
                        errs.append(f'{c}：频偏表 {len(FD.dedup(saved.values)[0])} 个 ＞ 每层 {need} 帧'
                                    f'（频偏数不能多于帧数）')
            return errs

        # Philips
        if not proj:
            return ['需要先选输出总目录（PROJ）——4D 的频率表要存到那里']
        from detect import series
        case = str(p.get('case') or '').strip() or Path(src).name
        need = (self._need.get(case) or {}).get('单序列')
        if need is None:
            d = series.scan_dims(src)
            need = max(0, d.fps - len(_split(p.get('m0_tpi'))))
        saved = FD.load_saved(proj, case)
        if need and saved is None:
            errs.append(f'{case}：4D 单序列缺少已确认的频率表（需 {need} 个 = '
                        f'每层帧数 − M0 帧数）→ 点「频率表…」')
        elif saved is not None and need and len(saved.values) != need:
            errs.append(f'{case}：频率表 {len(saved.values)} 个 ≠ 需要 {need} 个')
        return errs

    # ================================================================ 执行
    def run(self, p, ctx):
        import cest_recon as R
        from detect import freq_doc as FD
        from detect import manual_recon as MR
        from detect import series

        src = str(p['src']).strip()
        out_root = str(ctx.out_dir(self.out_subdir))
        do_norm = bool(p.get('do_normalize', True))
        written = []

        kind_run = ctx.det.kind if ctx.det else ''
        if kind_run == 'dicom_uih':
            cases = _split(p.get('cases'))
            seq_base = str(p.get('seq_base') or R.SEQ_BASE)
            slice_max = int(p.get('slice_max') or R.SLICE_MAX)
            order = str(p.get('dicom_order') or 'auto')

            manual = {c: self._manual[c] for c in cases if c in self._manual}
            auto = [c for c in cases if c not in self._manual]

            # ---- 1) 手动指定的病例：逐病例、用显式文件夹重建 ----
            for c, man in manual.items():
                cand = FD.load_saved(ctx.proj, c)
                for t, path in man['seqs'].items():
                    if t in NO_FREQ_TAGS:
                        continue
                    d = series.scan_dims(path, slice_max=slice_max)
                    if d.fps > 1 and cand is None:
                        raise RuntimeError(
                            f'{c}：手动指定的 4D 序列 {Path(path).name}（每层 {d.fps} 帧）'
                            f'缺少已确认的频率表 —— 4D 数据没有频率不能重建。\n'
                            f'请在 ① 页点『频率表…』生成并确认。')
                if cand is not None:
                    written.append((c, cand))
                    ctx.log(f'频率表：{len(cand.values)} 个（来自 {cand.source}）')
                ctx.log(f'① 手动指定重建：{c} → {Path(out_root) / c}')
                res = MR.recon_with_dirs(man['seqs'], Path(out_root) / c,
                                         slice_max=slice_max,
                                         freq_seq=list(cand.values) if cand else None,
                                         order=order, do_normalize=do_norm)
                ctx.log(f'  {c} 结果：{res}')
                for x in (man.get('single') or []):
                    ctx.log(f'  单序列 3D：{Path(x).name}')
                    r2 = MR.recon_single(x, Path(out_root) / c, slice_max=slice_max,
                                         order=order, name=Path(x).name)
                    res[f'单序列:{Path(x).name}'] = r2.get('3D')
                    ctx.log(f'    → {r2.get("3D")}')

            # ---- 2) 自动匹配的病例：按频率表分组批量跑 ----
            groups: dict = {}
            for c in auto:
                cand = FD.load_saved(ctx.proj, c)
                if cand is not None:
                    key = tuple(round(float(x), 6) for x in cand.values)
                    groups.setdefault(key, []).append(c)
                    written.append((c, cand))
                    continue
                g = series.match_group(Path(src) / c, seq_base=seq_base, tags=TAGS,
                                       slice_max=slice_max, scan=True)
                bad = [s for t, s in g.seqs.items()
                       if t not in NO_FREQ_TAGS and s.dim.fps > 1]
                if bad:
                    raise RuntimeError(
                        f'{c}：4D 序列 {bad[0].name}（每层 {bad[0].dim.fps} 帧）'
                        f'缺少已确认的频率表 —— 4D 数据没有频率不能重建。\n'
                        f'请在 ① 页「序列与频率」面板点『频率表…』生成并确认。')
                groups.setdefault(None, []).append(c)

            for key, sub in groups.items():
                cfg = dict(root_data=src, out_root=out_root, sub_list=sub,
                           seq_base=seq_base, slice_max=slice_max, dicom_order=order,
                           do_normalize=do_norm, nan_report=True)
                if key is not None:
                    cfg['freq_list'] = list(key)
                    cfg['n_rep'] = 1
                    u, _ = FD.dedup(key)
                    ctx.log(f'频率表：{len(key)} 个（去重后 {len(u)} 个），病例 {"、".join(sub)}')
                else:
                    ctx.log(f'按核心层默认频率表重建 3D 病例：{"、".join(sub)}')
                R.run_recon(cfg)

            for c, cand in written:
                self._write_freq_txt(Path(out_root) / c, cand, ctx)
            return dict(cases=cases, out_root=out_root,
                        manual=sorted(manual), auto=sorted(auto))

        # ---- Philips 单序列 ----
        case = str(p.get('case') or '').strip() or Path(src).name
        m0 = [int(v) for v in _split(p.get('m0_tpi'))]
        cand = FD.load_saved(ctx.proj, case)
        d = series.scan_dims(src)
        if cand is None and d.fps > 1:
            raise RuntimeError(
                f'{case}：4D 单序列（每层 {d.fps} 帧）缺少已确认的频率表 —— '
                f'4D 数据没有频率不能重建。请在 ① 页点『频率表…』。')
        if cand is not None and d.fps and len(cand.values) != d.fps - len(m0):
            raise RuntimeError(f'{case}：频率表 {len(cand.values)} 个 ≠ 需要的 '
                               f'{d.fps - len(m0)} 个（每层 {d.fps} 帧 − M0 {len(m0)} 帧）')
        cfg = dict(dicom_dir=src, out_dir=str(Path(out_root) / case),
                   n_layer=int(p.get('n_layer') or 10), m0_tpi=m0, order='auto',
                   do_normalize=do_norm)
        if cand is not None:
            cfg['freq_list'] = [float(x) for x in cand.values]
            ctx.log(f'频率表：{len(cand.values)} 个（来自 {cand.source}）')
        else:
            ft = str(p.get('freq_list') or '').strip()
            if ft:
                cfg['freq_list'] = [float(v) for v in _split(ft)]
        ctx.log(f'① Philips 单序列：{case} → {cfg["out_dir"]}')
        res = R.recon_philips_cest(cfg)
        if cand is not None:
            self._write_freq_txt(Path(out_root) / case, cand, ctx)
        return dict(shape=res, case=case, out_dir=cfg['out_dir'])

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def _write_freq_txt(case_dir: Path, cand, ctx=None):
        from detect import freq_doc as FD
        try:
            case_dir.mkdir(parents=True, exist_ok=True)
            u, _ = FD.dedup(cand.values)
            (case_dir / 'freq_list.txt').write_text(
                f'# 采集顺序频率表（{len(cand.values)} 个，含重复）\n'
                + ' '.join(f'{v:g}' for v in cand.values)
                + f'\n\n# 去重后 w_offset（{len(u)} 个，写进 mat 的就是这份）\n'
                + ' '.join(f'{v:g}' for v in u)
                + f'\n\n# 来源\n{cand.note}\n{cand.source}\n', encoding='utf-8')
        except Exception as e:                                    # noqa: BLE001
            if ctx:
                ctx.log(f'（提示）写 freq_list.txt 失败：{e}')
