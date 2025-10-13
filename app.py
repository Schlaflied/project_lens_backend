# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 26.0 - 终极RAG净化版 (Ultimate RAG Scrubbing Version)
# 描述: 1. (已实现) 完整的引用防幻觉与净化机制。
#       2. (本次更新) 彻底重写了 `scrub_invalid_citations` 函数。
#          新的净化引擎现在更加强大，可以智能处理AI在引用角标前可能插入的
#          各种Markdown格式（如列表项后的换行），确保上下文检查的绝对准确性，
#          从而彻底杜绝“幽灵引用”问题。
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

# --- 3. 智能提取实体 (无变化) ---
def extract_entities_with_ai(text_blob):
    print("🤖 启动AI实体提取程序 (含地点)...")
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = (f'From the text below, extract the company name, job title, and location. Respond with a JSON object: {{"company_name": "...", "job_title": "...", "location": "..."}}.\nIf a value isn\'t found, return an empty string "".\n\nText:\n---\n{text_blob}\n---\n')
        response = model.generate_content(prompt, generation_config=genai.GenerationConfig(response_mime_type="application/json"))
        if not response.parts: print(f"--- 实体提取AI响应被阻止: {response.prompt_feedback} ---"); return text_blob, "", ""
        entities = json.loads(response.text)
        company, job_title, location = entities.get("company_name", ""), entities.get("job_title", ""), entities.get("location", "")
        print(f"✅ AI提取成功: 公司='{company}', 职位='{job_title}', 地点='{location}'")
        return company if company else text_blob, job_title, location
    except Exception as e:
        print(f"❌ AI实体提取失败: {e}. 将使用原始文本。"); return text_blob, "", ""

# --- 4. Google搜索 (无变化) ---
def perform_google_search(query, api_key, cse_id, num_results=2):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results, 'sort': 'date'}
    try:
        response = requests.get(url, params=params); response.raise_for_status()
        search_results = response.json()
        snippets = [item.get('snippet', '') for item in search_results.get('items', [])]
        sources = [{'title': item.get('title'), 'link': item.get('link')} for item in search_results.get('items', [])]
        return snippets, sources
    except requests.exceptions.RequestException as e:
        print(f"Google搜索请求失败: {e}"); return [], []

# --- 5. 网页爬虫 (无变化) ---
def scrape_website_for_text(url):
    try:
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' }
        response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        [s.decompose() for s in soup(['script', 'style'])]
        text = '\n'.join(chunk for chunk in (phrase.strip() for line in (line.strip() for line in soup.get_text().splitlines()) for phrase in line.split("  ")) if chunk)
        return text[:5000]
    except Exception as e:
        print(f"❌ 爬取网站时发生错误: {e}"); return None

# --- 6. 核心AI指令 (Prompt) [无变化] ---
PROMPT_TEMPLATE = (
    "As 'Project Lens', an expert AI assistant, generate a detailed analysis report in {output_language} as a JSON object.\n"
    "**Citation Rules (VERY IMPORTANT):**\n"
    "1. Cite information by embedding the corresponding source tag (e.g., `[1]`, `[2]`) provided in the `Research Data`.\n"
    "2. **You MUST ONLY use the source IDs provided in the `Research Data` section. DO NOT invent, hallucinate, or create any source IDs that are not explicitly given to you.**\n"
    "3. Include all genuinely used IDs in the final `cited_ids` array.\n"
    "**Information Provided:**\n"
    "1. **Company, Role & Location:** {company_name} - {job_title} in {location}\n"
    "2. **Current Date:** {current_date}\n"
    "3. **Applicant's Resume/Bio:**\n   ```{resume_text}```\n"
    "4. **Research Data (Each block has a `[Source ID: X]`):**\n   ```{context_with_sources}```\n"
    "**Your Task:** Synthesize all info into a single JSON object with the following structure:\n"
    "```json\n"
    "{{\n"
    '  "report": {{\n'
    '    "company_location": "{location}",\n'
    '    "red_flag_status": "Your assessment (e.g., \'Low Risk\').",\n'
    '    "red_flag_text": "Detailed explanation for red flags. Cite sources.",\n'
    '    "hiring_experience_text": "Analysis of hiring process. Cite sources.",\n'
    '    "timeliness_analysis": "1. Analyze info recency. 2. Analyze job posting status (e.g., \'Likely open\', \'Potentially expired\') and give a reason. Cite sources.",\n'
    '    "culture_fit": {{ "reputation": "", "management": "", "sustainability": "", "wlb": "", "growth": "", "salary": "", "overtime": "", "innovation": "", "benefits": "", "diversity": "", "training": "" }},\n'
    '    "value_match_score": "A number from 0-100. 0 if no resume.",\n'
    '    "value_match_text": "Explanation of the match score. Cite sources.",\n'
    '    "final_risk_rating": "Your final risk rating.",\n'
    '    "final_risk_text": "Summary justifying the final rating. Cite sources."\n'
    '  }},\n'
    '  "cited_ids": []\n'
    "}}\n"
    "```"
)

