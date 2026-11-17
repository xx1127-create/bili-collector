"""频道筛选：按规则自动判断每个频道「入选 / 筛掉 / 待定」，支持手动改判，结果存数据库。
（数据库里的结论值是 通过 / 淘汰 / 待定，页面上显示为 入选 / 筛掉 / 待定。）

规则和词库保存在 data/filter_rules.json（每次修改前自动备份），可在「筛选」页面修改。
词库（word_lists）：一个个有名字的词表，如「国产品牌」「美食」。规则里要用词的地方都从词库里选，
一条规则可以选多个词表，还可以另加几个词。
内置规则（每条可单独启用/停用）：
  1. 频道里跟所属一级类别无关的视频超过比例 → 筛掉（每个一级类别选哪些词表算「相关」）
  2. 某类频道里讲国产品牌的视频超过比例 → 筛掉
  3. 某类频道里讲鉴宝的视频超过比例 → 筛掉
  4. 粉丝数超过上限 → 筛掉
  5. 已抓到的视频太少 → 待定，不做判断
自定义规则（custom_rules，都是「满足条件就筛掉」）：
  title_ratio   某类频道里，标题含所选词表里的词的视频占比 超过/低于 X% → 筛掉
  name_contains 频道名含所选词表里的词 → 筛掉
  number        粉丝数 / 视频总数 / 停更天数 / 平均播放量 大于/小于 某值 → 筛掉
手动改判过的频道，重新运行筛选不会覆盖。入选的频道随机起一个像名字的新名字（如「听松记」），可以改。
"""
import collections
import copy
import datetime
import json
import os
import random
import re
import time
import uuid

import userdata

SUFFIXES = ["评测", "测评", "开箱", "教程", "好物", "推荐", "科普", "设备", "产品", "技术", "开发"]

NUMBER_FIELDS = {"fans": "粉丝数", "total_videos": "视频总数", "days_since_update": "停更天数",
                 "avg_play": "平均播放量"}
RULE_TYPES = ("title_ratio", "name_contains", "number")

