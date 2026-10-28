@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== WebDesk 单文件打包脚本（目标机无需安装 Python）=====
REM 产物：dist\WebDesk.exe —— 双击打开控制面板；面板点「启动服务」即以分离进程自举服务模式
REM 拷到新电脑：只需 WebDesk.exe（config.json / layout.json / webdesk.log 首次运行后生成在 exe 旁）

python -c "import PyInstaller" 2>nul || (
  echo [1/2] 安装 PyInstaller...
  pip install pyinstaller || goto :fail
)

echo [2/2] 打包中（约 2~5 分钟，请耐心等待）...
pyinstaller --noconfirm --clean --onefile --windowed --name WebDesk ^
  --add-data "static;static" ^
  --exclude-module matplotlib ^
  --exclude-module scipy ^
  --collect-all aiortc ^
  --collect-all av ^
  --collect-all pyautogui ^
  --collect-submodules pydirectinput ^
  --collect-all pyaudiowpatch ^
  --hidden-import mss.windows ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  webdesk_app.py || goto :fail

echo.
echo ============================================
echo  打包完成：dist\WebDesk.exe
echo  用法：双击打开控制面板；首次运行请允许防火墙通行
echo ============================================
pause
exit /b 0

:fail
echo 打包失败，请把上方报错发给我。
pause
exit /b 1
