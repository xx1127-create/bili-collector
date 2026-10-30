#!/bin/bash
# 双击启动 B站视频采集器（会自动打开浏览器）。关闭这个终端窗口即停止。
cd "$(dirname "$0")"
if curl -s -o /dev/null http://127.0.0.1:8765/; then
    echo "采集器已经在运行，直接打开页面。"
    open http://127.0.0.1:8765
    exit 0
fi
python3 -m pip install --user -q flask requests openpyxl 2>/dev/null
python3 app.py
