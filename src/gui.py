import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import os
import pathlib
import time
import sqlite3
import json
import base64
import ctypes
from ctypes import wintypes
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter.font as tkfont
from .scanner import Scanner
from .pdf_generator import PDFGenerator
from .ai_service import AIService


class AIConfigStore:
    def __init__(self, app_name: str = "SoftCopyRightDocGen"):
        self.app_name = app_name

        class _DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        self._DATA_BLOB = _DATA_BLOB
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._CryptProtectData = self._crypt32.CryptProtectData
        self._CryptUnprotectData = self._crypt32.CryptUnprotectData
        self._CryptProtectData.argtypes = [
            ctypes.POINTER(self._DATA_BLOB),
            wintypes.LPCWSTR,
            ctypes.POINTER(self._DATA_BLOB),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(self._DATA_BLOB),
        ]
        self._CryptProtectData.restype = wintypes.BOOL
        self._CryptUnprotectData.argtypes = [
            ctypes.POINTER(self._DATA_BLOB),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(self._DATA_BLOB),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(self._DATA_BLOB),
        ]
        self._CryptUnprotectData.restype = wintypes.BOOL

    def _base_dir(self) -> str:
        return str(pathlib.Path(__file__).resolve().parent.parent)

    def _db_dir(self) -> str:
        return os.path.join(self._base_dir(), "data")

    def _db_path(self) -> str:
        return os.path.join(self._db_dir(), "settings.db")

    def _entropy_path(self) -> str:
        return os.path.join(self._db_dir(), "entropy.bin")

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(self._db_dir(), exist_ok=True)
        conn = sqlite3.connect(self._db_path())
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        return conn

    def _legacy_db_path(self) -> str:
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, self.app_name, "settings.db")

    def _get_entropy(self) -> bytes:
        os.makedirs(self._db_dir(), exist_ok=True)
        path = self._entropy_path()
        try:
            if os.path.isfile(path):
                data = pathlib.Path(path).read_bytes()
                if data:
                    return data
        except Exception:
            pass

        data = secrets.token_bytes(32)
        try:
            with open(path, "xb") as f:
                f.write(data)
        except FileExistsError:
            try:
                existing = pathlib.Path(path).read_bytes()
                if existing:
                    return existing
            except Exception:
                pass
        except Exception:
            pass
        return data

    def _dpapi_encrypt(self, data: bytes, entropy: bytes) -> bytes | None:
        if not data:
            return b""
        ent_buf = ctypes.create_string_buffer(entropy)
        blob_entropy = self._DATA_BLOB(len(entropy), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_ubyte)))
        buf = ctypes.create_string_buffer(data)
        blob_in = self._DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        blob_out = self._DATA_BLOB()
        ok = self._CryptProtectData(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            self._kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))

    def _dpapi_decrypt(self, data: bytes, entropy: bytes) -> bytes | None:
        if data is None:
            return None
        if data == b"":
            return b""
        ent_buf = ctypes.create_string_buffer(entropy)
        blob_entropy = self._DATA_BLOB(len(entropy), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_ubyte)))
        buf = ctypes.create_string_buffer(data)
        blob_in = self._DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        blob_out = self._DATA_BLOB()
        ok = self._CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            self._kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))

    def _load_ai_config_from_db(self, db_path: str) -> dict | None:
        try:
            if not os.path.isfile(db_path):
                return None
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                row = conn.execute("SELECT value FROM settings WHERE key = ?", ("ai_config",)).fetchone()
                if not row:
                    return None
                value = row[0]
                data = json.loads(value)
                return data if isinstance(data, dict) else None
            finally:
                conn.close()
        except Exception:
            return None

    def migrate_legacy_if_needed(self) -> None:
        try:
            existing = self.load_ai_config()
            if existing:
                return
            legacy = self._load_ai_config_from_db(self._legacy_db_path())
            if legacy and isinstance(legacy, dict):
                self.save_ai_config(legacy)
        except Exception:
            return

    def load_ai_config(self) -> dict | None:
        try:
            entropy = self._get_entropy()
            with self._connect() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key = ?", ("ai_config",)).fetchone()
                if not row:
                    return None
                obj = json.loads(row[0])
                if not isinstance(obj, dict):
                    return None
                if obj.get("enc") == "dpapi" and isinstance(obj.get("data"), str):
                    raw = base64.b64decode(obj["data"].encode("ascii"))
                    plain = self._dpapi_decrypt(raw, entropy)
                    if plain is None:
                        return None
                    decoded = json.loads(plain.decode("utf-8"))
                    return decoded if isinstance(decoded, dict) else None
                return obj
        except Exception:
            return None

    def save_ai_config(self, config: dict) -> None:
        try:
            plain = json.dumps(config, ensure_ascii=False).encode("utf-8")
            entropy = self._get_entropy()
            enc = self._dpapi_encrypt(plain, entropy)
            if enc is None:
                return
            payload = json.dumps(
                {"enc": "dpapi", "data": base64.b64encode(enc).decode("ascii")},
                ensure_ascii=False,
            )
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("ai_config", payload),
                )
        except Exception:
            return

