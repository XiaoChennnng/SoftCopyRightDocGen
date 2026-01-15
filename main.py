import sys
import os

# 添加 src 目录到系统路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.gui import MainApplication

if __name__ == "__main__":
    # 启动主程序
    app = MainApplication()
    app.mainloop()
