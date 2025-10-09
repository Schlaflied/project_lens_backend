# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 12.1 - 图标逻辑集成 (Icon Logic Integration)
# 描述: 这个版本没有核心功能变化，主要是为了与前端的图标显示逻辑保持一致，
#       确保后端在处理来源时，能正确识别并标记源类型（linkedin, glassdoor, etc.）。
# -----------------------------------------------------------------------------

import os
import requests
import google.generativeai as genai
import time
import re
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- 1. 初始化和配置 (无变化) ---
app = Flask(__name__)
CORS(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# --- 2. API密钥配置 (无变化) ---
try:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
    SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
    if not all([GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID]):
        raise ValueError("一个或多个API密钥缺失。")
    genai.configure(api_key=GEMINI_API_KEY)
    print("所有API密钥配置成功！")
except Exception as e:
    print(f"API密钥配置失败: {e}")

# --- 3. 辅助函数：执行Google搜索 (无变化) ---
def perform_google_search(query, api_key, cse_id, num_results=4):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        search_results = response.json()
        snippets = [item.get('snippet', '') for item in search_results.get('items', [])]
        sources = [{'title': item.get('title'), 'link': item.get('link')} for item in search_results.get('items', [])]
        return snippets, sources
    except requests.exceptions.RequestException as e:
        print(f"Google搜索请求失败: {e}")
        return [], []

# --- 4. 辅助函数：网页爬虫 (无变化) ---
def scrape_website_for_text(url):
    print(f"🚀 准备爬取网站: {url}")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        for script_or_style in soup(['script', 'style']):
            script_or_style.decompose()
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        print(f"✅ 成功爬取并解析了 {len(text)} 个字符。")
        return text[:5000]
    except Exception as e:
        print(f"❌ 爬取或解析网站时发生错误: {e}")
        return None

# --- 5. 核心AI指令 (Prompt) --- (无变化) ---
PROMPT_TEMPLATE = """
As 'Project Lens', an expert AI assistant for job seekers, your task is to generate a detailed analysis report.
**Crucially, you must generate the entire response strictly as a JSON object and in {output_language}.**

**Citation Rules (VERY IMPORTANT):**
1. The information provided below is structured with a unique `[Source ID: X]`.
2. When you use information from a source, you **MUST** embed its corresponding ID tag (e.g., `[1]`, `[2]`) directly into the text where the information is used.
3. In the final JSON, include a `cited_ids` array containing all unique source IDs you used in the report.

**Information Provided:**
1. **Company & Context:** {company_name}
2. **User-Selected Aspects of Interest:** {aspects_list}
3. **Applicant's Resume/Bio (if provided):**
   ```
   {resume_text}
   ```
4. **Research Data (Search results and website content):**
   ```
   {context_with_sources}
   ```

**Your Task:**
Synthesize all the information to create a comprehensive report. The output **MUST** be a single JSON object with the following structure. Populate each text field based on your analysis.

```json
{{
  "report": {{
    "red_flag_status": "Your assessment (e.g., 'Low Risk', 'Medium Risk'). Include an emoji.",
    "red_flag_text": "Detailed explanation for the red flag assessment. Embed citation tags like [1][2].",
    "hiring_experience_text": "Analysis of the hiring process and candidate experience. Focus on communication and ghosting patterns. Embed citation tags like [3][4]. If no info is found, provide a default explanatory text.",
    "culture_fit": {{
      "reputation": "Analysis of company reputation. Embed citation tags.",
      "management": "Analysis of management style. Embed citation tags.",
      "sustainability": "Analysis of sustainability practices. Embed citation tags.",
      "wlb": "Analysis of work-life balance. Embed citation tags.",
      "growth": "Analysis of growth opportunities. Embed citation tags.",
      "salary": "Analysis of salary and benefits. Embed citation tags.",
      "overtime": "Analysis of overtime culture. Embed citation tags."
    }},
    "value_match_score": "A number between 0 and 100 representing the match score. Provide 0 if no resume is given.",
    "value_match_text": "Detailed explanation of the match score based on the resume. Embed citation tags. If no resume, state it's unavailable.",
    "final_risk_rating": "Your final risk rating (e.g., 'Low-to-Medium Risk').",
    "final_risk_text": "A summary justifying the final risk rating, considering all factors. Embed citation tags."
  }},
  "cited_ids": [1, 2, 3, 4, ...]
}}
```
"""
# --- End of Prompt ---

# --- 6. API路由 --- (无变化, 逻辑已包含图标类型) ---
@app.route('/analyze', methods=['POST'])
@limiter.limit("5 per day")
def analyze_company_text():
    print("--- v12.1 Structured JSON Analysis request received! ---")
    try:
        data = request.get_json()
        company_name = data.get('companyName')
        resume_text = data.get('resumeText', 'No resume provided.')
        lang_code = data.get('language', 'en')
        aspects = data.get('aspects', [])

        if not company_name:
            return jsonify({"error": "Company name / job info is required."}), 400

        context_blocks = []
        source_map = {}
        source_id_counter = 1

        print(f"Searching for: {company_name}")
        
        base_queries = [
            f'"{company_name}" company culture review',
            f'"{company_name}" scam OR fraud OR fake',
            f'site:linkedin.com "{company_name}" employees OR culture',
            f'site:indeed.com "{company_name}" reviews',
            f'site:glassdoor.com "{company_name}" reviews',
            f'"{company_name}" hiring process review',
            f'"{company_name}" no response after interview OR ghosted'
        ]
        
        aspect_query_map = {
            'wlb': f'"{company_name}" work life balance',
            'growth': f'"{company_name}" growth opportunities',
            'salary': f'"{company_name}" salary level benefits',
            'overtime': f'"{company_name}" overtime culture',
            'management': f'"{company_name}" management style',
            'sustainability': f'"{company_name}" sustainability social responsibility',
        }
        
        for aspect_key in aspects:
            if aspect_key in aspect_query_map:
                base_queries.append(aspect_query_map[aspect_key])

        search_queries = list(set(base_queries))

        for query in search_queries:
            snippets, sources_data = perform_google_search(query, SEARCH_API_KEY, SEARCH_ENGINE_ID, num_results=2)
            for i, snippet in enumerate(snippets):
                if i < len(sources_data):
                    source_info = sources_data[i]
                    link = source_info.get('link', '').lower()
                    
                    # --- 这部分逻辑就是为前端准备图标类型的关键 ---
                    if 'linkedin.com' in link: source_info['source_type'] = 'linkedin'
                    elif 'glassdoor.com' in link: source_info['source_type'] = 'glassdoor'
                    elif 'indeed.com' in link: source_info['source_type'] = 'indeed'
                    else: source_info['source_type'] = 'default'

                    context_blocks.append(f"[Source ID: {source_id_counter}] {snippet}")
                    source_map[source_id_counter] = source_info
                    source_id_counter += 1
            time.sleep(0.5)

        if not context_blocks:
             return jsonify({"report": {"red_flag_text":"No information found for this company."}, "sources": []})

        context_with_sources = "\n\n".join(context_blocks)
        print(f"Prepared {len(context_blocks)} context blocks for AI.")

        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        output_language = language_instructions.get(lang_code, 'English')
        
        full_prompt = PROMPT_TEMPLATE.format(
            output_language=output_language,
            company_name=company_name,
            aspects_list=", ".join(aspects),
            resume_text=resume_text,
            context_with_sources=context_with_sources
        )
        
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json"
        )
        
        response = model.generate_content(full_prompt, generation_config=generation_config)
        
        try:
            ai_json_response = json.loads(response.text)
            report_data = ai_json_response.get("report", {})
            cited_ids = ai_json_response.get("cited_ids", [])
        except json.JSONDecodeError:
            print("!!! Gemini did not return valid JSON. Falling back. !!!")
            return jsonify({"error": "AI failed to generate a valid structured report."}), 500

        final_sources = []
        for sid in cited_ids:
            if sid in source_map:
                source_detail = source_map[sid]
                source_detail['id'] = sid
                if source_detail not in final_sources:
                    final_sources.append(source_detail)

        print(f"Successfully parsed report and {len(final_sources)} cited sources.")
        return jsonify({"report": report_data, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误: {e} !!!")
        return jsonify({"error": "An internal server error occurred."}), 500

# --- 7. 错误处理 (无变化) ---
@app.errorhandler(429)
def ratelimit_handler(e):
    error_message = (
        "开拓者，您今日的免费分析额度已用尽！🚀\n\n"
        "Project Lens 每天为所有用户提供5次免费分析。\n"
        "如果您是需要进行大量研究的‘超级用户’，可以考虑升级到 Pro 版本，或通过‘请我喝杯咖啡☕️’来立即重置额度！"
    )
    return jsonify(error="rate_limit_exceeded", message=error_message), 429

# --- 8. 启动 (无变化) ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)










