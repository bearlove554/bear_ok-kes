import ok
import sys
import os
import traceback
from src.config import config
from src.ui.SponsorDialog import install_sponsor_dialog


def redirect_log_to_exe_dir(exe_dir):
    """把日志输出目录重定向到 exe 同级目录。

    打包为 onefile 后 cwd 必须切到 sys._MEIPASS（feature 图片等资源用相对路径
    加载），而 ok 框架的日志路径由 get_relative_path 基于 cwd 拼出，结果写进了 C 盘
    临时解压目录。这里只替换 logger 模块内的 get_relative_path 引用：涉及 logs 的
    路径改从 exe 目录解析（首次运行目录不存在时由框架自动创建），其余路径保持原样，
    避免影响资源加载。
    """
    import ok.util.logger as logger_module

    original = logger_module.get_relative_path

    def get_relative_path_for_log(*files):
        # config_logger 的调用形式是 get_relative_path('logs/ok-script.log')，
        # 即单个参数且带子路径，因此按首段是否为 logs 判断。
        if files:
            first = str(files[0]).replace('/', os.sep)
            head = first.split(os.sep, 1)[0]
            if head == 'logs':
                return os.path.normpath(os.path.join(exe_dir, *files))
        return original(*files)

    logger_module.get_relative_path = get_relative_path_for_log
    os.makedirs(os.path.join(exe_dir, 'logs'), exist_ok=True)


if __name__ == '__main__':
    try:
        install_sponsor_dialog()
        # PyInstaller --onefile mode: 切换数据目录，但配置文件保存在 exe 所在位置
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            os.chdir(sys._MEIPASS)
            config["config_folder"] = os.path.join(exe_dir, "configs")
            redirect_log_to_exe_dir(exe_dir)
        config = config
        ok = ok.OK(config)
        ok.start()
    except Exception as e:
        log_path = os.path.join(os.path.dirname(sys.executable), "error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"Error: {e}\n\n")
            traceback.print_exc(file=f)
        raise
