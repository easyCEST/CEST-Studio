# -*- coding: utf-8 -*-
"""cest_agent.intent —— 把一句话变成一次运行请求。

两级：
  * 本地规则（默认，不联网）：槽位匹配，覆盖「跑完 X」「只跑 ③④」「上次哪挂了」这类说法；
  * 大模型（可选，用户自己填 key 才启用）：规则认不出时兜底，返回同样的 JSON 结构。

★ 不管哪一级，最终都落到一个结构化 request，真正执行的是确定性代码；
  LLM 不碰数据、不碰算法，所以结果不会因模型幻觉而变。
"""
from __future__ import annotations

import json
import re

# 阶段别名 → 模块 id
STAGE_ALIAS = [
    ('m1_recon', r'重建|recon|导入|dicom|①'),
    ('m_roi', r'掩模|掩膜|mask|roi|去噪|⑧'),
    ('m3_post', r'后处理|post|插值|b0|③'),
    ('m4_ld', r'ld|逐体素|洛伦兹|拟合|④'),
    ('m2_display', r'展示|出图|图谱|display|②'),
    ('m5_prf', r'prf|提取|⑤'),
    ('m6_prf_analysis', r'分析|随机森林|rf|⑥'),
]

ALL_STAGES = ('m1_recon', 'm_roi', 'm3_post', 'm4_ld')

QUERY_WORDS = r'为什么|怎么回事|啥问题|怎么排查|看看|诊断|错了|挂了|失败|报告'


def local(text: str) -> dict:
    """规则解析。返回 dict(action, stages, cases, force, ask, note)。"""
    t = (text or '').strip()
    low = t.lower()

    if not t:
        return dict(action='unknown', note='空指令')
    if re.search(QUERY_WORDS, low):
        return dict(action='diagnose', cases=[], stages=[], force=False,
                    note='问诊断')

    force = bool(re.search(r'重跑|force|覆盖|重做|强跑', low))

    stages, hits = [], []
    for mid, pat in STAGE_ALIAS:
        if re.search(pat, low):
            stages.append(mid)
            hits.append(re.search(pat, low).group(0))
    # 「只跑 ③④」这类：按出现顺序保留
    if stages:
        seen, uniq = set(), []
        for s in stages:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        stages = uniq

    action = 'run'
    if re.search(r'预检|检查|能不能跑|缺什么|check', low):
        action = 'check'
    if not stages and action == 'run':
        stages = list(ALL_STAGES)

    cases = []
    m = re.search(r'病例\s*[:：]?\s*([^\s，,。]+)', t)
    if m:
        cases = [x.strip() for x in re.split(r'[、,，/]', m.group(1)) if x.strip()]
    if re.search(r'全部|所有|都跑|all', low):
        cases = []

    return dict(action=action, stages=stages, cases=cases, force=force,
                note=('识别到：' + '、'.join(hits)) if hits else '')


LLM_SYSTEM = """你是 CEST Studio 的指令解析器。把用户的一句话转成一个 JSON，不要输出任何别的文字。

可用字段：
  action: "run" | "check" | "diagnose"
     run      = 真正开始跑
     check    = 只做预检，不跑
     diagnose = 询问上次为什么失败 / 要排查
  stages: 字符串数组，取值只能是下面之一（按执行顺序）
     "m1_recon"（DICOM 重建）
     "m_roi"（去噪掩模 / 器官 ROI）
     "m3_post"（CEST 后处理）
     "m4_ld"（逐体素 LD）
     "m2_display"（多层图谱展示）
  cases: 字符串数组，病例名；空数组表示全部病例
  force: 布尔，是否强制重跑已完成的阶段

注意：
  - 用户只说「跑到 LD」= ["m1_recon","m_roi","m3_post","m4_ld"]
  - 用户说「只跑后处理」= ["m3_post"]
  - 不确定就返回 action="check"、stages 给全。
只输出 JSON。"""


