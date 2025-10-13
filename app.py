# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 22.0 - 幻觉净化版 (Hallucination Scrubber Version)
# 描述: 1. (已实现) 完整的引用防幻觉与双重验证机制。
#       2. (本次更新) 引入终极“上下文净化”机制。在收到AI报告后，后端会：
#          a. 提取AI文本中所有提及的引用ID。
#          b. 将这些ID与我们提供给AI的原始来源进行比对，找出有效的ID。
#          c. 遍历AI生成的报告文本，将所有无效的（幻觉出的）引用角标直接删除。
#          d. 只将清理过的报告和完全匹配的有效来源发送给前端，从根源杜绝问题。
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

# --- 7. 引用净化辅助函数 [新增] ---
def extract_all_mentioned_ids(report_data):
    """遍历报告的所有文本字段，提取所有 [数字] 形式的引用标记。"""
    all_text = json.dumps(report_data)
    found_ids = re.findall(r'\[(\d+)\]', all_text)
    return set(int(id_str) for id_str in found_ids)

def scrub_invalid_citations(data, valid_ids_set):
    """
    递归遍历数据结构，从字符串中移除所有无效的引用角标 [n]。
    一个引用角标是无效的，如果它的ID不在 valid_ids_set 中。
    """
    if isinstance(data, dict):
        return {k: scrub_invalid_citations(v, valid_ids_set) for k, v in data.items()}
    elif isinstance(data, list):
        return [scrub_invalid_citations(elem, valid_ids_set) for elem in data]
    elif isinstance(data, str):
        def repl(match):
            citation_id = int(match.group(1))
            return match.group(0) if citation_id in valid_ids_set else ""
        return re.sub(r'\[(\d+)\]', repl, data)
    else:
        return data

# --- 8. API路由 [已升级] ---
@app.route('/analyze', methods=['POST', 'OPTIONS'])
@limiter.limit("5 per day")
def analyze_company_text():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
        
    print("--- v22.0 Hallucination Scrubber Version Analysis request received! ---")
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
            
            # --- 【核心升级】终极“幻觉净化”流程 ---
            # 1. 从AI生成的文本中，提取它提到的所有引用ID。
            all_mentioned_ids = extract_all_mentioned_ids(report_data)
            
            # 2. 找出真正有效的ID（即AI提到的，并且我们确实提供过的）。
            valid_ids_set = all_mentioned_ids.intersection(source_map.keys())
            print(f"✅ 验证完成: AI提及 {len(all_mentioned_ids)}个引用, 其中 {len(valid_ids_set)}个是有效的: {sorted(list(valid_ids_set))}")

            # 3. 清理报告：从报告文本中移除所有无效的（幻觉出来的）引用角标。
            scrubbed_report_data = scrub_invalid_citations(report_data, valid_ids_set)
            print("✅ 报告清理完成: 已移除所有幻觉出的引用角标。")

        except json.JSONDecodeError:
            print(f"!!! Gemini 返回了无效的 JSON: {response.text[:500]}... !!!"); return jsonify({"error": "AI failed to generate valid report."}), 500

        # 4. 只发送那些在清理后报告中依然存在的、有效的来源信息。
        final_sources = [ {**source_map[sid], 'id': sid} for sid in sorted(list(valid_ids_set)) if sid in source_map ]

        # 5. 返回被彻底清理过的报告和完全匹配的来源列表。
        return jsonify({"company_name": company_name, "report": scrubbed_report_data, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误: {e} !!!"); print(traceback.format_exc()); return jsonify({"error": "Internal server error."}), 500

# --- 9. 错误处理 (无变化) ---
@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(error="rate_limit_exceeded"), 429

# --- 10. 启动 (无变化) ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)

