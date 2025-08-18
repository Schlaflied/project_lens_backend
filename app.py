# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 5.0 - 集成限流策略 (Rate Limiting Integrated)
# 描述: 增加了基于IP的每日请求限流功能，并根据策划书定制了超额提示。
# -----------------------------------------------------------------------------

import os
import requests
import google.generativeai as genai
import time
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup
# --- 新增：导入限流库 ---
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- 1. 初始化和配置 ---
app = Flask(__name__)
CORS(app)

# --- 新增：初始化限流器 ---
# 使用 get_remote_address 来根据用户的IP地址进行限流
# Cloud Run 等环境会自动处理代理背后的真实IP，所以这很可靠
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"], # 设置默认的全局限流
    storage_uri="memory://" # 使用内存存储，简单高效
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
    except requests.exceptions.RequestException as e:
        print(f"❌ 爬取网站失败: {e}")
        return None
    except Exception as e:
        print(f"❌ 解析HTML时发生未知错误: {e}")
        return None

# --- 5. 核心AI指令 (Prompt) (无变化) ---
PROMPT_TEMPLATE = """
As 'Project Lens', an expert AI assistant for job seekers, your task is to generate a detailed analysis report.
**Crucially, you must adhere to the citation rules and generate the entire response strictly in {output_language}.**

**Citation Rules (VERY IMPORTANT):**
1.  The information provided below is structured with a unique `[Source ID: X]`.
2.  When you use information from a source, you **MUST** append its corresponding ID tag at the end of the sentence.
3.  **DO NOT GROUP CITATIONS.** Each citation must be in its own brackets. For example, to cite sources 1, 2, and 3, you MUST write it as `[Source ID: 1][Source ID: 2][Source ID: 3]`. **NEVER write `[Source ID: 1, 2, 3]` or `[1, 2, 3]`**. This is a strict formatting rule.
4.  At the end of your entire report, you **MUST** include a section titled `---REFERENCES---`.
5.  Under this title, list **ONLY** the sources you actually cited. Format it as: `[Source ID: X] Title of the source`.

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
Synthesize all the information to create a comprehensive report with citations. The report MUST include the following sections IN THIS ORDER:

**1. Culture-Fit Analysis:**
Analyze Work-Life Balance, Team Collaboration, and Growth Opportunities. **Cite your sources.**

**2. Corporate Investigator Report (Highest Priority):**
Identify 'red flags' for shell companies or scams. **Cite your sources for every claim.**

**3. Personalized Match Analysis (if resume is provided):**
Analyze the applicant's match with the company and role. **Cite your sources.** If no resume is provided, state that this section is unavailable.

**4. Online Reputation Summary (from Job Sites):**
This is a new, mandatory section. Specifically look for information from sources identified as LinkedIn, Glassdoor, or Indeed in the Research Data. Summarize the key positive and negative points mentioned on these platforms. **Cite every point you make.** If no information from these sites was found in the provided data, state: "A specific summary from job sites like Glassdoor or LinkedIn is unavailable due to a lack of direct employee reviews in the search results for this query."

**5. Final Risk Assessment:**
Conclude with a risk rating: **Low, Medium, or High**. Justify your rating with evidence from ALL previous sections and **cite the sources** that led to your conclusion.

**Remember to end your response with the `---REFERENCES---` section.**
"""
# --- End of Prompt ---


