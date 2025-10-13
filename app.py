# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 18.0 - 维度扩展最终版 (Aspect Expansion Final Version)
# 描述: 1. (已实现) 完整的国际化错误处理。
#       2. (本次更新) 全面扩展了分析维度，根据用户截图增加了创新文化、福利待遇、多元化、培训等新标签。
#       3. (本次更新) 同步升级了后端的搜索逻辑和AI Prompt，使其能够理解并分析所有新增的维度，
#          确保生成的报告内容更丰富、更全面。
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
import traceback
import datetime

# --- 1. 初始化和配置 ---
app = Flask(__name__)
CORS(app, resources={r"/analyze": {"origins": "*"}})
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"], storage_uri="memory://")

# --- 2. API密钥配置 ---
try:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
    SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
    if not all([GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID]): raise ValueError("API密钥缺失")
    genai.configure(api_key=GEMINI_API_KEY)
    print("API密钥配置成功！")
except Exception as e:
    print(f"API密钥配置失败: {e}")

# --- 3. 智能提取公司和职位名称 (无变化) ---
def extract_entities_with_ai(text_blob):
    print("🤖 启动AI实体提取程序...")
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = (f'Extract company name and job title from the text below. Respond with a JSON object: {{"company_name": "...", "job_title": "..."}}.\n\nText:\n---\n{text_blob}\n---\n')
        response = model.generate_content(prompt, generation_config=genai.GenerationConfig(response_mime_type="application/json"))
        if not response.parts:
            print(f"--- 实体提取AI响应被阻止: {response.prompt_feedback} ---")
            return text_blob, ""
        entities = json.loads(response.text)
        company, job_title = entities.get("company_name", ""), entities.get("job_title", "")
        print(f"✅ AI提取成功: 公司='{company}', 职位='{job_title}'")
        return company if company else text_blob, job_title
    except Exception as e:
        print(f"❌ AI实体提取失败: {e}. 将使用原始文本。")
        return text_blob, ""

# --- 4. 辅助函数：执行Google搜索 (无变化) ---
def perform_google_search(query, api_key, cse_id, num_results=2):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results, 'sort': 'date'}
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

# --- 5. 辅助函数：网页爬虫 (无变化) ---
def scrape_website_for_text(url):
    try:
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        [s.decompose() for s in soup(['script', 'style'])]
        text = '\n'.join(chunk for chunk in (phrase.strip() for line in (line.strip() for line in soup.get_text().splitlines()) for phrase in line.split("  ")) if chunk)
        return text[:5000]
    except Exception as e:
        print(f"❌ 爬取网站时发生错误: {e}")
        return None

# --- 6. 核心AI指令 (Prompt) [已升级] ---
PROMPT_TEMPLATE = (
    "As 'Project Lens', an expert AI assistant for job seekers, generate a detailed analysis report in {output_language} as a JSON object.\n"
    "**Citation Rules:** Use information from sources tagged with `[Source ID: X]` and embed the corresponding ID tag (e.g., `[1]`, `[2]`) in your text. Include all used IDs in the `cited_ids` array.\n"
    "**Information Provided:**\n"
    "1. **Company & Role:** {company_name} - {job_title}\n"
    "2. **Current Date:** {current_date}\n"
    "3. **Applicant's Resume/Bio:**\n   ```{resume_text}```\n"
    "4. **Research Data:**\n   ```{context_with_sources}```\n"
    "**Your Task:** Synthesize all information into a single JSON object with the following structure:\n"
    "```json\n"
    "{{\n"
    '  "report": {{\n'
    '    "red_flag_status": "Your assessment (e.g., \'Low Risk\').",\n'
    '    "red_flag_text": "Detailed explanation for red flags. Cite sources like [1][2].",\n'
    '    "hiring_experience_text": "Analysis of hiring process and candidate experience. Cite sources.",\n'
    '    "timeliness_analysis": "Analyze information recency based on the current date. Cite sources.",\n'
    '    "culture_fit": {{\n'
    '      "reputation": "Analysis of company reputation. Cite sources.",\n'
    '      "management": "Analysis of management style. Cite sources.",\n'
    '      "sustainability": "Analysis of sustainability practices. Cite sources.",\n'
    '      "wlb": "Analysis of work-life balance. Cite sources.",\n'
    '      "growth": "Analysis of growth opportunities. Cite sources.",\n'
    '      "salary": "Analysis of salary levels. Cite sources.",\n'
    '      "overtime": "Analysis of overtime culture. Cite sources.",\n'
    '      "innovation": "Analysis of innovation culture. Cite sources.",\n'
    '      "benefits": "Analysis of benefits package. Cite sources.",\n'
    '      "diversity": "Analysis of diversity & inclusion. Cite sources.",\n'
    '      "training": "Analysis of training & learning. Cite sources."\n'
    '    }},\n'
    '    "value_match_score": "A number between 0 and 100. Provide 0 if no resume.",\n'
    '    "value_match_text": "Detailed explanation of the match score. Cite sources.",\n'
    '    "final_risk_rating": "Your final risk rating.",\n'
    '    "final_risk_text": "A summary justifying the final risk rating. Cite sources."\n'
    '  }},\n'
    '  "cited_ids": []\n'
    "}}\n"
    "```"
)

