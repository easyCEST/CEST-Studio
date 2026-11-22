# -*- coding: utf-8 -*-
"""cest_agent.agent —— 大模型主导的智能体循环（function calling）。

和旧的 intent.py 根本区别：
    intent.py  正则优先，LLM 只在正则认不出时兜底，且只能输出 4 个字段
    agent.py   模型**先看当前项目真实状态**，再自己决定调哪个工具、传什么参数

一轮对话 = 模型 → 工具调用 → 真实执行结果 → 模型 → …（最多 MAX_ROUNDS 轮）
直到模型给出一段人话。历史全程保留，所以能追问、能改主意。

★ 执行内核一行没改：所有工具最终都调 runner.run_module / runner.run_chain，
  就是 GUI 页面按按钮跑的那套代码。模型只是「代替你点按钮、填参数」。
★ 三条硬红线写在工具内部（不是提示词），模型想绕也绕不过：
    1) 掩模/ROI 绝不代生成，缺了直接拒
    2) ③④② 没有掩模不许跑
    3) 频率表必须人核对过才写
"""
from __future__ import annotations

import json
import os
import time

MAX_ROUNDS = 40                       # 单轮对话里最多几次工具往返（原来 12 太少，查几层目录就见底）

# ============================================================ 工具实现
def _tool_list_cases(proj, io, args):
    from . import plan
    cases = plan.cases_of(proj) if proj else []
    io_cases = list((io or {}).get('cases') or [])
    out = cases or io_cases
    return dict(proj=proj, cases=out, count=len(out),
                note='' if out else '还没认出病例：先选输入目录或跑 ⓪ 导入重建')


def _tool_get_status(proj, io, args):
    from . import plan
    case = str(args.get('case') or '').strip()
    stages = args.get('stages') or None
    if stages:
        stages = [s for s in stages if s in plan.STAGE_LABEL]
    r = plan.check(proj, case, stages=stages, io=io)
    # 只留模型需要看的字段，别把整个结果塞进去
    slim = dict(proj=r.get('proj'), error=r.get('error'),
                summary=r.get('summary'), ready=r.get('ready_cases'),
                stuck=r.get('stuck_cases'), next_action=r.get('next_action'),
                cases=[])
    for c in r.get('cases') or []:
        slim['cases'].append(dict(
            case=c.get('case'), runnable=c.get('runnable'),
            stages=[dict(id=s.get('id'), status=s.get('status'),
                         problems=[p.get('text') for p in (s.get('problems') or [])])
                    for s in (c.get('stages') or [])],
            needs_human=[p.get('text') for p in (c.get('needs_human') or [])]))
    return slim


def _proj_map(proj, io, args):
    """项目地图：真实扫 PROJ，把目录结构、每个模块的产物、⑤⑥ 各模式有没有，全摊开。

    ★ 这是「了如指掌」的基础。模型不再靠猜——它看到的就是磁盘上真实的东西。
    """
    from pathlib import Path
    from .plan import STAGE_LABEL, CHAIN_STAGES
    if not proj:
        return dict(error='还没选 PROJ')
    root = Path(proj)
    if not root.is_dir():
        return dict(error=f'PROJ 不存在：{proj}')

    def _tree(d: Path, depth=0, maxdepth=2, limit=40):
        """两级目录树（只给目录名 + 文件数），避免把几万个体素文件扔给模型。"""
        out = []
        try:
            subs = sorted([x for x in d.iterdir() if x.is_dir()],
                          key=lambda x: x.name.lower())
        except OSError:
            return out
        for s in subs[:limit]:
            try:
                nf = sum(1 for _ in s.iterdir())
            except OSError:
                nf = 0
            out.append(dict(name=s.name, files=nf, path=str(s)))
            if depth + 1 < maxdepth:
                out += [dict(x, name=f"{s.name}/{x['name']}")
                        for x in _tree(s, depth + 1, maxdepth, 10)]
        return out

    from detect import rules as R

    def _sub(key):
        try:
            return R.find_subdir(str(root), key)
        except Exception:                                # noqa: BLE001
            return None

    info = dict(proj=str(root),
                top_dirs=[x.name for x in sorted(root.iterdir()) if x.is_dir()]
                if root.is_dir() else [])

    # ---- ① 重建：病例清单 ----
    d1 = _sub('01_recon')
    cases = []
    if d1:
        try:
            cases = sorted([x.name for x in d1.iterdir() if x.is_dir()])
        except OSError:
            pass
    info['cases'] = cases
    info['n_cases'] = len(cases)

    # ---- ⑧ 掩模：每例有没有 ----
    from detect import chain as _chain
    masks = {}
    for c in cases[:200]:
        try:
            mf = _chain.mask_file(str(root), c)
            masks[c] = bool(mf)
        except Exception:                                # noqa: BLE001
            masks[c] = False
    info['masks'] = dict(have=sum(1 for v in masks.values() if v),
                         missing=[k for k, v in masks.items() if not v][:20],
                         total=len(masks))

    # ---- ③④② 产物目录 ----
    for key, label in (('03_post', '③ 后处理'), ('04_ld', '④ 逐体素LD'),
                       ('02_display', '② 展示')):
        d = _sub(key)
        subs = [x.name for x in sorted(d.iterdir()) if x.is_dir()] if d else []
        info[label] = dict(exists=bool(d), path=str(d) if d else '',
                           n=len(subs), dirs=subs[:12])

    # ---- ⑤ PRF 提取：三种模式各自有没有 ----
    d5 = _sub('05_prf_extract')
    m5 = {}
    if d5:
        for sub, name, mode in (('组内_按组', 'groupPaths.mat', '组内'),
                                ('组内_单个', 'resultPaths.mat', '单个对比'),
                                ('组间', 'newGroupPaths.mat', '组间')):
            f = d5 / sub / name
            if not f.exists():
                f = d5 / name
            m5[mode] = dict(exists=f.exists(), path=str(f) if f.exists() else str(d5 / sub / name))
    else:
        m5 = {k: dict(exists=False, path='') for k in ('组内', '单个对比', '组间')}
    info['⑤ PRF提取'] = m5

    # ---- ⑥ PRF 分析：三种模式各自有没有 ----
    d6 = _sub('06_prf_analysis')
    m6 = {}
    for folder in ('组内', '单个对比', '组间'):
        f = (d6 / folder / 'prf_analysis_meta.json') if d6 else None
        item = dict(exists=bool(f and f.exists()), path=str(f) if f and f.exists() else '')
        if item['exists']:
            try:
                meta = json.loads(f.read_text(encoding='utf-8'))
                charts = meta.get('charts') or []
                item['mode'] = meta.get('mode')
                item['style'] = meta.get('style')
                item['range'] = meta.get('range')
                item['savepaths'] = meta.get('savepaths')
                item['sec'] = meta.get('sec')
                item['charts'] = [dict(chart=c.get('chart'),
                                       n_series=c.get('n_series'),
                                       n=c.get('n'), r2=c.get('r2'))
                                  for c in charts]
            except Exception:                            # noqa: BLE001
                pass
        m6[folder] = item
    info['⑥ PRF分析'] = m6

    # ---- ⑩ 图像对比：已有哪些对比对 ----
    d10 = _sub('10_compare')
    pairs = []
    if d10:
        try:
            pairs = sorted([x.name for x in d10.iterdir() if x.is_dir()])
        except OSError:
            pass
    info['⑩ 图像对比'] = dict(exists=bool(d10), path=str(d10) if d10 else '',
                              n=len(pairs), pairs=pairs[:12])

    # ---- 阶段完成度（只看数据链）----
    st = {}
    try:
        from . import plan
        if cases:
            r = plan.check(str(root), cases[0], stages=list(CHAIN_STAGES), io=io)
            c0 = (r.get('cases') or [{}])[0]
            st = {s.get('id'): s.get('status') for s in (c0.get('stages') or [])}
    except Exception:                                    # noqa: BLE001
        pass
    info['chain_status_sample'] = dict(case=cases[0] if cases else '', stages=st)

    # ---- 目录树（两级，给模型看整体形状）----
    info['tree'] = [dict(name=x['name'], files=x['files']) for x in _tree(root, 0, 2)]
    return info


