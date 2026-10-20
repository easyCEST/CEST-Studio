# -*- coding: utf-8 -*-
"""app/core_path.py —— 接入「核心层」（本包内的 03_Python_全链路\\函数库）。

★ 本项目自 2026-10-02 起**只在这一个目录里工作**：cest-gui\\。
  核心层就在本包内（<包根>\\03_Python_全链路\\函数库），不再去外面找、也不许有第二个真源。

查找顺序（第一个命中的就用）：
    1) <包根>\\03_Python_全链路\\函数库      ← 正常情况（cest-gui 自带）
    2) <壳>\\核心层\\函数库                  ← 万一挪了位置
    3) <壳>\\函数库
    4) <包根>\\函数库
    5) 环境变量 CEST_CORE_LIB
全都找不到 → 直接报错，并把找过的位置全列出来（**不会**去用别的目录，避免"偷偷用外面的"）。
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent      # …\cest-gui\CEST_Studio\app
_SHELL = _APP.parent                        # …\cest-gui\CEST_Studio
_ROOT = _SHELL.parent                       # …\cest-gui

_installed = False


def candidates() -> list:
    c = [
        _ROOT / '03_Python_全链路' / '函数库',
        _SHELL / '核心层' / '函数库',
        _SHELL / '函数库',
        _ROOT / '函数库',
    ]
    env = os.environ.get('CEST_CORE_LIB', '').strip()
    if env:
        c.append(Path(env))
    return c


def _is_core(d) -> bool:
    try:
        d = Path(d)
        return d.is_dir() and any(d.glob('cest_*.py'))
    except OSError:
        return False


def find_core() -> Path:
    for d in candidates():
        if _is_core(d):
            return Path(d)
    return candidates()[0]          # 找不到也返回首选，交给 install() 报错


CORE_LIB = find_core()


def install() -> Path:
    """把核心层挂进 sys.path（幂等）。"""
    global _installed
    if not CORE_LIB.exists():
        tried = '\n'.join(f'  · {d}' for d in candidates())
        raise FileNotFoundError(
            f'找不到核心层（函数库）目录 —— 请确认 03_Python_全链路 和 CEST_Studio '
            f'在同一个目录下（即整个 cest-gui 一起拷）。\n找过这些位置：\n{tried}')
    p = str(CORE_LIB)
    if p not in sys.path:
        sys.path.insert(0, p)
    _installed = True
    return CORE_LIB


def fingerprint() -> str:
    """核心层各文件的 md5（前 8 位）——写进日志，方便知道当时用的是哪版函数库。"""
    if not CORE_LIB.exists():
        return f'核心层缺失：{CORE_LIB}'
    items = []
    for f in sorted(CORE_LIB.glob('*.py')):
        try:
            items.append(f'{f.stem}:{hashlib.md5(f.read_bytes()).hexdigest()[:8]}')
        except OSError:
            pass
    return ' '.join(items)


def version() -> str:
    """读 <壳>\VERSION（更新包会写它）—— 拿不到就返回空串，绝不报错。"""
    for p in (_SHELL / 'VERSION', _ROOT / 'VERSION'):
        try:
            if p.is_file():
                v = p.read_text(encoding='utf-8-sig').strip()
                if v:
                    return v
        except OSError:
            pass
    return ''
