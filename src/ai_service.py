import json
import httpx
from typing import List, Dict, Any, Optional

class AIService:
    # AI 模型服务商配置
    PROVIDERS = {
        "DeepSeek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "type": "openai"
        },
        "Qwen (阿里云)": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "type": "openai"
        },
        "MiniMax": {
            "base_url": "https://api.minimax.chat/v1",
            "model": "abab5.5-chat",
            "type": "openai"
        },
        "Doubao (火山引擎)": {
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "ep-20240604-xxxxx", # 用户需手动填 Endpoint ID
            "type": "openai"
        },
        "Gemini (Google)": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model": "gemini-pro",
            "type": "google"
        },
        "Claude (Anthropic)": {
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-3-opus-20240229",
            "type": "anthropic"
        },
        "Grok (xAI)": {
            "base_url": "https://api.x.ai/v1",
            "model": "grok-beta",
            "type": "openai"
        }
    }

    def __init__(self, provider: str, api_key: str, custom_base_url: str = "", custom_model: str = ""):
        """初始化 AI 服务实例"""
        self.provider = provider
        self.api_key = api_key
        
        config = self.PROVIDERS.get(provider, {})
        self.base_url = custom_base_url or config.get("base_url", "")
        self.model = custom_model or config.get("model", "")
        self.api_type = config.get("type", "openai")

    def suggest_exclusions(self, dirs: List[str], extensions: List[str]) -> Dict[str, List[str]]:
        """向 AI 发送目录结构并获取排除建议"""
        prompt = self._build_prompt(dirs, extensions)
        
        try:
            if self.api_type == "openai":
                return self._call_openai_compatible(prompt)
            elif self.api_type == "anthropic":
                return self._call_anthropic(prompt)
            elif self.api_type == "google":
                return self._call_google(prompt)
            else:
                raise ValueError(f"不支持的 API 类型: {self.api_type}")
        except Exception as e:
            raise Exception(f"AI 请求失败: {str(e)}")

    def _build_prompt(self, dirs: List[str], extensions: List[str]) -> str:
        """构建 AI 提示词"""
        return f"""
你是一位拥有 10 年经验的资深软件著作权审查专家。你的任务是协助开发者从项目目录中精准剔除“非源代码”文件，以生成符合软著申请要求的精简代码文档。

请基于以下项目摘要信息，分析该项目可能使用的技术栈（如 Vue/React, Python/Django, Java/Spring, C#/.NET 等），并据此制定一份**严格的排除清单**。

【排除原则】
1. **依赖库与包管理**：必须排除 node_modules, venv, .venv, env, site-packages, vendor, pods, Carthage 等。
2. **构建与编译产物**：必须排除 dist, build, out, target, bin, obj, debug, release, __pycache__, .class, .o, .exe, .dll, .so, .dylib 等。
3. **IDE与版本控制**：必须排除 .git, .svn, .idea, .vscode, .vs, .settings 等。
4. **资源文件**：必须排除图片(.png, .jpg, .ico, .svg), 音频(.mp3, .wav), 视频(.mp4), 字体(.ttf, .woff) 等。
5. **非代码文档**：建议排除 logs, docs, doc, report, coverage, .log, .pdf, .zip, .rar, .7z 等。
6. **配置文件**：对于非核心逻辑的配置（如 package-lock.json, yarn.lock, .gitignore, .editorconfig, .DS_Store）建议排除。

【待分析项目摘要】
- **一级目录列表**: {', '.join(dirs)}
- **文件后缀列表**: {', '.join(extensions)}

【输出要求】
请返回纯 JSON 格式数据，不要包含 Markdown 标记（如 ```json）。结构如下：
{{
    "analysis": "简短分析：这是一个基于 [技术栈] 的项目...",
    "excluded_dirs": ["node_modules", "dist", "build", ...],
    "excluded_extensions": [".exe", ".dll", ".png", ".lock", ...]
}}

注意：
- 哪怕目录列表中没有出现 node_modules，如果你判断它是 Node 项目且后缀中有 .js/.ts，也请在 excluded_dirs 中预警性地加上 node_modules（以防子目录中存在）。
- 请保留真正的源码目录（如 src, lib, app, core, utils, components, pages, views, api 等）。
- 宁可多排（非代码），不可错排（核心代码）。
"""

    def _call_openai_compatible(self, prompt: str) -> Dict[str, Any]:
        """调用 OpenAI 兼容接口"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"} if "deepseek" in self.base_url or "aliyun" in self.base_url else None
        }

        response = httpx.post(url, headers=headers, json=data, timeout=30.0)
        response.raise_for_status()
        
        result = response.json()
        content = result['choices'][0]['message']['content']
        return self._parse_json(content)

    def _call_anthropic(self, prompt: str) -> Dict[str, Any]:
        """调用 Anthropic 接口"""
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024
        }
        
        response = httpx.post(url, headers=headers, json=data, timeout=30.0)
        response.raise_for_status()
        
        result = response.json()
        content = result['content'][0]['text']
        return self._parse_json(content)

    def _call_google(self, prompt: str) -> Dict[str, Any]:
        """调用 Google Gemini 接口"""
        # Gemini 使用 query 参数传递 key
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        response = httpx.post(url, headers=headers, json=data, timeout=30.0)
        response.raise_for_status()
        
        result = response.json()
        content = result['candidates'][0]['content']['parts'][0]['text']
        return self._parse_json(content)

    def _parse_json(self, content: str) -> Dict[str, Any]:
        """解析并清洗 AI 返回的 JSON 字符串"""
        content = content.strip()
        # 清除 Markdown 代码块标记
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content.rsplit("\n", 1)[0]
        content = content.replace("```json", "").replace("```", "")
        
        return json.loads(content)