def _tool_schema(mid, proj, io, args):
    """把一个模块的完整参数表交给模型：key / 类型 / 当前默认 / 合法取值。"""
    from . import runner
    from .plan import STAGE_LABEL
    if mid not in STAGE_LABEL:
        return dict(error=f'没有模块 {mid}，可用：' + '、'.join(STAGE_LABEL))
    m = runner._module(mid)
    if m is None:
        return dict(error=f'找不到模块 {mid}')
    fields = []
    for f in getattr(m, 'schema', ()):
        fields.append(dict(key=f.key, label=f.label, kind=f.kind,
                           default=f.default,
                           choices=list(f.choices or ()),
                           tip=(f.tip or '').replace('\n', ' ')[:160]))
    return dict(mid=mid, title=f'{m.badge} {m.title}', fields=fields,
                note='run_module 的 params 用这里的 key；choice 只能填 choices 里的原值')


def _need_mask(proj, case):
    """③④② 都要掩模；返回 (ok, 说明)。"""
    try:
        from detect import chain
        mf = chain.mask_file(proj, case)
    except Exception as e:                               # noqa: BLE001
        return False, f'查不到掩模：{e}'
    if not mf:
        return False, (f'{case} 还没有去噪掩模。掩模必须人在 ⑧ 页手画，'
                       f'智能体不代生成——请先去 ⑧ 画好再跑。')
    return True, str(mf)


def _tool_run_module(proj, case, io, args, emit, cancel, confirm_freq):
    from . import runner
    from .plan import STAGE_LABEL
    mid = str(args.get('mid') or '').strip()
    if mid not in STAGE_LABEL:
        return dict(ok=False, error=f'没有模块 {mid}，可用：' + '、'.join(STAGE_LABEL))
    params = args.get('params') or {}
    if not isinstance(params, dict):
        return dict(ok=False, error='params 必须是对象')

    case = str(args.get('case') or case or '').strip()

    # ---- 硬红线 1：⑧ 不代跑 ----
    if mid == 'm_roi':
        return dict(ok=False, rejected=True,
                    error='⑧ 去噪掩模/ROI 必须人手工画，智能体不代生成。请让用户去 ⑧ 页操作。')
    # ---- 硬红线 2：③④② 无掩模不许跑 ----
    if mid in ('m3_post', 'm4_ld', 'm2_display'):
        ok, why = _need_mask(proj, case)
        if not ok:
            return dict(ok=False, rejected=True, need_human=True, error=why,
                        goto=dict(mid='m_roi', case=case))

    # ⑩ 图像对比：要 A / B 两个病例，不吃单个 case；两例都得有掩模
    if mid == 'm7_compare':
        for key in ('case_a', 'case_b'):
            if not str(params.get(key) or '').strip():
                v = str(args.get(key) or '').strip()
                if v:
                    params[key] = v
        if not (str(params.get('case_a') or '').strip()
                and str(params.get('case_b') or '').strip()):
            # 没指定就按模块自己的建议（默认 GS / SSE 那例）自动配一对
            try:
                sug = runner._module(mid).suggest(None, proj) or {}
                params.setdefault('case_a', sug.get('case_a') or '')
                params.setdefault('case_b', sug.get('case_b') or '')
            except Exception:                            # noqa: BLE001
                pass
        for key in ('case_a', 'case_b'):
            c = str(params.get(key) or '').strip()
            if not c:
                return dict(ok=False, error='⑩ 需要两个不同病例做对比，但 PROJ 里只认出 '
                                            f'一个（{key} 是空的）。先用 get_status 看病例。')
            ok, why = _need_mask(proj, c)
            if not ok:
                return dict(ok=False, rejected=True, need_human=True, error=why,
                            goto=dict(mid='m_roi', case=c))
        if str(params['case_a']).strip() == str(params['case_b']).strip():
            return dict(ok=False, error=f'⑩ 的病例 A 和病例 B 都是 '
                                        f'{params["case_a"]}，没有可比的，换一个。')
        case = ''

    # 校验 params 的 choice 值，避免模型瞎填被静默吞掉
    m = runner._module(mid)
    bad = []
    for f in getattr(m, 'schema', ()):
        if f.key in params and f.choices and str(params[f.key]) not in list(f.choices):
            bad.append(f"{f.key}={params[f.key]}（只能：{' / '.join(map(str, f.choices))}）")
    if bad:
        return dict(ok=False, error='参数取值不合法：' + '；'.join(bad))

    # ⑥ 填了 mode 就必须真有对应那份输入，否则 ⑤ 没跑过会静默退回组内
    if mid == 'm6_prf_analysis' and str(params.get('mode') or '').strip():
        try:
            from detect import rules as _R
            from app.modules.m6_prf_analysis import MODE_SP
            mode = str(params['mode']).strip()
            sub, name = MODE_SP.get(mode, ('', ''))
            d5 = _R.find_subdir(proj, '05_prf_extract')
            has = bool(d5 and sub and ((d5 / sub / name).exists() or (d5 / name).exists()))
            if not has:
                return dict(ok=False, rejected=True, need_human=False,
                            error=f'要做「{mode}」分析，但 05_PRF提取\\{sub}\\{name} 不存在。'
                                  f'请先用 ⑤ PRF 提取、分析模式选「组间」跑一遍，'
                                  f'生成这份产物后再做 ⑥。')
        except Exception:                                # noqa: BLE001
            pass

    # ① 重建涉及频率表核对：run_module 不接受 confirm_freq 回调，
    # 走 run_chain(单阶段单病例) 才能真的把频率表弹给用户确认。
    if mid == 'm1_recon':
        res = runner.run_chain(proj, [case], ['m1_recon'], overrides=params,
                               on_event=emit, force=bool(params.get('force')),
                               cancel_flag=cancel, confirm_freq=confirm_freq, io=io)
    else:
        res = runner.run_module(mid, proj, case, overrides=params,
                                on_event=emit, cancel_flag=cancel, io=io)
    return dict(ok=bool(res.get('ok')), summary=res.get('summary'),
                sec=res.get('sec'),
                cases=[dict(case=c.get('case'), status=c.get('status'),
                            stages=[dict(id=s.get('id'), status=s.get('status'),
                                         error=str(s.get('error') or '')[:200],
                                         verify_reason=((s.get('verify') or {}).get('reason') or '')[:200])
                                    for s in (c.get('stages') or [])])
                       for c in (res.get('cases') or [])],
                pending_human=[p.get('text') for p in (res.get('pending_human') or [])],
                diagnoses=[dict(name=d.get('name'), cause=d.get('cause'),
                                action=d.get('action'))
                           for d in (res.get('diagnoses') or [])])


