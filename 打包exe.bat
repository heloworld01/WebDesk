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
REM 直接按 WebDesk.spec 打包：spec 是打包参数的唯一事实来源。
REM 别改回命令行参数式——那会重新生成并覆盖 spec，丢失精调的 excludes/hiddenimports。
pyinstaller --noconfirm --clean WebDesk.spec || goto :fail

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
