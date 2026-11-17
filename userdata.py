"""你的设置（类别结构、筛选规则、词库）都放在 data/ 文件夹里，和程序代码分开。
更新程序（git pull、下载新版、页面上的「更新程序」）都不会碰 data/，所以设置和抓到的数据不会丢。

- 每次保存前，把旧版本自动备份到 data/backups/，每种保留最近 BACKUP_KEEP 份
- 可以把全部设置导出成一个文件，换电脑或重装后再导入
- 旧版本把 categories.json、filter_rules.json 放在程序目录里，启动时自动搬进 data/
- 新版本解压在另一个文件夹时，能找到旁边的旧版本，把设置和数据整个复制过来
"""
import glob
import json
import os
import shutil
import sqlite3
import time
from urllib.request import pathname2url

BACKUP_KEEP = 50


def prepare(path, legacy=None, default=None):
    """确保 data/ 里有这个设置文件：没有的话，先搬旧位置的文件，再用 defaults/ 里的默认值。返回做了什么。"""
    if os.path.exists(path):
        return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if legacy and os.path.exists(legacy):
        shutil.move(legacy, path)
        return f"已把 {os.path.basename(legacy)} 从程序目录搬到 data 文件夹"
    if default and os.path.exists(default):
        shutil.copy2(default, path)
    return ""


def backup_dir(path):
    return os.path.join(os.path.dirname(path), "backups")


def backup(path, kind):
    """把现有文件复制一份到 data/backups/，文件名带时间；超过 BACKUP_KEEP 份删掉最旧的"""
    if not os.path.exists(path):
        return None
    d = backup_dir(path)
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, f"{kind}_{time.strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copy2(path, dst)
    for old in sorted(glob.glob(os.path.join(d, f"{kind}_*.json")))[:-BACKUP_KEEP]:
        os.remove(old)
    return dst


def save_json(path, obj, kind):
    """保存设置文件：内容有变化时先备份旧的，再整体替换（写一半断电也不会损坏）"""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
        backup(path, kind)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def bundle(categories, reviewer):
    """全部设置打包成一个文件的内容"""
    return {"app": "B站视频采集器", "kind": "settings", "version": 1,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "categories": categories.tree, "filter_rules": reviewer.rules()}


def import_bundle(data, categories, reviewer):
    """导入设置。认得三种文件：导出的设置文件、旧版的 categories.json、旧版的 filter_rules.json。
    导入前的设置会自动备份。返回导入了什么。"""
    tree = rules = None
    if isinstance(data, list):
        tree = data
    elif isinstance(data, dict) and data.get("kind") == "settings":
        tree, rules = data.get("categories"), data.get("filter_rules")
    elif isinstance(data, dict) and ("custom_rules" in data or "related_words" in data or "word_lists" in data):
        rules = data
    else:
        raise ValueError("认不出这个文件：请选择从这里导出的设置文件，或旧版的 categories.json / filter_rules.json")
    done = []
    if tree is not None:
        categories.set_tree(tree)
        done.append(f"类别结构（{len(tree)} 个主类别）")
    if rules is not None:
        r = reviewer.normalize(rules)
        reviewer.save_rules({**reviewer.rules(), **r})
        done.append(f"筛选规则和词库（{len(r.get('word_lists', []))} 个词表，{len(r.get('custom_rules', []))} 条自定义规则）")
    return done


# ---------- 从旧版本搬过来 ----------
# 别人用旧版本建好了类别、抓了数据，又下载了新版本（解压成另一个文件夹）。
# 新版本找到旧文件夹，把类别结构、规则、数据库、登录状态、采集进度复制过来。旧文件夹不改动。
SKIP_DIRS = {"data", "output", "templates", "defaults", "node_modules", "Library", "__MACOSX", "__pycache__"}
SCAN_LIMIT = 3000


def _first(*paths):
    return next((p for p in paths if os.path.exists(p)), None)


def old_files(d):
    """旧版本文件夹里的各个文件（新版放在 data/ 里，旧版类别和规则放在程序目录里）"""
    j = os.path.join
    return {"categories": _first(j(d, "data", "categories.json"), j(d, "categories.json")),
            "rules": _first(j(d, "data", "filter_rules.json"), j(d, "filter_rules.json")),
            "db": _first(j(d, "data", "bili.db")),
            "settings": _first(j(d, "data", "settings.json")),
            "crawl_state": _first(j(d, "data", "crawl_state.json")),
            "backups": _first(j(d, "data", "backups"))}


