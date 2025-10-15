# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 31.4 - 详细错误诊断版
# 描述: 1. (已实现) 修复了CORS、搜索参数等核心功能Bug，并增加了健康检查。
#       2. (本次更新) 在'analyze'函数内部增加了更详细的try-except块，
#          分别包裹了两个关键的Gemini API调用。现在如果其中任何一个环节
#          失败，前端将收到一个更具体的错误信息（例如 "ai_entity_extraction_error"），
#          而不是一个笼统的 "internal_server_error"，极大地简化了远程调试的难度。
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
# ✨ 核心：导入Google API核心异常
from google.api_core import exceptions as google_exceptions

# --- 1. 初始化和配置 ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}) # 允许所有路由的CORS
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"], storage_uri="memory://")

# --- 2. API密钥配置 ---
# 将密钥加载移到全局作用域，方便健康检查函数访问
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
API_KEYS_CONFIGURED = all([GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID])

try:
    if API_KEYS_CONFIGURED:
        genai.configure(api_key=GEMINI_API_KEY)
        print("✅ API密钥配置成功！服务已准备就绪。")
    else:
        print("⚠️ 警告：一个或多个API密钥环境变量未设置。服务将以受限模式运行，/analyze 端点将不可用。")
except Exception as e:
    API_KEYS_CONFIGURED = False
    print(f"❌ API密钥配置失败: {e}")

# --- 3. 错误响应辅助函数 ---
def make_error_response(error_type, message, status_code):
    """创建一个标准的、带有CORS头的JSON错误响应。"""
    response = jsonify(error=error_type, message=message)
    response.status_code = status_code
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response
    
# --- 4. 速率限制消息辅助函数 ---
def get_rate_limit_message(request):
    """根据请求语言返回标准化的速率限制消息。"""
    messages = {
        'zh-CN': "开拓者，你已经用完了今日的额度。🚀 Project Lens每天为用户提供五次免费公司查询，如果你是重度用户，通过订阅Pro（Coming Soon）或者请我喝杯咖啡☕️来重置查询次数。",
        'zh-TW': "開拓者，你已經用完了今日的額度。🚀 Project Lens每天為用户提供五次免費公司查詢，如果你是重度用戶，通過訂閱Pro（Coming Soon）或者請我喝杯咖啡☕️來重置查詢次數。",
        'en': "Explorer, you have used up your free analysis quota for today. 🚀 Project Lens provides five free company analyses per day. If you're a heavy user, you can reset your query count by subscribing to Pro (Coming Soon) or by buying me a coffee ☕️."
    }
    lang_code = 'en'
    try:
        data = request.get_json(silent=True)
        if data and 'language' in data:
            lang_code = data.get('language')
    except Exception:
        pass
    return messages.get(lang_code, messages['en'])


# --- 5. 智能提取实体 ---
def extract_entities_with_ai(text_blob):
    print("🤖 启动AI实体提取程序 (含地点)...")
    try:
        model = genai.GenerativeModel('gemini-pro')
        prompt = (f'From the text below, extract the company name, job title, and location. Respond with a JSON object: {{"company_name": "...", "job_title": "...", "location": "..."}}.\nIf a value isn\'t found, return an empty string "".\n\nText:\n---\n{text_blob}\n---\n')
        response = model.generate_content(prompt, generation_config=genai.GenerationConfig(response_mime_type="application/json"))
        if not response.parts: print(f"--- 实体提取AI响应被阻止: {response.prompt_feedback} ---"); return text_blob, "", ""
        entities = json.loads(response.text)
        company, job_title, location = entities.get("company_name", ""), entities.get("job_title", ""), entities.get("location", "")
        print(f"✅ AI提取成功: 公司='{company}', 职位='{job_title}', 地点='{location}'")
        return company if company else text_blob, job_title, location
    except Exception as e:
        raise e

# --- 6. Google搜索 (已修复) ---
def perform_google_search(query, api_key, cse_id, num_results=2):
    """
    [BUG修复] 移除了无效的 'sort': 'date' 参数。
    查询字符串中已经通过 'after:YYYY' 进行了年份筛选，
    额外的 sort 参数不符合API规范，会导致API不返回任何结果。
    """
    url = "https://www.googleapis.com/customsearch/v1"
    # 修复：移除了 'sort': 'date'
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results}
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        search_results = response.json()
        if 'items' not in search_results:
            print(f"⚠️ Google搜索成功但没有结果: 查询='{query}'")
            return [], []
        snippets = [item.get('snippet', '') for item in search_results.get('items', [])]
        sources = [{'title': item.get('title'), 'link': item.get('link')} for item in search_results.get('items', [])]
        return snippets, sources
    except requests.exceptions.RequestException as e:
        print(f"❌ Google搜索请求失败: {e}"); return [], []
    except Exception as e:
        print(f"❌ Google搜索时发生未知错误: {e}"); return [], []

