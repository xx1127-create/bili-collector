"""B站视频采集器 —— 本地网页应用。运行：python3 app.py，然后在浏览器打开 http://127.0.0.1:8765"""
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_file

from bili import BiliClient, QRLogin
from categories import Categories
from crawler import Job
import results
from export import export_all, export_review
from review import Reviewer
from store import Store
from updater import Updater
import userdata

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BILI_DATA") or os.path.join(BASE, "data")  # 测试时可指向数据副本
OUT = os.environ.get("BILI_OUT") or os.path.join(BASE, "output")
os.makedirs(DATA, exist_ok=True)

SETTINGS = os.path.join(DATA, "settings.json")  # 保存 B站 Cookie，只在本机


def load_settings():
    if os.path.exists(SETTINGS):
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    return {"cookie": ""}


def save_settings(s):
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.chmod(SETTINGS, 0o600)


# 你的设置都在 data/ 里，更新程序不会覆盖；旧版放在程序目录里的设置文件，第一次启动时自动搬过来
CATS_FILE = os.path.join(DATA, "categories.json")
RULES_FILE = os.environ.get("BILI_RULES") or os.path.join(DATA, "filter_rules.json")
for f, name in ((CATS_FILE, "categories.json"), (RULES_FILE, "filter_rules.json")):
    legacy = None if os.environ.get("BILI_DATA") else os.path.join(BASE, name)  # 测试用数据副本时不搬真文件
    moved = userdata.prepare(f, legacy=legacy, default=os.path.join(BASE, "defaults", name))
    if moved:
        print(moved)

store = Store(os.path.join(DATA, "bili.db"))
cats = Categories(CATS_FILE)
reviewer = Reviewer(store, cats, RULES_FILE)
updater = Updater(BASE)
job = Job(store, cats, reviewer,
          state_path=os.path.join(DATA, "crawl_state.json"),  # 采集断点和休息时间
          cookie_fn=lambda: load_settings().get("cookie", ""))
app = Flask(__name__)


@app.get("/")
def index():
    return send_file(os.path.join(BASE, "index.html"))


@app.get("/results")
def results_page():
    return send_file(os.path.join(BASE, "results.html"))


def _page_args():
    a = request.args
    return dict(q=a.get("q", "").strip(), sort=a.get("sort", ""),
                page=max(1, int(a.get("page", 1))), size=min(200, max(10, int(a.get("size", 50)))))


@app.get("/api/results/videos")
def results_videos():
    return jsonify(results.videos(store, request.args.get("category", ""), **_page_args()))


@app.get("/api/results/channels")
def results_channels():
    return jsonify(results.channels(store, request.args.get("category", ""), **_page_args()))


@app.get("/api/results/channel/<int:mid>")
def results_channel(mid):
    return jsonify(results.channel_videos(store, mid, **_page_args()))


@app.get("/api/results/categories")
def results_categories():
    return jsonify(results.category_counts(store))


@app.get("/api/results/orphans")
def results_orphans():
    return jsonify(store.orphan_categories(cats.paths()))


@app.post("/api/results/prune")
def results_prune():
    if job.running:
        return jsonify(error="任务运行中，不能清理"), 400
    return jsonify(store.prune_categories(cats.paths()))


# ---------------- 筛选 ----------------
@app.get("/filter")
def filter_page():
    return send_file(os.path.join(BASE, "filter.html"))


@app.get("/api/filter/rules")
def filter_rules():
    return jsonify({**reviewer.rules(), "tops": reviewer.top_categories()})


@app.put("/api/filter/rules")
def filter_save_rules():
    try:
        reviewer.save_rules(request.get_json())
    except (KeyError, ValueError, TypeError) as e:
        return jsonify(error=f"规则格式不对：{e}"), 400
    return jsonify(ok=True)


@app.post("/api/filter/run")
def filter_run():
    return jsonify(reviewer.run())


@app.get("/api/filter/list")
def filter_list():
    a = request.args
    return jsonify(summary=reviewer.summary(),
                   rows=reviewer.list(a.get("decision", ""), a.get("q", "").strip(), a.get("manual") == "1"))


@app.get("/api/filter/channel/<int:mid>")
def filter_channel(mid):
    return jsonify(reviewer.channel_detail(mid))