def _tool_run_chain(proj, io, args, emit, cancel, confirm_freq):
    from . import runner
    from .plan import CHAIN_STAGES
    stages = [s for s in (args.get('stages') or []) if s in CHAIN_STAGES]
    if not stages:
        return dict(ok=False, error='stages 必须取自：' + '、'.join(CHAIN_STAGES))
    cases = [str(c) for c in (args.get('cases') or []) if str(c).strip()]
    force = bool(args.get('force'))
    if not cases:
        from . import plan
        cases = plan.cases_of(proj)
    if not cases:
        return dict(ok=False, error='没认出任何病例')
    # 硬红线 2（整链版）
    if any(s in ('m3_post', 'm4_ld', 'm2_display') for s in stages) \
            and 'm_roi' not in stages and 'm1_recon' not in stages:
        for c in cases:
            ok, why = _need_mask(proj, c)
            if not ok:
                return dict(ok=False, rejected=True, need_human=True,
                            error=why, goto=dict(mid='m_roi', case=c))
    res = runner.run_chain(proj, cases, stages, on_event=emit, force=force,
                           cancel_flag=cancel, confirm_freq=confirm_freq, io=io)
    return dict(ok=bool(res.get('ok')), summary=res.get('summary'),
                sec=res.get('sec'),
                cases=[dict(case=c.get('case'), status=c.get('status'),
                            stages=[dict(id=s.get('id'), status=s.get('status'),
                                         error=str(s.get('error') or '')[:200])
                                    for s in (c.get('stages') or [])])
                       for c in (res.get('cases') or [])[:20]],
                pending_human=[p.get('text') for p in (res.get('pending_human') or [])],
                diagnoses=[dict(name=d.get('name'), cause=d.get('cause'),
                                action=d.get('action'))
                           for d in (res.get('diagnoses') or [])])


def _tool_ask_human(args):
    return dict(asked=True, question=str(args.get('question') or ''),
                options=[str(x) for x in (args.get('options') or [])],
                note='已把问题抛给用户，本轮到此为止，等用户回答')


def _tool_export_ortho(sess, args):
    """三正交面出图（与 GUI ② 的「三视图」同源取数，不含 GUI）。"""
    from . import ortho
    case = str(args.get('case') or '').strip()
    if not case:
        return dict(ok=False, error='要出三视图得说清是哪个病例（case）')
    if not sess.proj:
        return dict(ok=False, error='还没设 PROJ，先用 set_project 设一个')
    if sess.cancel():
        return dict(ok=False, error='已取消')
    try:
        r = ortho.export(
            sess.proj, case,
            mode=str(args.get('mode') or 'LD'),
            w=float(args.get('w') if args.get('w') is not None else 3.5),
            ld_mode=str(args.get('ld_mode') or 'POS'),
            use_mask=bool(args.get('use_mask', True)),
            x=args.get('x'), y=args.get('y'), z=args.get('z'),
            caxis=args.get('caxis'),
            dpi=int(args.get('dpi') or 200))
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'{type(e).__name__}: {e}')
    return r


# ============================================================ 访问电脑（按权限档）
def _g(sess):
    from . import grants
    return grants.cfg_of(sess.cfg_all)


def _tool_list_dir(sess, args):
    """列目录。只看目录名 + 文件数，不读内容。"""
    from pathlib import Path
    from . import grants
    g = _g(sess)
    ok, rp, why = grants.check_read(args.get('path') or sess.proj,
                                    g['level'], sess.proj)
    if not ok:
        return dict(ok=False, error=why)
    p = Path(rp)
    if p.is_file():
        return dict(ok=False, error=f'这是文件不是目录：{rp}，用 read_text_file')
    depth = max(0, min(int(args.get('depth') or 1), 3))
    maxitems = max(10, min(int(args.get('limit') or 200), 500))

    def walk(d: Path, lv: int):
        rows = []
        try:
            items = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError as e:
            return [dict(name='（无法读取）', error=str(e))]
        for it in items[:maxitems]:
            if it.name.startswith('$') or it.name.lower() in (
                    'system volume information', 'recycle.bin'):
                continue
            if it.is_dir():
                try:
                    n = sum(1 for _ in it.iterdir())
                except OSError:
                    n = 0
                row = dict(name=it.name, type='dir', items=n, path=str(it))
                if lv < depth:
                    sub = walk(it, lv + 1)
                    if sub:
                        row['children'] = sub[:30]
                rows.append(row)
            else:
                rows.append(dict(name=it.name, type='file',
                                 size=it.stat().st_size if it.exists() else 0,
                                 path=str(it)))
        return rows

    return dict(ok=True, path=rp, level=g['level'], tree=walk(p, 0))


def _tool_scan_path(sess, args):
    """认一个路径是什么：DICOM / 病例目录 / PROJ / 掩模 / 训练集…"""
    from . import grants
    from detect.scanner import detect
    g = _g(sess)
    ok, rp, why = grants.check_read(args.get('path') or '', g['level'], sess.proj)
    if not ok:
        return dict(ok=False, error=why)
    try:
        d = detect(rp)
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'识别失败：{type(e).__name__}: {e}')
    return dict(ok=True, path=rp, kind=d.kind, label=d.label,
                cases=(d.cases or [])[:60], n_cases=len(d.cases or []),
                modules=d.modules or [], candidates=(d.candidates or [])[:20],
                lines=(d.lines or [])[:12])


def _tool_find_data(sess, args):
    """在某个目录下找原始数据（DICOM 目录 / S0+Z 谱目录）。"""
    from pathlib import Path
    from . import grants
    from detect.scanner import detect
    g = _g(sess)
    root = args.get('path') or sess.proj
    ok, rp, why = grants.check_read(root, g['level'], sess.proj)
    if not ok:
        return dict(ok=False, error=why)
    maxdepth = max(1, min(int(args.get('max_depth') or 3), 5))
    limit = max(5, min(int(args.get('limit') or 20), 50))
    want = str(args.get('kind') or '').strip().lower()

    hits = []
    base = Path(rp)
    stack = [(base, 0)]
    seen = 0
    while stack and len(hits) < limit and seen < 3000:
        d, lv = stack.pop(0)
        seen += 1
        try:
            kids = [x for x in sorted(d.iterdir())
                    if x.is_dir() and not x.name.startswith(('.', '$'))]
        except OSError:
            continue
        for k in kids:
            try:
                det = detect(str(k))
            except Exception:                            # noqa: BLE001
                det = None
            if det is not None and det.kind != 'unknown':
                if want and want not in det.kind.lower() and want not in det.label.lower():
                    pass
                else:
                    hits.append(dict(path=str(k), kind=det.kind, label=det.label,
                                     n_cases=len(det.cases or []),
                                     cases=(det.cases or [])[:12]))
                    continue
            if lv < maxdepth:
                stack.append((k, lv + 1))
    return dict(ok=True, root=rp, scanned=seen, found=len(hits), hits=hits,
                note='' if hits else '这一层没找到能识别的数据，可以放宽 max_depth 再试')


