# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 17.0 - 语言健壮性最终版 (Language Robustness Final Version)
# 描述: 1. (已实现) 智能日期精确化功能。
#       2. (已实现) 统一的全面搜索策略。
#       3. (本次更新) 优化了所有错误处理，后端不再返回任何面向用户的UI文本，而是返回结构化的错误类型，
#          由前端负责进行完整的国际化(i18n)展示，确保语言显示绝对正确。
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

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# --- 2. API密钥配置 ---
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

# --- 3. 智能提取公司和职位名称 (无变化) ---
def extract_entities_with_ai(text_blob):
    print("🤖 启动AI实体提取程序 (使用 gemini-2.5-pro)...")
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = (
            'From the following job description or text, please extract the company name and the job title.\n'
            'Provide the output as a JSON object with two keys: "company_name" and "job_title".\n'
            'If you cannot find a specific job title, set its value to an empty string "".\n\n'
            'Text:\n---\n'
            f'{text_blob}\n'
            '---\n'
        )
        generation_config = genai.GenerationConfig(response_mime_type="application/json")
        response = model.generate_content(prompt, generation_config=generation_config)
        
        if not response.parts:
            print("--- 实体提取AI响应被阻止 ---")
            print(f"--- Prompt Feedback: {response.prompt_feedback} ---")
            return text_blob, ""

        entities = json.loads(response.text)
        company = entities.get("company_name", "")
        job_title = entities.get("job_title", "")
        
        print(f"✅ AI提取成功: 公司='{company}', 职位='{job_title}'")
        return company if company else text_blob, job_title
        
    except Exception as e:
        print(f"❌ AI实体提取失败: {e}. 将使用原始文本进行搜索。")
        return text_blob, ""

# --- 4. 辅助函数：执行Google搜索 (无变化) ---
def perform_google_search(query, api_key, cse_id, num_results=4):
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
    print(f"🚀 准备爬取网站: {url}")
    try:
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' }
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

# --- 6. 核心AI指令 (Prompt) (无变化) ---
PROMPT_TEMPLATE = (
    "As 'Project Lens', an expert AI assistant for job seekers, your task is to generate a detailed analysis report.\n"
    "**Crucially, you must generate the entire response strictly as a JSON object and in {output_language}.**\n"
    "**Citation Rules (VERY IMPORTANT):**\n"
    "1. The information provided below is structured with a unique `[Source ID: X]`.\n"
    "2. When you use information from a source, you **MUST** embed its corresponding ID tag (e.g., `[1]`, `[2]`) directly into the text where the information is used.\n"
    "3. In the final JSON, include a `cited_ids` array containing all unique source IDs you used in the report.\n"
    "**Information Provided:**\n"
    "1. **Company & Role:** {company_name} - {job_title}\n"
    "2. **Current Date:** {current_date}\n"
    "3. **Applicant's Resume/Bio (if provided):**\n"
    "   ```\n"
    "   {resume_text}\n"
    "   ```\n"
    "4. **Research Data (Search results and website content):**\n"
    "   ```\n"
    "   {context_with_sources}\n"
    "   ```\n"
    "**Your Task:**\n"
    "Synthesize all the information to create a comprehensive report. The output **MUST** be a single JSON object with the following structure. Populate each text field based on your analysis.\n"
    "```json\n"
    "{{\n"
    '  "report": {{\n'
    '    "red_flag_status": "Your assessment (e.g., \'Low Risk\', \'Medium Risk\'). Include an emoji.",\n'
    '    "red_flag_text": "Detailed explanation for the red flag assessment. Embed citation tags like [1][2].",\n'
    '    "hiring_experience_text": "Analysis of the hiring process and candidate experience. Focus on communication and ghosting patterns. Embed citation tags like [3][4]. If no info is found, provide a default explanatory text.",\n'
    '    "timeliness_analysis": "Based on the current date, analyze the recency of the information found. Note if reviews are old or if the job posting seems stale. Embed citation tags.",\n'
    '    "culture_fit": {{\n'
    '      "reputation": "Analysis of company reputation. Embed citation tags.",\n'
    '      "management": "Analysis of management style. Embed citation tags.",\n'
    '      "sustainability": "Analysis of sustainability practices. Embed citation tags.",\n'
    '      "wlb": "Analysis of work-life balance. Embed citation tags.",\n'
    '      "growth": "Analysis of growth opportunities. Embed citation tags.",\n'
    '      "salary": "Analysis of salary and benefits. Embed citation tags.",\n'
    '      "overtime": "Analysis of overtime culture. Embed citation tags."\n'
    '    }},\n'
    '    "value_match_score": "A number between 0 and 100 representing the match score. Provide 0 if no resume is given.",\n'
    '    "value_match_text": "Detailed explanation of the match score based on the resume. Embed citation tags. If no resume, state it\'s unavailable.",\n'
    '    "final_risk_rating": "Your final risk rating (e.g., \'Low-to-Medium Risk\').",\n'
    '    "final_risk_text": "A summary justifying the final risk rating, considering all factors. Embed citation tags."\n'
    '  }},\n'
    '  "cited_ids": [1, 2, 3, 4, ...]\n'
    "}}\n"
    "```"
)

