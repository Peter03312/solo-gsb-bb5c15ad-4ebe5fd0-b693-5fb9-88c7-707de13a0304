"""按稳定字幕 ID（uid）比对两个版本快照。

比对结果分四类：
- added：仅新版本中存在（新增）
- deleted：仅旧版本中存在（删除）
- modified：两边都有，但起点 / 终点 / 文本至少一项不同（显示双方时间与文本）
- reordered：两边起点、终点、文本完全一致，仅在轨道中的顺序位置不同
  （单独标记，不误报为内容修改）

位置以快照中的下标（0 起）表示。
"""
from __future__ import annotations

# 参与“内容变化”判定的字段及其展示名
FIELD_KEYS = ("start_ms", "end_ms", "text")
FIELD_LABELS = {"start_ms": "起点", "end_ms": "终点", "text": "文本"}


def _content(e: dict) -> dict:
    return {"start_ms": e["start_ms"], "end_ms": e["end_ms"], "text": e["text"]}


def diff_entries(old: list, new: list) -> dict:
    """按 uid 比对旧、新两个快照的字幕条列表。

    返回 {"summary", "added", "deleted", "modified", "reordered"}。
    """
    old_map = {e["uid"]: e for e in old}
    new_map = {e["uid"]: e for e in new}
    old_pos = {e["uid"]: i for i, e in enumerate(old)}
    new_pos = {e["uid"]: i for i, e in enumerate(new)}

    # 新增：按新版本轨道顺序
    added = [
        {**_content(e), "uid": e["uid"], "new_index": new_pos[e["uid"]]}
        for e in new
        if e["uid"] not in old_map
    ]
    # 删除：按旧版本轨道顺序
    deleted = [
        {**_content(e), "uid": e["uid"], "old_index": old_pos[e["uid"]]}
        for e in old
        if e["uid"] not in new_map
    ]

    modified, reordered = [], []
    unchanged = 0
    for o in old:  # 变化条目按旧版本顺序列出
        uid = o["uid"]
        n = new_map.get(uid)
        if n is None:
            continue  # 删除，已在上面统计
        changes = [k for k in FIELD_KEYS if o[k] != n[k]]
        oi, ni = old_pos[uid], new_pos[uid]
        if changes:
            # 内容有变化即归为 modified；即使同时移动也不算“仅顺序变化”
            modified.append({
                "uid": uid,
                "changes": changes,
                "old_index": oi,
                "new_index": ni,
                "old": _content(o),
                "new": _content(n),
            })
        elif oi != ni:
            reordered.append({
                "uid": uid,
                "old_index": oi,
                "new_index": ni,
                **_content(o),
            })
        else:
            unchanged += 1

    return {
        "summary": {
            "added": len(added),
            "deleted": len(deleted),
            "modified": len(modified),
            "reordered": len(reordered),
            "unchanged": unchanged,
        },
        "added": added,
        "deleted": deleted,
        "modified": modified,
        "reordered": reordered,
    }
