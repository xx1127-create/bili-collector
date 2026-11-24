"""本地数据库（SQLite）：视频、关键词命中记录、频道。所有抓到的数据都先存这里，再导出文件。"""
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    bvid TEXT PRIMARY KEY, title TEXT, url TEXT, mid INTEGER, author TEXT,
    play INTEGER, pubdate TEXT, duration TEXT, typename TEXT, tags TEXT,
    description TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_mid ON videos(mid);
CREATE TABLE IF NOT EXISTS hits (
    bvid TEXT, category TEXT, keyword TEXT, found_at TEXT,
    PRIMARY KEY (bvid, category, keyword)
);
CREATE INDEX IF NOT EXISTS idx_hits_cat ON hits(category);
CREATE TABLE IF NOT EXISTS channels (
    mid INTEGER PRIMARY KEY, name TEXT, url TEXT, fans INTEGER,
    total_videos INTEGER, fetched_videos INTEGER, status TEXT, crawled_at TEXT
);
CREATE TABLE IF NOT EXISTS video_res (
    bvid TEXT PRIMARY KEY, res INTEGER, checked_at TEXT
);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.ensure_schema()

    def ensure_schema(self):
        with self.lock:
            self.db.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def save_videos(self, videos):
        with self.lock:
            self.db.executemany("""
                INSERT INTO videos VALUES (:bvid,:title,:url,:mid,:author,:play,:pubdate,
                                           :duration,:typename,:tags,:description,:t)
                ON CONFLICT(bvid) DO UPDATE SET
                    title=excluded.title, play=MAX(play, excluded.play),
                    author=COALESCE(NULLIF(excluded.author,''), author),
                    typename=COALESCE(NULLIF(excluded.typename,''), typename),
                    tags=COALESCE(NULLIF(excluded.tags,''), tags),
                    description=COALESCE(NULLIF(excluded.description,''), description),
                    updated_at=excluded.updated_at
            """, [{**v, "t": now()} for v in videos])
            self.db.commit()

    def save_hits(self, bvids, category, keyword):
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO hits VALUES (?,?,?,?)",
                                [(b, category, keyword, now()) for b in bvids])
            self.db.commit()

    def ensure_channel(self, mid, name):
        with self.lock:
            self.db.execute("""INSERT INTO channels (mid, name, url, status) VALUES (?,?,?, '未展开')
                               ON CONFLICT(mid) DO UPDATE SET name=COALESCE(NULLIF(excluded.name,''), name)""",
                            (mid, name, f"https://space.bilibili.com/{mid}"))
            self.db.commit()

    def save_res(self, bvid, res):
        """记下抽查到的视频清晰度（短边像素，查不到是 NULL，也记下免得反复查）"""
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO video_res VALUES (?,?,?)", (bvid, res, now()))
            self.db.commit()

    def update_channel(self, mid, **fields):
        fields["crawled_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.db.execute(f"UPDATE channels SET {sets} WHERE mid=?", (*fields.values(), mid))
            self.db.commit()

    def channel_status(self, mid):
        r = self.q("SELECT status FROM channels WHERE mid=?", (mid,))
        return r[0]["status"] if r else None

    def stats(self):
        return {
            "videos": self.q("SELECT COUNT(*) n FROM videos")[0]["n"],
            "hits": self.q("SELECT COUNT(DISTINCT bvid) n FROM hits")[0]["n"],
            "channels": self.q("SELECT COUNT(*) n FROM channels")[0]["n"],
            "channels_done": self.q("SELECT COUNT(*) n FROM channels WHERE status='已完成'")[0]["n"],
        }

    def orphan_categories(self, valid_paths):
        """搜索结果里有、但类别树里已经没有的类别（被删掉或改名的）"""
        rows = self.q("""SELECT h.category, COUNT(DISTINCT h.bvid) videos, COUNT(DISTINCT v.mid) channels
                         FROM hits h LEFT JOIN videos v ON v.bvid = h.bvid GROUP BY h.category""")
        return [r for r in rows if r["category"] not in valid_paths]

    def prune_categories(self, valid_paths):
        """删掉不在类别树里的命中记录；频道如果因此一个类别都不剩，连同它的视频、筛选记录一起删掉"""
        cats = [r["category"] for r in self.orphan_categories(valid_paths)]
        if not cats:
            return {"categories": [], "hits": 0, "channels": 0, "videos": 0}
        marks = ",".join("?" * len(cats))
        with self.lock:
            db = self.db
            mids = [r[0] for r in db.execute(f"""SELECT DISTINCT v.mid FROM hits h JOIN videos v ON v.bvid = h.bvid
                                                 WHERE h.category IN ({marks}) AND v.mid IS NOT NULL""", cats)]
            hits = db.execute(f"DELETE FROM hits WHERE category IN ({marks})", cats).rowcount
            kept = {r[0] for r in db.execute("""SELECT DISTINCT v.mid FROM hits h JOIN videos v ON v.bvid = h.bvid""")}
            gone = [(m,) for m in mids if m not in kept]
            videos = sum(db.execute("DELETE FROM videos WHERE mid = ?", g).rowcount for g in gone)
            db.executemany("DELETE FROM channels WHERE mid = ?", gone)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'reviews'").fetchone():
                db.executemany("DELETE FROM reviews WHERE mid = ?", gone)
            db.commit()
        return {"categories": cats, "hits": hits, "channels": len(gone), "videos": videos}

    def clear(self):
        with self.lock:
            self.db.executescript("DELETE FROM videos; DELETE FROM hits; DELETE FROM channels;")
            self.db.commit()