def _tool_set_project(sess, args):
    """设置工作区：PROJ、原始 DICOM、数据根、掩模源。等同于用户在左边填框。"""
    from pathlib import Path
    from . import grants, layout
    g = _g(sess)
    proj = str(args.get('proj') or '').strip()
    dicom = str(args.get('dicom_src') or '').strip()
    data = str(args.get('data_src') or '').strip()
    mask = str(args.get('mask_src') or '').strip()
    case = str(args.get('case') or '').strip()

    for label, v in (('proj', proj), ('dicom_src', dicom),
                     ('data_src', data), ('mask_src', mask)):
        if not v:
            continue
        ok, rp, why = grants.check_read(v, g['level'], sess.proj or proj)
        if not ok and label == 'proj':
            # 新项目目录可能还不存在：退一步按父目录判权限
            from pathlib import Path as _P
            par = _P(v).parent
            ok2, _, why2 = grants.check_read(par, g['level'], sess.proj or str(par))
            if ok2:
                ok = True
            else:
                return dict(ok=False, error=f'{label}：{why2 or why}')
        if not ok:
            return dict(ok=False, error=f'{label}：{why}')

    if proj:
        Path(proj).mkdir(parents=True, exist_ok=True)
        sess.proj = str(Path(proj).resolve())
    sess.src = dict(dicom_src=dicom, data_src=data, mask_src=mask, case=case)
    sess.io = layout.targets(sess.proj, case, dicom_src=dicom,
                             data_src=data, mask_src=mask) if (sess.proj and case) else {}
    if not sess.io and sess.proj:
        try:
            sess.io = layout.resolve_io(sess.proj, sess.proj, case)
        except Exception:                                # noqa: BLE001
            pass
    # 让界面上的「输入/输出」框跟着变，否则下一轮会被界面旧值覆盖回去
    if callable(getattr(sess, 'on_project', None)):
        try:
            sess.on_project(sess.proj, dict(sess.src))
        except Exception:                                # noqa: BLE001
            pass
    return dict(ok=True, proj=sess.proj, **sess.src,
                note='工作区已设置，接下来可以直接跑')


def _tool_read_text_file(sess, args):
    """读文本文件（日志/报错/配置）。二进制直接拒。"""
    from pathlib import Path
    from . import grants
    g = _g(sess)
    ok, rp, why = grants.check_read(args.get('path') or '', g['level'], sess.proj)
    if not ok:
        return dict(ok=False, error=why)
    p = Path(rp)
    if p.is_dir():
        return dict(ok=False, error='这是目录不是文件')
    maxkb = max(1, min(int(args.get('max_kb') or 64), 512))
    if p.stat().st_size > maxkb * 1024 * 1024:
        return dict(ok=False, error=f'文件太大（>{maxkb}MB），不读')
    try:
        raw = p.read_bytes()[:maxkb * 1024]
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'读失败：{e}')
    if b'\x00' in raw[:2048]:
        return dict(ok=False, error='这是二进制文件（含空字节），不按文本读')
    txt = raw.decode('utf-8', 'replace')
    if len(txt) > 12000:
        txt = txt[:6000] + '\n…（中间省略）…\n' + txt[-4000:]
    return dict(ok=True, path=rp, size=p.stat().st_size, text=txt)


def _tool_find_masks(sess, args):
    """在目录里找已画好的掩模 / ROI（.mat）。"""
    from pathlib import Path
    from . import grants
    from app.modules import scan_helper as S
    g = _g(sess)
    ok, rp, why = grants.check_read(args.get('path') or '', g['level'], sess.proj)
    if not ok:
        return dict(ok=False, error=why)
    organ = str(args.get('organ') or '').strip()
    try:
        rows = S.scan_masks(str(rp), organ or 'liver')
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'扫描掩模失败：{type(e).__name__}: {e}')
    slim = []
    for r in (rows or [])[:80]:
        slim.append(dict(case=r.get('case'), file=r.get('file') or r.get('path'),
                         organ=r.get('organ'), role=r.get('role'),
                         shape=r.get('shape')))
    return dict(ok=True, root=rp, found=len(slim), masks=slim,
                note='' if slim else '这个目录下没扫到可识别的掩模变量'
                                     '（变量名通常含 mask / roi）')


def _tool_import_masks(sess, args):
    """把外部已画好的掩模搬进 PROJ 的 00_掩模（复用 ⓪ 的掩模模式）。"""
    from . import grants, runner
    g = _g(sess)
    src = str(args.get('src') or '').strip()
    ok, rp, why = grants.check_read(src, g['level'], sess.proj)
    if not ok:
        return dict(ok=False, error=why)
    if not sess.proj:
        return dict(ok=False, error='还没设 PROJ，先用 set_project 设一个')
    cases = [str(c) for c in (args.get('cases') or []) if str(c).strip()]
    try:
        res = runner._run_mask_stage(sess.proj, rp, sess.io, sess.emit,
                                     sess.cancel, case='', batch=True)
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'导入掩模失败：{type(e).__name__}: {e}')
    # 导完立刻复核哪些病例真有了
    from detect import chain as _chain
    from . import plan
    have, lack = [], []
    for c in (plan.cases_of(sess.proj) or cases):
        try:
            (have if _chain.mask_file(sess.proj, c) else lack).append(c)
        except Exception:                                # noqa: BLE001
            lack.append(c)
    return dict(ok=True, imported=str(res)[:200] if not isinstance(res, dict) else
                str(res.get('summary') or res.get('out_dir') or 'ok'),
                have=len(have), missing=lack[:20],
                note='' if not lack else f'{len(lack)} 例还没掩模，需要人补画')


