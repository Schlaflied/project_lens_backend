# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 1.7 - 最终安全加固版 (Final Security Hardened)
# 描述: 注入了详细的“皮包公司”侦测指令，确保AI能精准识别高风险诈骗。
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
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': 4}
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

# --- 4. 核心AI指令 (Prompt) ---
# 【重要更新】注入了详细的“皮包公司”侦测指令
PROMPT_TEMPLATE = """
As 'Project Lens', an expert AI assistant for job seekers, your task is to generate a detailed analysis report based on the provided information.
**Crucially, you must generate the entire response strictly in {output_language}.**

**Information Provided:**
1.  **Company & Role:** {company_name} {job_title_context}
2.  **Search Snippets:** The following are recent search results about the company and role.
    ```
    {search_context}
    ```
3.  **Applicant's Resume/Bio (if provided):**
    ```
    {resume_text}
    ```

**Your Task:**
Synthesize all the information above to create a comprehensive report. The report MUST include the following sections:

**1. Culture-Fit Analysis:**
Based on the search results, analyze the company's Work-Life Balance, Team Collaboration Style, and Growth Opportunities, specifically considering the provided Job Title if available.

**2. Corporate Investigator Report (Highest Priority):**
Act as a sharp corporate investigator. Based ONLY on the provided search snippets, identify any potential 'red flags' that might suggest this is a shell company or a scam. You MUST check for the following signals:
* **Vague or Glamorous Descriptions:** Does the information promise unusually high rewards for low skill requirements? Is the job description unclear?
* **Requests for Fees:** Is there any mention of upfront fees, training costs, security deposits, or any payment required from the applicant?
* **Inconsistent Information:** Are there contradictions in company size, location, or business scope across different snippets?
* **Poor Digital Footprint:** Do the search results suggest a very new website, low social media activity, or a general lack of a professional online presence?

**3. Personalized Match Analysis (if resume is provided):**
Analyze how well the applicant's background and skills match the company culture and the specific role. Provide actionable advice. If no resume is provided, state that this section is unavailable.

**4. Final Risk Assessment:**
Conclude with a clear risk rating: **Low, Medium, or High**.
* If you find **even one** of the red flags from the "Corporate Investigator Report" (especially any mention of fees), you **MUST** rate the risk as **High** and strongly advise the user to proceed with extreme caution.
* Otherwise, provide a rating based on the overall findings.
"""

# --- 5. API路由 ---
@app.route('/analyze', methods=['POST'])
def analyze_company_text():
    print("--- V1.7 Analysis request received! ---")
    try:
        data = request.get_json()
        company_name = data.get('companyName')
        job_title = data.get('jobTitle', '') 
        resume_text = data.get('resumeText', 'No resume provided.')
        lang_code = data.get('language', 'en')

        if not company_name:
            return jsonify({"error": "Company name is required."}), 400

        print(f"Searching for: {company_name} - {job_title if job_title else 'General'}")
        base_query = f'"{company_name}"'
        if job_title:
            base_query += f' "{job_title}"'
        
        # 增加一个专门搜索负面词汇的查询
        search_queries = [
            f'{base_query} company culture review',
            f'{base_query} work life balance',
            f'site:glassdoor.com {base_query} reviews',
            f'{base_query} scam OR fraud OR fake'
        ]
        all_snippets = []
        all_sources = []
        for query in search_queries:
            snippets, sources = perform_google_search(query, SEARCH_API_KEY, SEARCH_ENGINE_ID)
            all_snippets.extend(snippets)
            all_sources.extend(sources)
        
        search_context = "\n".join(all_snippets) if all_snippets else "No information found in web search."
        print(f"Found {len(all_snippets)} snippets.")

        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        output_language = language_instructions.get(lang_code, 'English')
        job_title_context = f"for the role of '{job_title}'" if job_title else ""

        full_prompt = PROMPT_TEMPLATE.format(
            output_language=output_language,
            company_name=company_name,
            job_title_context=job_title_context,
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)