@app.post("/api/filter/channel/<int:mid>")
def filter_set(mid):
    b = request.get_json()
    if b.get("decision") not in (None, "通过", "淘汰", "待定"):
        return jsonify(error="结论只能是 通过 / 淘汰 / 待定"), 400
    reviewer.set_manual(mid, decision=b.get("decision"), note=b.get("note"), channel_name=b.get("channel_name"),
                        clear=bool(b.get("clear")), unexport=bool(b.get("unexport")))
    return jsonify(ok=True)


@app.post("/api/filter/export")
def filter_export():
    b = request.get_json(silent=True) or {}
    return jsonify(export_review(reviewer, store, OUT, os.path.join(BASE, "templates", "B站模版.xlsx"),
                                 include_exported=bool(b.get("include_exported"))))


@app.get("/api/categories")
def get_categories():
    return jsonify(cats.tree)


@app.put("/api/categories")
def put_categories():
    try:
        cats.set_tree(request.get_json())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@app.get("/api/login")
def get_login():
    """检测当前保存的 Cookie 是否处于登录状态（不把 Cookie 返回给网页）"""
    cookie = load_settings().get("cookie", "")
    if not cookie:
        return jsonify(has_cookie=False, logged_in=False, uname="")
    try:
        ok, uname = BiliClient(cookie=cookie, log=lambda m: None).whoami()
    except Exception as e:
        return jsonify(has_cookie=True, logged_in=False, uname="", error=str(e))
    return jsonify(has_cookie=True, logged_in=ok, uname=uname)


@app.put("/api/login")
def put_login():
    s = load_settings()
    s["cookie"] = (request.get_json().get("cookie") or "").strip()
    save_settings(s)
    return get_login()


qr = {"login": None}


@app.post("/api/qr/start")
def qr_start():
    try:
        qr["login"] = QRLogin()
    except Exception as e:
        return jsonify(error=f"获取二维码失败：{e}"), 502
    return jsonify(url=qr["login"].url)


@app.get("/api/qr/poll")
def qr_poll():
    if not qr["login"]:
        return jsonify(state="expired")
    try:
        state, cookie = qr["login"].poll()
    except Exception as e:
        return jsonify(state="error", error=str(e))
    if state == "success":
        s = load_settings()
        s["cookie"] = cookie
        save_settings(s)
        qr["login"] = None
        return jsonify(state=state, login=get_login().get_json())
    return jsonify(state=state)