def _python_forbidden(code: str) -> str:
    """run_python 的硬拦截：不许拿它执行模块，也不许拿它翻源码。

    拦截的不是「危险」，而是「走错路」——模块必须走 run_module，
    这样产物才落在标准目录、才有产物校验、才进报告。
    """
    import re as _re
    s = str(code or '')
    rules = [
        (r'from\s+modules\s+import|import\s+modules\b|from\s+app\.modules',
         '不要在 run_python 里 import GUI 的模块。要跑模块请用 run_module（传 mid + params）。'),
        (r'\.run\s*\(|PrfAnalysisModule|PrfExtractModule|PostModule|LDModule|'
         r'DisplayModule|CompareModule|ROIModule|ReconModule|ImportModule',
         '不要在 run_python 里直接调模块的 run()。要跑模块请用 run_module（传 mid + params）。'),
        (r'glob\.glob\s*\([^)]*\.py|rglob\s*\([^)]*\.py|listdir.*\.py',
         '不要用 run_python 在源码目录里搜 .py 文件。想知道模块行为请用 get_module_schema。'),
        (r'importlib|__import__\s*\(|inspect\.getsource|open\s*\([^)]*\.py',
         '不要用 run_python 探查模块源码。参数用 get_module_schema，状态用 get_status。'),
        # ★ 不许预建 PROJ 骨架：目录只能由模块在自己真正产出时创建
    ]
    for rx, msg in rules:
        if _re.search(rx, s):
            return msg + '（run_python 只用于你明确要求的临时统计/核对。）'
    # ★ 建目录拦截：代码里出现 mkdir 且同时出现 PROJ 子目录名（或 proj 变量）就拦。
    #   目录只能由模块在真正产出时创建，不许先搭一套空骨架。
    if _re.search(r'\b(mkdir|makedirs)\s*\(', s):
        if _re.search(r'00_掩模|00_roi|01_重建|01_recon|02_展示|02_display|03_CEST|'
                      r'04_逐体素|04_ld|05_PRF|06_PRF|10_对比|10_compare|日志', s):
            return ('不许用 run_python 预先创建 PROJ 子目录。目录由模块在真正产出时'
                    '自己创建：⓪ 导入了数据才有 01_重建，提取到掩模才有 00_掩模，'
                    '没跑过的阶段不该有目录。要建项目结构请用 run_module(m_import)。')
        if _re.search(r'proj|PROJ|sess\.proj|输出结果', s):
            return ('不许用 run_python 在 PROJ 下建目录。要建项目结构请用 '
                    'run_module(m_import)，它会按实际提取到的内容决定建哪个目录。')
    return ''


def _tool_run_python(sess, args):
    """跑一段 Python（同源解释器 runtime\\python.exe，cwd=PROJ）。"""
    import subprocess
    from . import grants, paths
    g = _g(sess)
    ok, why = grants.check_python(g)
    if not ok:
        return dict(ok=False, rejected=True, error=why)
    code = str(args.get('code') or '')
    if not code.strip():
        return dict(ok=False, error='code 是空的')
    if len(code) > 20000:
        return dict(ok=False, error='代码太长（>20000 字符），拆成几步')
    # ★ 硬拦截：不许用 run_python 替代模块执行，也不许拿它探源码
    bad = _python_forbidden(code)
    if bad:
        return dict(ok=False, rejected=True, error=bad)
    # 跑代码前问用户（默认开）
    if g.get('confirm_python') and sess.confirm_python:
        if not sess.confirm_python(code):
            return dict(ok=False, rejected=True, need_human=True,
                        error='用户拒绝了这次代码执行。可以用别的方式做，或先问清楚。')
    timeout = max(5, min(int(args.get('timeout') or 120), 1800))
    exe = paths.python_exe()
    # ★ Windows 上不带 creationflags 会闪一个黑框（控制台窗口），
    #   CREATE_NO_WINDOW 让子进程不建控制台，界面才不会被闪屏干扰。
    flags = 0
    if os.name == 'nt':
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    try:
        pr = subprocess.run([exe, '-c', code], cwd=sess.proj or None,
                            capture_output=True, text=True, timeout=timeout,
                            encoding='utf-8', errors='replace',
                            creationflags=flags,
                            stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return dict(ok=False, error=f'执行超过 {timeout}s 被中止')
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, error=f'{type(e).__name__}: {e}')

    def _cut(s):
        s = s or ''
        return s if len(s) <= 3000 else s[:2000] + '\n…（截断）…\n' + s[-800:]

    return dict(ok=pr.returncode == 0, returncode=pr.returncode,
                stdout=_cut(pr.stdout), stderr=_cut(pr.stderr),
                interpreter=exe, cwd=sess.proj)


