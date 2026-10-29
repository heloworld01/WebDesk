"""WebDesk 单文件 exe 入口（PyInstaller 打包用）。

  WebDesk.exe            → 打开控制面板（GUI）
  WebDesk.exe --service  → 仅运行服务（面板以分离进程自举此模式，日志重定向 webdesk.log）
  WebDesk.exe --minimized → 面板静默启动（隐藏窗口缩到托盘；可选高级用法，开机自启已改为展示面板）

开发态仍可直接 python panel.py / python main.py，行为不变。
"""
import sys


def main():
    if "--service" in sys.argv:
        import os
        # 服务模式一律自己接管 stdout/stderr：UTF-8 行缓冲追加写 webdesk.log。
        # 不做条件判断——打包态 bootloader 对 stdout 有三种历史注入形态，都翻过车：
        #   None（旧判据命中，正常）/ NULL 设备写入器（非 None → 旧判据漏网，
        #   日志整段丢失，2026-10-29 探针实证）/ 继承句柄的本地编码包装
        #   （中文系统 GBK → 与面板 UTF-8 混写同一文件，乱码，字节取证 35:99 行）。
        # 无条件重开三态通杀；"a" 模式各写句柄原子追加，与面板写头行互不踩。
        base = os.path.dirname(os.path.abspath(sys.executable))
        logf = open(os.path.join(base, "webdesk.log"), "a",
                    encoding="utf-8", errors="replace", buffering=1)  # 行缓冲：逐行落盘
        sys.stdout = sys.stderr = logf
        try:
            import time
            for _try in range(3):       # onefile 解包与杀软实时扫描有竞态：刚解压的
                try:                    # pyd/DLL 可能被瞬时锁住 → ImportError。短等重读
                    import main
                    break
                except ImportError:
                    if _try == 2:
                        raise
                    time.sleep(0.6)
            main.main()
        except BaseException:           # windowed exe 无控制台：异常写 crash.log 便于排障
            import traceback
            base = os.path.dirname(os.path.abspath(sys.executable))
            with open(os.path.join(base, "crash.log"), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            # 绝不 re-raise：windowed 下 bootloader 会把未捕获异常弹成模态
            # “Fatal Error”对话框砸在用户桌面（2026-10-29 pylibsrtp 偶发
            # DLL 加载失败实证，关窗时弹出惊扰用户）。无头服务模式不弹窗：
            # 细节在 crash.log，面板探活发现服务没起来会转灰灯并自动重拉。
            os._exit(1)
        return
    import panel
    # 面板入口：内含单实例互斥（已有面板在跑如开机自启过则静默退出）、
    # QApplication 构建与事件循环
    panel.main()


if __name__ == "__main__":
    main()
