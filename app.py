"""B站视频采集器 —— 本地网页应用。运行：python3 app.py，然后在浏览器打开 http://127.0.0.1:8765"""
import json
import os
import subprocess
import sys
import threading
import webbrowser

from flask import Flask, jsonify, request, send_file

from bili import BiliClient, QRLogin
from categories import Categories
from crawler import Job
from export import export_all
from store import Store

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "output")
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


store = Store(os.path.join(DATA, "bili.db"))
cats = Categories(os.path.join(BASE, "categories.json"))
job = Job(store, cats, lambda: export_all(store, OUT))
app = Flask(__name__)


@app.get("/")
def index():
    return send_file(os.path.join(BASE, "index.html"))


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
        job.start({**request.get_json(), "cookie": load_settings().get("cookie", "")})
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
                   stats=store.stats(), logs=list(job.logs)[-300:])


@app.post("/api/export")
def export():
    return jsonify(files=export_all(store, OUT))


@app.post("/api/open-output")
def open_output():
    os.makedirs(OUT, exist_ok=True)
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    subprocess.Popen([opener, OUT])
    return jsonify(ok=True)


@app.post("/api/clear")
def clear():
    if job.running:
        return jsonify(error="任务运行中，不能清空"), 400
    store.clear()
    return jsonify(ok=True)


if __name__ == "__main__":
    port = 8765
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
