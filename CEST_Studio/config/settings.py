# -*- coding: utf-8 -*-
"""config/settings.py —— 记住上次的输入/输出总目录与各模块参数（放 config/settings.json）。

想改默认值直接改 DEFAULTS；想清空就把 settings.json 删掉。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ★ 离屏/测试脚本**必须**先设环境变量 CEST_SETTINGS_FILE 指向临时文件，否则
#   MainWindow() 一构造就会把输入/输出路径回写到用户真实的 settings.json（踩过两次）。
#   例：$env:CEST_SETTINGS_FILE = "$env:TEMP\cest_test_settings.json"
FILE = Path(os.environ.get('CEST_SETTINGS_FILE')
            or (Path(__file__).resolve().parent / 'settings.json'))

DEFAULTS = {
    'input_root': '',       # 上次的输入总目录（= 当前模块的，兼容旧配置）
    'inputs': {},           # {模块 id: 该模块的输入根}——① 吃原始 DICOM；⑧④② 默认吃 PROJ
    'out_root': '',         # 上次的输出总目录（PROJ）
    'module': 'm1_recon',   # 上次停在哪一页
    'params': {},           # {模块 id: {参数名: 值}}
}


def load() -> dict:
    d = dict(DEFAULTS)
    try:
        if FILE.exists():
            d.update(json.loads(FILE.read_text(encoding='utf-8')))
    except Exception as e:                                   # noqa: BLE001
        print(f'[settings] 读取失败，用默认值：{e}')
    if not isinstance(d.get('params'), dict):
        d['params'] = {}
    if not isinstance(d.get('inputs'), dict):
        d['inputs'] = {}
    return d


def save(d: dict) -> None:
    try:
        FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:                                   # noqa: BLE001
        print(f'[settings] 保存失败：{e}')