DEFAULT_RULES = {
    "unrelated_on": True, "domestic_on": True, "jianbao_on": True, "fans_on": True, "min_videos_on": True,
    "unrelated_max": 33.3,      # 百分比
    "domestic_max": 33.3,
    "jianbao_max": 33.3,
    "fans_max": 1000000,
    "min_videos": 10,
    "custom_rules": [],
    "domestic_scope": "科技",   # 规则 2 只对这个一级类别生效
    "jianbao_scope": "考古",    # 规则 3 只对这个一级类别生效
    "domestic_list": "guochan",  # 规则 2 用的词表
    "jianbao_list": "jianbao",   # 规则 3 用的词表
    "related": {"科技": ["keji"], "考古": ["kaogu"]},  # 规则 1：每个一级类别，哪些词表算「相关」
    "word_lists": [
        {"id": "keji", "name": "科技相关词", "words":
            "科技 数码 评测 测评 开箱 拆解 芯片 处理器 CPU GPU 显卡 电脑 笔记本 主机 装机 手机 平板 "
            "相机 镜头 耳机 音箱 音频 屏幕 显示器 电视 投影 电池 充电 快充 充电宝 无线 蓝牙 WiFi 5G 路由器 "
            "硬盘 SSD 内存 主板 机箱 散热 键盘 鼠标 手表 手环 眼镜 VR AR AI 人工智能 大模型 机器人 "
            "自动化 编程 代码 程序 软件 系统 硬件 电子 半导体 新能源 电动 航天 火箭 卫星 太空 量子 "
            "纳米 智能 家电 苹果 iPhone iPad Mac 安卓 Android 鸿蒙 Windows Linux 性能 跑分 续航 "
            "参数 发布会 新品 配置 选购 实测 体验 对比 黑科技 掌机 Switch Steam 游戏本 NAS 打印机 "
            "屏 墨水屏 电纸书 阅读器 寸 Hz 4K 2K 色域 亮度 刷新率 骁龙 天玑 显存 降噪 电源 充电器 氮化镓 "
            "数据线 接口 USB Type-C PD 硬件 拆机 改装 DIY 上手 新机 3D打印 激光 模拟器 固件 算力"},
        {"id": "kaogu", "name": "考古相关词", "words":
            "考古 文物 遗址 古墓 墓葬 出土 发掘 博物馆 历史 古代 王朝 朝代 青铜 甲骨 遗迹 古城 文明 "
            "史前 新石器 旧石器 良渚 三星堆 殷墟 二里头 兵马俑 秦始皇 马王堆 古籍 碑刻 壁画 国宝 遗产 "
            "商朝 周朝 夏朝 秦朝 汉代 汉朝 唐朝 宋朝 元朝 明朝 清朝 春秋 战国 三国 魏晋 帝王 皇帝 "
            "陵墓 古人 文字 古文字 陶器 玉器 瓷器 丝绸之路"},
        {"id": "guochan", "name": "国产品牌", "words":
            "国产 国货 华为 HUAWEI 鸿蒙 麒麟 小米 Xiaomi 红米 Redmi 澎湃 OPPO 一加 OnePlus vivo "
            "iQOO 荣耀 HONOR 真我 realme 魅族 努比亚 红魔 中兴 ZTE 传音 联想 Lenovo 拯救者 机械革命 "
            "雷神 神舟 七彩虹 铭瑄 影驰 大疆 DJI 影石 Insta360 漫步者 绿联 安克 Anker 倍思 酷态科 "
            "公牛 摩尔线程 龙芯 兆芯 海光 寒武纪 昇腾 中芯 长江存储 致态 长鑫 比亚迪 蔚来 小鹏 "
            "理想汽车 问界 极氪 零跑 吉利 长城汽车 奇瑞 领克 智己 小米汽车 SU7 YU7 宁德时代 海尔 美的 "
            "格力 TCL 海信 创维 长虹 追觅 科沃斯 石头科技 云鲸 九号 极米 当贝 坚果投影 宇树 优必选 智元"},
        {"id": "jianbao", "name": "鉴宝", "words":
            "鉴宝 鉴定 古董 古玩 收藏品 藏品 捡漏 文玩 拍卖 估价 值多少钱 值钱 真假 赝品 仿品 "
            "开门老 老物件 盘玩 银元 钱币 古钱币 寻宝 鬼市 潘家园 和田玉 翡翠 包浆 宝贝"},
    ],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    mid INTEGER PRIMARY KEY, top_category TEXT, main_category TEXT, n_videos INTEGER,
    unrelated_ratio REAL, domestic_ratio REAL, jianbao_ratio REAL,
    auto_decision TEXT, auto_reason TEXT,
    manual_decision TEXT, note TEXT, channel_name TEXT, custom_stats TEXT,
    reviewed_at TEXT, updated_at TEXT, exported_at TEXT, exported_file TEXT
);
"""


DONE = "已完成"  # 频道展开并抓全了全部视频，只有这种频道参加筛选


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# 随机起名用：意象词 + 可选的后缀，组合出来像个频道名，但和内容无关、没有规律
NAME_WORDS = (
    "青禾 远山 白鹭 听松 半夏 南风 北辰 云栖 星野 墨竹 晴川 月白 雪松 枫桥 溪亭 鹿鸣 拾光 长庚 清和 暮雨 "
    "晨露 野渡 山岚 海棠 银杏 苍梧 竹里 春山 秋水 归鸿 浮光 沧浪 疏影 闻溪 未央 知秋 惊蛰 小满 白露 谷雨 "
    "夜航 远舟 微澜 青柠 橘井 松间 栖霞 望舒 扶摇 鸣蝉 飞白 观澜 怀瑾 若木 长歌 拾遗 星河 沙洲 雁回 晚照 "
    "栗子 蓝鲸 白桦 木棉 石榴 麦田 芦苇 青瓦 灯塔 纸鸢 松果 风铃 檐下 渔火 山茶 竹影 霁月 清溪 云杉 雾凇 "
    "鲸落 橙子 柚见 南枝 西窗 东篱 半山 一苇 三更 九歌 千帆 万象 鹿野 稻香 荷塘 柳岸 桃溪 梨涡 青蔓 紫藤"
).split()
NAME_SUFFIXES = ["", "", "", "", "集", "记", "社", "堂", "屋", "馆", "志", "录", "小站", "笔记", "工坊", "手记", "日常", "杂谈"]


def random_name(used, rng=random):
    """随机生成一个不在 used 里的名字"""
    for _ in range(200):
        a = rng.choice(NAME_WORDS)
        name = a + rng.choice(NAME_SUFFIXES)
        if rng.random() < 0.15:  # 偶尔用两个词拼一下，比如「远山听松」
            name = a + rng.choice(NAME_WORDS)
        if name not in used:
            return name
    return rng.choice(NAME_WORDS) + rng.choice(NAME_WORDS) + str(rng.randint(10, 99))


def words(s):
    return [w for w in re.split(r"[\s,，、;；]+", s or "") if w]


class Matcher:
    """标题里是否包含词表中的任何一个词。英文词前后不能紧挨字母（避免 AI 匹配到 air，但 200Hz 能匹配 Hz），不分大小写。"""

    def __init__(self, ws):
        ws = sorted(set(w.lower() for w in ws), key=len, reverse=True)
        parts = [rf"(?<![a-z]){re.escape(w)}(?![a-z])" if re.fullmatch(r"[a-z0-9 .+-]+", w)
                 else re.escape(w) for w in ws]
        self.re = re.compile("|".join(parts)) if parts else None

    def find(self, title):
        if not self.re:
            return None
        m = self.re.search((title or "").lower())
        return m.group(0) if m else None


class Reviewer:
    def __init__(self, store, categories, rules_path):
        self.store = store
        self.categories = categories
        self.rules_path = rules_path
        self.ensure_schema()

    def ensure_schema(self):
        store = self.store
        with store.lock:
            store.db.executescript(SCHEMA)
            cols = {r[1] for r in store.db.execute("PRAGMA table_info(reviews)")}
            for col in ("custom_stats", "exported_at", "exported_file"):  # 旧数据库补列
                if col not in cols:
                    store.db.execute(f"ALTER TABLE reviews ADD COLUMN {col} TEXT")
            store.db.commit()

    # ---------- 规则 ----------
    @staticmethod
    def normalize(saved):
        """旧格式（词直接写在规则里：related_words / domestic_words / jianbao_words / 自定义规则的 words）
        转成词库格式。新格式原样返回。"""
        s = copy.deepcopy(saved)
        old_related = s.pop("related_words", None)
        old_dom, old_jb = s.pop("domestic_words", None), s.pop("jianbao_words", None)
        if "word_lists" not in s:
            lists = {wl["id"]: wl for wl in copy.deepcopy(DEFAULT_RULES["word_lists"])}
            related = copy.deepcopy(DEFAULT_RULES["related"])
            if old_dom is not None:
                lists["guochan"]["words"] = old_dom
            if old_jb is not None:
                lists["jianbao"]["words"] = old_jb
            for top, ws in (old_related or {}).items():
                lid = (related.get(top) or [None])[0]
                if lid in lists:
                    lists[lid]["words"] = ws
                elif words(ws):
                    lid = uuid.uuid4().hex[:6]
                    lists[lid] = {"id": lid, "name": f"{top}相关词", "words": ws}
                    related[top] = [lid]
            s["word_lists"] = list(lists.values())
            s.setdefault("related", related)
        for c in s.get("custom_rules", []):
            c.setdefault("lists", [])
        return s

    def rules(self):
        r = copy.deepcopy(DEFAULT_RULES)
        if os.path.exists(self.rules_path):
            with open(self.rules_path, encoding="utf-8") as f:
                r.update(self.normalize(json.load(f)))
        for top in self.top_categories():
            r["related"].setdefault(top, [])
        return r

    def save_rules(self, r):
        clean = {k: r[k] for k in DEFAULT_RULES if k in r}
        for k in ("unrelated_max", "domestic_max", "jianbao_max"):
            clean[k] = float(clean[k])
        for k in ("fans_max", "min_videos"):
            clean[k] = int(clean[k])
        for k in ("unrelated_on", "domestic_on", "jianbao_on", "fans_on", "min_videos_on"):
            clean[k] = bool(clean.get(k, True))

        lists, names = [], set()
        for wl in r.get("word_lists", []):
            name = (wl.get("name") or "").strip()
            if not name:
                raise ValueError("有个词表没有名字")
            if name in names:
                raise ValueError(f"词表「{name}」重名了")
            names.add(name)
            lid = str(wl.get("id") or "")
            lists.append({"id": lid if re.fullmatch(r"[\w-]{1,32}", lid) else uuid.uuid4().hex[:6],
                          "name": name, "words": " ".join(words(wl.get("words")))})
        ids = {wl["id"]: wl["name"] for wl in lists}
        clean["word_lists"] = lists
        clean["related"] = {top: [i for i in v if i in ids] for top, v in (r.get("related") or {}).items()}
        for k, no in (("domestic_list", 2), ("jianbao_list", 3)):
            if clean.get(k) and clean[k] not in ids:
                raise ValueError(f"规则 {no} 用的词表已经不存在了，请重新选一个")

        customs = []
        for c in r.get("custom_rules", []):
            if c.get("type") not in RULE_TYPES:
                raise ValueError(f"未知的规则类型：{c.get('type')}")
            if c["type"] == "number" and c.get("field") not in NUMBER_FIELDS:
                raise ValueError(f"未知的数值字段：{c.get('field')}")
            label = (c.get("label") or "").strip() or "自定义规则"
            sel = [i for i in c.get("lists") or [] if i in ids]
            if c["type"] != "number" and not sel and not words(c.get("words")):
                raise ValueError(f"规则「{label}」还没选词表，也没有另加词")
            customs.append({
                "id": c.get("id") or uuid.uuid4().hex[:6],
                "enabled": bool(c.get("enabled", True)),
                "label": label,
                "type": c["type"],
                "scope": c.get("scope", ""),
                "lists": sel if c["type"] != "number" else [],
                "words": " ".join(words(c.get("words"))) if c["type"] != "number" else "",
                "op": ">" if c.get("op", ">") == ">" else "<",
                "value": float(c.get("value") or 0),
                "field": c.get("field", "fans"),
            })
        clean["custom_rules"] = customs
        userdata.save_json(self.rules_path, clean, "筛选规则")

    def top_categories(self):
        return [n["name"] for n in self.categories.tree]

    def tree_words(self, top):
        """一级类别树里的所有类别名、关键词，以及去掉「评测」等后缀的核心词"""
        out = set()

        def walk(nodes):
            for n in nodes:
                for w in [n["name"]] + n["keywords"]:
                    out.add(w)
                    for suf in SUFFIXES:
                        if w.endswith(suf) and len(w) > len(suf) + 1:
                            out.add(w[:-len(suf)])
                walk(n["children"])
        walk([n for n in self.categories.tree if n["name"] == top])
        return out

    def _matchers(self, r):
        lists = {wl["id"]: words(wl["words"]) for wl in r["word_lists"]}

        def pick(ids, extra=""):
            return {w for i in ids for w in lists.get(i, [])} | set(words(extra))
        dom_words = pick([r["domestic_list"]])
        # 讲国产品牌的视频本身也属于科技内容，所以国产品牌词也算「相关」
        related = {top: Matcher(self.tree_words(top) | pick(ids)
                                | (dom_words if top == r["domestic_scope"] else set()))
                   for top, ids in r["related"].items()}
        custom = {c["id"]: Matcher(pick(c["lists"], c["words"])) for c in r["custom_rules"]
                  if c["enabled"] and c["type"] in ("title_ratio", "name_contains")}
        return related, Matcher(dom_words), Matcher(pick([r["jianbao_list"]])), custom

    # ---------- 自动判断 ----------
    def _channel_cats(self):
        """每个频道搜索命中的类别路径计数"""
        cats = collections.defaultdict(collections.Counter)
        for r in self.store.q("""SELECT v.mid, h.category, COUNT(DISTINCT h.bvid) n
                                 FROM hits h JOIN videos v ON v.bvid = h.bvid GROUP BY v.mid, h.category"""):
            cats[r["mid"]][r["category"]] += r["n"]
        return cats

    def _flags(self, titles, tops, r, matchers):
        """每个标题：相关依据词、国产品牌词、鉴宝词、命中的自定义标题规则"""
        related, domestic, jianbao, custom = matchers
        title_rules = [c for c in r["custom_rules"] if c["enabled"] and c["type"] == "title_ratio"
                       and (not c["scope"] or c["scope"] in tops)]
        out = []
        for t in titles:
            rel = next((w for top in tops if top in related for w in [related[top].find(t)] if w), None)
            out.append({
                "related": rel,
                "domestic": domestic.find(t) if r["domestic_scope"] in tops else None,
                "jianbao": jianbao.find(t) if r["jianbao_scope"] in tops else None,
                "custom": [{"id": c["id"], "label": c["label"], "word": w} for c in title_rules
                           for w in [custom[c["id"]].find(t)] if w],
            })
        return out

    @staticmethod
    def _cmp(v, op, limit):
        return v > limit if op == ">" else v < limit

    def run(self, mids=None):
        """重新判断频道（默认全部）。只有展开抓全的频道参加筛选；没抓全的频道去掉自动结论（手动改判的保留）。
        手动改判的结论保留，只更新自动部分。返回各结论数量。"""
        with self.store.lock:
            self.store.db.execute(f"""DELETE FROM reviews WHERE manual_decision IS NULL
                                      AND mid NOT IN (SELECT mid FROM channels WHERE status = '{DONE}')""")
            self.store.db.commit()
        r = self.rules()
        matchers = self._matchers(r)
        custom_m = matchers[3]
        cats = self._channel_cats()
        vids = collections.defaultdict(list)
        for v in self.store.q("SELECT mid, title, pubdate, play FROM videos"):
            vids[v["mid"]].append(v)
        today = datetime.date.today()
        rows = []
        for c in self.store.q("SELECT * FROM channels WHERE status = ?", (DONE,)):
            mid = c["mid"]
            if mids is not None and mid not in mids:
                continue
            cc = cats.get(mid) or collections.Counter()
            tops = collections.Counter()
            for path, k in cc.items():
                tops[path.split(" > ")[0]] += k
            top = tops.most_common(1)[0][0] if tops else ""
            main = cc.most_common(1)[0][0] if cc else ""
            vs = vids.get(mid, [])
            n = len(vs)
            flags = self._flags([v["title"] for v in vs], set(tops), r, matchers)
            pct = lambda cnt: round(100 * cnt / n, 1) if n else None
            unrelated = pct(sum(not f["related"] for f in flags))
            dom = pct(sum(bool(f["domestic"]) for f in flags)) if r["domestic_scope"] in tops else None
            jb = pct(sum(bool(f["jianbao"]) for f in flags)) if r["jianbao_scope"] in tops else None
            enough = n >= r["min_videos"] or not r["min_videos_on"]

            reasons, stats = [], {}
            if r["fans_on"] and c["fans"] is not None and c["fans"] > r["fans_max"]:
                reasons.append(f"粉丝 {c['fans']} 超过上限 {r['fans_max']}")
            if enough and n:
                if r["unrelated_on"] and unrelated > r["unrelated_max"]:
                    reasons.append(f"无关视频 {unrelated}%")
                if r["domestic_on"] and dom is not None and dom > r["domestic_max"]:
                    reasons.append(f"国产品牌视频 {dom}%")
                if r["jianbao_on"] and jb is not None and jb > r["jianbao_max"]:
                    reasons.append(f"鉴宝视频 {jb}%")

            # 自定义规则
            dates = [v["pubdate"] for v in vs if v["pubdate"]]
            numbers = {
                "fans": c["fans"], "total_videos": c["total_videos"],
                "days_since_update": (today - datetime.date.fromisoformat(max(dates))).days if dates else None,
                "avg_play": round(sum(v["play"] or 0 for v in vs) / n) if n else None,
            }
            for cr in r["custom_rules"]:
                if not cr["enabled"] or (cr["scope"] and cr["scope"] not in tops):
                    continue
                if cr["type"] == "title_ratio":
                    if not (enough and n):
                        continue
                    v = pct(sum(any(x["id"] == cr["id"] for x in f["custom"]) for f in flags))
                    stats[cr["label"]] = f"{v}%"
                    if self._cmp(v, cr["op"], cr["value"]):
                        reasons.append(f"{cr['label']}（{v}%）")
                elif cr["type"] == "name_contains":
                    w = custom_m[cr["id"]].find(c["name"])
                    if w:
                        reasons.append(f"{cr['label']}（频道名含「{w}」）")
                else:
                    v = numbers.get(cr["field"])
                    # 停更天数、平均播放量是从已抓到的视频算的，频道没展开时不准，先不判断
                    if v is None or (cr["field"] in ("days_since_update", "avg_play") and not enough):
                        continue
                    stats[cr["label"]] = v
                    if self._cmp(v, cr["op"], cr["value"]):
                        reasons.append(f"{cr['label']}（{NUMBER_FIELDS[cr['field']]} {v}）")

            if reasons:
                decision, reason = "淘汰", "；".join(reasons)
            elif not enough:
                decision, reason = "待定", f"只抓到 {n} 个视频"
            else:
                decision, reason = "通过", ""
            rows.append((mid, top, main, n, unrelated, dom, jb, decision, reason,
                         json.dumps(stats, ensure_ascii=False), now()))
        with self.store.lock:
            self.store.db.executemany("""
                INSERT INTO reviews (mid, top_category, main_category, n_videos, unrelated_ratio,
                    domestic_ratio, jianbao_ratio, auto_decision, auto_reason, custom_stats, reviewed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mid) DO UPDATE SET top_category=excluded.top_category,
                    main_category=excluded.main_category, n_videos=excluded.n_videos,
                    unrelated_ratio=excluded.unrelated_ratio, domestic_ratio=excluded.domestic_ratio,
                    jianbao_ratio=excluded.jianbao_ratio, auto_decision=excluded.auto_decision,
                    auto_reason=excluded.auto_reason, custom_stats=excluded.custom_stats,
                    reviewed_at=excluded.reviewed_at""", rows)
            self.store.db.commit()
        self.assign_names()
        return self.summary()

    # ---------- 新名字 ----------
    def assign_names(self):
        """给还没有新名字的入选频道随机起一个像名字的名字（如「听松记」「南风小站」），不重复，起过的不变。"""
        rows = self.store.q(f"""SELECT r.mid FROM reviews r
                                WHERE {self.DECISION} = '通过' AND r.channel_name IS NULL""")
        if not rows:
            return
        used = {r["channel_name"] for r in self.store.q("SELECT channel_name FROM reviews WHERE channel_name IS NOT NULL")}
        updates = []
        for row in rows:
            name = random_name(used)
            used.add(name)
            updates.append((name, row["mid"]))
        if updates:
            with self.store.lock:
                self.store.db.executemany("UPDATE reviews SET channel_name = ? WHERE mid = ?", updates)
                self.store.db.commit()

    # ---------- 查询与手动改判 ----------
    BASE = """FROM channels c JOIN reviews r ON r.mid = c.mid"""
    DECISION = "COALESCE(r.manual_decision, r.auto_decision)"

    def summary(self):
        out = {"通过": 0, "淘汰": 0, "待定": 0, "手动": 0}
        for row in self.store.q(f"SELECT {self.DECISION} d, COUNT(*) n {self.BASE} GROUP BY d"):
            out[row["d"]] = row["n"]
        out["手动"] = self.store.q(f"SELECT COUNT(*) n {self.BASE} WHERE r.manual_decision IS NOT NULL")[0]["n"]
        out["未筛选"] = self.store.q("""SELECT COUNT(*) n FROM channels WHERE status = ?
                                       AND mid NOT IN (SELECT mid FROM reviews)""", (DONE,))[0]["n"]
        out["未抓全"] = self.store.q("SELECT COUNT(*) n FROM channels WHERE status IS NOT ?", (DONE,))[0]["n"]
        out["已抓全"] = self.store.q("SELECT COUNT(*) n FROM channels WHERE status = ?", (DONE,))[0]["n"]
        out["待导出"] = self.store.q(f"""SELECT COUNT(*) n {self.BASE}
                                      WHERE {self.DECISION} = '通过' AND r.exported_at IS NULL""")[0]["n"]
        return out

    def list(self, decision="", q="", manual_only=False, new_only=False):
        self.assign_names()
        conds, args = [], []
        if new_only:
            conds.append("r.exported_at IS NULL")
        if decision:
            conds.append(f"{self.DECISION} = ?")
            args.append(decision)
        if manual_only:
            conds.append("r.manual_decision IS NOT NULL")
        if q:
            conds.append("(c.name LIKE ? OR r.channel_name LIKE ?)")
            args += [f"%{q}%"] * 2
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.store.q(f"""SELECT c.mid, c.name, c.url, c.fans, c.total_videos, c.status, r.*,
                                {self.DECISION} AS decision
                                {self.BASE} {where}
                                ORDER BY CASE {self.DECISION} WHEN '通过' THEN 0 WHEN '待定' THEN 1 ELSE 2 END,
                                         r.channel_name, c.fans DESC""", args)
        for row in rows:
            row["custom_stats"] = json.loads(row["custom_stats"] or "{}")
        return rows

    def set_manual(self, mid, decision=None, note=None, channel_name=None, clear=False, unexport=False):
        sets, args = [], []
        if unexport:
            sets.append("exported_at = NULL, exported_file = NULL")
        if clear:
            sets.append("manual_decision = NULL")
        elif decision is not None:
            sets.append("manual_decision = ?")
            args.append(decision)
        if note is not None:
            sets.append("note = ?")
            args.append(note)
        if channel_name is not None:
            sets.append("channel_name = ?")
            args.append(channel_name.strip() or None)
        sets.append("updated_at = ?")
        args.append(now())
        with self.store.lock:
            self.store.db.execute(f"UPDATE reviews SET {', '.join(sets)} WHERE mid = ?", (*args, mid))
            self.store.db.commit()
        self.assign_names()

    def mark_exported(self, mids, file_name):
        with self.store.lock:
            self.store.db.executemany("UPDATE reviews SET exported_at = ?, exported_file = ? WHERE mid = ?",
                                      [(now(), file_name, m) for m in mids])
            self.store.db.commit()

    def recorded(self):
        """已有筛选结论（入选或筛掉）的频道：{mid: 结论}。这些频道下次不再抓取。"""
        return {r["mid"]: r["d"] for r in
                self.store.q(f"SELECT r.mid, {self.DECISION} d {self.BASE} WHERE {self.DECISION} IN ('通过', '淘汰')")}

    def channel_detail(self, mid):
        """频道的每个视频，标出被判为无关 / 国产 / 鉴宝 / 自定义规则的依据词，方便核对"""
        r = self.rules()
        rev = self.store.q("SELECT * FROM reviews WHERE mid = ?", (mid,))
        cc = self._channel_cats().get(mid) or {}
        tops = {p.split(" > ")[0] for p in cc}
        vids = self.store.q("SELECT bvid, title, url, play, pubdate FROM videos WHERE mid = ? ORDER BY pubdate DESC",
                            (mid,))
        for v, f in zip(vids, self._flags([v["title"] for v in vids], tops, r, self._matchers(r))):
            v.update(f)
        ch = self.store.q("SELECT * FROM channels WHERE mid = ?", (mid,))
        title_rules = [{"id": c["id"], "label": c["label"]} for c in r["custom_rules"]
                       if c["enabled"] and c["type"] == "title_ratio" and (not c["scope"] or c["scope"] in tops)]
        return {"channel": ch[0] if ch else None, "review": rev[0] if rev else None,
                "categories": sorted(cc), "videos": vids, "title_rules": title_rules}