# --- 辅助函数：从文本中提取所有引用ID (无变化) ---
def extract_cited_ids_from_report(report_data):
    all_text = json.dumps(report_data)
    found_ids = re.findall(r'\[(\d+)\]', all_text)
    unique_ids = sorted(list(set(int(id_str) for id_str in found_ids)))
    return unique_ids

# --- 7. API路由 [已更新] ---
@app.route('/analyze', methods=['POST', 'OPTIONS'])
@limiter.limit("5 per day")
def analyze_company_text():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
        
    print("--- v17.0 Language Robustness Final Version Analysis request received! ---")
    try:
        data = request.get_json()
        if data is None: return jsonify({"error": "Invalid JSON in request body."}), 400

        smart_paste_content = data.get('companyName') 
        resume_text = data.get('resumeText', 'No resume provided.')
        lang_code = data.get('language', 'en')

        if not smart_paste_content: return jsonify({"error": "Company name / job info is required."}), 400

        company_name, job_title = extract_entities_with_ai(smart_paste_content)

        if not company_name: return jsonify({"error": "Could not identify a company name."}), 400

        context_blocks = []; source_map = {}; source_id_counter = 1
        print(f"Searching for extracted company: {company_name}")
        
        comprehensive_queries = [
            f'"{company_name}" company culture review', f'"{company_name}" work life balance',
            f'"{company_name}" salary benefits', f'"{company_name}" growth opportunities career path',
            f'"{company_name}" hiring process interview candidate experience', f'"{company_name}" management style leadership',
            f'"{company_name}" overtime crunch culture', f'"{company_name}" company reputation news',
            f'"{company_name}" scam OR fraud OR fake', f'site:linkedin.com "{company_name}" employees OR culture',
            f'site:indeed.com "{company_name}" reviews', f'site:glassdoor.com "{company_name}" reviews',
            f'"{company_name}" no response after interview OR ghosted'
        ]
        search_queries = list(set(comprehensive_queries))

        for query in search_queries:
            snippets, sources_data = perform_google_search(f'{query} after:{datetime.date.today().year - 1}', SEARCH_API_KEY, SEARCH_ENGINE_ID, num_results=2)
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

        # 【核心优化】如果搜索后没有任何内容，返回一个特定的、可由前端翻译的错误。
        if not context_blocks:
             return jsonify({"error": "no_info_found", "message": "No information could be found for the given company."}), 404

        context_with_sources = "\n\n".join(context_blocks)
        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        output_language = language_instructions.get(lang_code, 'English')
        current_date_str = datetime.date.today().strftime("%Y-%m-%d")

        full_prompt = PROMPT_TEMPLATE.format(
            output_language=output_language, company_name=company_name, job_title=job_title,
            current_date=current_date_str, resume_text=resume_text, context_with_sources=context_with_sources
        )
        
        model = genai.GenerativeModel('gemini-2.5-pro')
        generation_config = genai.GenerationConfig(response_mime_type="application/json")
        safety_settings = { "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE", "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE", "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE", "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE" }
        
        response = model.generate_content(full_prompt, generation_config=generation_config, safety_settings=safety_settings)
        
        if not response.parts:
            print("!!! 主报告生成被阻止或为空 !!!"); print(f"--- Prompt Feedback: {response.prompt_feedback} ---")
            if response.candidates: print(f"--- Finish Reason: {response.candidates[0].finish_reason} ---"); print(f"--- Safety Ratings: {response.candidates[0].safety_ratings} ---")
            return jsonify({"error": "AI response was blocked due to safety filters."}), 500

        try:
            ai_json_response = json.loads(response.text)
            report_data = ai_json_response.get("report", {})
            cited_ids = extract_cited_ids_from_report(report_data)
            print(f"✅ 双重保险：从报告文本中成功提取了 {len(cited_ids)} 个唯一引用: {cited_ids}")
        except json.JSONDecodeError:
            print(f"!!! Gemini 没有返回有效的 JSON !!!\n--- 接收到的文本: {response.text[:500]}... ---")
            return jsonify({"error": "AI failed to generate a valid structured report."}), 500

        final_sources = []
        for sid in cited_ids:
            if sid in source_map:
                source_detail = source_map[sid]
                source_detail['id'] = sid
                if source_detail not in final_sources: final_sources.append(source_detail)

        print(f"Successfully parsed report and {len(final_sources)} cited sources.")
        return jsonify({"report": report_data, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误: {e} !!!"); print(traceback.format_exc())
        return jsonify({"error": "An internal server error occurred."}), 500

# --- 8. 错误处理 [已优化] ---
@app.errorhandler(429)
def ratelimit_handler(e):
    # 【最佳实践优化】不再由后端发送具体的错误文案。
    # 只返回一个结构化的错误类型，由前端根据用户的语言设置来显示对应的本地化文案。
    return jsonify(error="rate_limit_exceeded", message="User rate limit exceeded."), 429

# --- 9. 启动 (无变化) ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)

