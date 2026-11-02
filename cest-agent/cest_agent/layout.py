# -*- coding: utf-8 -*-
"""cest_agent.layout —— 输入/输出目录自动推断。

★ 唯一真源仍是 cest-gui 的 detect/chain + detect/rules：
  这里只做「用户给了一个路径，判断它是 DICOM 还是 PROJ、病例叫什么、
  该往哪个子目录落」的编排，不自己发明目录名。

两个问题：
    输入是什么？  layout.sniff(path) -> dict(kind, case, cases, proj, note)
    输出去哪？    layout.targets(proj, case) -> dict 各阶段的输入/输出绝对路径

kind 取值：
    dicom     原始 DICOM（病例目录，如 ...\\20260909\\c1）
    dicom_root 原始 DICOM 的上级（里面多个病例，如 ...\\20260909）
    recon     ① 的重建结果目录（含 s0/ssat/w_offset）
    proj      CEST Studio 的输出总目录（含 00_掩模/01_重建/...）
    post      ③ 的产物目录
    ld        ④ 的产物目录
    unknown
"""
from __future__ import annotations

from pathlib import Path

from detect import chain
from detect import rules as R


def _sub(proj, key):
    return R.find_subdir(proj, key)


def _has_dcm(d: Path) -> bool:
    try:
        return R.count_dcm(d) > 0
    except Exception:                                    # noqa: BLE001
        return False


def _dicom_cases(d: Path) -> list:
    """d 下面哪些子目录像 DICOM 病例（含 .dcm 且不是 PROJ 子目录）。"""
    out = []
    try:
        for c in sorted(d.iterdir()):
            if not c.is_dir() or c.name.startswith(('_', '.')):
                continue
            if R.cn_name(c.name) in [R.cn_name(x) for x in R.PROJ_SUBDIRS]:
                continue
            if _has_dcm(c):
                out.append(c.name)
    except OSError:
        pass
    return out


def sniff(path) -> dict:
    """判断这个路径是什么，病例叫什么。

    ★ 判定一律问 GUI 自己的 detect()，不自己写规则——
      否则会出现「GUI 认得出、agent 认不出」的分裂。
    """
    from detect.scanner import detect

    p = str(path or '').strip()
    r = dict(path=p, kind='unknown', case='', cases=[], proj='', note='')
    if not p:
        r['note'] = '（空路径）'
        return r
    d = Path(p)
    if not d.exists():
        r['note'] = '路径不存在'
        return r

    try:
        det = detect(p)
    except Exception:                                    # noqa: BLE001
        det = None
    kind = getattr(det, 'kind', '') if det else ''

    # 1) PROJ
    if kind == 'project_root':
        cases = chain.list_cases(d)
        r.update(kind='proj', proj=str(d), cases=cases,
                 case=(cases[0] if cases else ''),
                 note=f'输出总目录（{len(cases)} 个病例）')
        return r

    # 2) ① 重建结果
    if chain.has_recon(d):
        r.update(kind='recon', case=d.name, cases=[d.name],
                 proj=str(d.parent.parent), note=f'① 重建结果：{d.name}')
        return r
    # 3) ③ / ④ 产物
    try:
        if (d / 'post_meta.json').exists():
            r.update(kind='post', case=d.name, cases=[d.name],
                     proj=str(d.parent.parent), note=f'③ 产物：{d.name}')
            return r
        if (d / 'voxel_LD.mat').exists():
            r.update(kind='ld', case=d.name, cases=[d.name],
                     proj=str(d.parent.parent), note=f'④ 产物：{d.name}')
            return r
    except OSError:
        pass

    # 4) DICOM：GUI 对「病例目录」和「上级目录」都返 dicom_*，
    #    真正的区别在 meta['seq'] 的 key —— 它就是病例名集合。
    if kind.startswith('dicom'):
        seq = ((getattr(det, 'meta', None) or {}).get('seq') or {})
        keys = [k for k in seq.keys() if k]
        if len(keys) == 1:
            r.update(kind='dicom', case=keys[0], cases=keys,
                     note=f'DICOM 病例：{keys[0]}')
            return r
        if len(keys) > 1:
            r.update(kind='dicom_root', case=keys[0], cases=keys,
                     note=f'DICOM 上级目录（{len(keys)} 个病例：'
                          f'{", ".join(keys[:6])}{"…" if len(keys) > 6 else ""}）')
            return r
        r.update(kind='dicom', case=d.name, cases=[d.name],
                 note=f'DICOM 病例：{d.name}（未识别到三序列命名）')
        return r

    # 5) 兜底：子目录里有重建结果 → 当成 PROJ
    try:
        for c in sorted(d.iterdir()):
            if c.is_dir() and chain.has_recon(c):
                r.update(kind='proj', proj=str(d),
                         cases=chain.list_cases(d) or [c.name], case=c.name,
                         note=f'看起来是输出总目录（{c.name} 已有重建结果）')
                return r
    except OSError:
        pass
    r['note'] = ('认不出这个目录（既不是 DICOM，也没有重建结果）'
                 + (f'　GUI 的判定：{getattr(det, "label", "")}' if det else ''))
    return r


