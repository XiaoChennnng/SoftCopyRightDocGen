import os
from collections import deque
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 字体注册单例，避免重复注册开销
_FONT_REGISTERED = False
_REGISTERED_FONT_NAME = 'Helvetica'

class PDFGenerator:
    def __init__(self, output_path, software_name, version):
        """初始化 PDF 生成器"""
        self.output_path = output_path
        self.software_name = software_name
        self.version = version
        
        # 页面布局配置 (A4 纸张)
        self.page_width, self.page_height = A4
        self.margin_top = 25 * mm
        self.margin_bottom = 20 * mm
        self.margin_left = 20 * mm
        self.margin_right = 20 * mm
        
        self.content_width = self.page_width - self.margin_left - self.margin_right
        self.content_height = self.page_height - self.margin_top - self.margin_bottom
        
        # 字体与排版配置
        self.font_name = 'SimSun'
        self.font_size = 9  # 较小的字体以容纳更多内容
        self.leading = 11   # 行间距
        
        self._register_font()
        
        # 计算每页最大行数 (软著要求每页至少 50 行)
        self.lines_per_page = int(self.content_height / self.leading)
        if self.lines_per_page < 50:
            print(f"警告: 计算的每页行数 ({self.lines_per_page}) 小于 50。请调整页边距或字号。")

        # 字符宽度预估，用于换行计算
        self.avg_char_width = pdfmetrics.stringWidth('A', self.font_name, self.font_size)
        self.chars_per_line = int(self.content_width / self.avg_char_width)
        self._char_width_cache = {}

    def _register_font(self):
        """注册中文字体（单例模式）"""
        global _FONT_REGISTERED, _REGISTERED_FONT_NAME
        
        if _FONT_REGISTERED:
            self.font_name = _REGISTERED_FONT_NAME
            return
            
        try:
            # 常见的 Windows 字体候选路径
            font_candidates = [
                ('SimSun', r'C:\Windows\Fonts\simsun.ttc', 0),      # 宋体 (TTC 索引 0)
                ('SimSun', r'C:\Windows\Fonts\simsun.ttf', None),   # 宋体 TTF
                ('SimHei', r'C:\Windows\Fonts\simhei.ttf', None),   # 黑体
                ('Microsoft YaHei', r'C:\Windows\Fonts\msyh.ttc', 0),
                ('Microsoft YaHei', r'C:\Windows\Fonts\msyh.ttf', None),
            ]
            
            found = False
            for name, path, index in font_candidates:
                if os.path.exists(path):
                    try:
                        if path.lower().endswith('.ttc'):
                            font = TTFont(name, path, subfontIndex=index if index is not None else 0)
                        else:
                            font = TTFont(name, path)
                            
                        pdfmetrics.registerFont(font)
                        self.font_name = name
                        print(f"成功注册字体: {name}, 路径: {path}")
                        found = True
                        break
                    except Exception as e:
                        print(f"注册字体 {name} 失败: {e}")
                        continue
            
            if not found:
                print("警告: 未找到中文字体，回退至 Helvetica (不支持中文)。")
                self.font_name = 'Helvetica'
        except Exception as e:
            print(f"注册字体时出错: {e}")
            self.font_name = 'Helvetica'
        
        _FONT_REGISTERED = True
        _REGISTERED_FONT_NAME = self.font_name

    def generate(self, file_contents: list[tuple[str, str]], check_cancel=None):
        """生成 PDF 文档，保留前 30 页和后 30 页（软著要求）"""
        total_pages = 0
        first_pages: list[list[str]] = []
        tail_pages: deque[list[str]] = deque(maxlen=30)

        for page_lines in self._iter_pages(file_contents, check_cancel):
            if check_cancel and check_cancel():
                return 0, 0
            total_pages += 1
            if total_pages <= 60:
                first_pages.append(page_lines)
            else:
                tail_pages.append(page_lines)

        if total_pages == 0:
            return 0, 0

        # 合并需要导出的页面 (前 30 + 后 30 或全部)
        if total_pages <= 60:
            selected_pages = first_pages
        else:
            selected_pages = first_pages[:30] + list(tail_pages)

        c = canvas.Canvas(self.output_path, pagesize=A4)
        print(f"[PDF] 开始生成，使用字体: {self.font_name}, 软件名: {self.software_name}")
        c.setFont(self.font_name, self.font_size)
        
        for i, page_lines in enumerate(selected_pages):
            if check_cancel and check_cancel():
                return 0, 0
            
            c.setFont(self.font_name, self.font_size)
            page_num = i + 1
            self._draw_header(c, page_num)
            
            c.setFont(self.font_name, self.font_size)
            self._draw_content(c, page_lines)
            
            c.showPage()
            
        c.save()
        return total_pages, len(selected_pages)

    def _iter_pages(self, file_contents, check_cancel=None):
        """迭代生成页面内容，处理自动换行"""
        current_page_lines = []
        current_lines_count = 0
        
        for filename, content in file_contents:
            if check_cancel and check_cancel():
                return
                
            # 写入文件头分隔符
            header_line = f"--- File: {filename} ---"
            wrapped_header = self._wrap_line(header_line)
            
            for line in wrapped_header:
                if current_lines_count >= self.lines_per_page:
                    yield current_page_lines
                    current_page_lines = []
                    current_lines_count = 0
                current_page_lines.append(line)
                current_lines_count += 1
            
            # 处理制表符和连续空行
            content = content.replace('\t', '    ')
            lines = content.splitlines()
            
            cleaned_lines = []
            last_empty = False
            for l in lines:
                is_empty = not l.strip()
                if is_empty:
                    if not last_empty:
                        cleaned_lines.append("")
                    last_empty = True
                else:
                    cleaned_lines.append(l)
                    last_empty = False
            
            # 写入文件内容并自动换行
            for line in cleaned_lines:
                wrapped = self._wrap_line(line)
                for w_line in wrapped:
                    if current_lines_count >= self.lines_per_page:
                        yield current_page_lines
                        current_page_lines = []
                        current_lines_count = 0
                    current_page_lines.append(w_line)
                    current_lines_count += 1
                    
        if current_page_lines:
            yield current_page_lines

    def _wrap_line(self, line):
        """根据页面宽度进行物理换行计算"""
        if not line:
            return [""]

        # 快速路径：纯 ASCII 且长度安全
        if line.isascii() and len(line) <= self.chars_per_line:
            return [line]

        full_width = pdfmetrics.stringWidth(line, self.font_name, self.font_size)
        if full_width <= self.content_width:
            return [line]

        wrapped_lines = []
        current_chars = []
        current_width = 0.0
        cache = self._char_width_cache

        for char in line:
            width = cache.get(char)
            if width is None:
                width = pdfmetrics.stringWidth(char, self.font_name, self.font_size)
                cache[char] = width

            if current_chars and current_width + width > self.content_width:
                wrapped_lines.append("".join(current_chars))
                current_chars = [char]
                current_width = width
            else:
                current_chars.append(char)
                current_width += width

        if current_chars:
            wrapped_lines.append("".join(current_chars))

        return wrapped_lines

    def _draw_header(self, c, page_num):
        """绘制页眉 (软件名、版本号、页码)"""
        header_y = self.page_height - 15 * mm
        c.saveState()
        c.setFont(self.font_name, 10)
        
        left_text = f"{self.software_name} {self.version}"
        right_text = f"第 {page_num} 页"
        
        c.drawString(self.margin_left, header_y, left_text)
        
        # 右对齐页码
        page_num_width = c.stringWidth(right_text, self.font_name, 10)
        c.drawString(self.page_width - self.margin_right - page_num_width, header_y, right_text)
        
        # 绘制分割线
        c.line(self.margin_left, header_y - 2*mm, self.page_width - self.margin_right, header_y - 2*mm)
        
        c.restoreState()

    def _draw_content(self, c, lines):
        """在页面上绘制内容行"""
        y = self.page_height - self.margin_top
        for line in lines:
            y -= self.leading
            c.drawString(self.margin_left, y, line)
