"""WebDesk 单文件 exe 入口（PyInstaller 打包用）。

  WebDesk.exe            → 打开控制面板（GUI）
  WebDesk.exe --service  → 仅运行服务（面板以分离进程自举此模式，日志重定向 webdesk.log）
  WebDesk.exe --minimized → 面板静默启动（隐藏窗口缩到托盘；开机自启用）

开发态仍可直接 python panel.py / python main.py，行为不变。
"""
import sys


def main():
    if "--service" in sys.argv:
        import os
        # windowed exe 无控制台时 sys.stdout/stderr 均为 None（PyInstaller 无视句柄
        # 继承），uvicorn 日志格式化器调 isatty() 会直接崩 → 补一个真实文件流
        if sys.stdout is None and sys.stderr is None:
            base = os.path.dirname(os.path.abspath(sys.executable))
            logf = open(os.path.join(base, "webdesk.log"), "a",
                        encoding="utf-8", errors="replace", buffering=1)  # 行缓冲：日志逐行落盘
            sys.stdout = sys.stderr = logf
        elif sys.stdout is None:
            sys.stdout = sys.stderr
        elif sys.stderr is None:
            sys.stderr = sys.stdout
        try:
            import main
            main.main()
        except BaseException:           # windowed exe 无控制台：异常写 crash.log 便于排障
            import traceback
            base = os.path.dirname(os.path.abspath(sys.executable))
            with open(os.path.join(base, "crash.log"), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            raise
        return
    import panel
    # 面板入口：内含单实例互斥（已有面板在跑如开机自启过则静默退出）、
    # QApplication 构建与事件循环
    panel.main()


if __name__ == "__main__":
    main()
