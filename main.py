import sys
import os

# Ensure src is in path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.gui import MainApplication

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()
