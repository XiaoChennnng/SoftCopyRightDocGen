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
    # 默认排除的目录
    DEFAULT_EXCLUDED_DIRS = {
        'node_modules', 'venv', 'env', '.venv', '.git', '.idea', '__pycache__',
        'bin', 'obj', 'target', '.vscode', 'build', 'dist', '.vs', '.gradle',
        '.svn', '.hg', 'bower_components', '.tox', '.pytest_cache', '.mypy_cache',
        '.cache', 'logs', 'log'
    }
    
    # 默认排除的文件后缀
    DEFAULT_EXCLUDED_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico', '.svg',
        '.exe', '.dll', '.so', '.dylib', '.bin',
        '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx',
        '.zip', '.rar', '.7z', '.tar', '.gz',
        '.pyc', '.pyo', '.pyd', '.class', '.o', '.a',
        '.db', '.sqlite', '.sqlite3',
        '.mp3', '.mp4', '.avi', '.mov', '.wav'
    }

    # 预编译正则表达式，提高处理性能
    _C_STYLE_COMMENT_RE = re.compile(
        r'("(?:\\.|[^"\\])*"|' r"'(?:\\.|[^'\\])*')" r'|(/\*[\s\S]*?\*/|//.*)',
        re.MULTILINE
    )
    _HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
    _PYTHON_HASH_COMMENT_RE = re.compile(r'(?m)^ *#.*\n?')

    @classmethod
    def remove_code_comments(cls, content: str, ext: str) -> str:
        """根据文件后缀删除代码注释"""
        if not content:
            return ""
            
        ext = ext.lower()
        
        # Python 文件处理
        if ext in ['.py', '.pyw']:
            try:
                # 使用 tokenize 进行稳健解析（区分文档字符串与普通字符串）
                io_obj = io.BytesIO(content.encode('utf-8'))
                out = []
                prev_toktype = tokenize.INDENT
                last_lineno = -1
                last_col = 0
                
                try:
                    tokens = tokenize.tokenize(io_obj.readline)
                except tokenize.TokenError:
                    # 解析失败时回退到正则匹配
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
                        pass # 删除 # 注释
                    elif token_type == tokenize.STRING:
                        if prev_toktype in (tokenize.INDENT, tokenize.NEWLINE, tokenize.ENCODING):
                            pass # 删除文档字符串 (docstring)
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
                print(f"解析 Python 注释时出错: {e}")
                return content
            
        # C 风格注释 (C, C++, Java, JS, TS, C#, Go, Rust 等)
        elif ext in ['.c', '.cpp', '.h', '.hpp', '.cc', '.cxx', '.m', '.mm', 
                     '.java', '.js', '.ts', '.jsx', '.tsx', '.cs', '.go', '.rs', '.swift', '.kt', '.scala',
                     '.php', '.css', '.scss', '.less']:
            def replace(match):
                if match.group(2) is not None:
                    return ""
                return match.group(1)
            return cls._C_STYLE_COMMENT_RE.sub(replace, content)
            
        # HTML/XML 注释
        elif ext in ['.html', '.htm', '.xml', '.svg', '.vue']:
            return cls._HTML_COMMENT_RE.sub("", content)
            
        return content

    @staticmethod
    def get_structure_summary(root_dir: str) -> tuple[List[str], List[str]]:
        """获取项目目录结构摘要，用于 AI 分析"""
        root = pathlib.Path(root_dir)
        if not root.exists():
            return [], []

        top_level_dirs = []
        extensions = set()

        try:
            # 获取一级目录列表
            for item in root.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    top_level_dirs.append(item.name)
            
            # 遍历获取所有文件后缀
            for r, dirs, files in os.walk(root):
                # 排除隐藏目录
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext:
                        extensions.add(ext)
                        
        except Exception as e:
            print(f"扫描项目结构时出错: {e}")
            
        return sorted(top_level_dirs), sorted(list(extensions))

    def __init__(self, root_dir: str, custom_excluded_dirs: Optional[List[str]] = None, custom_excluded_exts: Optional[List[str]] = None):
        """初始化扫描器实例"""
        self.root_dir = pathlib.Path(root_dir)
        self.excluded_dirs = self.DEFAULT_EXCLUDED_DIRS.copy()
        if custom_excluded_dirs:
            self.excluded_dirs.update(custom_excluded_dirs)
            
        self.excluded_exts = self.DEFAULT_EXCLUDED_EXTENSIONS.copy()
        if custom_excluded_exts:
            self.excluded_exts.update(custom_excluded_exts)

    def scan(self, check_cancel=None, progress_callback=None) -> List[pathlib.Path]:
        """扫描目录并返回有效文件列表（默认使用并行扫描）"""
        return self.scan_parallel(check_cancel=check_cancel, progress_callback=progress_callback)

    def scan_parallel(self, check_cancel=None, progress_callback=None, max_workers: int | None = None) -> List[pathlib.Path]:
        """多线程并行扫描文件"""
        valid_files = []
        scanned_count = 0

        if not self.root_dir.exists():
            return []

        if max_workers is None:
            # 动态计算工作线程数，上限 12
            max_workers = min(12, max(4, (os.cpu_count() or 4)))

        lock = threading.Lock()
        q: queue.Queue[str | None] = queue.Queue()
        q.put(str(self.root_dir))

        cancel_flag = threading.Event()
        
        # 节流控制：避免频繁触发 GUI 回调导致卡顿
        import time
        last_callback_time = [0.0]
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

                                    # 节流汇报进度
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

        # 扫描完成汇报
        if progress_callback:
            progress_callback(len(valid_files), "Done")
            
        return sorted(valid_files)

    def _is_valid_file(self, file_path: pathlib.Path) -> bool:
        """根据排除规则判断文件是否有效"""
        if file_path.suffix.lower() in self.excluded_exts:
            return False
        # 排除隐藏文件
        if file_path.name.startswith('.'):
            return False
        return True

    @staticmethod
    def read_file_content(file_path: pathlib.Path) -> str:
        """自动检测编码并读取文件内容"""
        try:
            raw_data = file_path.read_bytes()
            if not raw_data:
                return ""

            # 优先尝试 UTF-8
            try:
                return raw_data.decode("utf-8")
            except UnicodeDecodeError:
                pass

            # 尝试 GBK
            try:
                return raw_data.decode("gbk")
            except UnicodeDecodeError:
                pass

            # 采样前 32KB 进行深度检测，平衡速度与准确度
            sample = raw_data[:32768] if len(raw_data) > 32768 else raw_data
            result = chardet.detect(sample)
            encoding = result.get('encoding') or 'utf-8'

            try:
                return raw_data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                # 最后手段：忽略解码错误
                return raw_data.decode('utf-8', errors='ignore')
                        
        except Exception as e:
            print(f"读取文件 {file_path} 时出错: {e}")
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
