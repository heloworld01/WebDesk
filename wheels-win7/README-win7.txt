========================================================================
 WebDesk Win7 离线打包环境 —— 使用说明
========================================================================

本文件夹内容：requirements-win7.txt + 全部依赖的 wheel 离线包（共 50+ 个）。
已在 Win11 上用 pip 按 Python 3.8 / win_amd64 做过完整离线解析验证，闭环无缺件。

【目标环境硬性要求】
  - Windows 7 SP1，64 位（无 SP1 装不了 Python 3.8）
  - Python 3.8.10 x64 —— 最后支持 Win7 的 Python，3.9+ 一律装不上或缺 DLL
    下载：https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe
  - VC++ 2015-2022 运行库（或系统补丁 KB2999226 通用 CRT）——
    Python 3.8 / av / cryptography 的 DLL 都依赖 UCRT
  - 安装 Python 时勾选 "Add Python 3.8 to PATH"

【打包步骤（在 Win7 机器上执行）】
  1. 把整个项目目录（含本 wheels-win7 文件夹）拷到 Win7 机器，例如 D:\WebDesk
  2. 装 Python 3.8.10 x64、VC++ 运行库
  3. 命令行执行（全程离线，不联网）：
       cd /d D:\WebDesk
       REM 先升级 pip —— Python 3.8.10 自带的 pip 19.x 解析器过旧，需 25.0.1
       python -m pip install --no-index --find-links wheels-win7 --upgrade pip
       python -m pip install --no-index --find-links wheels-win7 -r wheels-win7\requirements-win7.txt

  ※ 2026-10-28 修复：补入 importlib_metadata 8.5.0 + zipp 3.20.2
    （pyinstaller-hooks-contrib 在 Py<3.10 下的条件依赖，在 3.10 机上做交叉
    下载时会被标记求值跳过，Win7 上 3.8 实际安装时才暴露）。已按 3.8 标记
    对包内全部 wheel 的 Requires-Dist 做过全量审计，闭环无其他缺件。
  4. 打包：
       pyinstaller --noconfirm --clean WebDesk.spec
     （spec 是打包参数的唯一事实来源，与「打包exe.bat」共用；2026-10-29 起
       bat 也改为直接跑 spec——命令行式打包会重新生成并覆盖 spec，丢失精调的
       excludes/hiddenimports，勿改回。2026-10-29 面板改 PyQt5，托盘为
       QSystemTrayIcon，PyInstaller 自带 PyQt5 hooks，spec 无需额外声明；
       pystray/six 已从离线包移除。
       PyInstaller 已含在离线包里，版本钉死 5.13.2，
       切勿在 Win7 上 pip install -U pyinstaller —— 6.x 产物不支持 Win7）

【Win7 版与 Win10/11 版的依赖差异（已在 PyPI 核实）】
  aiortc        1.9.0    （1.10+ 要求 Py>=3.9；无 cp38 wheel）
  av            12.3.0   （cp38 最后可用线，aiortc 1.9 允许 <13）
  mss           9.0.2    （与主线一致；勿升 10.x，子线程抓屏卡死）
  pyaudiowpatch 0.2.12.8 （最后的 cp38 wheel）
  PyInstaller   5.13.2   （6.x bootloader 不支持 Win7）
  PyQt5         5.15.7   （Qt6/PyQt6/PySide6 仅 Win10+；Qt 5.15 = 最后支持 Win7
                          的 Qt 线。运行时 PyQt5-Qt5==5.15.2 + sip 12.15.0；
                          切勿 pip install -U PyQt5 —— 会破坏 Win7 兼容）
  cryptography  42.0.7   （官方确认 43+ 不支持 Win7：_rust.pyd 调用 Win10+ 才有的
                          bcryptprimitives!ProcessPrng，Win7 上报
                          "DLL load failed while importing _rust"；
                          42.0.7 是官方修复的 Win7 可用上限，pyOpenSSL issue #10944）
  pyopenssl     25.1.0   （约束 cryptography<46，与 42.0.7 兼容；26.x 要求 >=45 会冲突）
  fastapi 0.124.4 / pydantic 2.10.6 / uvicorn 0.33.0 / websockets 13.1 /
  numpy 1.24.4 / pillow 10.4.0 等由 pip 按 Py3.8 自动解析出的上限版本

【代码适配说明】
  webrtc.py 的 x264 编码补丁版本门已放宽为 ("1.15", "1.9")，
  Win7 构建（aiortc 1.9.0）同样生效。验证时看服务日志：
  不应出现「跳过编码器补丁」字样，出现了就说明装的不是 1.9.x/1.15.x。

【打包完成后的验证清单】
  - 核对 dist\WebDesk.exe 的生成时间与 sha256（防止拷到旧产物）
  - 双击 exe：面板窗口出现（onefile 在老机械盘首次解压会明显慢，不是卡死；
    Qt 面板比 tkinter 大 ~35MB，属预期）
  - Win7 托盘：QSystemTrayIcon 图标出现、右键菜单（显示/启停/退出）可用、
    通知气泡正常；若托盘无图标，检查 exe 是否被 Win7 托盘折叠区隐藏
  - 启动服务 + 防火墙放行（Win7 防火墙单独确认一次）
  - 屏幕采集坐标正确（Win7 无 per-monitor DPI，缩放非 100% 时重点核对）
  - WebRTC：视频流畅度、声音（pyaudiowpatch loopback 能否枚举播放设备）、键鼠控制
  - 长连接挂几分钟，确认无「黑屏几分钟刷新才恢复」的僵尸 WS 回归
========================================================================
