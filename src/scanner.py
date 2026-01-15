import os
import pathlib
import chardet
from typing import List, Optional

class Scanner:
    DEFAULT_EXCLUDED_DIRS = {
        'node_modules', 'venv', '.git', '.idea', '__pycache__', 
        'bin', 'obj', 'target', '.vscode', 'build', 'dist', 
        '.svn', '.hg', 'bower_components'
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
        valid_files = []
        scanned_count = 0
        
        if not self.root_dir.exists():
            return []

        for root, dirs, files in os.walk(self.root_dir):
            if check_cancel and check_cancel():
                return []

            # Modify dirs in-place to exclude directories
            # Also notify progress about directory change if needed, but per-file is better for count
            dirs[:] = [d for d in dirs if d not in self.excluded_dirs and not d.startswith('.')]
            
            for file in files:
                if check_cancel and check_cancel():
                    return []
                    
                file_path = pathlib.Path(root) / file
                scanned_count += 1
                
                if self._is_valid_file(file_path):
                    valid_files.append(file_path)
                
                # Report progress every 50 files or so to avoid UI flooding
                if progress_callback and scanned_count % 50 == 0:
                    progress_callback(len(valid_files), file_path)
        
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
            # First try reading as binary to detect encoding
            raw_data = file_path.read_bytes()
            if not raw_data:
                return ""
                
            result = chardet.detect(raw_data)
            encoding = result['encoding']
            
            if not encoding:
                # Fallback to utf-8 if detection fails
                encoding = 'utf-8'
            
            # If confidence is low, might be binary file that slipped through?
            # But let's try decoding.
            
            try:
                return raw_data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                # Fallback strategies
                try:
                    return raw_data.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        return raw_data.decode('gbk')
                    except UnicodeDecodeError:
                        # Last resort: ignore errors or return empty
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