@app.post("/api/start")
def start():
    try:
        job.start(request.get_json())
    except (RuntimeError, ValueError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@app.post("/api/resume")
def resume():
    try:
        job.start(request.get_json(silent=True) or {}, resume=True)
    except (RuntimeError, ValueError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@app.post("/api/stop")
def stop():
    job.stop()
    return jsonify(ok=True)


@app.get("/api/status")
def status():
    return jsonify(running=job.running, **{k: v for k, v in job.state.items() if k != "running"},
                   stats=store.stats(), logs=list(job.logs)[-300:],
                   checkpoint=job.checkpoint_info(), now=time.time())


@app.post("/api/export")
def export():
    try:
        return jsonify(files=export_all(store, OUT))
    except Exception as e:
        return jsonify(error=f"导出失败：{e}"), 500


def _open_folder(path):
    os.makedirs(path, exist_ok=True)
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    subprocess.Popen([opener, path])
    return jsonify(ok=True)


@app.post("/api/open-output")
def open_output():
    return _open_folder(OUT)


# ---------------- 设置备份与程序更新 ----------------
@app.get("/api/userdata/export")
def userdata_export():
    body = json.dumps(userdata.bundle(cats, reviewer), ensure_ascii=False, indent=2)
    name = f"B站采集器设置_{time.strftime('%Y%m%d')}.json"
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.post("/api/userdata/import")
def userdata_import():
    try:
        done = userdata.import_bundle(request.get_json(), cats, reviewer)
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        return jsonify(error=f"导入失败：{e}"), 400
    reviewer.run()
    return jsonify(done=done)


@app.post("/api/open-backups")
def open_backups():
    return _open_folder(userdata.backup_dir(CATS_FILE))


@app.get("/api/whoami")
def whoami():
    """启动脚本用：看看占着端口的是不是这个文件夹里的采集器"""
    return Response(os.path.realpath(BASE), mimetype="text/plain; charset=utf-8")


@app.get("/api/update/check")
def update_check():
    auto = request.args.get("auto") == "1"  # 打开页面时的自动检查：用缓存，失败也不报错
    try:
        return jsonify(updater.check(cached=auto))
    except Exception as e:
        if auto:
            return jsonify({})
        return jsonify(error=f"检查更新失败（可能是连不上 GitHub）：{e}"), 502


@app.post("/api/update/zip")
def update_zip():
    """用下载好的程序 ZIP 更新。不带 apply=1 时只检查，返回更新包和现在的版本"""
    data = request.get_data()
    try:
        _, _, ver = updater.inspect_zip(data)
        if request.args.get("apply") != "1":
            cur = updater.current()
            return jsonify(package=ver, current=cur,
                           older=bool(ver and cur and ver["date"] < cur["date"]))
        if job.running:
            return jsonify(error="正在采集，等采集结束或点「停止」后再更新"), 400
        userdata.backup(CATS_FILE, "类别结构")
        userdata.backup(RULES_FILE, "筛选规则")
        n = updater.apply_zip(data)
    except Exception as e:
        return jsonify(error=f"更新失败：{e}"), 400
    when = f"{ver['date']} 的版本" if ver else "更新包里的版本"
    return jsonify(message=f"已更新到 {when}（{n} 个程序文件）")


# ---------------- 从旧版本搬过来 ----------------
@app.get("/api/old/scan")
def old_scan():
    if request.args.get("auto") == "1" and store.stats()["videos"]:
        return jsonify(found=[])  # 自动提示只在这个版本还没有数据时才找
    return jsonify(found=userdata.find_old(BASE))


def choose_folder():
    """弹出系统的选择文件夹窗口，返回选的路径；取消返回 None"""
    prompt = "请选择旧版本的 B站视频采集器文件夹"
    if sys.platform == "darwin":
        r = subprocess.run(["osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")'],
                           capture_output=True, text=True, timeout=900)
        return (r.stdout.strip().rstrip("/") or None) if r.returncode == 0 else None  # 点了取消时返回码不是 0
    if sys.platform == "win32":
        ps = ("Add-Type -AssemblyName System.Windows.Forms; [Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              f"$d.Description = '{prompt}'; if ($d.ShowDialog() -eq 'OK') {{ $d.SelectedPath }}")
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8", timeout=900)
        return r.stdout.strip() or None
    raise RuntimeError("这个系统上没法弹出选择文件夹的窗口，请把旧版本的 data 文件夹手动复制过来")


@app.post("/api/old/choose")
def old_choose():
    try:
        d = choose_folder()
    except Exception as e:
        return jsonify(error=str(e)), 400
    if not d:
        return jsonify(cancelled=True)
    # 选成了 data 文件夹，或者选了套着程序文件夹的外层文件夹，都帮着找一下
    cands = [d, os.path.dirname(d)] + [os.path.join(d, e) for e in sorted(os.listdir(d))
                                         if os.path.isdir(os.path.join(d, e))]
    found = next((c for c in cands if userdata.is_install(c)), None)
    if not found:
        return jsonify(error=f"「{d}」里没有找到采集器的设置或数据（应该有 app.py，以及 categories.json 或 data 文件夹）"), 400
    return jsonify(info=userdata.describe(found))


@app.post("/api/old/migrate")
def old_migrate():
    if job.running:
        return jsonify(error="正在采集，等采集结束或点「停止」后再搬"), 400
    try:
        done = userdata.migrate_from(request.get_json()["path"], store, cats, reviewer, job, DATA)
    except (KeyError, ValueError, TypeError, OSError) as e:
        return jsonify(error=f"搬的时候出错了：{e}"), 400
    reviewer.run()
    return jsonify(done=done)


@app.post("/api/update")
def update_apply():
    if job.running:
        return jsonify(error="正在采集，等采集结束或点「停止」后再更新"), 400
    userdata.backup(CATS_FILE, "类别结构")  # 保险起见，更新前再备份一次设置（更新本身不会碰 data/）
    userdata.backup(RULES_FILE, "筛选规则")
    try:
        return jsonify(message=updater.update())
    except Exception as e:
        return jsonify(error=f"更新失败：{e}"), 502


@app.post("/api/clear")
def clear():
    if job.running:
        return jsonify(error="任务运行中，不能清空"), 400
    store.clear()
    job.forget()  # 数据清空了，断点也没意义了
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