# --- 7. 网页爬虫 ---
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

# --- 8. 核心AI指令 (Prompt) ---
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

# --- 9. 引用净化与链接注入 ---
def extract_all_mentioned_ids(report_data):
    all_text = json.dumps(report_data)
    found_ids = re.findall(r'\[(\d+)\]', all_text)
    return set(int(id_str) for id_str in found_ids)

def scrub_invalid_citations(data, valid_ids_set):
    if isinstance(data, dict): return {k: scrub_invalid_citations(v, valid_ids_set) for k, v in data.items()}
    if isinstance(data, list): return [scrub_invalid_citations(elem, valid_ids_set) for elem in data]
    if isinstance(data, str):
        return re.sub(r'\[(\d+)\]', lambda m: m.group(0) if int(m.group(1)) in valid_ids_set else "", data)
    return data

def replace_citations_with_links(data, source_map):
    if isinstance(data, dict): return {k: replace_citations_with_links(v, source_map) for k, v in data.items()}
    if isinstance(data, list): return [replace_citations_with_links(elem, source_map) for elem in data]
    if isinstance(data, str):
        def repl(match):
            citation_id = int(match.group(1))
            if citation_id in source_map and source_map[citation_id].get('link'):
                return f'[{citation_id}]({source_map[citation_id]["link"]})'
            return match.group(0)
        return re.sub(r'\[(\d+)\]', repl, data)
    return data

# --- 10. API路由 ---

# [新增] 健康检查端点
@app.route('/', methods=['GET'])
def health_check():
    """提供一个简单的健康检查端点来验证服务是否在线和API密钥是否配置。"""
    key_status = {
        "GEMINI_API_KEY": "配置成功" if GEMINI_API_KEY else "缺失",
        "SEARCH_API_KEY": "配置成功" if SEARCH_API_KEY else "缺失",
        "SEARCH_ENGINE_ID": "配置成功" if SEARCH_ENGINE_ID else "缺失"
    }
    status_message = "服务运行正常" if all([GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID]) else "警告：API密钥配置不完整，核心功能将无法使用"
    
    return jsonify({
        "service_name": "Project Lens Backend",
        "status": status_message,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "api_keys_status": key_status
    }), 200

