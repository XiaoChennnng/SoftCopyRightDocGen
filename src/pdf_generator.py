import os
from collections import deque
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

class PDFGenerator:
    def __init__(self, output_path, software_name, version):
        self.output_path = output_path
        self.software_name = software_name
        self.version = version
        
        # Layout configurations
        self.page_width, self.page_height = A4
        self.margin_top = 25 * mm
        self.margin_bottom = 20 * mm
        self.margin_left = 20 * mm
        self.margin_right = 20 * mm
        
        self.content_width = self.page_width - self.margin_left - self.margin_right
        self.content_height = self.page_height - self.margin_top - self.margin_bottom
        
        # Font configurations
        self.font_name = 'SimSun'
        self.font_size = 9  # Small enough to fit long lines and many lines
        self.leading = 11   # Line spacing
        
        self._register_font()
        
        # Calculate max lines per page
        # Ensure at least 50 lines. 
        # Available height / leading
        self.lines_per_page = int(self.content_height / self.leading)
        # Verify it meets requirement
        if self.lines_per_page < 50:
            print(f"Warning: Calculated lines per page ({self.lines_per_page}) is less than 50. Adjusting margins or font.")
            # Adjust strategy if needed, but 9pt/11leading on A4 gives ~68 lines. Safe.

        # Calculate char width for wrapping (approximate for monospaced/SimSun)
        self.avg_char_width = pdfmetrics.stringWidth('A', self.font_name, self.font_size)
        self.chars_per_line = int(self.content_width / self.avg_char_width)
        self._char_width_cache = {}

    def _register_font(self):
        try:
            # Try common Windows font paths with proper configuration
            # (FontName, FilePath, SubFontIndex for TTC)
            font_candidates = [
                ('SimSun', r'C:\Windows\Fonts\simsun.ttc', 0),      # Standard SimSun (TTC index 0)
                ('SimSun', r'C:\Windows\Fonts\simsun.ttf', None),   # Fallback if TTF exists
                ('SimHei', r'C:\Windows\Fonts\simhei.ttf', None),   # SimHei as backup
                ('Microsoft YaHei', r'C:\Windows\Fonts\msyh.ttc', 0),
                ('Microsoft YaHei', r'C:\Windows\Fonts\msyh.ttf', None),
            ]
            
            found = False
            for name, path, index in font_candidates:
                if os.path.exists(path):
                    try:
                        if path.lower().endswith('.ttc'):
                            # Register TTC font with specific index
                            # subfontIndex=0 usually maps to the Regular version
                            font = TTFont(name, path, subfontIndex=index if index is not None else 0)
                        else:
                            font = TTFont(name, path)
                            
                        pdfmetrics.registerFont(font)
                        self.font_name = name # Use the name we successfully registered
                        print(f"Successfully registered font: {name} from {path}")
                        found = True
                        break
                    except Exception as e:
                        print(f"Failed to register font {name} from {path}: {e}")
                        continue
            
            if not found:
                print("Warning: Chinese font not found. Fallback to Helvetica (no Chinese support).")
                self.font_name = 'Helvetica'
        except Exception as e:
            print(f"Error registering font: {e}")
            self.font_name = 'Helvetica'

    def generate(self, file_contents: list[tuple[str, str]], check_cancel=None):
        """
        file_contents: list of (filename, content)
        check_cancel: function to check if cancelled
        """
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

        if total_pages <= 60:
            selected_pages = first_pages
        else:
            selected_pages = first_pages[:30] + list(tail_pages)

        c = canvas.Canvas(self.output_path, pagesize=A4)
        c.setFont(self.font_name, self.font_size)
        
        for i, page_lines in enumerate(selected_pages):
            if check_cancel and check_cancel():
                return 0, 0
            
            # Ensure font is set for each page to avoid state loss after showPage
            c.setFont(self.font_name, self.font_size)
                
            page_num = i + 1
            self._draw_header(c, page_num)
            
            # Re-set font for content just in case header modified it (though header uses saveState)
            c.setFont(self.font_name, self.font_size)
            self._draw_content(c, page_lines)
            
            c.showPage()
            
        c.save()
        return total_pages, len(selected_pages)

    def _iter_pages(self, file_contents, check_cancel=None):
        current_page_lines = []
        current_lines_count = 0
        
        for filename, content in file_contents:
            if check_cancel and check_cancel():
                return
                
            header_line = f"--- File: {filename} ---"
            wrapped_header = self._wrap_line(header_line)
            
            for line in wrapped_header:
                if current_lines_count >= self.lines_per_page:
                    yield current_page_lines
                    current_page_lines = []
                    current_lines_count = 0
                current_page_lines.append(line)
                current_lines_count += 1
            
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
        if not line:
            return [""]

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
        header_y = self.page_height - 15 * mm
        c.saveState()
        c.setFont(self.font_name, 10)
        
        # Format: [Software Name] [Version]          Page X
        left_text = f"{self.software_name} {self.version}"
        right_text = f"第 {page_num} 页"
        
        c.drawString(self.margin_left, header_y, left_text)
        
        # Right align page number
        page_num_width = c.stringWidth(right_text, self.font_name, 10)
        c.drawString(self.page_width - self.margin_right - page_num_width, header_y, right_text)
        
        # Optional: Draw a line below header
        c.line(self.margin_left, header_y - 2*mm, self.page_width - self.margin_right, header_y - 2*mm)
        
        c.restoreState()

    def _draw_content(self, c, lines):
        y = self.page_height - self.margin_top
        for line in lines:
            y -= self.leading
            c.drawString(self.margin_left, y, line)
