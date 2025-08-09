# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 3.0 - Flask移植成功版 (Flask Transplant Success)
# 描述: 参照「灵感方舟」的成功经验，将应用核心从FastAPI切换为Flask，
#       以解决在Cloud Run上的部署问题。
# -----------------------------------------------------------------------------

import os
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- 1. 初始化和配置 (使用Flask) ---
app = Flask(__name__)
CORS(app) # 启用CORS，允许前端访问

# --- 2. 配置Google Gemini API (参照Plot Ark的成功配置) ---
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("FATAL ERROR: GOOGLE_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    print("Gemini API 配置成功！")
except Exception as e:
    print(f"Gemini API 配置失败: {e}")

# --- 3. 设计核心AI指令 (Prompt) ---
PROMPT_TEMPLATE = """
As 'Project Lens', an expert AI assistant for job seekers, your task is to analyze the provided text about a company.

First, perform a detailed **Culture-Fit Analysis**. Focus on:
- **Work-Life Balance:** Is there evidence of long hours, high pressure, or a relaxed environment?
- **Team Collaboration Style:** Does it seem collaborative, competitive, or individualistic?
- **Growth Opportunities:** What are the potential learning and career development prospects?

Second, and most importantly, act as a sharp **Corporate Investigator** to identify any potential 'red flags' that might suggest this is a shell company or a scam. Based ONLY on the provided text, look for:
- Vague or overly glamorous job descriptions.
- Unusually high pay for low skill requirements.
- Any mention of upfront fees, training costs, or security deposits.
- Inconsistencies in company descriptions or business models.

Finally, conclude with a clear, actionable summary:
- **[CULTURE SUMMARY]:** A brief overview of the company culture.
- **[RISK ASSESSMENT]:** Rate the risk as **Low, Medium, or High**. Explain your reasoning based on the red flags found.

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
    """核心功能端点，接收文本并返回AI分析。"""
    print("--- Analysis request received! ---")
    try:
        data = request.get_json()
        if not data or 'text' not in data or not data['text'].strip():
            return jsonify({"error": "Invalid request. 'text' field is required."}), 400

        user_text = data['text']
        print("Data validated. Building prompt for Gemini API...")

        # 使用我们为职场透镜设计的Prompt
        full_prompt = PROMPT_TEMPLATE.format(user_text=user_text)

        # 【关键】使用Plot Ark中验证成功的模型和安全设置
        model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]

        response = model.generate_content(full_prompt, safety_settings=safety_settings)
        
        # 检查响应是否被拦截
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

