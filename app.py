# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 3.0 - 引用增强版 (Citation Enhanced)
# 描述: 重构了数据处理和Prompt，使AI能够生成带有来源引用的分析报告，确保信息可追溯。
# -----------------------------------------------------------------------------

import os
import requests
import google.generativeai as genai
import time
import re # 导入正则表达式库用于解析引用
from flask import Flask, request, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup

# --- 1. 初始化和配置 (无变化) ---
app = Flask(__name__)
CORS(app)

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
    except requests.exceptions.RequestException as e:
        print(f"❌ 爬取网站失败: {e}")
        return None
    except Exception as e:
        print(f"❌ 解析HTML时发生未知错误: {e}")
        return None


# --- 5. 【核心改造】核心AI指令 (Prompt) ---
PROMPT_TEMPLATE = """
As 'Project Lens', an expert AI assistant for job seekers, your task is to generate a detailed analysis report.
**Crucially, you must adhere to the citation rules and generate the entire response strictly in {output_language}.**

**Citation Rules (VERY IMPORTANT):**
1.  The information provided below is structured with a unique `[Source ID: X]`.
2.  When you use information from a source to form your analysis, you **MUST** append its corresponding ID tag at the end of the sentence. For example: "The company focuses on AI development [Source ID: 1]."
3.  If a sentence synthesizes information from multiple sources, cite all of them. For example: "The work-life balance is reportedly poor, but the pay is high [Source ID: 2][Source ID: 3]."
4.  At the end of your entire report, you **MUST** include a section titled `---REFERENCES---`.
5.  Under this title, list **ONLY** the sources you actually cited in your report. Format it as: `[Source ID: X] Title of the source`

**Information Provided:**
1.  **Company & Role:** {company_name} {job_title_context}
2.  **Applicant's Resume/Bio (if provided):**
    ```
    {resume_text}
    ```
3.  **Research Data (Search results and website content):**
    ```
    {context_with_sources}
    ```

**Your Task:**
Synthesize all the information to create a comprehensive report with citations. The report MUST include the following sections:

**1. Culture-Fit Analysis:**
Analyze Work-Life Balance, Team Collaboration, and Growth Opportunities. **Cite your sources.**

**2. Corporate Investigator Report (Highest Priority):**
Identify 'red flags' for shell companies or scams. **Cite your sources for every claim.** Check for:
* Vague or glamorous descriptions.
* Requests for fees.
* Inconsistent information.
* Poor digital footprint.

**3. Personalized Match Analysis (if resume is provided):**
Analyze the applicant's match with the company and role. **Cite your sources.** If no resume is provided, state that this section is unavailable.

**4. Final Risk Assessment:**
Conclude with a risk rating: **Low, Medium, or High**. Justify your rating with evidence and **cite the sources** that led to your conclusion.

**Remember to end your response with the `---REFERENCES---` section.**
"""

# --- 6. 【核心改造】API路由 ---
@app.route('/analyze', methods=['POST'])
def analyze_company_text():
    print("--- V3.0 Citation-Enhanced Analysis request received! ---")
    try:
        data = request.get_json()
        company_name = data.get('companyName')
        job_title = data.get('jobTitle', '') 
        resume_text = data.get('resumeText', 'No resume provided.')
        lang_code = data.get('language', 'en')

        if not company_name:
            return jsonify({"error": "Company name is required."}), 400

        # --- 数据准备阶段 ---
        context_blocks = []
        source_map = {} # 用于存储ID到源信息的映射
        source_id_counter = 1

        # 1. 执行Google搜索并结构化
        print(f"Searching for: {company_name}")
        search_queries = [
            f'"{company_name}" company culture review',
            f'"{company_name}" work life balance',
            f'"{company_name}" scam OR fraud OR fake'
        ]
        for query in search_queries:
            snippets, sources_data = perform_google_search(SEARCH_API_KEY, SEARCH_ENGINE_ID, query)
            for i, snippet in enumerate(snippets):
                if i < len(sources_data):
                    source_info = sources_data[i]
                    context_blocks.append(f"[Source ID: {source_id_counter}] {snippet}")
                    source_map[source_id_counter] = source_info
                    source_id_counter += 1
            time.sleep(0.5)

        # 2. 爬取官网并结构化
        _, official_site_sources = perform_google_search(SEARCH_API_KEY, SEARCH_ENGINE_ID, f'"{company_name}" official website', 1)
        if official_site_sources and 'link' in official_site_sources[0]:
            website_url = official_site_sources[0]['link']
            scraped_content = scrape_website_for_text(website_url)
            if scraped_content:
                context_blocks.append(f"[Source ID: {source_id_counter}] {scraped_content}")
                source_info = {'title': f"{company_name} Official Website", 'link': website_url}
                source_map[source_id_counter] = source_info
                source_id_counter += 1
        
        if not context_blocks:
             return jsonify({"analysis": "No information found for this company.", "sources": []})

        context_with_sources = "\n\n".join(context_blocks)
        print(f"Prepared {len(context_blocks)} context blocks for AI.")

        # --- Prompt 格式化和 AI 调用 ---
        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        output_language = language_instructions.get(lang_code, 'English')
        job_title_context = f"for the role of '{job_title}'" if job_title else ""

        full_prompt = PROMPT_TEMPLATE.format(
            output_language=output_language,
            company_name=company_name,
            job_title_context=job_title_context,
            resume_text=resume_text,
            context_with_sources=context_with_sources
        )

        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(full_prompt)
        ai_response_text = response.text
        
        # --- 结果后处理阶段 ---
        print("Received response from Gemini. Parsing citations...")
        analysis_part = ai_response_text
        references_part = ""
        final_sources = []

        if "---REFERENCES---" in ai_response_text:
            parts = ai_response_text.split("---REFERENCES---")
            analysis_part = parts[0].strip()
            references_part = parts[1].strip()

            # 从 REFERENCES 部分解析出被引用的 source ID
            cited_ids = re.findall(r'\[Source ID: (\d+)\]', references_part)
            
            # 根据被引用的ID，从source_map中查找完整的源信息
            for sid_str in cited_ids:
                sid = int(sid_str)
                if sid in source_map:
                    # 添加一个 'id' 字段用于前端显示
                    source_detail = source_map[sid]
                    source_detail['id'] = sid
                    final_sources.append(source_detail)
            
            # 为了美观，将主报告中的 [Source ID: X] 替换为更简洁的 [X]
            analysis_part = re.sub(r'\[Source ID: (\d+)\]', r'[\1]', analysis_part)

        print(f"Successfully parsed {len(final_sources)} cited sources.")
        return jsonify({"analysis": analysis_part, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误: {e} !!!")
        return jsonify({"error": "An internal server error occurred."}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)


