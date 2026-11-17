#!/bin/bash
# 双击启动 B站视频采集器：先检查环境，缺什么自动装什么，然后打开浏览器。关闭这个终端窗口即停止。
# 依赖装在本目录的 .venv 里，不影响电脑上的其他 Python。
cd "$(dirname "$0")" || exit 1
PORT=8765
VENV=".venv"
MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"  # 官方源下载失败时改用清华镜像

pause_exit() {
    echo
    read -r -p "按回车键关闭窗口…" _
    exit "${1:-1}"
}

if [ "$1" != "--setup-only" ] && curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
    RUNNING="$(curl -s "http://127.0.0.1:$PORT/api/whoami")"
    if [ "$RUNNING" = "$(pwd -P)" ]; then
        echo "采集器已经在运行，直接打开页面。"
        open "http://127.0.0.1:$PORT"
        exit 0
    fi
    # 端口被别的文件夹里的采集器占着（多半是还开着的旧版本），打开页面会看到旧版本
    echo "另一个文件夹里的采集器正在运行（刚下载了新版本的话，多半是还开着的旧版本），占用了端口 $PORT："
    case "$RUNNING" in /*) echo "   $RUNNING" ;; *) echo "   （旧版本，看不到它在哪个文件夹）" ;; esac
    echo "请先关掉它的黑色终端窗口，再双击这里的「启动」。"
    pause_exit
fi

# ---------- ① Python（3.8 以上） ----------
find_python() {
    local p path
    for p in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
             /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3; do
        path="$(command -v "$p" 2>/dev/null)" || continue
        # 没装命令行工具时，/usr/bin/python3 只是个空壳，一运行就会弹安装窗口，先跳过
        if [ "$path" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if "$path" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
            PY="$path"
            return 0
        fi
    done
    return 1
}

echo "① 检查 Python…"
if ! find_python; then
    if command -v brew >/dev/null 2>&1; then
        echo "   没找到 Python，用 Homebrew 安装…"
        brew install python || pause_exit
    else
        echo "   没找到 Python。马上会弹出「安装命令行开发者工具」的窗口，请点「安装」。"
        echo "   安装大约需要几分钟，装好后这里会自动继续（不用关窗口）。"
        xcode-select --install >/dev/null 2>&1
        for _ in $(seq 1 180); do  # 最多等 30 分钟
            xcode-select -p >/dev/null 2>&1 && find_python && break
            sleep 10
        done
    fi
    if ! find_python; then
        echo "   还是没找到可用的 Python。请从 https://www.python.org/downloads/ 下载安装，装好后再双击启动。"
        open "https://www.python.org/downloads/"
        pause_exit
    fi
fi
echo "   ✓ $("$PY" --version 2>&1)（$PY）"

# ---------- ② 独立运行环境 .venv ----------
echo "② 检查运行环境…"
if ! "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
    echo "   第一次运行（或 Python 换过了），创建运行环境…"
    rm -rf "$VENV"
    "$PY" -m venv "$VENV" || { echo "   创建运行环境失败。"; pause_exit; }
fi
echo "   ✓ $VENV"

# ---------- ③ 依赖包 ----------
echo "③ 检查依赖包（flask、requests、openpyxl）…"
if ! "$VENV/bin/python" -c 'import flask, requests, openpyxl' >/dev/null 2>&1; then
    echo "   安装缺少的依赖包，需要联网，请稍等…"
    PIP=("$VENV/bin/python" -m pip install -q --disable-pip-version-check -r requirements.txt)
    if ! "${PIP[@]}"; then
        echo "   官方源下载失败，改用清华镜像重试…"
        "${PIP[@]}" -i "$MIRROR" || { echo "   依赖安装失败，请检查网络后重新双击启动。"; pause_exit; }
    fi
fi
echo "   ✓ 依赖齐全"

[ "$1" = "--setup-only" ] && exit 0

# ---------- ④ 启动 ----------
echo "④ 启动采集器，浏览器会自动打开 http://127.0.0.1:$PORT （关闭这个窗口即停止）"
echo
"$VENV/bin/python" app.py || { echo "采集器异常退出，请把上面的错误信息发给开发者。"; pause_exit; }