TOOLS = [
    dict(type='function', function=dict(
        name='inspect_project',
        description='看项目真实结构：目录树、病例清单、每例有没有掩模、'
                    '③④② 产物、⑤ PRF 的三种模式产物、⑥ 三种模式的图出没出。'
                    '任何「现在能不能做组间」「有没有输入」这类问题，先调它，别猜。',
        parameters=dict(type='object', properties={}))),
    dict(type='function', function=dict(
        name='list_cases',
        description='列出当前 PROJ 里已识别到的病例名。不知道有哪些病例时先调它。',
        parameters=dict(type='object', properties={}))),
    dict(type='function', function=dict(
        name='get_status',
        description='查病例的真实状态：每个阶段是 done/todo/blocked、卡在哪、需要人做什么。'
                    '任何「能不能跑」「跑到哪了」「缺什么」先调它，别猜。',
        parameters=dict(type='object', properties=dict(
            case=dict(type='string', description='病例名；留空=全部病例'),
            stages=dict(type='array', items=dict(type='string'),
                        description='只查这几个阶段，留空=整链'))))),
    dict(type='function', function=dict(
        name='get_module_schema',
        description='读一个模块的完整参数表（key/类型/默认值/合法取值）。'
                    '要改任何参数（如 ⑥ 的组内/组间、出图样式、频偏范围）之前必须先调它，'
                    '不许凭印象填参数名。',
        parameters=dict(type='object', properties=dict(
            mid=dict(type='string', description='模块 id')), required=['mid']))),
    dict(type='function', function=dict(
        name='run_module',
        description='真正执行一个模块（只这一个，不跑上游）。参数走 params。',
        parameters=dict(type='object', properties=dict(
            mid=dict(type='string'),
            case=dict(type='string', description='病例名'),
            case_a=dict(type='string', description='⑩ 图像对比的病例 A'),
            case_b=dict(type='string', description='⑩ 图像对比的病例 B'),
            params=dict(type='object',
                        description='模块参数，key 见 get_module_schema；'
                                    '例如 ⑥ 组间分析：{"mode":"组间"}；'
                                    '⑩ 对比：{"case_a":"GS","case_b":"XXX","mode":"ST","w":3.5}')),
            required=['mid']))),
    dict(type='function', function=dict(
        name='run_chain',
        description='跑整条链（多病例 × 多阶段）。只用于 ⓪①⑧③④② 这条数据链。',
        parameters=dict(type='object', properties=dict(
            stages=dict(type='array', items=dict(type='string')),
            cases=dict(type='array', items=dict(type='string'),
                       description='留空=全部病例'),
            force=dict(type='boolean', description='强制重跑已完成的阶段')),
            required=['stages']))),
    dict(type='function', function=dict(
        name='export_ortho',
        description='出三正交面图：横断 / 冠状 / 矢状三联图 + 三张单面图，'
                    '落盘到 02_展示\\<病例>\\<模式> <ppm>ppm_ortho\\。'
                    '用户说「三视图」「三正交面」「正交切面」「三个面都看一下」时用它。',
        parameters=dict(type='object', properties=dict(
            case=dict(type='string', description='病例名'),
            mode=dict(type='string', description='图类型 raw / ST / APTW / LD，默认 LD'),
            w=dict(type='number', description='目标频偏 ppm，默认 3.5'),
            ld_mode=dict(type='string', description='LD 取正/负：POS / NEG，默认 POS'),
            use_mask=dict(type='boolean', description='用掩模压背景，默认 true'),
            x=dict(type='integer', description='矢状面切在第几列（1 基），留空=正中'),
            y=dict(type='integer', description='冠状面切在第几行（1 基），留空=正中'),
            z=dict(type='integer', description='横断面切在第几层（1 基），留空=正中'),
            caxis=dict(type='array', items=dict(type='number'),
                       description='色标 [下限, 上限]，留空=自动取 5~95%'),
            dpi=dict(type='integer', description='保存分辨率，默认 200')),
            required=['case']))),
    dict(type='function', function=dict(
        name='ask_human',
        description='信息不足或需要人决策时，问用户一句话并停下。'
                    '只在真的缺关键信息时用，不要为了确认而确认。',
        parameters=dict(type='object', properties=dict(
            question=dict(type='string'),
            options=dict(type='array', items=dict(type='string'),
                         description='可选项，用户可以点')),
            required=['question']))),
    # -------- 访问电脑（受权限档限制） --------
    dict(type='function', function=dict(
        name='list_dir',
        description='列一个目录里有什么（目录名、文件、大小）。'
                    '用户说「我数据放在 XX 文件夹」但没说全路径时，先用它看。',
        parameters=dict(type='object', properties=dict(
            path=dict(type='string', description='留空=当前 PROJ'),
            depth=dict(type='integer', description='往下展开几层，默认 1，最大 3'),
            limit=dict(type='integer', description='每层最多几项'))))),
    dict(type='function', function=dict(
        name='scan_path',
        description='认一个路径到底是什么（原始 DICOM / 病例目录 / PROJ / 掩模 / '
                    '后处理结果 / 训练集），返回识别结果和病例清单。'
                    '不确定用户给的路径能用在哪一步时先调它。',
        parameters=dict(type='object', properties=dict(
            path=dict(type='string')), required=['path']))),
    dict(type='function', function=dict(
        name='find_data',
        description='在一个目录下递归找原始数据（DICOM 目录、S0+Z 谱目录），'
                    '返回候选清单。用户说「某处有原始数据」时用它定位。',
        parameters=dict(type='object', properties=dict(
            path=dict(type='string'),
            max_depth=dict(type='integer', description='往下找几层，默认 3'),
            kind=dict(type='string', description='只找某种类型，可选'))))),
    dict(type='function', function=dict(
        name='set_project',
        description='设置工作区：PROJ（输出总目录）、原始 DICOM、数据根、掩模源、当前病例。'
                    '等同于用户在界面左边填那一排框。设好之后才能跑。',
        parameters=dict(type='object', properties=dict(
            proj=dict(type='string', description='输出总目录'),
            dicom_src=dict(type='string', description='① 的输入：原始 DICOM 目录'),
            data_src=dict(type='string', description='⓪ 的输入：已有 S0/Z 谱的目录'),
            mask_src=dict(type='string', description='已画好的掩模/ROI 所在目录'),
            case=dict(type='string', description='当前病例'))))),
    dict(type='function', function=dict(
        name='find_masks',
        description='在一个目录里找用户已画好的掩模 / ROI（.mat），'
                    '返回病例名、文件、部位。用户说「某文件夹有画好的掩模」时用它。',
        parameters=dict(type='object', properties=dict(
            path=dict(type='string'),
            organ=dict(type='string', description='部位名，可留空自动认'))))),
    dict(type='function', function=dict(
        name='import_masks',
        description='把外部已画好的掩模搬进 PROJ 的 00_掩模 目录，按标准格式摆好。'
                    '这是「用户提供掩模」的唯一正确做法——你仍然不能自己生成掩模。',
        parameters=dict(type='object', properties=dict(
            src=dict(type='string', description='掩模所在目录'),
            cases=dict(type='array', items=dict(type='string'),
                       description='只导这几例，留空=全部'))))),
    dict(type='function', function=dict(
        name='read_text_file',
        description='读一个文本文件（日志、报错、配置）。二进制会被拒绝。',
        parameters=dict(type='object', properties=dict(
            path=dict(type='string'),
            max_kb=dict(type='integer', description='最多读多少 KB，默认 64'))))),
    dict(type='function', function=dict(
        name='run_python',
        description='跑一段 Python 做临时统计/核对（读 .mat 数个数、核对文件清单这种）。'
                    '★ 不能用来跑模块（模块请用 run_module）、'
                    '不能用来读源码或搜索 .py（模块参数请用 get_module_schema）、'
                    '不能用来创建 PROJ 子目录（目录由模块自己按需创建）。',
        parameters=dict(type='object', properties=dict(
            code=dict(type='string', description='要执行的 Python 代码'),
            timeout=dict(type='integer', description='超时秒数，默认 120')),
            required=['code']))),
]


