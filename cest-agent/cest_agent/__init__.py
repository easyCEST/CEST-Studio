# -*- coding: utf-8 -*-
"""cest-agent —— CEST Studio 的自动编排智能体（独立目录）。

设计要点：
  * 不改 GUI、不改核心层；直接 import 现有 Module 并调用同一个 run(params, ctx)；
  * 缺掩模 / 缺频率表 → 停下交人（绝不自动生成、绝不自动补）；
  * 每阶段之后做**产物校验**，不把「没抛异常」当成功。
"""
from __future__ import annotations

from . import paths

paths.install()          # 先把 GUI 壳与核心层挂进 sys.path，后面才能 import

__all__ = ['paths']
__version__ = '0.1.0'