class AISettingsDialog(tk.Toplevel):
    def __init__(self, parent, initial_config):
        super().__init__(parent)
        self.title("AI 设置")
        self.geometry("400x420")
        self.resizable(False, False)
        
        # Center window
        self.update_idletasks()
        width = 400
        height = 420
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        
        self.parent = parent
        self.result = None
        self.configure(bg=parent._colors["bg"])
        
        # Style mapping
        self._colors = parent._colors
        self._fonts = parent._fonts
        
        self._init_ui(initial_config)
        self.transient(parent)
        self.grab_set()
        
    def _init_ui(self, config):
        frame = ttk.Frame(self, style="App.TFrame", padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="模型厂商", style="CardLabel.TLabel").pack(anchor=tk.W, pady=(0, 5))
        self.provider_var = tk.StringVar(value=config.get("provider", "DeepSeek"))
        providers = list(AIService.PROVIDERS.keys())
        cb = ttk.Combobox(frame, textvariable=self.provider_var, values=providers, state="readonly", style="App.TCombobox")
        cb.pack(fill=tk.X, pady=(0, 15))
        cb.bind("<<ComboboxSelected>>", self._on_provider_change)
        
        ttk.Label(frame, text="API Key", style="CardLabel.TLabel").pack(anchor=tk.W, pady=(0, 5))
        self.api_key_var = tk.StringVar(value=config.get("api_key", ""))
        ttk.Entry(frame, textvariable=self.api_key_var, style="App.TEntry", show="*").pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(frame, text="Base URL (可选)", style="CardLabel.TLabel").pack(anchor=tk.W, pady=(0, 5))
        self.base_url_var = tk.StringVar(value=config.get("base_url", ""))
        ttk.Entry(frame, textvariable=self.base_url_var, style="App.TEntry").pack(fill=tk.X, pady=(0, 15))
        
        ttk.Label(frame, text="Model Name (可选)", style="CardLabel.TLabel").pack(anchor=tk.W, pady=(0, 5))
        self.model_var = tk.StringVar(value=config.get("model", ""))
        ttk.Entry(frame, textvariable=self.model_var, style="App.TEntry").pack(fill=tk.X, pady=(0, 15))
        
        btn_frame = ttk.Frame(frame, style="App.TFrame")
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(btn_frame, text="取消", command=self.destroy, style="Secondary.TButton").pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text="保存", command=self._save, style="Primary.TButton").pack(side=tk.RIGHT)

    def _on_provider_change(self, event):
        provider = self.provider_var.get()
        info = AIService.PROVIDERS.get(provider, {})
        # Only auto-fill if empty or user wants to reset? For now, keep it simple.
        # If user switches provider, they likely want defaults if they haven't set custom ones.
        if not self.base_url_var.get():
            self.base_url_var.set(info.get("base_url", ""))
        if not self.model_var.get():
            self.model_var.set(info.get("model", ""))

    def _save(self):
        self.result = {
            "provider": self.provider_var.get(),
            "api_key": self.api_key_var.get().strip(),
            "base_url": self.base_url_var.get().strip(),
            "model": self.model_var.get().strip(),
        }
        self.destroy()

class AIExclusionConfirmDialog(tk.Toplevel):
    def __init__(self, parent, suggestions):
        super().__init__(parent)
        self.title("AI 智能排除确认")
        self.geometry("600x500")
        self.resizable(True, True)
        self.configure(bg=parent._colors["bg"])
        
        # Center window
        self.update_idletasks()
        width = 600
        height = 500
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        
        self.suggestions = suggestions
        self.selected_dirs = []
        self.selected_exts = []
        
        self._init_ui(parent)
        self.transient(parent)
        self.grab_set()
        
    def _init_ui(self, parent):
        frame = ttk.Frame(self, style="App.TFrame", padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Title & Analysis (Scrollable)
        analysis = self.suggestions.get("analysis", "AI 已完成分析。")
        
        analysis_frame = ttk.Frame(frame, style="App.TFrame")
        analysis_frame.pack(fill=tk.X, pady=(0, 10))
        
        analysis_scroll = ttk.Scrollbar(analysis_frame)
        analysis_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        analysis_text = tk.Text(analysis_frame, height=6, bg=parent._colors["bg"], 
                              fg=parent._colors["text"], font=parent._fonts["body"],
                              relief="flat", wrap="word", yscrollcommand=analysis_scroll.set)
        analysis_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        analysis_scroll.config(command=analysis_text.yview)
        
        analysis_text.insert(tk.END, analysis)
        analysis_text.config(state="disabled")
        
        ttk.Label(frame, text="建议排除以下项目（可点击取消勾选）：", style="Title.TLabel", font=parent._fonts["subtitle"]).pack(anchor=tk.W, pady=(0, 10))
        
        # Treeview Table
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        columns = ("checked", "type", "name")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        
        self.tree.heading("checked", text="选择")
        self.tree.heading("type", text="类型")
        self.tree.heading("name", text="名称")
        
        self.tree.column("checked", width=60, anchor="center", stretch=False)
        self.tree.column("type", width=100, anchor="center", stretch=False)
        self.tree.column("name", anchor="center")
        
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=ysb.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Populate
        self.items = [] # list of dict: {id, type, name, checked}
        
        for d in self.suggestions.get("excluded_dirs", []):
            self._add_item("目录", d)
            
        for e in self.suggestions.get("excluded_extensions", []):
            self._add_item("后缀", e)
            
        # Bind click
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._toggle_selection)

        btn_frame = ttk.Frame(frame, style="App.TFrame")
        btn_frame.pack(fill=tk.X)
        
        ttk.Button(btn_frame, text="取消", command=self.destroy, style="Secondary.TButton").pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text="确认排除", command=self._confirm, style="Primary.TButton").pack(side=tk.RIGHT)

    def _add_item(self, type_str, name):
        item_id = self.tree.insert("", tk.END, values=("☑", type_str, name))
        # Tag for coloring rows if needed, but let's keep simple
    
    def _on_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region == "heading":
            return
            
        item_id = self.tree.identify_row(event.y)
        if item_id:
            self._toggle_item(item_id)

    def _toggle_selection(self, event):
        item_id = self.tree.focus()
        if item_id:
            self._toggle_item(item_id)

    def _toggle_item(self, item_id):
        values = self.tree.item(item_id, "values")
        if not values: return
        
        current_check = values[0]
        new_check = "☐" if current_check == "☑" else "☑"
        
        self.tree.item(item_id, values=(new_check, values[1], values[2]))

    def _confirm(self):
        self.selected_dirs = []
        self.selected_exts = []
        
        for item_id in self.tree.get_children():
            values = self.tree.item(item_id, "values")
            checked = values[0] == "☑"
            type_str = values[1]
            name = values[2]
            
            if checked:
                if type_str == "目录":
                    self.selected_dirs.append(name)
                elif type_str == "后缀":
                    self.selected_exts.append(name)
                    
        self.destroy()