@app.route('/analyze', methods=['POST', 'OPTIONS'])
@limiter.limit("5 per day")
def analyze_company_text():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200
    
    if not API_KEYS_CONFIGURED:
        return make_error_response("configuration_error", "一个或多个必需的API密钥未在服务器上配置。", 503)

    print("--- v31.4 Detailed Diag analysis request received! ---")
    
    # [BUG修复] 将整个主逻辑包裹在一个大的try-except中，但内部增加了更详细的错误捕获
    try:
        data = request.get_json()
        if not data: return make_error_response("invalid_json", "Request body is not valid JSON.", 400)

        smart_paste_content = data.get('companyName')
        if not smart_paste_content: return make_error_response("missing_parameter", "Company name is required.", 400)
        
        # 详细诊断点 1: 实体提取
        try:
            company_name, job_title, location = extract_entities_with_ai(smart_paste_content)
        except Exception as e:
            print(f"!!! 实体提取AI调用失败: {e} !!!"); print(traceback.format_exc())
            error_message = f"AI entity extraction failed. Error: {type(e).__name__}. This might be a problem with the Generative Language API permissions or billing."
            return make_error_response("ai_entity_extraction_error", error_message, 500)

        if not company_name: return make_error_response("entity_extraction_failed", "Could not identify company name from input.", 400)

        context_blocks, source_map, source_id_counter = [], {}, 1
        location_query_part = f' "{location}"' if location else ""
        
        comprehensive_queries = list(set([ f'"{company_name}"{location_query_part} {aspect}' for aspect in ["company culture review", "work life balance", "salary benefits", "growth opportunities", "hiring process interview", "management style", "overtime culture", "innovation culture", "diversity inclusion", "training programs", "sustainability", "scam fraud"] ] + [f'site:linkedin.com "{company_name}" "{location}"', f'site:indeed.com "{company_name}" "{location}" reviews', f'site:glassdoor.com "{company_name}" "{location}" reviews']))
        
        for query in comprehensive_queries:
            search_query = f'{query} after:{datetime.date.today().year - 2}'
            snippets, sources_data = perform_google_search(search_query, SEARCH_API_KEY, SEARCH_ENGINE_ID)
            for i, snippet in enumerate(snippets):
                if i < len(sources_data):
                    source_info = sources_data[i]
                    link = source_info.get('link', '').lower()
                    source_info['source_type'] = 'linkedin' if 'linkedin.com' in link else 'glassdoor' if 'glassdoor.com' in link else 'indeed' if 'indeed.com' in link else 'default'
                    context_blocks.append(f"[Source ID: {source_id_counter}] {snippet}")
                    source_map[source_id_counter] = source_info
                    source_id_counter += 1
            time.sleep(0.1)

        if not context_blocks: return make_error_response("no_info_found", "No information found for this company. This might be due to the company being very new, very small, or the search query being too specific. Please try a broader search term.", 404)

        lang_code = data.get('language', 'en')
        language_instructions = {'en': 'English', 'zh-CN': 'Simplified Chinese (简体中文)', 'zh-TW': 'Traditional Chinese (繁體中文)'}
        full_prompt = PROMPT_TEMPLATE.format(output_language=language_instructions.get(lang_code, 'English'), company_name=company_name, job_title=job_title, location=location or "Not Specified", current_date=datetime.date.today().strftime("%Y-%m-%d"), resume_text=data.get('resumeText', 'No resume provided.'), context_with_sources="\n\n".join(context_blocks))
        
        # 详细诊断点 2: 核心分析
        try:
            model = genai.GenerativeModel('gemini-pro')
            safety_settings = { category: "BLOCK_NONE" for category in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]}
            response = model.generate_content(full_prompt, generation_config=genai.GenerationConfig(response_mime_type="application/json"), safety_settings=safety_settings)
        except Exception as e:
            print(f"!!! 核心分析AI调用失败: {e} !!!"); print(traceback.format_exc())
            error_message = f"Main AI analysis call failed. Error: {type(e).__name__}. This could be due to API permissions, billing, or an issue with the content sent for analysis."
            return make_error_response("ai_analysis_error", error_message, 500)
        
        if not response.parts: return make_error_response("ai_response_blocked", "AI content generation was blocked by safety settings.", 500)

        try:
            ai_json_response = json.loads(response.text)
            report_data = ai_json_response.get("report", {})
            all_mentioned_ids = extract_all_mentioned_ids(report_data)
            valid_ids_set = all_mentioned_ids.intersection(source_map.keys())
            scrubbed_report_data = scrub_invalid_citations(report_data, valid_ids_set)
            linked_report_data = replace_citations_with_links(scrubbed_report_data, source_map)
        except json.JSONDecodeError:
            return make_error_response("ai_malformed_json", "AI failed to generate a valid JSON report.", 500)

        final_sources = [ {**source_map[sid], 'id': sid} for sid in sorted(list(valid_ids_set)) if sid in source_map ]
        return jsonify({"company_name": company_name, "report": linked_report_data, "sources": final_sources})

    except google_exceptions.ResourceExhausted as e:
        print(f"!!! Gemini API Rate Limit Exceeded: {e} !!!")
        message = get_rate_limit_message(request)
        return make_error_response("rate_limit_exceeded", message, 429)
    except google_exceptions.PermissionDenied as e:
        print(f"!!! Gemini API Permission Denied: {e} !!!")
        error_message = "AI model permission denied. Please check your GEMINI_API_KEY and ensure the API and billing are enabled in your Google Cloud project."
        return make_error_response("gemini_permission_denied", error_message, 500)
    except Exception as e:
        print(f"!!! 发生未知错误(被主路由捕获): {e} !!!"); print(traceback.format_exc())
        return make_error_response("internal_server_error", "An unexpected error occurred. Please check server logs for details.", 500)

# --- 11. 速率限制与全局错误处理器 ---
@app.errorhandler(429)
def ratelimit_handler(e):
    """这个函数现在只处理我们自己设置的每日5次限制。"""
    print(f"Flask-Limiter rate limit triggered: {e.description}")
    message = get_rate_limit_message(request)
    return make_error_response("rate_limit_exceeded", message, 429)

@app.errorhandler(500)
def handle_internal_server_error(e):
    """
    捕获所有未处理的500内部服务器错误（作为终极安全网）。
    这可以确保即使发生意外崩溃，也能返回带CORS头的JSON响应。
    """
    print(f"!!! 全局500错误处理器被触发: {e} !!!")
    print(traceback.format_exc())
    error_message = "An unexpected internal server error occurred. The development team has been notified."
    return make_error_response("internal_server_error", error_message, 500)


# --- 12. 启动 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)

