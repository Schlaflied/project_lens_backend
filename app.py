# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 4.0 - 多语言输出版 (Multi-Language Output)
# 描述: 接收前端的语言指令，并命令AI用对应的语言生成报告。
# -----------------------------------------------------------------------------

import os
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- 1. 初始化和配置 (使用Flask) ---
app = Flask(__name__)
CORS(app) # 启用CORS，允许前端访问

# --- 2. 配置Google Gemini API ---
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("FATAL ERROR: GOOGLE_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    print("Gemini API 配置成功！")
except Exception as e:
    print(f"Gemini API 配置失败: {e}")

# --- 3. 设计核心AI指令 (Prompt) ---
# 【关键改动】增加了一个 {output_language} 的占位符
PROMPT_TEMPLATE = """
As 'Project Lens', an expert AI assistant for job seekers, your task is to generate a detailed analysis report based on the provided text about a company.
**Crucially, you must generate the entire response strictly in {output_language}.**

The report should include:
1.  **Culture-Fit Analysis**: Focus on Work-Life Balance, Team Collaboration Style, and Growth Opportunities.
2.  **Corporate Investigator Report**: Identify potential 'red flags' that might suggest a shell company or a scam. Look for vague job descriptions, mismatched pay, or requests for fees.
3.  **Risk Assessment**: Conclude with a clear risk rating (Low, Medium, or High) and explain your reasoning.

Please structure your entire response in Markdown format for clear presentation.

Analyze the following text:
---
{user_text}
---
"""

# --- 4. 定义API路由 ---
@app.route('/')
def index():
    """API健康检查，确认服务正在运行。"""
    return jsonify({"status": "online", "message": "Welcome to the Project Lens API!"})

@app.route('/analyze', methods=['POST'])
def analyze_company_text():
    """核心功能端点，接收文本和语言指令，并返回AI分析。"""
    print("--- Analysis request received! ---")
    try:
        data = request.get_json()
        if not data or 'text' not in data or not data['text'].strip():
            return jsonify({"error": "Invalid request. 'text' field is required."}), 400

        user_text = data['text']
        # 【关键改动】获取前端传来的语言码，如果没有则默认为英文
        lang_code = data.get('language', 'en')
        print(f"Data validated. Language requested: {lang_code}")

        # 创建一个字典，将语言码映射为给AI的明确指令
        language_instructions = {
            'en': 'English',
            'zh-CN': 'Simplified Chinese (简体中文)',
            'zh-TW': 'Traditional Chinese (繁體中文)'
        }
        output_language = language_instructions.get(lang_code, 'English')

        # 将用户文本和语言指令都填入Prompt模板
        full_prompt = PROMPT_TEMPLATE.format(user_text=user_text, output_language=output_language)

        model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        print(f"Sending request to Gemini, asking for output in {output_language}...")
        response = model.generate_content(full_prompt, safety_settings=safety_settings)
        
        if not response.parts:
            block_reason = response.prompt_feedback.block_reason.name if response.prompt_feedback else "Unknown"
            print(f"Response blocked by API. Reason: {block_reason}")
            return jsonify({"error": f"Content blocked by safety system. Reason: {block_reason}"}), 400

        print("Successfully received response from Gemini API.")
        return jsonify({"analysis": response.text})

    except Exception as e:
        print(f"!!! An unexpected error occurred: {e} !!!")
        return jsonify({"error": "An internal server error occurred."}), 500

# --- 5. 本地测试启动点 ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)

