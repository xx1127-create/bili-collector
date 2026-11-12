"""「查看结果」页面用的查询：搜索到的视频、频道、频道里的视频、类别统计。"""

VIDEO_SORTS = {"play": "v.play DESC", "pubdate": "v.pubdate DESC", "found": "found_at DESC"}
CHANNEL_SORTS = {"hits": "hit_count DESC, c.fans DESC", "fans": "c.fans DESC",
                 "videos": "c.total_videos DESC"}
CHANNEL_VIDEO_SORTS = {"pubdate": "v.pubdate DESC", "play": "v.play DESC"}


def _where(category, q, q_cols):
    """类别筛选（含所有子类）+ 关键字模糊搜索"""
    conds, args = [], []
    if category:
        conds.append("(h.category = ? OR h.category LIKE ?)")
        args += [category, category + " > %"]
    if q:
        conds.append("(" + " OR ".join(f"{c} LIKE ?" for c in q_cols) + ")")
        args += [f"%{q}%"] * len(q_cols)
    return ("WHERE " + " AND ".join(conds)) if conds else "", args


def _split(s):
    return sorted(set(s.split(","))) if s else []


def videos(store, category="", q="", sort="play", page=1, size=50):
    where, args = _where(category, q, ["v.title", "v.author", "v.tags"])
    base = f"FROM hits h JOIN videos v ON v.bvid = h.bvid {where}"
    total = store.q(f"SELECT COUNT(DISTINCT h.bvid) n {base}", args)[0]["n"]
    rows = store.q(f"""
        SELECT v.*, GROUP_CONCAT(DISTINCT h.category) cats, GROUP_CONCAT(DISTINCT h.keyword) kws,
               MAX(h.found_at) found_at
        {base} GROUP BY v.bvid ORDER BY {VIDEO_SORTS.get(sort, VIDEO_SORTS['play'])}
        LIMIT ? OFFSET ?""", args + [size, (page - 1) * size])
    for r in rows:
        r["cats"], r["kws"] = _split(r["cats"]), _split(r["kws"])
    return {"total": total, "rows": rows}


def channels(store, category="", q="", sort="hits", page=1, size=50):
    where, args = _where(category, q, ["c.name"])
    base = f"""FROM channels c JOIN videos v ON v.mid = c.mid JOIN hits h ON h.bvid = v.bvid
               {where} GROUP BY c.mid"""
    total = store.q(f"SELECT COUNT(*) n FROM (SELECT c.mid {base})", args)[0]["n"]
    rows = store.q(f"""
        SELECT c.*, COUNT(DISTINCT h.bvid) hit_count, GROUP_CONCAT(DISTINCT h.category) cats,
               (SELECT COALESCE(manual_decision, auto_decision) FROM reviews r WHERE r.mid = c.mid) decision
        {base} ORDER BY {CHANNEL_SORTS.get(sort, CHANNEL_SORTS['hits'])}
        LIMIT ? OFFSET ?""", args + [size, (page - 1) * size])
    for r in rows:
        r["cats"] = _split(r["cats"])
    return {"total": total, "rows": rows}


def channel_videos(store, mid, q="", sort="pubdate", page=1, size=50):
    conds, args = ["v.mid = ?"], [mid]
    if q:
        conds.append("v.title LIKE ?")
        args.append(f"%{q}%")
    where = " AND ".join(conds)
    total = store.q(f"SELECT COUNT(*) n FROM videos v WHERE {where}", args)[0]["n"]
    rows = store.q(f"""
        SELECT v.*, EXISTS(SELECT 1 FROM hits h WHERE h.bvid = v.bvid) AS is_hit
        FROM videos v WHERE {where}
        ORDER BY {CHANNEL_VIDEO_SORTS.get(sort, CHANNEL_VIDEO_SORTS['pubdate'])}
        LIMIT ? OFFSET ?""", args + [size, (page - 1) * size])
    ch = store.q("SELECT * FROM channels WHERE mid = ?", (mid,))
    return {"total": total, "rows": rows, "channel": ch[0] if ch else None}


def category_counts(store):
    """每个类别路径（含上级）命中的视频数，按路径排序"""
    sets = {}
    for r in store.q("SELECT DISTINCT category, bvid FROM hits"):
        parts = r["category"].split(" > ")
        for i in range(1, len(parts) + 1):
            sets.setdefault(" > ".join(parts[:i]), set()).add(r["bvid"])
    return [{"path": p, "count": len(s)} for p, s in sorted(sets.items())]