# --- 6. API路由 (已更新限流) ---
@app.route('/analyze', methods=['POST'])
@limiter.limit("10 per day") # --- 新增：应用限流规则！每天每个IP 10次 ---
def analyze_company_text():
    print("--- V5.0 Rate-Limited Analysis request received! ---")
    try:
        data = request.get_json()
        company_name = data.get('companyName')
        job_title = data.get('jobTitle', '') 
        resume_text = data.get('resumeText', 'No resume provided.')
        lang_code = data.get('language', 'en')

        if not company_name:
            return jsonify({"error": "Company name is required."}), 400

        context_blocks = []
        source_map = {}
        source_id_counter = 1

        print(f"Searching for: {company_name}")
        
        search_queries = [
            f'"{company_name}" company culture review',
            f'"{company_name}" work life balance',
            f'"{company_name}" scam OR fraud OR fake',
            f'site:linkedin.com "{company_name}" employees OR culture',
            f'site:indeed.com "{company_name}" reviews',
            f'site:glassdoor.com "{company_name}" reviews OR salaries'
        ]
        
        for query in search_queries:
            snippets, sources_data = perform_google_search(query, SEARCH_API_KEY, SEARCH_ENGINE_ID, num_results=2)
            for i, snippet in enumerate(snippets):
                if i < len(sources_data):
                    source_info = sources_data[i]
                    link = source_info.get('link', '').lower()
                    if 'linkedin.com' in link:
                        source_info['source_type'] = 'linkedin'
                    elif 'glassdoor.com' in link:
                        source_info['source_type'] = 'glassdoor'
                    elif 'indeed.com' in link:
                        source_info['source_type'] = 'indeed'
                    else:
                        source_info['source_type'] = 'default'

                    context_blocks.append(f"[Source ID: {source_id_counter}] {snippet}")
                    source_map[source_id_counter] = source_info
                    source_id_counter += 1
            time.sleep(0.5)

        _, official_site_sources = perform_google_search(f'"{company_name}" official website', SEARCH_API_KEY, SEARCH_ENGINE_ID, 1)
        if official_site_sources and 'link' in official_site_sources[0]:
            website_url = official_site_sources[0]['link']
            scraped_content = scrape_website_for_text(website_url)
            if scraped_content:
                context_blocks.append(f"[Source ID: {source_id_counter}] {scraped_content}")
                source_info = {'title': f"{company_name} Official Website", 'link': website_url, 'source_type': 'default'}
                source_map[source_id_counter] = source_info
                source_id_counter += 1
        
        if not context_blocks:
             return jsonify({"analysis": "No information found for this company.", "sources": []})

        context_with_sources = "\n\n".join(context_blocks)
        print(f"Prepared {len(context_blocks)} context blocks for AI.")

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
        
        print("Received response from Gemini. Applying final auto-correction...")

        def expand_full_grouped_citations(match):
            full_match_string = match.group(0)
            ids = re.findall(r'\d+', full_match_string)
            return "".join([f"[Source ID: {i}]" for i in ids])

        def expand_simple_grouped_citations(match):
            id_string = match.group(1)
            ids = [i.strip() for i in id_string.split(',')]
            return "".join([f"[Source ID: {i}]" for i in ids])

        corrected_text = re.sub(r'\[(Source ID: \d+(?:,\s*Source ID: \d+)+)\]', expand_full_grouped_citations, ai_response_text)
        corrected_text = re.sub(r'\[(\d+,\s*[\d,\s]*)\]', expand_simple_grouped_citations, corrected_text)
        
        print("Parsing citations...")
        analysis_part = corrected_text
        references_part = ""
        final_sources = []

        if "---REFERENCES---" in corrected_text:
            parts = corrected_text.split("---REFERENCES---")
            analysis_part = parts[0].strip()
            references_part = parts[1].strip()
            cited_ids = re.findall(r'\[Source ID: (\d+)\]', references_part)
            for sid_str in cited_ids:
                sid = int(sid_str)
                if sid in source_map:
                    source_detail = source_map[sid]
                    source_detail['id'] = sid
                    final_sources.append(source_detail)
            
            analysis_part = re.sub(r'\[Source ID: (\d+)\]', r'[\1]', analysis_part)

        print(f"Successfully parsed {len(final_sources)} cited sources.")
        return jsonify({"analysis": analysis_part, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误: {e} !!!")
        return jsonify({"error": "An internal server error occurred."}), 500

# --- 新增：自定义错误处理函数 ---
# 当用户超过限流次数时，返回这个定制的JSON信息
@app.errorhandler(429)
def ratelimit_handler(e):
    # 这是根据你的策划书定制的提示信息
    error_message = (
        "开拓者，您今日的免费分析额度已用尽！🚀\n\n"
        "Project Lens 每天为所有用户提供10次免费分析。\n"
        "如果您是需要进行大量研究的‘超级用户’，可以考虑升级到 Pro 版本，或通过‘请我喝杯咖啡☕️’来立即重置额度！"
    )
    # 返回一个特殊的字段，让前端可以识别这是限流错误
    return jsonify(error="rate_limit_exceeded", message=error_message), 429


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)







