# -*- coding: utf-8 -*-
"""cest_agent.runner —— 逐步执行 + 事件流 + 产物校验 + 断点续跑。

★ 直接 import GUI 的 Module 并调用**同一个** run(params, ctx)：
  等于跑同一个程序，但不需要窗口，异常/进度/日志全拿得到。

事件（on_event 回调拿到 dict，GUI 里直接渲染）：
    start       开始一次运行
    stage       进入某阶段
    case_start  某病例开始
    log         一行日志
    progress    {cur,total,text}
    verify      产物校验结果
    case_done   某病例完成
    stage_done  某阶段完成
    need_human  需要人处理（掩模/频率表）
    fail        失败（带诊断）
    done        全部结束
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path

import numpy as np

from detect import chain
from detect import rules as R
from detect.scanner import detect
from jobs.ctx import Cancelled, JobContext

from . import doctor, freq as freqmod, verify
from . import plan
from .plan import STAGE_LABEL, roi_stage

# 吃 ①⑧ 数据链的阶段（需要前置门禁）；⑤⑥⑦ 跨病例/自检，不吃
_CHAIN_STAGES = ('m1_recon', 'm_roi', 'm3_post', 'm4_ld', 'm2_display')


def _module(mid):
    """按 id 取 GUI 的模块实例（registry 里就是单例）。"""
    from app.modules import registry
    return registry.get(mid)


def _det(proj):
    """识别一个路径（PROJ / DICOM / 病例目录都行）。"""
    try:
        return detect(proj) if proj else None
    except Exception:                                    # noqa: BLE001
        return None


def _mk_ctx(proj, det, emit, cancel_flag):
    """构造 JobContext，把 log/progress 接到事件流；取消走 cancel_flag。"""
    ctx = JobContext(proj=proj, det=det)

    def _sink(msg):
        emit(dict(type='log', text=str(msg)))

    def _prog(cur, total, text):
        emit(dict(type='progress', cur=int(cur), total=int(total), text=str(text)))

    ctx.bind(sink=_sink, progress=_prog)

    class _Ctx(JobContext):
        def check_cancel(self):
            if cancel_flag():
                raise Cancelled()

    ctx2 = _Ctx(proj=proj, det=det)
    ctx2.bind(sink=_sink, progress=_prog)
    return ctx2


def params_for(mid, proj, case, overrides=None, io=None) -> dict:
    """用模块自己的 suggest() 生成默认参数，再补病例 / 目录相关字段。

    io = layout.targets() 的结果；给了就按它显式指定输入输出，
    避免模块在自己猜 PROJ 时找错位置（③④ 的 src / mask_file 尤其重要）。
    """
    m = _module(mid)
    if m is None:
        raise RuntimeError(f'找不到模块 {mid}')

    # io 可能是 layout.resolve_io() 的整体结果，也可能只是它的 stages 字典
    _io = io or {}
    if 'stages' in _io:
        _io = _io.get('stages') or {}
    t = _io.get(mid) or {}

    # ★ 识别对象必须是**这个模块真正的输入**，不是 PROJ：
    #   ① 的输入是原始 DICOM 目录，用 PROJ 去识别会认成「项目目录」，
    #   于是 suggest 走 Philips 分支 → 拿错序列 → 重建失败。
    det_src = str(t.get('inp') or '').strip()
    det = _det(det_src) if det_src else _det(proj)

    try:
        p = dict(m.suggest(det, proj) or {})
    except Exception:                                    # noqa: BLE001
        p = {}
    # 统一补齐：模块 schema 里所有字段先落默认值，避免 run 里取不到 key
    keys = set()
    for f in getattr(m, 'schema', ()):
        keys.add(f.key)
        if f.key not in p:
            p[f.key] = f.default
    if 'case' in keys:
        p['case'] = case

    # io 可能是 layout.resolve_io() 的整体结果，也可能只是它的 stages 字典
    _io = io or {}
    if 'stages' in _io:
        _io = _io.get('stages') or {}
    t = _io.get(mid) or {}
    # ---- 输入 ----
    if 'src' in keys:
        src = t.get('inp') or ''
        if not src:
            rd = chain.recon_dir(proj, case)
            src = str(rd) if rd else (p.get('src') or '')
        p['src'] = src
    # ---- 输出（模块自己接受 out_root / out_dir 的，显式给到病例级目录）----
    for k in ('out_root', 'out_dir'):
        if k in keys and t.get('out'):
            p[k] = t['out']
    # ---- 掩模（③④② 都要）----
    if mid in ('m3_post', 'm4_ld', 'm2_display') and 'mask_file' in keys:
        mf = t.get('mask') or ''
        if not mf or not Path(mf).exists():
            mf2 = chain.mask_file(proj, case)
            mf = str(mf2) if mf2 else mf
        if mf:
            p['mask_file'] = mf
    if mid == 'm3_post':
        p['scope'] = '当前病例'
    if overrides:
        p.update({k: v for k, v in overrides.items() if v is not None})
    return p


def _num(v, d=None):
    try:
        if v is None or (isinstance(v, str) and not v.strip()):
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


def ensure_freq(proj, case, emit, need=0) -> dict:
    """跑 ① 之前的频率表检查：有存档就过；没有就检索，检索不到交人工。"""
    from detect import freq_doc as FD
    c = FD.load_saved(proj, case)
    if c:
        return dict(status='ok', source='存档', n=len(c.values), values=c.values,
                    note='本病例已有确认过的频率表')

    rd = chain.recon_dir(proj, case)
    src = Path(rd).parent.parent if rd else Path(proj)
    case_dir = str(Path(src) / case) if case else ''
    r = freqmod.resolve(proj, case, case_dir=case_dir, data_root=str(src), need=need)
    emit(dict(type='log', text=f'  频率表检索：命中方式 = {r["source_kind"]}'
                              f'（{r["n"]} 个频偏，校验 {"通过" if r["ok"] else "未通过"}）'))
    if r['ok'] and r['values']:
        return dict(status='found', source=r['source_kind'], n=r['n'],
                    values=r['values'], note=r['note'], need_confirm=True)
    return dict(status='need_human', source='none', n=0, values=[],
                note='检索不到可用频率表，需要手写',
                tried=r['tried'])


def run_chain(proj, cases, stages=('m1_recon', 'm_roi', 'm3_post', 'm4_ld'),
              overrides=None, on_event=None, force=False,
              cancel_flag=None, confirm_freq=None, io=None) -> dict:
    """跑一条链。

    confirm_freq(case, info) -> dict(values=..., ok=True/False)
        检索到频率表时回调一次，让**用户核对**；返回 None 表示拒绝。
    cancel_flag() -> bool  返回 True 就停。
    io = layout.targets() 的结果；给了就按它指定各阶段的输入/输出目录。
    """
    ev = on_event or (lambda e: None)
    cancel = cancel_flag or (lambda: False)
    t_all = time.time()
    run_id = time.strftime('%Y%m%d_%H%M%S')
    ev(dict(type='start', proj=proj, cases=list(cases), stages=list(stages),
            run_id=run_id, io=io or {}))

    results = []
    failures = []
    pending_human = []

    for ci, case in enumerate(cases, 1):
        if cancel():
            ev(dict(type='log', text='■ 已取消'))
            break
        ev(dict(type='case_start', case=case, index=ci, total=len(cases)))
        cres = dict(case=case, stages=[], status='ok')

        # ---- 前置门禁：只对吃数据链的阶段生效 ----
        # ⑤⑥⑦ 是跨病例/自检模块，不吃 ①⑧ 的数据链，不该被掩模拦住。
        gate_stages = [s for s in stages if s in _CHAIN_STAGES]
        if gate_stages:
            needs_roi = ('m3_post' in stages)
            gate = plan.check_case(proj, case, ('m_roi',))
            blocking = [p for p in (gate.get('needs_human') or [])
                        if p.get('kind') != 'roi_missing' or needs_roi]
            if blocking:
                for p in blocking:
                    pending_human.append(dict(case=case, stage='m_roi', **p))
                    ev(dict(type='need_human', case=case, stage='m_roi',
                            text=p['text'], goto=p.get('goto') or {},
                            fix_kind=p.get('fix_kind', 'human_gui')))
                # 不阻塞但有提醒的（缺 ROI 却只跑 ④）→ 只提示，继续跑
                for p in (gate.get('needs_human') or []):
                    if p not in blocking:
                        ev(dict(type='log',
                                text=f'  [提示] {p["text"]}（本次不跑 ③，可先不管）'))
                if blocking:
                    cres['status'] = 'blocked'
                    cres['stages'].append(dict(id='m_roi', status='blocked',
                                               problems=blocking))
                    ev(dict(type='log',
                            text=f'■ {case} 缺 ⑧ 的产物，本病例跳过（掩模/ROI 必须人工画）'))
                    ev(dict(type='case_done', case=case, status='blocked', result=cres))
                    results.append(cres)
                    continue

        for sid in stages:
            if cancel():
                break
            # ---- ⑧ 不能被 agent 跑：它只做检查，缺了就交人 ----
            if sid == 'm_roi':
                st = roi_stage(proj, case)
                cres['stages'].append(dict(id=sid, status=st['status'],
                                           problems=st['problems']))
                if st['status'] != 'done':
                    for p in st['problems']:
                        pending_human.append(dict(case=case, stage=sid, **p))
                        ev(dict(type='need_human', case=case, stage=sid,
                                text=p['text'], goto=p.get('goto') or {},
                                fix_kind=p.get('fix_kind', 'human_gui')))
                    cres['status'] = 'blocked'
                    ev(dict(type='log',
                            text=f'■ {STAGE_LABEL[sid]} 未完成，本病例后续跳过：{case}'))
                    break
                continue

            # ---- 频率表（只在 ① 之前查） ----
            if sid == 'm1_recon':
                info = ensure_freq(proj, case, ev)
                if info['status'] == 'found' and confirm_freq:
                    ok = confirm_freq(case, info)
                    if ok and ok.get('values'):
                        try:
                            pth = freqmod.commit(proj, case, ok['values'],
                                                 source=ok.get('source', ''))
                            ev(dict(type='log', text=f'  频率表已确认并写入：{pth}'))
                            info = dict(status='ok', source='confirmed',
                                        n=len(ok['values']), values=ok['values'])
                        except Exception as e:               # noqa: BLE001
                            ev(dict(type='log', text=f'  写频率表存档失败：{e}'))
                            info = dict(status='need_human', note=str(e))
                    else:
                        info = dict(status='need_human', note='用户未确认频率表')
                if info['status'] == 'need_human':
                    pending_human.append(dict(
                        case=case, stage=sid, fix_kind='human_gui',
                        text=f'频率表未确认：{info.get("note", "")}',
                        goto=dict(mid='m1_recon', case=case)))
                    ev(dict(type='need_human', case=case, stage=sid,
                            text=f'频率表未确认（{info.get("note", "")}）—— 需要你核对或手写',
                            goto=dict(mid='m1_recon', case=case)))
                    cres['status'] = 'blocked'
                    break

            # ---- 已完成就跳过（断点续跑） ----
            stmap = chain.stage_status(proj, case)
            if stmap.get(sid) and not force:
                v = verify.run(sid, proj, case)
                ev(dict(type='verify', case=case, stage=sid, result=v,
                        skipped=True))
                if v.get('ok'):
                    cres['stages'].append(dict(id=sid, status='done', skipped=True))
                    ev(dict(type='log', text=f'  {STAGE_LABEL[sid]} 已完成，跳过'))
                    continue
                ev(dict(type='log',
                        text=f'  {STAGE_LABEL[sid]} 标着完成但产物不全（{v.get("reason")}）→ 重跑'))

            # ---- 真跑 ----
            ev(dict(type='stage', case=case, stage=sid, label=STAGE_LABEL[sid]))
            m = _module(sid)
            if m is None:
                failures.append(dict(case=case, stage=sid, error=f'找不到模块 {sid}'))
                cres['stages'].append(dict(id=sid, status='fail'))
                cres['status'] = 'fail'
                break
            p = params_for(sid, proj, case, overrides, io=io)
            # ctx.det 必须是该模块自己的输入识别结果（① 靠它区分 UIH/Philips）
            ctx = _mk_ctx(proj, _det(p['src']) if p.get('src') else _det(proj),
                          ev, cancel)
            t0 = time.time()
            try:
                res = m.run(p, ctx)
                v = verify.run(sid, proj, case, res)
                ev(dict(type='verify', case=case, stage=sid, result=v))
                if v.get('ok'):
                    cres['stages'].append(dict(id=sid, status='done',
                                               sec=round(time.time() - t0, 1)))
                    ev(dict(type='log',
                            text=f'✔ {STAGE_LABEL[sid]} 完成（{time.time() - t0:.0f}s）'))
                else:
                    cres['stages'].append(dict(id=sid, status='partial', verify=v,
                                               sec=round(time.time() - t0, 1)))
                    cres['status'] = 'partial'
                    d = doctor.diagnose(v.get('reason', ''), sid)
                    failures.append(dict(case=case, stage=sid,
                                         error=v.get('reason', ''), verify=v))
                    ev(dict(type='fail', case=case, stage=sid,
                            text=v.get('reason', ''), diag=d, partial=True))
                    ev(dict(type='log',
                            text=f'⚠ {STAGE_LABEL[sid]} 跑完但产物不全：{v.get("reason")}'))
            except Cancelled:
                ev(dict(type='log', text='■ 已取消'))
                cres['stages'].append(dict(id=sid, status='cancelled'))
                cres['status'] = 'cancelled'
                break
            except Exception as e:                           # noqa: BLE001
                tb = traceback.format_exc()
                err = f'{type(e).__name__}: {e}'
                d = doctor.diagnose(err + '\n' + tb, sid)
                failures.append(dict(case=case, stage=sid, error=err, traceback=tb))
                cres['stages'].append(dict(id=sid, status='fail', error=err))
                cres['status'] = 'fail'
                ev(dict(type='fail', case=case, stage=sid, text=err, diag=d,
                        traceback=tb))
                ev(dict(type='log', text=f'× {STAGE_LABEL[sid]} 失败：{err}'))
                if d.get('fix_kind') == 'human_gui':
                    pending_human.append(dict(case=case, stage=sid,
                                              fix_kind='human_gui',
                                              text=d.get('action', ''),
                                              goto=dict(mid=d.get('goto_mid') or sid,
                                                        case=case)))
                break
        ev(dict(type='case_done', case=case, status=cres['status'], result=cres))
        results.append(cres)

    diags = doctor.explain_failures(failures)
    out = dict(type='done', run_id=run_id, proj=proj,
               sec=round(time.time() - t_all, 1),
               cases=results, failures=failures, diagnoses=diags,
               pending_human=pending_human,
               ok=not failures and not pending_human,
               summary=_summarize(results, failures, pending_human))
    ev(out)
    return out


def run_module(mid, proj, case, overrides=None, on_event=None,
               cancel_flag=None, io=None, do_verify=True) -> dict:
    """只跑**一个**模块。不吃整条链、不跑上游。

    该跑什么由模块自己决定（它的 run 会读 src / mask_file），
    这里只负责：构造参数 → 调 run → 校验产物 → 出错给诊断。
    """
    ev = on_event or (lambda e: None)
    cancel = cancel_flag or (lambda: False)
    t0 = time.time()
    run_id = time.strftime('%Y%m%d_%H%M%S')
    label = STAGE_LABEL.get(mid, mid)
    ev(dict(type='start', proj=proj, cases=[case], stages=[mid],
            run_id=run_id, single=True, io=io or {}))

    m = _module(mid)
    if m is None:
        d = dict(type='done', run_id=run_id, proj=proj, sec=0, single=True,
                 module=mid, cases=[], failures=[dict(case=case, stage=mid,
                 error=f'找不到模块 {mid}')], diagnoses=[], pending_human=[],
                 ok=False, summary=f'找不到模块 {mid}')
        ev(d); return d

    ev(dict(type='case_start', case=case, index=1, total=1))
    cres = dict(case=case, stages=[], status='ok')
    ev(dict(type='stage', case=case, stage=mid, label=label))

    try:
        p = params_for(mid, proj, case, overrides, io=io)
        ev(dict(type='log', text=f'　参数：' + '，'.join(
            f'{k}={v}' for k, v in p.items()
            if k in ('src', 'out_root', 'out_dir', 'mask_file', 'case', 'scope')
            and v)))
        ctx = _mk_ctx(proj, _det(p['src']) if p.get('src') else _det(proj),
                      ev, cancel)
        res = m.run(p, ctx)
        v = verify.run(mid, proj, case, res) if do_verify else dict(ok=True)
        ev(dict(type='verify', case=case, stage=mid, result=v))
        if v.get('ok'):
            cres['stages'].append(dict(id=mid, status='done',
                                       sec=round(time.time() - t0, 1)))
            ev(dict(type='log', text=f'✔ {label} 完成（{time.time() - t0:.0f}s）'))
        else:
            cres['status'] = 'partial'
            cres['stages'].append(dict(id=mid, status='partial', verify=v))
            dd = doctor.diagnose(v.get('reason', ''), mid)
            failures = [dict(case=case, stage=mid, error=v.get('reason', ''),
                             verify=v)]
            ev(dict(type='fail', case=case, stage=mid, text=v.get('reason', ''),
                    diag=dd, partial=True))
            return _finish_module(ev, run_id, proj, mid, case, cres, failures,
                                  t0, v.get('reason', ''))
    except Cancelled:
        cres['status'] = 'cancelled'
        cres['stages'].append(dict(id=mid, status='cancelled'))
        ev(dict(type='log', text='■ 已取消'))
        return _finish_module(ev, run_id, proj, mid, case, cres, [], t0, '已取消')
    except Exception as e:                               # noqa: BLE001
        tb = traceback.format_exc()
        err = f'{type(e).__name__}: {e}'
        dd = doctor.diagnose(err + '\n' + tb, mid)
        cres['status'] = 'fail'
        cres['stages'].append(dict(id=mid, status='fail', error=err))
        failures = [dict(case=case, stage=mid, error=err, traceback=tb)]
        ev(dict(type='fail', case=case, stage=mid, text=err, diag=dd,
                traceback=tb))
        ev(dict(type='log', text=f'× {label} 失败：{err}'))
        pending = []
        if dd.get('fix_kind') == 'human_gui':
            pending.append(dict(case=case, stage=mid, fix_kind='human_gui',
                                text=dd.get('action', ''),
                                goto=dict(mid=dd.get('goto_mid') or mid,
                                          case=case)))
            ev(dict(type='need_human', case=case, stage=mid,
                    text=dd.get('action', ''),
                    goto=dict(mid=dd.get('goto_mid') or mid, case=case),
                    fix_kind='human_gui'))
        return _finish_module(ev, run_id, proj, mid, case, cres, failures, t0,
                              err, pending)

    ev(dict(type='case_done', case=case, status='ok', result=cres))
    return _finish_module(ev, run_id, proj, mid, case, cres, [], t0,
                          f'{label} 已完成')


def _finish_module(ev, run_id, proj, mid, case, cres, failures, t0, summary,
                   pending=None) -> dict:
    pending = pending or []
    diags = doctor.explain_failures(failures)
    out = dict(type='done', run_id=run_id, proj=proj, single=True, module=mid,
               sec=round(time.time() - t0, 1), cases=[cres],
               failures=failures, diagnoses=diags, pending_human=pending,
               ok=not failures and not pending,
               summary=f'{case}：{summary}')
    ev(out)
    return out


def _summarize(results, failures, pending) -> str:
    n = len(results)
    ok = sum(1 for r in results if r['status'] == 'ok')
    part = sum(1 for r in results if r['status'] == 'partial')
    fail = sum(1 for r in results if r['status'] == 'fail')
    blk = sum(1 for r in results if r['status'] == 'blocked')
    bits = [f'共 {n} 个病例']
    if ok:
        bits.append(f'{ok} 个全成')
    if part:
        bits.append(f'{part} 个产物不全')
    if fail:
        bits.append(f'{fail} 个失败')
    if blk:
        bits.append(f'{blk} 个卡在前置')
    if pending:
        bits.append(f'{len(pending)} 项需要你手动处理')
    return '，'.join(bits) + '。'