def _dicom_kids(d: Path):
    """d 下面哪些子目录各自能被认成 DICOM（= 多病例上级目录）。"""
    from detect.scanner import detect
    out = []
    try:
        for c in sorted(d.iterdir()):
            if not c.is_dir() or c.name.startswith(('_', '.')):
                continue
            if R.cn_name(c.name) in [R.cn_name(x) for x in R.PROJ_SUBDIRS]:
                continue
            try:
                if getattr(detect(str(c)), 'kind', '').startswith('dicom'):
                    out.append(c.name)
            except Exception:                            # noqa: BLE001
                continue
    except OSError:
        pass
    return out, None


def resolve_io(src_path, proj_hint='', case='') -> dict:
    """把「用户给的输入」翻译成 (proj, case, 各阶段输入输出)。

    返回：
        ok / kind / proj / case / cases / src / note / problems[]
        stages: {stage_id: dict(in=..., out=...)}
    """
    s = sniff(src_path)
    proj = str(proj_hint or '').strip()
    case = str(case or '').strip() or s.get('case') or ''
    out = dict(ok=False, kind=s['kind'], proj=proj, case=case,
               cases=s.get('cases') or [], src=s['path'],
               note=s['note'], problems=[], stages={})

    if s['kind'] == 'proj':
        # 用户直接给了 PROJ
        proj = s['proj'] or proj
        out['proj'] = proj
        out['cases'] = s.get('cases') or []
        case = case or (out['cases'][0] if out['cases'] else '')
        out['case'] = case
        out['ok'] = bool(proj)
        out['stages'] = targets(proj, case)
        return out

    if s['kind'] in ('dicom', 'dicom_root'):
        # 原始 DICOM：PROJ 必须另给（或用 hint）；病例名取 DICOM 目录名
        out['cases'] = s.get('cases') or [case] if case else s.get('cases') or []
        out['case'] = case or s.get('case', '')
        if not proj:
            out['problems'].append(dict(
                kind='manual_arg',
                text='这是原始 DICOM，还需要指定「输出总目录(PROJ)」——'
                     '重建结果、掩模、③④ 产物都会按 PROJ 的固定子目录存放。',
                fix_kind='manual_arg'))
        else:
            out['ok'] = True
            out['stages'] = targets(proj, out['case'], dicom_src=s['path']
                                    if s['kind'] == 'dicom' else str(
                                        Path(s['path']) / out['case']))
        return out

    if s['kind'] in ('recon', 'post', 'ld'):
        proj = proj or s['proj']
        out['proj'] = proj
        out['case'] = case or s.get('case', '')
        if not proj:
            out['problems'].append(dict(
                kind='manual_arg', text='找不到所属的输出总目录(PROJ)。',
                fix_kind='manual_arg'))
        else:
            out['ok'] = True
            out['stages'] = targets(proj, out['case'])
        return out

    out['problems'].append(dict(kind='manual_arg',
                                text=s.get('note') or '认不出这个目录',
                                fix_kind='manual_arg'))
    return out


def targets(proj, case, dicom_src='') -> dict:
    """各阶段的输入/输出绝对路径（PROJ 的固定布局，直接用 GUI 的 cn_name）。"""
    proj = str(proj or '').strip()
    case = str(case or '').strip()
    if not proj or not case:
        return {}
    p = Path(proj)
    rd = p / R.cn_name('01_recon') / case
    roi = p / R.cn_name('00_roi') / case
    post = p / R.cn_name('03_post') / case
    ld = p / R.cn_name('04_ld') / case
    disp = p / R.cn_name('02_display') / case

    def _mask():
        mf = chain.mask_file(proj, case)
        return str(mf) if mf else str(roi / 'Mask.mat')

    return {
        'm1_recon': dict(inp=dicom_src or '', out=str(rd)),
        'm_roi': dict(inp=str(rd), out=str(roi), mask=_mask(),
                      roi_total=str(roi / 'ROI_total.mat')),
        'm3_post': dict(inp=str(rd), out=str(post), mask=_mask()),
        'm4_ld': dict(inp=str(rd), out=str(ld), mask=_mask()),
        'm5_prf': dict(inp=proj, out=str(p / R.cn_name('05_prf_extract'))),
        'm6_prf_analysis': dict(inp=proj, out=str(p / R.cn_name('06_prf_analysis'))),
        'm2_display': dict(inp=str(rd), out=str(disp), mask=_mask()),
        'm0_check': dict(inp=proj, out=str(p / R.cn_name('logs'))),
    }


def describe(io: dict) -> str:
    """人话说明这次会读写哪里。"""
    if not io:
        return ''
    t = io.get('stages') or {}
    lines = [f'输入：{io.get("note") or io.get("src")}']
    if io.get('proj'):
        lines.append(f'输出总目录：{io["proj"]}')
    if io.get('case'):
        lines.append(f'病例：{io["case"]}')
    for mid, label in (('m1_recon', '①'), ('m_roi', '⑧'),
                       ('m3_post', '③'), ('m4_ld', '④'), ('m2_display', '②')):
        d = t.get(mid) or {}
        if d.get('out'):
            lines.append(f'  {label} → {d["out"]}')
    return '\n'.join(lines)
