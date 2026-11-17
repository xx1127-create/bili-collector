@echo off
chcp 65001 >nul
rem 双击启动 B站视频采集器（Windows）：先检查环境，缺什么自动装什么，然后打开浏览器。关闭这个窗口即停止。
cd /d "%~dp0"
set PORT=8765
set VENV=.venv
set MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

if not "%~1"=="--setup-only" curl -s -o nul http://127.0.0.1:%PORT%/ && goto :running

echo ① 检查 Python…
call :running
rem 端口已经被占：是这个文件夹里的采集器就直接打开页面；是别的文件夹里的（多半是还开着的旧版本）就提示先关掉
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set "RUNNING="
for /f "usebackq delims=" %%p in (`curl -s http://127.0.0.1:%PORT%/api/whoami`) do if not defined RUNNING set "RUNNING=%%p"
if /i "%RUNNING%"=="%HERE%" (
    echo 采集器已经在运行，直接打开页面。
    start "" http://127.0.0.1:%PORT%
    exit /b 0
)
echo 另一个文件夹里的采集器正在运行（刚下载了新版本的话，多半是还开着的旧版本），占用了端口 %PORT%。
echo 请先关掉它的黑色窗口，再双击这里的「启动」。
pause
exit /b 1

:find_python
if not defined PY (
    echo    没找到 Python，用 winget 安装 Python 3.12…
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    call :find_python
)
if not defined PY (
    echo    还是没找到可用的 Python。请从 https://www.python.org/downloads/ 下载安装（安装时勾选 Add Python to PATH），装好后再双击启动。
    start "" https://www.python.org/downloads/
    goto :fail
)
echo    ✓ %PY%

echo ② 检查运行环境…
"%VENV%\Scripts\python.exe" -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo    第一次运行（或 Python 换过了），创建运行环境…
    if exist "%VENV%" rmdir /s /q "%VENV%"
    %PY% -m venv "%VENV%" || goto :fail
)
echo    ✓ %VENV%

echo ③ 检查依赖包（flask、requests、openpyxl）…
"%VENV%\Scripts\python.exe" -c "import flask, requests, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo    安装缺少的依赖包，需要联网，请稍等…
    "%VENV%\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo    官方源下载失败，改用清华镜像重试…
        "%VENV%\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt -i %MIRROR% || goto :fail
    )
)
echo    ✓ 依赖齐全
if "%~1"=="--setup-only" exit /b 0

echo ④ 启动采集器，浏览器会自动打开 http://127.0.0.1:%PORT% （关闭这个窗口即停止）
echo.
"%VENV%\Scripts\python.exe" app.py || goto :fail
exit /b 0

:find_python
set PY=
rem 先试 py 启动器，再试 python（注意：没装 Python 时 python 可能只是应用商店的空壳）
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && (set "PY=py -3" & exit /b 0)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && (set "PY=python" & exit /b 0)
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%d\python.exe" set "PY="%%d\python.exe""
)
exit /b 0

:fail
echo.
echo 出错了，请把上面的信息发给开发者。
pause
exit /b 1