# --- 辅助函数：从文本中提取所有引用ID (无变化) ---
def extract_cited_ids_from_report(report_data):
    all_text = json.dumps(report_data)
    found_ids = re.findall(r'\[(\d+)\]', all_text)
    return sorted(list(set(int(id_str) for id_str in found_ids)))

# --- 7. API路由 [已更新] ---
@app.route('/analyze', methods=['POST', 'OPTIONS'])
@limiter.limit("5 per day")
def analyze_company_text():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
        
    print("--- v18.0 Aspect Expansion Final Version Analysis request received! ---")
    try:
        data = request.get_json()
        if not data: return jsonify({"error": "Invalid JSON"}), 400

        smart_paste_content = data.get('companyName')
        if not smart_paste_content: return jsonify({"error": "Company name required"}), 400
        
        company_name, job_title = extract_entities_with_ai(smart_paste_content)
        if not company_name: return jsonify({"error": "Could not identify company name"}), 400

        context_blocks, source_map, source_id_counter = [], {}, 1
        
        # 【核心优化】扩展全面的搜索查询以覆盖所有新旧方面
        comprehensive_queries = [
            f'"{company_name}" company culture review', f'"{company_name}" work life balance', f'"{company_name}" salary benefits package',
            f'"{company_name}" growth opportunities career path', f'"{company_name}" hiring process interview candidate experience',
            f'"{company_name}" management style leadership', f'"{company_name}" overtime crunch culture', f'"{company_name}" innovation culture',
            f'"{company_name}" diversity and inclusion policy', f'"{company_name}" employee training and learning programs',
            f'"{company_name}" sustainability social responsibility', f'"{company_name}" scam OR fraud OR fake',
            f'site:linkedin.com "{company_name}" culture', f'site:indeed.com "{company_name}" reviews', f'site:glassdoor.com "{company_name}" reviews'
        ]
        
        for query in list(set(comprehensive_queries)):
            snippets, sources_data = perform_google_search(f'{query} after:{datetime.date.today().year - 1}', SEARCH_API_KEY, SEARCH_ENGINE_ID)
            for i, snippet in enumerate(snippets):
                if i < len(sources_data):
                    source_info = sources_data[i]
                    link = source_info.get('link', '').lower()
                    if 'linkedin.com' in link: source_info['source_type'] = 'linkedin'
                    elif 'glassdoor.com' in link: source_info['source_type'] = 'glassdoor'
                    elif 'indeed.com' in link: source_info['source_type'] = 'indeed'
                    else: source_info['source_type'] = 'default'
                    context_blocks.append(f"[Source ID: {source_id_counter}] {snippet}")
                    source_map[source_id_counter] = source_info
                    source_id_counter += 1
            time.sleep(0.5)

        if not context_blocks:
             return jsonify({"error": "no_info_found", "message": "No info found for this company."}), 404

        lang_code = data.get('language', 'en')
        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        
        full_prompt = PROMPT_TEMPLATE.format(
            output_language=language_instructions.get(lang_code, 'English'), company_name=company_name, job_title=job_title,
            current_date=datetime.date.today().strftime("%Y-%m-%d"), resume_text=data.get('resumeText', 'No resume provided.'),
            context_with_sources="\n\n".join(context_blocks)
        )
        
        model = genai.GenerativeModel('gemini-2.5-pro')
        safety_settings = { category: "BLOCK_NONE" for category in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]}
        response = model.generate_content(full_prompt, generation_config=genai.GenerationConfig(response_mime_type="application/json"), safety_settings=safety_settings)
        
        if not response.parts:
            print(f"!!! 主报告生成被阻止: {response.prompt_feedback} !!!")
            return jsonify({"error": "AI response blocked by safety filters."}), 500

        try:
            ai_json_response = json.loads(response.text)
            report_data = ai_json_response.get("report", {})
            cited_ids = extract_cited_ids_from_report(report_data)
            print(f"✅ 成功从报告文本中提取了 {len(cited_ids)} 个唯一引用: {cited_ids}")
        except json.JSONDecodeError:
            print(f"!!! Gemini 返回了无效的 JSON: {response.text[:500]}... !!!")
            return jsonify({"error": "AI failed to generate valid structured report."}), 500

        final_sources = [ {**source_map[sid], 'id': sid} for sid in cited_ids if sid in source_map ]
        return jsonify({"report": report_data, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误: {e} !!!"); print(traceback.format_exc())
        return jsonify({"error": "Internal server error."}), 500

# --- 8. 错误处理 (无变化) ---
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(error="rate_limit_exceeded", message="User rate limit exceeded."), 429

# --- 9. 启动 (无变化) ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)