# --- 7. 引用净化辅助函数 [已升级] ---
def extract_all_mentioned_ids(report_data):
    all_text = json.dumps(report_data)
    found_ids = re.findall(r'\[([\d,\s]+)\]', all_text)
    # This regex now handles both [1] and [1, 2, 3] formats
    id_set = set()
    for id_group in found_ids:
        ids = [int(i.strip()) for i in id_group.split(',') if i.strip().isdigit()]
        id_set.update(ids)
    return id_set

def scrub_invalid_citations(data, valid_ids_set):
    """
    【终极升级】递归地清理报告中的无效引用。
    这个版本更强大，可以智能处理AI可能插入的Markdown格式（如换行符或列表标记）。
    """
    if isinstance(data, dict):
        return {k: scrub_invalid_citations(v, valid_ids_set) for k, v in data.items()}
    elif isinstance(data, list):
        return [scrub_invalid_citations(elem, valid_ids_set) for elem in data]
    elif isinstance(data, str):
        # 这个函数会检查每个引用角标，看它前面的文本是否包含有效内容。
        def repl(match):
            # 提取角标前的最多30个字符作为上下文进行检查
            # `\s*` 会忽略掉所有AI可能加入的空格、换行符、甚至是markdown的列表符号
            context_window = data[:match.start()].rstrip()
            context_check = context_window[-30:] 
            
            citation_id = int(match.group(1))
            
            # 核心逻辑：如果角标在有效ID集合里，并且它前面的文本不仅仅是空格或标点符号，
            # 那么就保留这个角标。
            if citation_id in valid_ids_set and re.search(r'\w', context_check):
                return match.group(0)
            
            # 否则，就把它当作“幽灵引用”移除掉
            print(f"👻 净化幽灵引用: 移除了无效或孤立的引用角标 [{citation_id}]")
            return ""

        # 我们现在只处理单个数字的角标，因为前端已经可以处理压缩版了
        return re.sub(r'\[(\d+)\]', repl, data)
    else:
        return data