import webbrowser

class MainApplication(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("软著源码文档生成助手")
        self.geometry("1200x700")
        self.minsize(860, 580)
        self.resizable(True, True)

        self.ai_config = {
            "provider": "DeepSeek",
            "api_key": "",
            "base_url": "",
            "model": ""
        }

        self._ai_config_store = AIConfigStore()
        self._ai_config_store.migrate_legacy_if_needed()
        stored = self._ai_config_store.load_ai_config()
        if isinstance(stored, dict):
            for k in ("provider", "api_key", "base_url", "model"):
                v = stored.get(k)
                if isinstance(v, str):
                    self.ai_config[k] = v
        self.custom_excluded_dirs = []
        self.custom_excluded_exts = []

        self._colors = {
            "bg": "#f6f8fa",
            "surface": "#ffffff",
            "border": "#d0d7de",
            "text": "#24292f",
            "muted": "#57606a",
            "accent": "#0969da",
            "success": "#1a7f37",
            "warning": "#9a6700",
            "danger": "#cf222e",
        }

        self._log_records: list[tuple[str, str]] = []
        self._task_start_ts: float | None = None
        self._last_output_path: str | None = None

        self._log_queue = queue.Queue()
        self._log_flush_interval_ms = 80
        self._log_flush_job = None

        self._apply_theme()
        
        self._init_ui()
        
    def _init_ui(self):
        container = ttk.Frame(self, style="App.TFrame")
        container.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        container.grid_rowconfigure(1, weight=1)
        container.grid_columnconfigure(0, weight=1)

        topbar = ttk.Frame(container, style="Top.TFrame", padding=(18, 14))
        topbar.grid(row=0, column=0, sticky="ew")
        topbar.grid_columnconfigure(0, weight=1)

        title_box = ttk.Frame(topbar, style="Top.TFrame")
        title_box.grid(row=0, column=0, sticky="w")

        ttk.Label(title_box, text="软著源码文档生成助手", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="自动扫描、排除依赖、规范排版，一键生成 PDF", style="Subtitle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )

        tools = ttk.Frame(topbar, style="Top.TFrame")
        tools.grid(row=0, column=1, sticky="e")

        self.open_dir_btn = ttk.Button(tools, text="打开输出目录", command=self._open_output_dir, style="Secondary.TButton")
        self.open_dir_btn.grid(row=0, column=0, padx=(0, 8))

        self.open_pdf_btn = ttk.Button(tools, text="打开 PDF", command=self._open_output_pdf, style="Secondary.TButton")
        self.open_pdf_btn.grid(row=0, column=1, padx=(0, 8))
        self.open_pdf_btn.state(["disabled"])

        ttk.Button(tools, text="AI 设置", command=self._show_ai_settings, style="Secondary.TButton").grid(row=0, column=2, padx=(0, 8))
        ttk.Button(tools, text="帮助", command=self._show_help, style="Secondary.TButton").grid(row=0, column=3, padx=(0, 8))
        
        # Author Link
        author_link = ttk.Label(tools, text="GitHub", style="Muted.TLabel", cursor="hand2")
        author_link.grid(row=0, column=4, padx=(5, 0))
        author_link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/XiaoChennnng/SoftCopyRightDocGen"))

        content = ttk.Frame(container, style="App.TFrame", padding=(18, 14, 18, 16))
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=3)
        content.grid_columnconfigure(1, weight=2)
        content.grid_rowconfigure(0, weight=1)

        left = ttk.Frame(content, style="App.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.grid_columnconfigure(0, weight=1)

        right = ttk.Frame(content, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.name_var = tk.StringVar()
        self.version_var = tk.StringVar(value="V1.0.0")
        self.project_dir_var = tk.StringVar()
        self.output_path_var = tk.StringVar()

        self.progress_var = tk.DoubleVar(value=0)
        self.status_var = tk.StringVar(value="准备就绪")
        self.elapsed_var = tk.StringVar(value="00:00")

        self.metric_files_var = tk.StringVar(value="-")
        self.metric_non_empty_files_var = tk.StringVar(value="-")
        self.metric_lines_var = tk.StringVar(value="-")
        self.metric_total_pages_var = tk.StringVar(value="-")
        self.metric_output_pages_var = tk.StringVar(value="-")
        
        self.remove_comments_var = tk.BooleanVar(value=False)

        self._build_card_software(left)
        self._build_card_paths(left)
        self._build_card_notes(left)
        self._build_actions(left)

        self._build_status_panel(right)

        self._refresh_open_buttons()
        self._start_log_flusher()

    def _show_ai_settings(self):
        dialog = AISettingsDialog(self, self.ai_config)
        self.wait_window(dialog)
        if dialog.result:
            self.ai_config = dialog.result
            self._ai_config_store.save_ai_config(self.ai_config)
            self._log("AI 配置已更新", level="key")

    def _run_ai_analysis(self):
        project_dir = self.project_dir_var.get().strip()
        if not project_dir or not os.path.isdir(project_dir):
            messagebox.showwarning("提示", "请先选择有效的项目目录！")
            return
            
        if not self.ai_config.get("api_key"):
            self._show_ai_settings()
            if not self.ai_config.get("api_key"):
                return # User cancelled or didn't input key

        # Disable UI
        self._set_running(True) # Re-use this to lock UI
        self._set_status("正在进行 AI 分析…")
        self._set_progress(0)
        self._clear_log()
        
        def run():
            try:
                self._log("正在提取项目结构摘要…")
                dirs, exts = Scanner.get_structure_summary(project_dir)
                self._log(f"发现 {len(dirs)} 个一级目录，{len(exts)} 种文件后缀")
                
                self._log(f"正在调用 {self.ai_config['provider']} API 进行分析…", level="key")
                
                service = AIService(
                    self.ai_config["provider"],
                    self.ai_config["api_key"],
                    self.ai_config.get("base_url"),
                    self.ai_config.get("model")
                )
                
                suggestions = service.suggest_exclusions(dirs, exts)
                
                # Show confirm dialog in main thread
                self.after(0, lambda: self._show_exclusion_confirm(suggestions))
                
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda: messagebox.showerror("AI 分析失败", err_msg))
                self.after(0, lambda: self._log(f"AI 分析出错: {err_msg}", level="danger"))
            finally:
                self.after(0, lambda: self._set_running(False))
                self.after(0, lambda: self._set_status("准备就绪"))

        threading.Thread(target=run, daemon=True).start()

    def _show_exclusion_confirm(self, suggestions):
        dialog = AIExclusionConfirmDialog(self, suggestions)
        self.wait_window(dialog)
        
        self.custom_excluded_dirs = dialog.selected_dirs
        self.custom_excluded_exts = dialog.selected_exts
        
        count = len(self.custom_excluded_dirs) + len(self.custom_excluded_exts)
        if count > 0:
            msg = f"已设置 {len(self.custom_excluded_dirs)} 个排除目录和 {len(self.custom_excluded_exts)} 个排除后缀。"
            self._log(msg, level="key")
            messagebox.showinfo("设置成功", msg)
        else:
            self._log("未设置任何排除项")

    def _browse_project_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.project_dir_var.set(path)
            # Auto set output path if empty
            if not self.output_path_var.get():
                name = self.name_var.get() or "SourceCode"
                self.output_path_var.set(os.path.join(path, f"{name}_SourceCode.pdf"))

    def _browse_output_file(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")]
        )
        if path:
            self.output_path_var.set(path)

    def _build_card_software(self, parent: ttk.Frame):
        card = ttk.LabelFrame(parent, text="软件信息", style="Card.TLabelframe", padding=(14, 12))
        card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        # Center vertically with entry
        ttk.Label(card, text="软件全称", style="CardLabel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.name_entry = ttk.Entry(card, textvariable=self.name_var, style="App.TEntry", width=25)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(card, text="版本号", style="CardLabel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.version_entry = ttk.Entry(card, textvariable=self.version_var, style="App.TEntry", width=25)
        self.version_entry.grid(row=1, column=1, sticky="ew")

    def _build_card_paths(self, parent: ttk.Frame):
        card = ttk.LabelFrame(parent, text="路径设置", style="Card.TLabelframe", padding=(14, 12))
        card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        ttk.Label(card, text="项目目录", style="CardLabel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.project_entry = ttk.Entry(card, textvariable=self.project_dir_var, style="App.TEntry", width=25)
        self.project_entry.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        ttk.Button(card, text="浏览", command=self._browse_project_dir, style="Secondary.TButton").grid(row=0, column=2, padx=(10, 0), pady=(0, 10))

        ttk.Label(card, text="保存位置", style="CardLabel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.output_entry = ttk.Entry(card, textvariable=self.output_path_var, style="App.TEntry", width=25)
        self.output_entry.grid(row=1, column=1, sticky="ew")
        ttk.Button(card, text="浏览", command=self._browse_output_file, style="Secondary.TButton").grid(row=1, column=2, padx=(10, 0))

    def _build_card_notes(self, parent: ttk.Frame):
        card = ttk.LabelFrame(parent, text="选项", style="Card.TLabelframe", padding=(14, 12))
        card.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)

        msg = "默认会自动排除常见依赖目录（如 node_modules/venv/.git）与非代码文件。"
        ttk.Label(card, text=msg, style="Muted.TLabel", wraplength=280, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 5))
        
        cb = ttk.Checkbutton(card, text="生成时移除代码注释", variable=self.remove_comments_var)
        cb.grid(row=1, column=0, sticky="w")
        # Ensure checkbutton background matches
        # cb.configure(style="App.TCheckbutton") # If we had one, but default usually picks up parent bg if theme is right


    def _build_actions(self, parent: ttk.Frame):
        actions = ttk.Frame(parent, style="App.TFrame")
        actions.grid(row=3, column=0, sticky="ew")
        # Give more weight to the first column (Generate button)
        actions.grid_columnconfigure(0, weight=2)
        actions.grid_columnconfigure(1, weight=2) # AI Button
        actions.grid_columnconfigure(2, weight=1)
        actions.grid_columnconfigure(3, weight=1)

        self.generate_btn = ttk.Button(actions, text="开始生成文档", command=self._start_generation, style="Primary.TButton", width=12)
        self.generate_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4), ipady=4)
        
        self.ai_btn = ttk.Button(actions, text="AI 识别排除", command=self._run_ai_analysis, style="Primary.TButton", width=10)
        self.ai_btn.grid(row=0, column=1, sticky="ew", padx=4, ipady=4)

        self.stop_btn = ttk.Button(actions, text="停止", command=self._stop_generation, style="Danger.TButton", width=6)
        self.stop_btn.grid(row=0, column=2, sticky="ew", padx=4, ipady=4)
        self.stop_btn.state(["disabled"])

        ttk.Button(actions, text="清空日志", command=self._clear_log, style="Secondary.TButton", width=8).grid(
            row=0, column=3, sticky="ew", padx=(4, 0), ipady=4
        )

    def _build_status_panel(self, parent: ttk.Frame):
        panel = ttk.LabelFrame(parent, text="运行状态", style="Card.TLabelframe", padding=(14, 12))
        panel.grid(row=0, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(3, weight=1)

        header = ttk.Frame(panel, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel", wraplength=360, justify="left").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.elapsed_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e")

        self.progress_bar = ttk.Progressbar(panel, variable=self.progress_var, maximum=100, style="App.Horizontal.TProgressbar")
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(10, 10))

        metrics = ttk.Frame(panel, style="App.TFrame")
        metrics.grid(row=2, column=0, sticky="ew")
        for c in range(4):
            metrics.grid_columnconfigure(c, weight=1)

        self._metric_cell(metrics, 0, 0, "文件", self.metric_files_var)
        self._metric_cell(metrics, 0, 1, "有效", self.metric_non_empty_files_var)
        self._metric_cell(metrics, 0, 2, "行数", self.metric_lines_var)
        self._metric_cell(metrics, 0, 3, "页数", self.metric_output_pages_var)

        log_box = ttk.Frame(panel, style="App.TFrame")
        log_box.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        log_box.grid_rowconfigure(1, weight=1)
        log_box.grid_columnconfigure(0, weight=1)

        log_header = ttk.Frame(log_box, style="App.TFrame")
        log_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        log_header.grid_columnconfigure(0, weight=1)

        ttk.Label(log_header, text="运行日志", style="CardLabel.TLabel").grid(row=0, column=0, sticky="w")

        self.log_filter_var = tk.StringVar(value="关键")
        self.log_filter = ttk.Combobox(
            log_header,
            textvariable=self.log_filter_var,
            values=["关键", "全部"],
            state="readonly",
            width=6,
            style="App.TCombobox",
        )
        self.log_filter.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.log_filter.bind("<<ComboboxSelected>>", lambda _e: self._refresh_log_view())

        ttk.Button(log_header, text="复制", command=self._copy_log, style="Secondary.TButton").grid(row=0, column=2, sticky="e")

        text_wrap = ttk.Frame(log_box, style="App.TFrame")
        text_wrap.grid(row=1, column=0, sticky="nsew")
        text_wrap.grid_rowconfigure(0, weight=1)
        text_wrap.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            text_wrap,
            height=18,
            state="disabled",
            font=self._fonts["mono"],
            bg=self._colors["bg"],
            fg=self._colors["text"],
            insertbackground=self._colors["text"],
            selectbackground=self._colors["border"],
            relief="flat",
            padx=10,
            pady=8,
            wrap="word",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(text_wrap, orient="vertical", command=self.log_text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=yscroll.set)

        self.log_text.tag_configure("muted", foreground=self._colors["muted"])
        self.log_text.tag_configure("danger", foreground=self._colors["danger"])
        self.log_text.tag_configure("key", foreground=self._colors["accent"])

    def _metric_cell(self, parent: ttk.Frame, row: int, col: int, title: str, value_var: tk.StringVar):
        cell = ttk.Frame(parent, style="Metric.TFrame", padding=(10, 8))
        cell.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0), pady=(0, 10))
        cell.grid_columnconfigure(0, weight=1)
        ttk.Label(cell, text=title, style="MetricTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(cell, textvariable=value_var, style="MetricValue.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _apply_theme(self):
        self.configure(bg=self._colors["bg"])

        # Use 10pt for better fit on Windows standard DPI
        base_font = tkfont.nametofont("TkDefaultFont")
        base_font.configure(family="Microsoft YaHei UI", size=9)
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(family="Microsoft YaHei UI", size=9)

        self._fonts = {
            "base": ("Microsoft YaHei UI", 9),
            "body": ("Microsoft YaHei UI", 9),
            "title": ("Microsoft YaHei UI", 14, "bold"),
            "subtitle": ("Microsoft YaHei UI", 9),
            "mono": ("Consolas", 9),
        }

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background=self._colors["bg"])
        style.configure("Top.TFrame", background=self._colors["bg"])

        style.configure(
            "Card.TLabelframe",
            background=self._colors["surface"],
            foreground=self._colors["text"],
            bordercolor=self._colors["border"],
            lightcolor=self._colors["border"],
            darkcolor=self._colors["border"],
        )
        style.configure("Card.TLabelframe.Label", background=self._colors["surface"], foreground=self._colors["muted"], font=self._fonts["subtitle"])

        style.configure("Title.TLabel", background=self._colors["bg"], foreground=self._colors["text"], font=self._fonts["title"])
        style.configure("Subtitle.TLabel", background=self._colors["bg"], foreground=self._colors["muted"], font=self._fonts["subtitle"])

        style.configure("CardLabel.TLabel", background=self._colors["surface"], foreground=self._colors["muted"], font=self._fonts["subtitle"])
        style.configure("Muted.TLabel", background=self._colors["surface"], foreground=self._colors["muted"], font=self._fonts["subtitle"])

        style.configure("Status.TLabel", background=self._colors["surface"], foreground=self._colors["text"], font=self._fonts["subtitle"])

        # Optimized Entry padding for better vertical alignment
        style.configure(
            "App.TEntry",
            fieldbackground=self._colors["bg"],
            foreground=self._colors["text"],
            bordercolor=self._colors["border"],
            lightcolor=self._colors["border"],
            darkcolor=self._colors["border"],
            insertcolor=self._colors["text"],
            padding=(5, 6) 
        )

        # Reduced button padding for compactness
        style.configure(
            "Primary.TButton",
            padding=(10, 4),
            background=self._colors["accent"],
            foreground="#ffffff",
            bordercolor=self._colors["accent"],
            focusthickness=2,
            focuscolor=self._colors["border"],
            font=self._fonts["base"]
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", self._colors["border"]), ("active", "#0550ae")],
            foreground=[("disabled", self._colors["muted"]), ("active", "#ffffff")],
        )

        style.configure(
            "Secondary.TButton",
            padding=(10, 4),
            background=self._colors["bg"],
            foreground=self._colors["text"],
            bordercolor=self._colors["border"],
            focusthickness=2,
            focuscolor=self._colors["border"],
            font=self._fonts["base"]
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#ebf0f4")],
        )

        style.configure(
            "Danger.TButton",
            padding=(10, 4),
            background=self._colors["surface"],
            foreground=self._colors["danger"],
            bordercolor=self._colors["border"],
            font=self._fonts["base"]
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#ffebe9")],
        )

        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor=self._colors["bg"],
            bordercolor=self._colors["border"],
            background=self._colors["accent"],
            lightcolor=self._colors["accent"],
            darkcolor=self._colors["accent"],
        )

        style.configure("Metric.TFrame", background=self._colors["bg"], bordercolor=self._colors["border"], relief="solid")
        style.configure("MetricTitle.TLabel", background=self._colors["bg"], foreground=self._colors["muted"], font=self._fonts["subtitle"])
        style.configure("MetricValue.TLabel", background=self._colors["bg"], foreground=self._colors["text"], font=(self._fonts["subtitle"][0], 12, "bold"))

        style.configure("App.TCombobox", fieldbackground=self._colors["bg"], background=self._colors["surface"], foreground=self._colors["text"])
        style.map("App.TCombobox", fieldbackground=[("readonly", self._colors["bg"])])

    def _refresh_open_buttons(self):
        if self._last_output_path and os.path.isfile(self._last_output_path):
            self.open_pdf_btn.state(["!disabled"])
        else:
            self.open_pdf_btn.state(["disabled"])

    def _show_help(self):
        messagebox.showinfo(
            "帮助",
            "1) 填写软件全称与版本号\n2) 选择项目目录与 PDF 保存位置\n3) 点击开始生成，等待完成\n\n提示：生成过程中可点击停止取消任务。",
        )

    def _open_output_dir(self):
        path = self.output_path_var.get().strip() or self._last_output_path
        if not path:
            return
        directory = os.path.dirname(path)
        if directory and os.path.isdir(directory):
            os.startfile(directory)

    def _open_output_pdf(self):
        path = self._last_output_path or self.output_path_var.get().strip()
        if path and os.path.isfile(path):
            os.startfile(path)

    def _copy_log(self):
        text = "\n".join(msg for _lvl, msg in self._log_records)
        self.clipboard_clear()
        self.clipboard_append(text)

    def _clear_log(self):
        self._log_records.clear()
        if hasattr(self, "_log_queue"):
            try:
                while True:
                    self._log_queue.get_nowait()
            except queue.Empty:
                pass
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

    def _refresh_log_view(self):
        mode = self.log_filter_var.get()
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        for lvl, msg in self._log_records:
            if mode == "关键" and lvl == "detail":
                continue
            tag = "danger" if lvl == "danger" else ("key" if lvl == "key" else "muted")
            self.log_text.insert(tk.END, msg + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def _start_log_flusher(self):
        if getattr(self, "_log_flush_job", None) is None:
            self._log_flush_job = self.after(self._log_flush_interval_ms, self._flush_log_queue)

    def _flush_log_queue(self):
        if not hasattr(self, "log_text") or not hasattr(self, "_log_queue"):
            self._log_flush_job = self.after(self._log_flush_interval_ms, self._flush_log_queue)
            return

        mode = self.log_filter_var.get() if hasattr(self, "log_filter_var") else "全部"
        self.log_text.config(state="normal")
        processed = 0
        try:
            while True:
                level, message = self._log_queue.get_nowait()
                if mode == "关键" and level == "detail":
                    continue
                tag = "danger" if level == "danger" else ("key" if level == "key" else "muted")
                self.log_text.insert(tk.END, message + "\n", tag)
                processed += 1
        except queue.Empty:
            pass

        if processed:
            self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

        self._log_flush_job = self.after(self._log_flush_interval_ms, self._flush_log_queue)

    def _log(self, message: str, level: str = "detail"):
        self._log_records.append((level, message))
        if hasattr(self, "_log_queue"):
            try:
                self._log_queue.put_nowait((level, message))
            except Exception:
                pass

    def _stop_generation(self):
        if hasattr(self, 'stop_event'):
            self.stop_event.set()
            self._set_status("正在停止任务…")
            self._log("正在停止任务…", level="key")
            self.stop_btn.state(["disabled"])

    def _start_generation(self):
        name = self.name_var.get().strip()
        version = self.version_var.get().strip()
        project_dir = self.project_dir_var.get().strip()
        output_path = self.output_path_var.get().strip()
        
        if not all([name, version, project_dir, output_path]):
            messagebox.showwarning("提示", "请填写所有必填信息！")
            return
            
        if not os.path.isdir(project_dir):
            messagebox.showerror("错误", "项目目录不存在！")
            return

        self._task_start_ts = time.time()
        self._last_output_path = output_path
        self._refresh_open_buttons()

        self._set_running(True)
        self.progress_var.set(0)
        self._clear_log()
        self._set_status("准备开始…")
        self._log("任务已启动", level="key")
        
        self.stop_event = threading.Event()
        
        # Pass current custom exclusions
        custom_dirs = list(self.custom_excluded_dirs)
        custom_exts = list(self.custom_excluded_exts)
        remove_comments = self.remove_comments_var.get()
        
        thread = threading.Thread(
            target=self._process_task,
            args=(name, version, project_dir, output_path, custom_dirs, custom_exts, remove_comments),
            daemon=True
        )
        thread.start()
        
    def _process_task(self, name, version, project_dir, output_path, custom_dirs=None, custom_exts=None, remove_comments=False):
        check_cancel = lambda: self.stop_event.is_set()
        start_time = time.time()
        
        try:
            self._set_progress(0)
            self._set_status("开始扫描文件…")
            self._log(f"开始扫描目录：{project_dir}", level="key")
            if custom_dirs or custom_exts:
                self._log(f"使用自定义排除：{len(custom_dirs or [])} 个目录, {len(custom_exts or [])} 个后缀")
            
            def scan_callback(count, current_path):
                if check_cancel():
                    return
                base = os.path.basename(str(current_path)) if current_path != "Done" else "完成"
                msg = f"扫描中：已找到 {count} 个文件（{base}）"
                self._set_status(msg)
                self._set_progress(min(18, 6 + (count % 12)))
            
            scanner = Scanner(project_dir, custom_excluded_dirs=custom_dirs, custom_excluded_exts=custom_exts)
            files = scanner.scan(check_cancel, scan_callback)
            
            if check_cancel():
                raise Exception("任务已取消")
            
            scan_duration = time.time() - start_time
            self._set_metric(self.metric_files_var, str(len(files)))
            self._log(f"扫描完成：{len(files)} 个文件，耗时 {scan_duration:.2f} 秒", level="key")
            
            if not files:
                raise Exception("未找到符合条件的代码文件！")
            
            self._set_status("正在读取文件内容…")
            self._set_progress(25)
            read_start_time = time.time()
            total_lines = 0
            non_empty_files = 0
            results: list[tuple[str, str] | None] = [None] * len(files)

            def worker(idx: int, path: pathlib.Path):
                if check_cancel():
                    return idx, path.name, "", 0, False

                content = Scanner.read_file_content(path)
                if remove_comments:
                    content = Scanner.remove_code_comments(content, path.suffix)

                if not content.strip():
                    return idx, path.name, "", 0, False

                return idx, path.name, content, len(content.splitlines()), True

            max_workers = min(32, max(4, (os.cpu_count() or 4) * 2))
            futures = []
            completed = 0
            last_name = ""

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for i, f in enumerate(files):
                    futures.append(executor.submit(worker, i, f))

                for fut in as_completed(futures):
                    if check_cancel():
                        for f in futures:
                            f.cancel()
                        raise Exception("任务已取消")

                    idx, name, content, lines, ok = fut.result()
                    last_name = name
                    completed += 1

                    if ok:
                        results[idx] = (name, content)
                        total_lines += lines
                        non_empty_files += 1

                    if completed % 25 == 0 or completed == len(files):
                        prog = 25 + (completed / max(1, len(files))) * 35
                        self._set_status(f"读取中（{completed}/{len(files)}）：{last_name}")
                        self._set_progress(prog)

            file_contents = [r for r in results if r is not None]
            results.clear()
            del results
            
            read_duration = time.time() - read_start_time
            self._set_metric(self.metric_non_empty_files_var, str(non_empty_files))
            self._set_metric(self.metric_lines_var, str(total_lines))
            self._log(f"读取完成：有效 {non_empty_files} 个文件，耗时 {read_duration:.2f} 秒", level="key")
            
            self._set_status("正在排版并生成 PDF…")
            self._set_progress(70)
            gen_start_time = time.time()
            generator = PDFGenerator(output_path, name, version)
            total_pages, generated_pages = generator.generate(file_contents, check_cancel)
            
            if check_cancel():
                raise Exception("任务已取消")
            
            gen_duration = time.time() - gen_start_time
            self._set_metric(self.metric_total_pages_var, str(total_pages))
            self._set_metric(self.metric_output_pages_var, f"{generated_pages}" if total_pages <= 60 else f"{generated_pages}/{total_pages}")
            self._log(f"生成完成：输出 {generated_pages} 页，耗时 {gen_duration:.2f} 秒", level="key")
            self._log(f"文件保存至：{output_path}", level="key")
                
            self._set_status("完成")
            self._set_progress(100)
            
            total_duration = time.time() - start_time
            self._log(f"总耗时：{total_duration:.2f} 秒", level="key")
            self._last_output_path = output_path
            self.after(0, self._refresh_open_buttons)
            
            self.after(0, lambda: messagebox.showinfo("成功", f"文档生成成功！\n共 {generated_pages} 页\n代码总行数: {total_lines}"))
            
        except Exception as e:
            if str(e) == "任务已取消":
                self._log("任务已取消", level="key")
                self._set_status("任务已取消")
                self._set_progress(0)
            else:
                self._log(f"错误：{str(e)}", level="danger")
                self._set_status("发生错误")
                self.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            self.after(0, lambda: self._set_running(False))

    def _set_running(self, running: bool):
        if running:
            self.generate_btn.state(["disabled"])
            self.ai_btn.state(["disabled"])
            self.stop_btn.state(["!disabled"])
            self.name_entry.state(["disabled"])
            self.version_entry.state(["disabled"])
            self.project_entry.state(["disabled"])
            self.output_entry.state(["disabled"])
            self.open_pdf_btn.state(["disabled"])
            self._start_elapsed_timer()
        else:
            self.generate_btn.state(["!disabled"])
            self.ai_btn.state(["!disabled"])
            self.stop_btn.state(["disabled"])
            self.name_entry.state(["!disabled"])
            self.version_entry.state(["!disabled"])
            self.project_entry.state(["!disabled"])
            self.output_entry.state(["!disabled"])
            self._stop_elapsed_timer()
            self._refresh_open_buttons()

    def _start_elapsed_timer(self):
        self._elapsed_job = None

        def tick():
            if self._task_start_ts is None:
                self.elapsed_var.set("00:00")
            else:
                delta = int(time.time() - self._task_start_ts)
                mm, ss = divmod(delta, 60)
                hh, mm = divmod(mm, 60)
                if hh:
                    self.elapsed_var.set(f"{hh:02d}:{mm:02d}:{ss:02d}")
                else:
                    self.elapsed_var.set(f"{mm:02d}:{ss:02d}")
            self._elapsed_job = self.after(500, tick)

        tick()

    def _stop_elapsed_timer(self):
        if getattr(self, "_elapsed_job", None):
            try:
                self.after_cancel(self._elapsed_job)
            except Exception:
                pass
        self._elapsed_job = None

    def _set_status(self, text: str):
        self.after(0, lambda: self.status_var.set(text))

    def _set_progress(self, value: float):
        self.after(0, lambda: self.progress_var.set(max(0, min(100, value))))

    def _set_metric(self, var: tk.StringVar, value: str):
        self.after(0, lambda: var.set(value))

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()
