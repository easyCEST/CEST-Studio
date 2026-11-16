# -*- coding: utf-8 -*-
"""cest_agent.config —— 智能体自己的配置（不含任何病情数据）。

位置：cest-agent\\_state\\config.json
GUI 的 ⑨ 页会读写它；大模型是可选项，不填就纯本地规则。
"""
from __future__ import annotations

import json

from . import paths

DEFAULTS = {
    'proj_hint': '',
    'stages': ['m1_recon', 'm_roi', 'm3_post', 'm4_ld'],
    'force': False,
    'llm': {
        'enabled': False,
        'base_url': 'https://api.openai.com/v1',
        'model': '',
        'api_key': '',
        'timeout': 20,
    },
    'history': [],
}


def path():
    return paths.agent_state_dir() / 'config.json'


def load() -> dict:
    d = json.loads(json.dumps(DEFAULTS))          # 深拷贝
    try:
        p = path()
        if p.exists():
            got = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(got, dict):
                for k, v in got.items():
                    if k == 'llm' and isinstance(v, dict):
                        d['llm'].update(v)
                    else:
                        d[k] = v
    except Exception:                                    # noqa: BLE001
        pass
    return d


def save(d: dict) -> None:
    try:
        path().write_text(json.dumps(d, ensure_ascii=False, indent=2),
                          encoding='utf-8')
    except Exception:                                    # noqa: BLE001
        pass


def llm_cfg(d: dict) -> dict:
    """给 intent.llm 用的配置；没启用就返回空 dict。"""
    c = d.get('llm') or {}
    if not c.get('enabled'):
        return {}
    if not str(c.get('api_key') or '').strip():
        return {}
    return c
