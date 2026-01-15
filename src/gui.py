import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import pathlib
import time
from .scanner import Scanner
from .pdf_generator import PDFGenerator

class MainApplication(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("软著源码文档生成助手")
        self.geometry("600x550")
        self.resizable(False, False)
        
        self._init_ui()
        
    def _init_ui(self):
        # Styles
        style = ttk.Style()
        style.configure("TButton", padding=6)
        style.configure("TLabel", padding=5)
        
        # Main Container
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 1. Software Info
        info_frame = ttk.LabelFrame(main_frame, text="软件信息", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(info_frame, text="软件全称:").grid(row=0, column=0, sticky=tk.W)
        self.name_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.name_var, width=40).grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(info_frame, text="版本号:").grid(row=1, column=0, sticky=tk.W)
        self.version_var = tk.StringVar(value="V1.0.0")
        ttk.Entry(info_frame, textvariable=self.version_var, width=40).grid(row=1, column=1, padx=5, pady=5)
        
        # 2. Paths
        path_frame = ttk.LabelFrame(main_frame, text="路径设置", padding="10")
        path_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Project Dir
        ttk.Label(path_frame, text="项目目录:").grid(row=0, column=0, sticky=tk.W)
        self.project_dir_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.project_dir_var, width=35).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(path_frame, text="浏览", command=self._browse_project_dir).grid(row=0, column=2, padx=5)
        
        # Output File
        ttk.Label(path_frame, text="保存位置:").grid(row=1, column=0, sticky=tk.W)
        self.output_path_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.output_path_var, width=35).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(path_frame, text="浏览", command=self._browse_output_file).grid(row=1, column=2, padx=5)
        
        # 3. Actions
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(fill=tk.X, pady=10)
        
        self.generate_btn = ttk.Button(action_frame, text="开始生成文档", command=self._start_generation)
        self.generate_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5), ipady=5)
        
        self.stop_btn = ttk.Button(action_frame, text="停止", command=self._stop_generation, state='disabled')
        self.stop_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0), ipady=5)
        
        # 4. Progress and Log
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=(10, 5))
        
        self.status_var = tk.StringVar(value="准备就绪")
        ttk.Label(main_frame, textvariable=self.status_var, foreground="gray").pack(anchor=tk.W)
        
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        self.log_text = tk.Text(log_frame, height=10, state='disabled', font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

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

    def _log(self, message):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
        
    def _stop_generation(self):
        if hasattr(self, 'stop_event'):
            self.stop_event.set()
            self._log("正在停止任务...")
            self.stop_btn.config(state='disabled')

    def _start_generation(self):
        # Validation
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
            
        # Disable button
        self.generate_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.progress_var.set(0)
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')
        
        self.stop_event = threading.Event()
        
        # Start thread
        thread = threading.Thread(
            target=self._process_task,
            args=(name, version, project_dir, output_path),
            daemon=True
        )
        thread.start()
        
    def _process_task(self, name, version, project_dir, output_path):
        check_cancel = lambda: self.stop_event.is_set()
        start_time = time.time()
        
        try:
            self._update_status("开始扫描文件...", 0)
            self._log(f"开始扫描目录: {project_dir}")
            
            def scan_callback(count, current_path):
                if check_cancel(): return
                msg = f"已找到 {count} 个文件... 正在扫描: {os.path.basename(str(current_path))}"
                self._update_status(msg, 10)
            
            scanner = Scanner(project_dir)
            files = scanner.scan(check_cancel, scan_callback)
            
            if check_cancel():
                raise Exception("任务已取消")
            
            scan_duration = time.time() - start_time
            self._log(f"扫描完成，耗时 {scan_duration:.2f} 秒。")
            self._log(f"共发现 {len(files)} 个符合条件的代码文件。")
            
            if not files:
                raise Exception("未找到符合条件的代码文件！")
            
            self._update_status("正在读取文件内容...", 30)
            file_contents = []
            total_lines = 0
            read_start_time = time.time()
            
            for i, f in enumerate(files):
                if check_cancel():
                    raise Exception("任务已取消")
                    
                content = Scanner.read_file_content(f)
                if content.strip(): # Ignore empty files
                    file_contents.append((f.name, content))
                    total_lines += len(content.splitlines())
                
                # Update progress subtly
                if i % 10 == 0:
                    prog = 30 + (i / len(files)) * 30
                    msg = f"读取中 ({i+1}/{len(files)}): {f.name}"
                    self._update_status(msg, prog)
            
            read_duration = time.time() - read_start_time
            self._log(f"读取完成，耗时 {read_duration:.2f} 秒。")
            self._log(f"有效读取 {len(file_contents)} 个文件（已过滤空文件）。")
            self._log(f"**代码总行数: {total_lines} 行**")
            
            self._update_status("正在排版并生成PDF...", 70)
            gen_start_time = time.time()
            generator = PDFGenerator(output_path, name, version)
            total_pages, generated_pages = generator.generate(file_contents, check_cancel)
            
            if check_cancel():
                raise Exception("任务已取消")
            
            gen_duration = time.time() - gen_start_time
            self._log(f"生成完成，耗时 {gen_duration:.2f} 秒。")
                
            self._update_status("完成！", 100)
            self._log(f"PDF生成成功！")
            self._log(f"总页数（排版后）：{total_pages}")
            self._log(f"实际输出页数：{generated_pages}")
            self._log(f"文件保存至：{output_path}")
            
            total_duration = time.time() - start_time
            self._log(f"总耗时: {total_duration:.2f} 秒")
            
            self.after(0, lambda: messagebox.showinfo("成功", f"文档生成成功！\n共 {generated_pages} 页\n代码总行数: {total_lines}"))
            
        except Exception as e:
            if str(e) == "任务已取消":
                self._log("任务已取消。")
                self._update_status("任务已取消", 0)
            else:
                self._log(f"错误: {str(e)}")
                import traceback
                traceback.print_exc()
                self.after(0, lambda: messagebox.showerror("错误", str(e)))
        finally:
            self.after(0, lambda: self.generate_btn.config(state='normal'))
            self.after(0, lambda: self.stop_btn.config(state='disabled'))
            if not self.stop_event.is_set():
                self.after(0, lambda: self.status_var.set("就绪"))

    def _update_status(self, text, progress):
        self.after(0, lambda: self.status_var.set(text))
        self.after(0, lambda: self.progress_var.set(progress))
        self.after(0, lambda: self._log(text))

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()