def is_install(d):
    if not (os.path.isfile(os.path.join(d, "app.py")) and os.path.isfile(os.path.join(d, "review.py"))):
        return False
    f = old_files(d)
    return bool(f["categories"] or f["db"])


def _ro(db):
    return sqlite3.connect("file:" + pathname2url(db) + "?mode=ro", uri=True)


def describe(d):
    f = old_files(d)
    info = {"path": d, "categories": [], "videos": None, "channels": None, "login": bool(f["settings"]),
            "modified": time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(max(os.path.getmtime(p) for p in f.values() if p)))}
    if f["categories"]:
        try:
            with open(f["categories"], encoding="utf-8") as fp:
                info["categories"] = [n["name"] for n in json.load(fp)]
        except (ValueError, KeyError, TypeError, OSError):
            pass
    if f["db"]:
        try:
            con = _ro(f["db"])
            info["videos"] = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            info["channels"] = con.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
            con.close()
        except sqlite3.Error:
            pass
    return info


def find_old(base):
    """在本程序旁边找其他版本的采集器：同一个上级文件夹里往下两层（解压出来的「xxx 2」、套了两层的文件夹都能找到）。
    只看上级文件夹，不去翻「桌面」「文稿」等其他地方（Mac 会弹出权限提示）。"""
    base = os.path.realpath(base)
    parent = os.path.dirname(base)
    roots = [parent]
    try:  # 自己是「套了两层」解压出来的（上级文件夹里只有自己），再往上一层找
        if len([e for e in os.listdir(parent) if not e.startswith(".") and e != "__MACOSX"]) <= 1:
            roots.append(os.path.dirname(parent))
    except OSError:
        pass
    found, seen, budget = [], {base}, [SCAN_LIMIT]

    def walk(d, depth):
        if budget[0] <= 0:
            return
        try:
            entries = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return
        for e in entries:
            budget[0] -= 1
            if e.name.startswith(".") or e.name in SKIP_DIRS or e.name.endswith(".app"):
                continue
            p = os.path.realpath(e.path)
            if p in seen:
                continue
            seen.add(p)
            if is_install(p):
                found.append(p)
            elif depth > 1:
                walk(p, depth - 1)
    for r in roots:
        walk(r, 2)
    return sorted((describe(p) for p in found), key=lambda i: i["modified"], reverse=True)


def migrate_from(d, store, categories, reviewer, job, data_dir):
    """把旧版本文件夹 d 里的设置和数据复制到这个版本。现在的设置和数据库先备份。返回搬了什么。"""
    if not is_install(d):
        raise ValueError("这个文件夹里没有找到采集器的设置或数据")
    if os.path.realpath(d) == os.path.realpath(os.path.dirname(os.path.abspath(__file__))):
        raise ValueError("这就是现在正在用的这个版本")
    f, done = old_files(d), []
    for key in ("categories", "rules"):
        if f[key]:
            with open(f[key], encoding="utf-8") as fp:
                done += import_bundle(json.load(fp), categories, reviewer)
    if f["db"]:
        bdir = os.path.join(data_dir, "backups")
        os.makedirs(bdir, exist_ok=True)
        if store.stats()["videos"]:  # 现在这个版本里已经有数据，先整个备份
            dst = sqlite3.connect(os.path.join(bdir, f"bili_搬旧版本前_{time.strftime('%Y%m%d-%H%M%S')}.db"))
            with store.lock:
                store.db.backup(dst)
            dst.close()
        src = _ro(f["db"])
        with store.lock:
            src.backup(store.db)  # 用 SQLite 自带的备份功能整库复制，旧库正在被打开也没关系
        src.close()
        store.ensure_schema()
        reviewer.ensure_schema()
        s = store.stats()
        done.append(f"抓到的数据（视频 {s['videos']} 个，频道 {s['channels']} 个，含筛选结果和导出记录）")
    if f["settings"]:
        dst = os.path.join(data_dir, "settings.json")
        shutil.copy2(f["settings"], dst)
        os.chmod(dst, 0o600)
        done.append("B站登录状态")
    if f["crawl_state"]:
        shutil.copy2(f["crawl_state"], os.path.join(data_dir, "crawl_state.json"))
        job.cp = job._load_cp()
        done.append("采集进度（可以「继续上次采集」）")
    if f["backups"]:
        n = 0
        for p in glob.glob(os.path.join(f["backups"], "*")):
            dst = os.path.join(data_dir, "backups", os.path.basename(p))
            if os.path.isfile(p) and not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(p, dst)
                n += 1
        if n:
            done.append(f"旧版本的自动备份 {n} 份")
    return done
