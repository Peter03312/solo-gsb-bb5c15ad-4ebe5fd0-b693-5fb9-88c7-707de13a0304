"""版本快照差异对比：按稳定字幕 uid 比较两个版本（纯函数，便于测试）。"""
from __future__ import annotations

from subtitle import ms_to_srt


def _view(e) -> dict:
    """字幕条的展示视图：毫秒与格式化时间都给，附文本。"""
    return {
        "uid": e["uid"],
        "start_ms": e["start_ms"],
        "end_ms": e["end_ms"],
        "start": ms_to_srt(e["start_ms"]),
        "end": ms_to_srt(e["end_ms"]),
        "text": e["text"],
    }


def _content(e):
    return (e["start_ms"], e["end_ms"], e["text"])


def _moved_uids(old_order, new_order) -> set:
    """内容未变的共同 uid 中，相对顺序发生变化的 uid 集合。

    以各 uid 在旧顺序中的位置为序列，对新顺序求最长递增子序列（LIS）：
    子序列内的条目相对顺序未动，子序列外的视为"被移动"。
    字幕轨规模下 O(n^2) 足够。
    """
    pos = {uid: i for i, uid in enumerate(old_order)}
    seq = [pos[u] for u in new_order]
    n = len(seq)
    if n == 0:
        return set()
    dp = [1] * n
    prev = [-1] * n
    best = 0
    for i in range(n):
        for j in range(i):
            if seq[j] < seq[i] and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                prev[i] = j
        if dp[i] > dp[best]:
            best = i
    keep = set()
    i = best
    while i >= 0:
        keep.add(i)
        i = prev[i]
    return {new_order[k] for k in range(n) if k not in keep}


def diff_entries(old, new) -> dict:
    """比较两个版本的字幕条列表（各自按轨道顺序排列）。

    按稳定 uid 对齐：
    - added / removed：仅一侧存在的字幕条（附所在侧时间与文本）；
    - changed：起止时间或文本有变化，before/after 双方时间与文本都给出；
    - reordered：内容完全未变、仅相对顺序变化的条目，单独标记，
      不计入 changed，避免误报为内容修改。
    """
    old_map = {e["uid"]: e for e in old}
    new_map = {e["uid"]: e for e in new}
    old_pos = {e["uid"]: i + 1 for i, e in enumerate(old)}  # 1 起始的轨道序号
    new_pos = {e["uid"]: i + 1 for i, e in enumerate(new)}

    added, removed, changed = [], [], []
    stable_old = []  # 内容未变的共同 uid（旧顺序）

    for e in old:
        n = new_map.get(e["uid"])
        if n is None:
            removed.append(dict(_view(e), old_index=old_pos[e["uid"]]))
        elif _content(e) != _content(n):
            changed.append({
                "uid": e["uid"],
                "before": _view(e),
                "after": _view(n),
                "time_changed": (e["start_ms"], e["end_ms"]) != (n["start_ms"], n["end_ms"]),
                "text_changed": e["text"] != n["text"],
            })
        else:
            stable_old.append(e["uid"])
    for e in new:
        if e["uid"] not in old_map:
            added.append(dict(_view(e), new_index=new_pos[e["uid"]]))

    stable = set(stable_old)
    stable_new = [e["uid"] for e in new if e["uid"] in stable]
    moved = _moved_uids(stable_old, stable_new)
    reordered = [
        dict(_view(new_map[u]), from_index=old_pos[u], to_index=new_pos[u])
        for u in stable_new
        if u in moved
    ]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "reordered": reordered,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "reordered": len(reordered),
            "unchanged": len(stable_old) - len(reordered),
        },
    }
