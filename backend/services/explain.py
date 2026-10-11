"""让模型用一句话解释「这是什么、删了会怎样」。

★★ 这是全项目**第二处会联网、会花 token 的地方**（第一处是 Agent 助手页）。

项目原本的硬约束是「除 Agent 助手页外，全部功能零 token、纯本地、可断网」。
这个模块是**经用户明确同意**加上的例外，所以三条边界必须守住：

1. **只能由用户主动点**触发 —— 没有任何自动调用、没有「批量解释全部」。
2. **没配 Key 就明说没配** —— 不静默失败，也绝不编内容。
3. **只读** —— 入参只有「要解释的东西长什么样」，没有任何文件操作入口。

换句话说：**不点它，全项目仍然零 token、可断网。**
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import agent

EXPLAIN_SYSTEM = """你是本机文件清理工具的说明助手。用户会给你一个待清理的文件或文件夹的信息，
你要用**一句话**说清两件事：它通常是干什么用的、删掉之后会怎样。

硬性要求：
- 只输出一句话，不超过 70 个字，不要换行、不要编号、不要 Markdown。
- 面向普通用户，说人话，别出现"句柄""元数据"这类术语。
- **不确定就必须说不确定**，例如「看起来是……，但我不确定，建议先备份」。
  宁可保守，也不要编一个听起来合理的答案 —— 用户可能照你说的去删文件。
- 不要给操作建议（不要说"可以删除"），只解释它是什么、删了会有什么影响。"""


def available() -> bool:
    return bool(agent.resolve_key())


def _payload(kind: str, item: dict) -> dict:
    """把待解释的东西整理成模型好读的一小块 —— 只给文件名、路径、大小、判定理由。"""
    return {
        "kind": kind,
        "name": item.get("name") or "",
        "path": item.get("path") or "",
        "size": item.get("size_text") or "",
        "category": item.get("category") or "",
        "reason": item.get("reason") or "",
    }


def explain(kind: str, item: dict) -> dict:
    """返回 `{available, text, tokens}` 或 `{available: False, reason}`。"""
    if not item:
        raise ValueError("没有要解释的内容")

    key = agent.resolve_key()
    if not key:
        return {
            "available": False,
            "reason": "还没配置模型。去「Atlas 智能助手」页右下角填一次 API Key 就能用这个解释。",
        }

    body = json.dumps({
        "model": agent.resolve_model(),
        "messages": [
            {"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": json.dumps(_payload(kind, item), ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 200,
    }).encode("utf-8")

    request = urllib.request.Request(
        agent.DEEPSEEK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:160]
        except Exception:  # noqa: BLE001
            pass
        return {"available": True, "text": "", "error": f"模型接口返回 {exc.code}：{detail}"}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # 断网时这里会走到 —— 如实说"连不上"，别让用户以为功能坏了
        return {"available": True, "text": "", "error": f"连不上模型服务（{exc}）。这个解释需要联网。"}

    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return {"available": True, "text": "", "error": "模型返回的内容看不懂"}

    return {
        "available": True,
        "text": text,
        "tokens": (data.get("usage") or {}).get("total_tokens", 0),
    }