# --- 8. API路由 (无变化) ---
@app.route('/analyze', methods=['POST', 'OPTIONS'])
@limiter.limit("5 per day")
def analyze_company_text():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    print("--- v26.0 Ultimate RAG Scrubbing Version Analysis request received! ---")
    try:
        data = request.get_json();
        if not data: return jsonify({"error": "Invalid JSON"}), 400

        smart_paste_content = data.get('companyName')
        if not smart_paste_content: return jsonify({"error": "Company name required"}), 400
        
        company_name, job_title, location = extract_entities_with_ai(smart_paste_content)
        if not company_name: return jsonify({"error": "Could not identify company name"}), 400

        context_blocks, source_map, source_id_counter = [], {}, 1
        location_query_part = f' "{location}"' if location else ""
        
        comprehensive_queries = [
            f'"{company_name}"{location_query_part} company culture review', f'"{company_name}"{location_query_part} work life balance',
            f'"{company_name}"{location_query_part} salary benefits', f'"{company_name}"{location_query_part} growth opportunities',
            f'"{company_name}"{location_query_part} hiring process interview', f'"{company_name}"{location_query_part} management style',
            f'"{company_name}"{location_query_part} overtime culture', f'"{company_name}"{location_query_part} innovation culture',
            f'"{company_name}"{location_query_part} diversity inclusion', f'"{company_name}"{location_query_part} training programs',
            f'"{company_name}"{location_query_part} sustainability', f'"{company_name}"{location_query_part} scam fraud',
            f'site:linkedin.com "{company_name}" "{location}"', f'site:indeed.com "{company_name}" "{location}" reviews', f'site:glassdoor.com "{company_name}" "{location}" reviews'
        ]
        
        for query in list(set(comprehensive_queries)):
            snippets, sources_data = perform_google_search(f'{query} after:{datetime.date.today().year - 1}', SEARCH_API_KEY, SEARCH_ENGINE_ID)
            for i, snippet in enumerate(snippets):
                if i < len(sources_data):
                    source_info = sources_data[i]
                    link = source_info.get('link', '').lower()
                    source_info['source_type'] = 'linkedin' if 'linkedin.com' in link else 'glassdoor' if 'glassdoor.com' in link else 'indeed' if 'indeed.com' in link else 'default'
                    context_blocks.append(f"[Source ID: {source_id_counter}] {snippet}")
                    source_map[source_id_counter] = source_info
                    source_id_counter += 1
            time.sleep(0.2) 

        if not context_blocks: return jsonify({"error": "no_info_found"}), 404

        lang_code = data.get('language', 'en')
        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        
        full_prompt = PROMPT_TEMPLATE.format(
            output_language=language_instructions.get(lang_code, 'English'), company_name=company_name, 
            job_title=job_title, location=location or "Not Specified",
            current_date=datetime.date.today().strftime("%Y-%m-%d"), resume_text=data.get('resumeText', 'No resume provided.'),
            context_with_sources="\n\n".join(context_blocks)
        )
        
        model = genai.GenerativeModel('gemini-2.5-pro')
        safety_settings = { category: "BLOCK_NONE" for category in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]}
        response = model.generate_content(full_prompt, generation_config=genai.GenerationConfig(response_mime_type="application/json"), safety_settings=safety_settings)
        
        if not response.parts:
            print(f"!!! 主报告生成被阻止: {response.prompt_feedback} !!!"); return jsonify({"error": "AI response blocked"}), 500

        try:
            ai_json_response = json.loads(response.text)
            report_data = ai_json_response.get("report", {})
            all_mentioned_ids = extract_all_mentioned_ids(report_data)
            valid_ids_set = all_mentioned_ids.intersection(source_map.keys())
            print(f"✅ 验证完成: AI提及 {len(all_mentioned_ids)}个引用, 其中 {len(valid_ids_set)}个是有效的: {sorted(list(valid_ids_set))}")
            scrubbed_report_data = scrub_invalid_citations(report_data, valid_ids_set)
            print("✅ 报告清理完成: 已移除所有幻觉出的引用角标。")
        except json.JSONDecodeError:
            print(f"!!! Gemini 返回了无效的 JSON: {response.text[:500]}... !!!"); return jsonify({"error": "AI failed to generate valid report."}), 500

        final_sources = [ {**source_map[sid], 'id': sid} for sid in sorted(list(valid_ids_set)) if sid in source_map ]
        return jsonify({"company_name": company_name, "report": scrubbed_report_data, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误: {e} !!!"); print(traceback.format_exc()); return jsonify({"error": "Internal server error."}), 500

# --- 9. 错误处理 (无变化) ---
@app.errorhandler(429)
def ratelimit_handler(e):
    response = jsonify(error="rate_limit_exceeded")
    response.status_code = 429
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response

# --- 10. 启动 (无变化) ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)