SYSTEM = """你是 CEST Studio 的操作员，代替用户**点按钮**。

你的唯一职责：把用户的话翻译成「跑哪个模块、用什么参数」，然后调 run_module / run_chain。
每一阶段都有真实执行代码在跑，你只负责决定「做什么、用什么参数」。

★ 你不是分析师，不做数据质量评估。以下这些**一律不要做**：
  - 不要评价数据好不好、样本量够不够、偏置间隔规不规则、统计上可不可信
  - 不要读源码去验证模块内部实现（不许 glob 找 .py、不许 import 模块看代码）
  - 不要自己写脚本复现模块逻辑（run_module 能做的事，绝不用 run_python 重做）
  - 不要给「我建议你选 1/2/3」这类多方案让用户挑；用户让你做什么就做什么
  - 不要主动扩展任务：用户说跑 ⑥，就只跑 ⑥，不要顺手分析数据、不要顺手清理目录

★ 但「看清数据是什么格式」是允许且必须做的（用户扔来一个文件夹时）：
  - 用 list_dir 看目录长什么样、有哪些文件、什么扩展名
  - 用 scan_path 认这属于哪种数据（原始 DICOM / S0+Z谱 / 病人目录 / PROJ / 掩模）
  - 必要时用 run_python **读数据文件本身**（loadmat 看变量名和 shape、读 DICOM 头），
    搞清「有哪些文件、每个文件里是什么、按什么规则组织」
  - 这是"看数据"，不是"评数据"：可以报格式、维度、命名规律、病例怎么分；
    不要报"数据质量好坏""样本够不够""能不能发文章"
  - 边界：读 .mat/.dcm/.json/.txt 数据文件 = 可以；读 .py 源码 = 禁止

★ 模块 id 只有下面这些，不要猜、不要自己造名字：  m_import      ⓪ 导入重建（原始 S0 + Z 谱 → 01_重建）
  m1_recon      ① DICOM 重建
  m_roi         ⑧ 去噪掩模 / 器官 ROI（只能人画）
  m3_post       ③ CEST 后处理
  m4_ld         ④ 逐体素 LD
  m2_display    ② 多层图谱展示
  m5_prf        ⑤ PRF 提取（训练集 → *Paths.mat）
  m6_prf_analysis  ⑥ PRF 分析（随机森林特征重要性）
  m7_compare    ⑩ 图像对比（两病例并排：多层 A/B/|A−B| + 单层 ROI 曲线）
数据链 = m_import/m1_recon/m_roi/m3_post/m4_ld/m2_display（run_chain 只收这些）
⑤ ⑥ ⑩ 是跨病例模块，只能 run_module 单独跑，不能进 run_chain。

★ 三视图（三正交面）走 export_ortho，不是模块：
  用户说「出三视图 / 三正交面 / 正交切面 / 三个面看一下」→ export_ortho(case, mode, w)。
  它和 GUI ② 的三视图**同一份取数**（同一份 voxel_LD.mat / 同一套掩模口径），
  产物落在 02_展示\<病例>\<MODE> <ppm>ppm_ortho\（合成图 + 横断/冠状/矢状 各一张）。
  LD 三视图要求该病例 ④ 已经跑出 voxel_LD.mat；没有就如实说「先跑 ④」，不要自己造。

铁律（违反会被工具直接拒绝，不要试）：
1. 去噪掩模、器官 ROI 必须用户人工画或由用户提供。
   你永远不跑 m_roi、不用 run_python 造掩模。
   用户说「某个文件夹有画好的掩模」→ find_masks 找出来 → import_masks 搬进 PROJ 00_掩模。
2. m3_post / m4_ld / m2_display 没有掩模就不能跑。
3. 频率表必须用户核对过才写入。
4. 参数取值不确定时，先调 get_module_schema 看合法取值，不要猜。
5. ★ 你绝对不能修改源代码。只能用 list_dir/scan_path/find_data/read_text_file 看，
   用 run_module/run_chain 调现成的模块。
   **不许写 .py 文件、不许改 cest-gui / cest-agent 里的任何程序文件**——
   这是硬限制，即使用户把权限开到全盘也不行。要改代码就告诉用户「这需要人来改」。
6. ★ 模块只能通过 run_module / run_chain 调用。
   **禁止用 run_python 里 import 模块、调 mod.run()、或在源码目录里 glob 找 .py**。
   run_python 只用于用户明确要求的临时统计/核对（读 .mat 算个数这种），
   不能替代模块执行，也不能用来探查模块内部实现。
7. ★ 不许用 run_python 预先创建任何 PROJ 子目录（00_掩模 / 01_重建 / 02_展示 /
   03_CEST后处理 / 04_逐体素LD / 05 / 06 / 10_对比 / 日志 都不许手动 mkdir）。
   目录只能由模块在**真正产出**时自己创建：⓪ 导入了 Z 谱才有 01_重建，
   提取到掩模才有 00_掩模，没跑过的阶段不该存在目录。
   用户给一堆原始数据让你「整理成 PROJ 格式」，正确做法是 set_project + run_module(m_import)，
   由 ⓪ 自己建目录，不是你先 mkdir 一套空文件夹再往里放。

关于找数据（用户给模糊指令时）：
- 「我在 D 盘 XX 有原始数据」→ list_dir 看看 → find_data 定位 → scan_path 确认是什么
  → set_project 把路径设进去 → 再跑。
- 路径不在授权范围会直接报错，这时如实告诉用户「这个路径需要你在设置里调权限」，
  不要反复试别的路径。
- 用户说「有画好的掩模/ROI」→ find_masks → import_masks。导完复核哪些还缺。

工作方式：
- 用户说要做的事 → 直接调 run_module / run_chain 执行，不要先长篇分析。
- 用户说「组间」就是 m6 的 params.mode="组间"；说「均值±SD」就是 params.style="均值±SD"。
  这类字眼是参数，不是形容词，必须真的传下去。
- 「组内 / 单个对比 / 组间」是 ⑥ 的 mode；「组内 / 组间」也是 ⑤ 的 task。
  用户说「分析」基本都是指 ⑥。要组间分析就 run_module(m6, params 里 mode=组间)。
- 指令明确（阶段/病例/模式都清楚）→ 直接执行。参数名不确定就 get_module_schema 看一眼。
- 只有在关键信息真的缺（没说哪个病例、没说组内还是组间且无法从上下文推断）时，
  才用 ask_human 问一句话。
- 用户说「先给我个方案」时，方案只写：跑哪个模块、传哪些参数、输出落在哪。
  不要写数据质量评价、不要写统计上的可信度讨论。
- 同一个工具不要重复调用；已经拿到的信息不要反复查。
- 用户问「到哪一步了」这类状态问题，get_status 调一次就够，不要逐病例刷。
- 执行完用中文简短汇报：跑了什么、成功没有、产物在哪。3 行以内，不要展开分析。
- 不要编造任何数值。所有数字只能来自工具返回。

当前项目状态：
{state}

当前权限：{grants}
"""


