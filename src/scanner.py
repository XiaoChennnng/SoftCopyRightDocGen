import os
import pathlib
# 优先使用 cchardet (C 实现，速度快 10x+)，fallback 到 chardet
try:
    import cchardet as chardet
except ImportError:
    import chardet
from typing import List, Optional

import re
import tokenize
import io
import threading
import queue

class Scanner:
    DEFAULT_EXCLUDED_DIRS = {
        'node_modules', 'venv', 'env', '.venv', '.git', '.idea', '__pycache__',
        'bin', 'obj', 'target', '.vscode', 'build', 'dist', '.vs', '.gradle',
        '.svn', '.hg', 'bower_components', '.tox', '.pytest_cache', '.mypy_cache',
        '.cache', 'logs', 'log'
    }
    
    DEFAULT_EXCLUDED_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico', '.svg',
        '.exe', '.dll', '.so', '.dylib', '.bin',
        '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx',
        '.zip', '.rar', '.7z', '.tar', '.gz',
        '.pyc', '.pyo', '.pyd', '.class', '.o', '.a',
        '.db', '.sqlite', '.sqlite3',
        '.mp3', '.mp4', '.avi', '.mov', '.wav'
    }

    # 预编译正则表达式，避免重复编译开销
    _C_STYLE_COMMENT_RE = re.compile(
        r'("(?:\\.|[^"\\])*"|' r"'(?:\\.|[^'\\])*')" r'|(/\*[\s\S]*?\*/|//.*)',
        re.MULTILINE
    )
    _HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
    _PYTHON_HASH_COMMENT_RE = re.compile(r'(?m)^ *#.*\n?')

    @classmethod
    def remove_code_comments(cls, content: str, ext: str) -> str:
        """
        Removes comments from code based on file extension.
        """
        if not content:
            return ""
            
        ext = ext.lower()
        
        # Python
        if ext in ['.py', '.pyw']:
            try:
                # Use tokenize for robust parsing (handles docstrings vs data strings)
                io_obj = io.BytesIO(content.encode('utf-8'))
                out = []
                prev_toktype = tokenize.INDENT
                last_lineno = -1
                last_col = 0
                
                try:
                    tokens = tokenize.tokenize(io_obj.readline)
                except tokenize.TokenError:
                    return re.sub(r'(?m)^ *#.*\n?', '', content)
                    
                for token in tokens:
                    token_type = token.type
                    token_string = token.string
                    start_line, start_col = token.start
                    end_line, end_col = token.end
                    
                    if start_line > last_lineno:
                        last_col = 0
                    if start_col > last_col:
                        out.append(" " * (start_col - last_col))
                        
                    if token_type == tokenize.COMMENT:
                        pass # Remove hash comments
                    elif token_type == tokenize.STRING:
                        if prev_toktype in (tokenize.INDENT, tokenize.NEWLINE, tokenize.ENCODING):
                            pass # Remove docstring
                        else:
                            out.append(token_string)
                    else:
                        out.append(token_string)
                        
                    if token_type not in (tokenize.NL, tokenize.COMMENT):
                        prev_toktype = token_type
                        
                    last_col = end_col
                    last_lineno = end_line
                    
                return "".join(out)
                
            except Exception as e:
                print(f"Error parsing Python comments: {e}")
                return content
            
        # C-style (C, C++, Java, JS, TS, C#, Go, Rust, Swift, Kotlin, etc.)
        elif ext in ['.c', '.cpp', '.h', '.hpp', '.cc', '.cxx', '.m', '.mm', 
                     '.java', '.js', '.ts', '.jsx', '.tsx', '.cs', '.go', '.rs', '.swift', '.kt', '.scala',
                     '.php', '.css', '.scss', '.less']:
            # 使用预编译的正则表达式
            def replace(match):
                if match.group(2) is not None:
                    return ""
                return match.group(1)
            return cls._C_STYLE_COMMENT_RE.sub(replace, content)
            
        # HTML/XML - 使用预编译的正则表达式
        elif ext in ['.html', '.htm', '.xml', '.svg', '.vue']:
            return cls._HTML_COMMENT_RE.sub("", content)
            
        # Lua, SQL, etc. can be added if needed
        
        return content

    @staticmethod
    def get_structure_summary(root_dir: str) -> tuple[List[str], List[str]]:
        """
        Returns a tuple of (top_level_dirs, all_extensions) for AI analysis.
        """
        root = pathlib.Path(root_dir)
        if not root.exists():
            return [], []

        top_level_dirs = []
        extensions = set()

        try:
            # Get top level dirs
            for item in root.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    top_level_dirs.append(item.name)
            
            # Walk lightly to find extensions (limit depth or count to avoid slow scan?)
            # For now, full walk but only collecting extensions is fast enough for typical projects
            for r, dirs, files in os.walk(root):
                # Skip hidden dirs to be faster
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext:
                        extensions.add(ext)
                        
        except Exception as e:
            print(f"Error scanning structure: {e}")
            
        return sorted(top_level_dirs), sorted(list(extensions))

    def __init__(self, root_dir: str, custom_excluded_dirs: Optional[List[str]] = None, custom_excluded_exts: Optional[List[str]] = None):
        self.root_dir = pathlib.Path(root_dir)
        self.excluded_dirs = self.DEFAULT_EXCLUDED_DIRS.copy()
        if custom_excluded_dirs:
            self.excluded_dirs.update(custom_excluded_dirs)
            
        self.excluded_exts = self.DEFAULT_EXCLUDED_EXTENSIONS.copy()
        if custom_excluded_exts:
            self.excluded_exts.update(custom_excluded_exts)

    def scan(self, check_cancel=None, progress_callback=None) -> List[pathlib.Path]:
        """
        Scans the root directory and returns a list of valid file paths.
        check_cancel: Optional callable that returns True if scan should be cancelled.
        progress_callback: Optional callable(scanned_count, current_path)
        """
        return self.scan_parallel(check_cancel=check_cancel, progress_callback=progress_callback)

    def scan_parallel(self, check_cancel=None, progress_callback=None, max_workers: int | None = None) -> List[pathlib.Path]:
        valid_files = []
        scanned_count = 0

        if not self.root_dir.exists():
            return []

        if max_workers is None:
            # 使用适中的线程数，避免在机械硬盘或大目录下线程过多导致系统调度压力过大
            max_workers = min(12, max(4, (os.cpu_count() or 4)))

        lock = threading.Lock()
        q: queue.Queue[str | None] = queue.Queue()
        q.put(str(self.root_dir))

        cancel_flag = threading.Event()
        
        # 时间节流：避免多线程同时触发回调导致 GUI 卡顿
        import time
        last_callback_time = [0.0]  # 使用列表以便在闭包中修改
        callback_lock = threading.Lock()
        MIN_CALLBACK_INTERVAL = 0.1  # 最小回调间隔 100ms

        def throttled_callback(count, path):
            if not progress_callback:
                return
            now = time.time()
            with callback_lock:
                if now - last_callback_time[0] < MIN_CALLBACK_INTERVAL:
                    return
                last_callback_time[0] = now
            progress_callback(count, path)

        def worker():
            nonlocal scanned_count
            while True:
                if cancel_flag.is_set():
                    break
                try:
                    dir_path = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    if dir_path is None:
                        break

                    if check_cancel and check_cancel():
                        cancel_flag.set()
                        continue

                    try:
                        with os.scandir(dir_path) as it:
                            for entry in it:
                                if cancel_flag.is_set() or (check_cancel and check_cancel()):
                                    cancel_flag.set()
                                    break

                                name = entry.name
                                if entry.is_dir(follow_symlinks=False):
                                    if name.startswith('.') or name in self.excluded_dirs:
                                        continue
                                    q.put(entry.path)
                                elif entry.is_file(follow_symlinks=False):
                                    file_path = pathlib.Path(entry.path)
                                    with lock:
                                        scanned_count += 1
                                        local_scanned = scanned_count

                                    if self._is_valid_file(file_path):
                                        with lock:
                                            valid_files.append(file_path)
                                            local_valid = len(valid_files)
                                    else:
                                        local_valid = None

                                    # 使用节流回调，避免 UI 卡顿
                                    if local_scanned % 500 == 0:
                                        throttled_callback(local_valid if local_valid is not None else len(valid_files), file_path)
                    except (PermissionError, FileNotFoundError, NotADirectoryError):
                        pass
                finally:
                    q.task_done()

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(max_workers)]
        for t in threads:
            t.start()

        q.join()
        for _ in threads:
            q.put(None)
        for t in threads:
            t.join(timeout=1)

        if cancel_flag.is_set():
            return []

        # Final report
        if progress_callback:
            progress_callback(len(valid_files), "Done")
            
        return sorted(valid_files)

    def _is_valid_file(self, file_path: pathlib.Path) -> bool:
        """
        Checks if a file should be included based on extensions and other rules.
        """
        if file_path.suffix.lower() in self.excluded_exts:
            return False
        # Exclude hidden files
        if file_path.name.startswith('.'):
            return False
        return True

    @staticmethod
    def read_file_content(file_path: pathlib.Path) -> str:
        """
        Reads file content with auto-encoding detection.
        """
        try:
            raw_data = file_path.read_bytes()
            if not raw_data:
                return ""

            try:
                return raw_data.decode("utf-8")
            except UnicodeDecodeError:
                pass

            try:
                return raw_data.decode("gbk")
            except UnicodeDecodeError:
                pass

            # 仅采样前 32KB 进行编码检测，避免大文件全量扫描
            sample = raw_data[:32768] if len(raw_data) > 32768 else raw_data
            result = chardet.detect(sample)
            encoding = result.get('encoding') or 'utf-8'

            try:
                return raw_data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                return raw_data.decode('utf-8', errors='ignore')
                        
        except Exception as e:
            print(f"Error reading file {file_path}: {e}")
            return ""

if __name__ == "__main__":
    # Simple test
    import sys
    if len(sys.argv) > 1:
        s = Scanner(sys.argv[1])
        files = s.scan()
        print(f"Found {len(files)} files:")
        for f in files[:10]:
            print(f)
