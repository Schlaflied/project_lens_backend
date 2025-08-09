# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 1.5 - 功能深化版 (Feature Enhancement)
# 描述: 集成了Google Search API实现自动信息聚合，并支持个人化简历分析。
# -----------------------------------------------------------------------------

import os
import requests
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- 1. 初始化和配置 ---
app = Flask(__name__)
CORS(app)

# --- 2. API密钥配置 ---
try:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
    SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")

    if not all([GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID]):
        raise ValueError("One or more API keys are missing from environment variables.")
    
    genai.configure(api_key=GEMINI_API_KEY)
    print("所有API密钥配置成功！")
except Exception as e:
    print(f"API密钥配置失败: {e}")

# --- 3. 辅助函数：执行Google搜索 ---
def perform_google_search(query, api_key, cse_id):
    """调用Google Custom Search API并返回结果摘要和链接。"""
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': api_key,
        'cx': cse_id,
        'q': query,
        'num': 5 # 获取前5个结果
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status() # 如果请求失败则抛出异常
        search_results = response.json()
        
        snippets = [item.get('snippet', '') for item in search_results.get('items', [])]
        sources = [{'title': item.get('title'), 'link': item.get('link')} for item in search_results.get('items', [])]
        
        return snippets, sources
    except requests.exceptions.RequestException as e:
        print(f"Google搜索请求失败: {e}")
        return [], []

# --- 4. 核心AI指令 (Prompt) ---
PROMPT_TEMPLATE = """
As 'Project Lens', an expert AI assistant for job seekers, your task is to generate a detailed analysis report based on the provided information.
**Crucially, you must generate the entire response strictly in {output_language}.**

**Information Provided:**
1.  **Search Snippets:** The following are recent search results about the company.
    ```
    {search_context}
    ```
2.  **Applicant's Resume/Bio (if provided):**
    ```
    {resume_text}
    ```

**Your Task:**
Synthesize all the information above to create a comprehensive report.

The report should include:
1.  **Culture-Fit Analysis**: Based on the search results, analyze the company's Work-Life Balance, Team Collaboration Style, and Growth Opportunities.
2.  **Corporate Investigator Report**: Identify potential 'red flags' or risks.
3.  **Personalized Match Analysis (if resume is provided)**: Analyze how well the applicant's background and skills match the company culture. Provide actionable advice. If no resume is provided, state that this section is unavailable.
4.  **Risk Assessment**: Conclude with a clear risk rating (Low, Medium, or High).

Please structure your entire response in Markdown format.
"""

# --- 5. API路由 ---
@app.route('/analyze', methods=['POST'])
def analyze_company_text():
    print("--- V1.5 Analysis request received! ---")
    try:
        data = request.get_json()
        company_name = data.get('companyName')
        resume_text = data.get('resumeText', 'No resume provided.')
        lang_code = data.get('language', 'en')

        if not company_name:
            return jsonify({"error": "Company name is required."}), 400

        # --- a. 执行Google搜索 ---
        print(f"Searching for: {company_name}")
        search_queries = [
            f'"{company_name}" company culture review',
            f'"{company_name}" work life balance',
            f'site:glassdoor.com "{company_name}" reviews'
        ]
        all_snippets = []
        all_sources = []
        for query in search_queries:
            snippets, sources = perform_google_search(query, SEARCH_API_KEY, SEARCH_ENGINE_ID)
            all_snippets.extend(snippets)
            all_sources.extend(sources)
        
        search_context = "\n".join(all_snippets) if all_snippets else "No information found in web search."
        print(f"Found {len(all_snippets)} snippets.")

        # --- b. 构建并调用Gemini ---
        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        output_language = language_instructions.get(lang_code, 'English')

        full_prompt = PROMPT_TEMPLATE.format(
            output_language=output_language,
            search_context=search_context,
            resume_text=resume_text
        )

        model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
        response = model.generate_content(full_prompt)
        
        print("Successfully received response from Gemini API.")
        return jsonify({"analysis": response.text, "sources": all_sources})

    except Exception as e:
        print(f"!!! An unexpected error occurred: {e} !!!")
        return jsonify({"error": "An internal server error occurred."}), 500

# --- 本地测试启动点 ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)