def test_conn(cfg: dict) -> dict:
    """真发一次最小请求，验接口地址 / Key / 模型名是否可用。

    返回 dict(ok, ms, model, reply, error, hint)
        error 只说明「哪一步错了」，hint 说明「怎么改」。
    """
    import time as _t
    import urllib.error
    import urllib.request

    base = str(cfg.get('base_url') or '').strip().rstrip('/')
    key = str(cfg.get('api_key') or '').strip()
    model = str(cfg.get('model') or '').strip()
    timeout = float(cfg.get('timeout') or 20)
    t0 = _t.time()

    def _out(ok, **kw):
        d = dict(ok=ok, ms=int((_t.time() - t0) * 1000), model=model,
                 reply='', error='', hint='')
        d.update(kw)
        return d

    if not base:
        return _out(False, error='接口地址是空的',
                    hint='填到 /v1 为止，例如 https://api.deepseek.com/v1')
    if not key:
        return _out(False, error='API Key 是空的', hint='把 key 粘进来再测')
    if not model:
        return _out(False, error='模型名是空的',
                    hint='填服务商给的准确名字，例如 deepseek-chat')

    # 1) 先试 /models —— 最快能区分「地址/Key 错」和「模型名错」
    try:
        req = urllib.request.Request(
            base + '/models',
            headers={'Authorization': f'Bearer {key}',
                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
        ids = [str(x.get('id') or '') for x in (data.get('data') or [])]
        if ids and model and model not in ids:
            return _out(False,
                        error=f'地址和 Key 通了，但没有模型「{model}」',
                        hint='可用模型里挑一个：' + '、'.join(ids[:8]))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return _out(False, error=f'认证失败（HTTP {e.code}）',
                        hint='API Key 不对或没权限，检查有没有多粘空格')
        if e.code == 404:
            return _out(False, error='地址不对（HTTP 404）',
                        hint='接口地址要以 /v1 结尾，不要带 /chat/completions')
        # 其它 4xx/5xx 不致命（有些服务商没开放 /models），继续试对话
    except urllib.error.URLError as e:
        return _out(False, error=f'连不上：{getattr(e, "reason", e)}',
                    hint='检查网络 / 代理，或接口地址是否写错')
    except Exception as e:                               # noqa: BLE001
        return _out(False, error=f'连接异常：{type(e).__name__}: {e}',
                    hint='检查接口地址')

    # 2) 真发一次对话（留够 token，有的模型会先思考再答）
    try:
        body = json.dumps({
            'model': model,
            'messages': [dict(role='user', content='只回复两个字：收到')],
            'max_tokens': 32, 'temperature': 0,
        }).encode('utf-8')
        req = urllib.request.Request(
            base + '/chat/completions', data=body,
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {key}'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
        ch = (data.get('choices') or [{}])[0]
        msg = ch.get('message') or {}
        reply = str(msg.get('content') or '').strip()
        # 有的模型把话放在 reasoning_content，content 为空也算接通
        if not reply:
            reply = str(msg.get('reasoning_content') or '').strip()
        used = (data.get('usage') or {}).get('completion_tokens')
        if not reply:
            reply = ('（接通了，但这轮没输出正文'
                     + (f'，用了 {used} 个 token' if used else '') + '）')
        return _out(True, reply=reply[:80])
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode('utf-8', 'replace')[:200]
        except Exception:                                # noqa: BLE001
            body_txt = ''
        if e.code in (401, 403):
            return _out(False, error=f'认证失败（HTTP {e.code}）',
                        hint='API Key 不对或没权限')
        if e.code == 404:
            return _out(False, error='对话接口 404', hint='接口地址不对')
        if e.code == 429:
            return _out(False, error='被限流（HTTP 429）',
                        hint='稍后再试，或换 key')
        return _out(False, error=f'HTTP {e.code}：{body_txt}',
                    hint='看服务商返回的原文，多半是模型名或额度问题')
    except urllib.error.URLError as e:
        return _out(False, error=f'连不上：{getattr(e, "reason", e)}',
                    hint='检查网络 / 代理')
    except Exception as e:                               # noqa: BLE001
        return _out(False, error=f'{type(e).__name__}: {e}', hint='')


def llm(text: str, cfg: dict) -> dict | None:
    """可选：调大模型解析。cfg 里要有 base_url / model / api_key。

    cfg 为空或没填 key 就返回 None（由调用方退回本地规则）。
    """
    key = str(cfg.get('api_key') or '').strip()
    base = str(cfg.get('base_url') or '').strip()
    model = str(cfg.get('model') or '').strip()
    if not (key and base and model):
        return None
    try:
        import urllib.request
        body = json.dumps({
            'model': model,
            'messages': [
                dict(role='system', content=LLM_SYSTEM),
                dict(role='user', content=text),
            ],
            'temperature': 0,
        }).encode('utf-8')
        req = urllib.request.Request(
            base.rstrip('/') + '/chat/completions', data=body,
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {key}'})
        with urllib.request.urlopen(req, timeout=float(cfg.get('timeout') or 20)) as r:
            data = json.loads(r.read().decode('utf-8'))
        content = data['choices'][0]['message']['content']
        m = re.search(r'\{.*\}', content, re.S)
        if not m:
            return None
        got = json.loads(m.group(0))
        stages = [s for s in (got.get('stages') or [])
                  if s in dict(STAGE_ALIAS)]
        return dict(action=got.get('action') or 'check',
                    stages=stages or list(ALL_STAGES),
                    cases=[str(x) for x in (got.get('cases') or [])],
                    force=bool(got.get('force')),
                    note='由大模型解析')
    except Exception:                                    # noqa: BLE001
        return None


def parse(text: str, cfg: dict | None = None) -> dict:
    """先本地规则；规则明显不足（action=unknown 或没识别到阶段）且配了 LLM 才用它。"""
    r = local(text)
    if cfg and (r['action'] == 'unknown' or not r.get('stages')):
        alt = llm(text, cfg)
        if alt:
            return alt
    return r
