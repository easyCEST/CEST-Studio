# -*- coding: utf-8 -*-
"""cest_agent.freq —— 频率表检索阶梯（先检索校验，全找不到才让人手写）。

★ 不自动「编造」频率：只从**已有事实**里找——上次确认的存档、已有结果的 mat、
  病例目录里的协议/文本文件。全部找不到就返回 None，交给 GUI 的频率表对话框。

阶梯（命中即停）：
    1. PROJ\\01_重建\\_频率\\<病例>.json     上次确认过
    2. 已有结果里的 freq.mat / w_offset.mat  继承
    3. 病例目录 / 数据根（深度≤2）下的 .prot UIH 协议
    4. 同范围的 .txt / .csv / .json          文本解析
    5. 全败 → None（人工手写）
"""
from __future__ import annotations

from pathlib import Path

from detect import freq_doc as FD


def _iter_files(root: Path, suffixes, depth: int = 2, limit: int = 400):
    """在 root 下（深度≤depth）列指定后缀的文件；跳过隐藏与输出目录。"""
    root = Path(root)
    if not root.is_dir():
        return
    skip = {'_频率', '_freq', '日志', 'logs', '__pycache__', '_state'}
    stack, n = [(root, 0)], 0
    while stack and n < limit:
        d, lvl = stack.pop(0)
        try:
            entries = sorted(d.iterdir(), key=lambda x: x.name)
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(('.', '_')) or e.name in skip:
                continue
            if e.is_dir():
                if lvl < depth:
                    stack.append((e, lvl + 1))
                continue
            if e.suffix.lower() in suffixes:
                yield e
                n += 1
                if n >= limit:
                    return


def _try_prot(path: Path) -> list:
    try:
        return FD.parse_prot(path) or []
    except Exception:                                    # noqa: BLE001
        return []


def _try_text(path: Path) -> list:
    try:
        text = FD.read_text(path)
    except Exception:                                    # noqa: BLE001
        return []
    vals = []
    for ln in (text or '').splitlines():
        if ln.strip():
            vals += FD.parse_spec(ln)
    return vals


def resolve(proj, case: str, case_dir: str = '', data_root: str = '',
            need: int = 0, exact: bool = False, max_scan: int = 40) -> dict:
    """返回 dict(source_kind, values, source, note, ok, summarize, tried)。

    ok=True 表示「找到一个通过个数校验的表」，此时 values 可直接写存档。
    """
    tried = []

    def _done(cand, kind, note=''):
        s = FD.summarize(cand.values, need, exact=exact) if need else \
            FD.summarize(cand.values, 0, exact=False)
        return dict(source_kind=kind, values=[float(x) for x in cand.values],
                    source=str(cand.source), note=note or cand.note,
                    n=len(cand.values), n_unique=s.get('n_unique'),
                    ok=bool(s.get('ok')), summarize=s, tried=tried)

    # ---- 1) 上次确认的存档 ----
    tried.append('上次确认的存档')
    c = FD.load_saved(proj, case) if (proj and case) else None
    if c:
        return _done(c, 'saved', '本病例上次确认过的表')

    # ---- 2) 继承已有结果的 mat ----
    tried.append('已有结果 freq.mat / w_offset.mat')
    if case_dir:
        c = FD.inherit_freq(case_dir, proj, case)
        if c:
            return _done(c, 'inherit', '继承已有结果')

    # ---- 3) UIH 协议 .prot ----
    tried.append('病例目录里的 .prot')
    for root in [p for p in (case_dir, data_root) if p]:
        for f in _iter_files(Path(root), {'.prot'}, depth=2, limit=max_scan):
            vals = _try_prot(f)
            if len(vals) >= 3:
                return _done(FD.Candidate(values=vals, source=str(f), raw=f.name,
                                          confidence='high', note='UIH 协议文件'),
                             'prot', f'从 {f.name} 解析')

    # ---- 4) 文本 / csv / json ----
    tried.append('病例目录里的 .txt / .csv / .json')
    for root in [p for p in (case_dir, data_root) if p]:
        for f in _iter_files(Path(root), {'.txt', '.csv', '.json'}, depth=2,
                             limit=max_scan):
            vals = _try_text(f)
            if len(vals) >= 3:
                return _done(FD.Candidate(values=vals, source=str(f), raw=f.name,
                                          confidence='mid', note='从文本文件解析'),
                             'text', f'从 {f.name} 解析')

    # ---- 5) 全败 ----
    tried.append('全部未命中 → 需人工手写')
    return dict(source_kind='none', values=[], source='', note='',
                n=0, n_unique=0, ok=False,
                summarize=dict(ok=False, msgs=['没有检索到可用的频率表'], n_unique=0),
                tried=tried)


def commit(proj, case: str, values, source: str = '') -> str:
    """把人工核对过的频率写进存档（GUI 与 agent 共用同一份存档）。"""
    p = FD.save(proj, case, values, source=source or 'agent 检索（已人工核对）',
                note='agent 检索后由用户核对确认')
    return str(p)