class Session:
    """一次会话（GUI 里就是 ⑨ 页的一个对话）。"""

    def __init__(self, cfg_llm: dict, proj: str = '', io: dict | None = None,
                 emit=None, cancel=None, confirm_freq=None, cfg_all=None,
                 confirm_python=None):
        self.cfg = cfg_llm or {}
        self.cfg_all = cfg_all if cfg_all is not None else {'llm': cfg_llm or {}}
        self.proj = proj or ''
        self.io = io or {}
        self.src = dict(dicom_src='', data_src='', mask_src='', case='')
        self.emit = emit or (lambda e: None)
        self.cancel = cancel or (lambda: False)
        self.confirm_freq = confirm_freq
        self.confirm_python = confirm_python        # code -> bool（跑代码前问用户）
        self.messages: list = []          # 多轮历史（含工具往返）
        self.last_result = None
        # 模型用 set_project 改了目录后，通知界面把左侧输入/输出框同步过去。
        # ★ 不接这个回调的话，下一轮 bind() 会用界面里的旧值把 PROJ 覆盖回去，
        #   模型就会以为没设成功、反复重设，最后撞上工具轮数上限。
        self.on_project = None            # (proj, src_dict) -> None

    # ---------------------------------------------------------- 可用性
    @property
    def ready(self) -> bool:
        c = self.cfg
        return bool(c.get('api_key') and c.get('base_url') and c.get('model'))

    def bind(self, proj='', io=None):
        """切换项目/输入后同步（不清历史）。"""
        if proj:
            self.proj = proj
        if io is not None:
            self.io = io or {}

    # ---------------------------------------------------------- 状态摘要
    def _state_text(self) -> str:
        """每轮都把**真实项目地图**注入系统提示。

        这样模型开口前就已经知道：目录长什么样、哪些病例有掩模、
        ⑤ 的组间产物在不在、⑥ 的组间图出没出。不用猜、不用反复查。
        """
        if not self.proj:
            return '（还没选输出总目录 PROJ，先让用户选目录）'
        try:
            m = _proj_map(self.proj, self.io, {})
        except Exception as e:                           # noqa: BLE001
            return f'（读项目结构失败：{e}）'
        if m.get('error'):
            return m['error']

        L = [f"PROJ = {m['proj']}",
             '顶层目录：' + '、'.join(m.get('top_dirs') or []),
             f"病例 {m.get('n_cases', 0)} 例"
             + ('：' + '、'.join(m.get('cases') or [])[:400] if m.get('cases') else '')]
        mk = m.get('masks') or {}
        L.append(f"⑧ 掩模：有 {mk.get('have', 0)}/{mk.get('total', 0)} 例"
                 + (f"，缺：{'、'.join(mk.get('missing') or [])}" if mk.get('missing') else ''))
        for k in ('③ 后处理', '④ 逐体素LD', '② 展示'):
            v = m.get(k) or {}
            L.append(f"{k}：{'有产物，' + str(v.get('n', 0)) + ' 例' if v.get('exists') else '无产物'}")
        p5 = m.get('⑤ PRF提取') or {}
        L.append('⑤ PRF提取产物：' + '；'.join(
            f"{k}={'有' if v.get('exists') else '缺'}" for k, v in p5.items()))
        p6 = m.get('⑥ PRF分析') or {}
        L.append('⑥ PRF分析产物：' + '；'.join(
            f"{k}={'有' if v.get('exists') else '缺'}" for k, v in p6.items()))
        smp = m.get('chain_status_sample') or {}
        if smp.get('stages'):
            L.append(f"数据链状态（样本 {smp.get('case')}）："
                     + '、'.join(f'{k}={v}' for k, v in smp['stages'].items()))
        return '\n'.join(L)

    def _grants_text(self) -> str:
        from . import grants
        g = grants.cfg_of(self.cfg_all)
        L = [f"路径权限={grants.LEVEL_LABEL.get(g['level'], g['level'])}",
             f"允许跑Python={'是' if g.get('allow_python') else '否'}"]
        if g.get('allow_python'):
            L.append(f"跑前需确认={'是' if g.get('confirm_python') else '否'}")
        if self.proj:
            L.append(f'PROJ={self.proj}')
        if self.src.get('dicom_src'):
            L.append(f"原始DICOM={self.src['dicom_src']}")
        if self.src.get('mask_src'):
            L.append(f"掩模源={self.src['mask_src']}")
        return '；'.join(L)

    # ---------------------------------------------------------- 调模型
    def _post(self, messages, tools=None):
        import urllib.request
        c = self.cfg
        body = dict(model=c.get('model'), messages=messages, temperature=0.2)
        if tools:
            body['tools'] = tools
            body['tool_choice'] = 'auto'
        req = urllib.request.Request(
            str(c.get('base_url')).rstrip('/') + '/chat/completions',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + str(c.get('api_key'))})
        # 思考型模型慢，给足时间（配置里的 20s 不够）
        timeout = max(float(c.get('timeout') or 20), 120)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        return (data.get('choices') or [{}])[0].get('message') or {}

    # ---------------------------------------------------------- 执行工具
    def _exec(self, name, args):
        emit, cancel = self.emit, self.cancel
        proj = self.proj
        if name == 'inspect_project':
            return _proj_map(proj, self.io, args)
        if name == 'list_cases':
            return _tool_list_cases(proj, self.io, args)
        if name == 'get_status':
            return _tool_get_status(proj, self.io, args)
        if name == 'get_module_schema':
            return _tool_schema(args.get('mid'), proj, self.io, args)
        if name == 'run_module':
            return _tool_run_module(proj, '', self.io, args, emit, cancel,
                                    self.confirm_freq)
        if name == 'run_chain':
            return _tool_run_chain(proj, self.io, args, emit, cancel,
                                   self.confirm_freq)
        if name == 'ask_human':
            return _tool_ask_human(args)
        if name == 'export_ortho':
            return _tool_export_ortho(self, args)
        # ---- 访问电脑（受权限档限制）----
        if name == 'list_dir':
            return _tool_list_dir(self, args)
        if name == 'scan_path':
            return _tool_scan_path(self, args)
        if name == 'find_data':
            return _tool_find_data(self, args)
        if name == 'set_project':
            return _tool_set_project(self, args)
        if name == 'find_masks':
            return _tool_find_masks(self, args)
        if name == 'import_masks':
            return _tool_import_masks(self, args)
        if name == 'read_text_file':
            return _tool_read_text_file(self, args)
        if name == 'run_python':
            return _tool_run_python(self, args)
        return dict(error=f'没有工具 {name}')

    # ---------------------------------------------------------- 主循环
    def send(self, text: str, on_say=None) -> str:
        """用户说一句 → 跑完整个工具循环 → 返回最终人话。

        没配 key / 断网 / 接口错 → 抛异常，由 GUI 退回本地规则。
        """
        if not self.ready:
            raise RuntimeError('大模型未启用（没填 key 或模型名）')
        say = on_say or (lambda s: None)

        if not self.messages:
            self.messages.append(dict(
                role='system', content=SYSTEM.format(
                    state=self._state_text(), grants=self._grants_text())))
        else:
            # 每轮刷新项目状态，模型看到的永远是最新的
            self.messages[0] = dict(
                role='system', content=SYSTEM.format(
                    state=self._state_text(), grants=self._grants_text()))

        self.messages.append(dict(role='user', content=str(text)))

        for rnd in range(MAX_ROUNDS):
            if self.cancel():
                return '已取消。'
            say('thinking' if rnd == 0 else 'tool')
            msg = self._post(self.messages, TOOLS)
            calls = msg.get('tool_calls') or []
            content = str(msg.get('content') or '')

            # 不保留 reasoning_content：下一轮回传会被部分服务商拒绝
            self.messages.append(dict(role='assistant', content=content,
                                      **({'tool_calls': calls} if calls else {})))

            if not calls:
                return content.strip() or '（模型没有输出内容）'

            for tc in calls:
                fn = (tc.get('function') or {})
                name = fn.get('name') or ''
                raw = fn.get('arguments') or '{}'
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:                        # noqa: BLE001
                    args = {}
                say(f'{name}: {json.dumps(args, ensure_ascii=False)[:80]}')
                t0 = time.time()
                try:
                    out = self._exec(name, args)
                except Exception as e:                   # noqa: BLE001
                    out = dict(error=f'{type(e).__name__}: {e}')
                out_txt = json.dumps(out, ensure_ascii=False, default=str)
                if len(out_txt) > 6000:
                    out_txt = out_txt[:6000] + '…（截断）'
                self.messages.append(dict(role='tool', tool_call_id=tc.get('id'),
                                          content=out_txt))
                if name.startswith('run_'):
                    self.last_result = out
                say(f'{name} 完成 {time.time() - t0:.0f}s')
                if self.cancel():
                    return '已取消。'

        return (f'这一轮我调了 {MAX_ROUNDS} 次工具还没收尾（通常是在反复试路径）。'
                '**是我这边的循环上限到了，不是你指令的问题。**\n\n'
                '你直接说一句「继续」，我会接在刚才的进度上往下做。')
