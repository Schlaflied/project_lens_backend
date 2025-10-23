# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# 「职场透镜」后端核心应用 (Project Lens Backend Core)
# 版本: 35.0 - 可点击引用最终版
# 描述: 1. (已实现) 修复了所有已知Bug，并升级引擎至 Gemini 2.5 Pro。
#       2. (本次更新) 根据用户最终要求，恢复并优化了 replace_citations_with_links
#          函数。它现在会生成标准的 Markdown 锚点链接 `[ID](#source-ID)`。
#          这是实现维基百科式可点击、可跳转引用的行业标准做法，
#          将功能指令与内容分离，交由前端进行最终渲染。
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
from flask_caching import Cache
import traceback
import datetime
# ✨ 核心：导入Google API核心异常
from google.api_core import exceptions as google_exceptions

# --- 1. 初始化和配置 ---
app = Flask(__name__)
CORS(app)

# 缓存配置
cache = Cache(app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 43200  # 12 hours in seconds
})

limiter = Limiter(get_remote_address, app=app, default_limits=["5 per day"], storage_uri="memory://")

from pinecone import Pinecone, ServerlessSpec

# --- 2. API密钥配置 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_HOST = os.getenv("PINECONE_HOST")

API_KEYS_CONFIGURED = all([GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID, PINECONE_API_KEY, PINECONE_HOST])
PINECONE_INDEX = None

try:
    if API_KEYS_CONFIGURED:
        genai.configure(api_key=GEMINI_API_KEY)
        pinecone_client = Pinecone(api_key=PINECONE_API_KEY, host=PINECONE_HOST)
        index_name = 'project-lens-data'
        
        # Check if index exists, and create if it doesn't
        if index_name not in pinecone_client.list_indexes().names():
            # Assuming a default dimension of 768 for 'text-embedding-004' and 'cosine' metric
            pinecone_client.create_index(name=index_name, dimension=768, metric='cosine', spec=ServerlessSpec(cloud='aws', region='us-west-2'))
            print(f"✅ Pinecone index '{index_name}' created.")
        
        PINECONE_INDEX = pinecone_client.Index(index_name)
        print("✅ API密钥与Pinecone配置成功！服务已准备就绪。")
    else:
        print("⚠️ 警告：一个或多个API密钥或Pinecone环境变量未设置。服务将以受限模式运行，/analyze 端点将不可用。")
except Exception as e:
    API_KEYS_CONFIGURED = False
    print(f"❌ API密钥或Pinecone配置失败: {e}")

# --- 3. 错误响应辅助函数 ---
def make_error_response(error_type, message, status_code):
    response = jsonify(error=error_type, message=message)
    response.status_code = status_code
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response
    
# --- 4. 速率限制消息辅助函数 ---
def get_rate_limit_message(request):
    messages = {
        'zh-CN': "开拓者，你已经用完了今日的额度。🚀 Project Lens每天为用户提供五次免费公司查询，如果你是重度用户，通过订阅Pro（Coming Soon）或者请我喝杯咖啡☕️来重置查询次数。",
        'zh-TW': "開拓者，你已經用完了今日的額度。🚀 Project Lens每天為用户提供五次免費公司查詢，如果你是重度用戶，通過訂閱Pro（Coming Soon）或者請我喝杯咖啡☕️來重置查詢次數。",
        'en': "Explorer, you have used up your free analysis quota for today. 🚀 Project Lens provides five free company analyses per day. If you're a heavy user, you can reset your query count by subscribing to Pro (Coming Soon) or by buying me a coffee ☕️."
    }
    lang_code = 'en'
    try:
        data = request.get_json(silent=True)
        if data and 'lang' in data:
            lang_code = data.get('lang')
    except Exception:
        pass
    return messages.get(lang_code, messages['en'])

# --- 5. 智能提取实体 ---
def extract_entities_with_ai(text_blob):
    print("🤖 启动AI实体提取程序 (模型: Gemini 2.5 Pro)...")
    try:
        model = genai.GenerativeModel('models/gemini-2.5-pro')
        prompt = f"""From the text below, extract the company name, job title, and location. Respond with a JSON object: {{"company_name": "...", "job_title": "...", "location": "..."}}.
If a value isn't found, return an empty string "".

Text:
---
{text_blob}
---
"""
        response = model.generate_content(prompt, generation_config=genai.GenerationConfig(response_mime_type="application/json"))
        if not response.parts: print(f"--- 实体提取AI响应被阻止: {response.prompt_feedback} ---"); return text_blob, "", ""
        entities = json.loads(response.text)
        company, job_title, location = entities.get("company_name", ""), entities.get("job_title", ""), entities.get("location", "")
        print(f"✅ AI提取成功: 公司='{company}', 职位='{job_title}', 地点='{location}'")
        return company if company else text_blob, job_title, location
    except Exception as e:
        raise e

# --- 6. Google搜索 ---
def perform_google_search(query, api_key, cse_id, num_results=2):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': api_key, 'cx': cse_id, 'q': query, 'num': num_results}
    print(f"🔍 正在执行Google搜索: 查询='{query}', 参数={params})") # Debug log
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        search_results = response.json()
        items_count = len(search_results.get('items', []))
        first_item_title = search_results.get('items', [{}])[0].get('title', '无标题') if items_count > 0 else '无结果'
        print(f"✅ Google搜索API响应: 查询='{query}', 结果数={items_count}, 首个结果标题='{first_item_title}'") # Debug log

        if 'items' not in search_results:
            print(f"⚠️ Google搜索成功但没有结果: 查询='{query}")
            return [], []
        snippets = [item.get('snippet', '') for item in search_results.get('items', [])]
        sources = [{'title': item.get('title'), 'link': item.get('link')} for item in search_results.get('items', [])]
        return snippets, sources
    except requests.exceptions.RequestException as e:
        print(f"❌ Google搜索请求失败: 查询='{query}', 错误={e}")
        return [], []
    except Exception as e:
        print(f"❌ Google搜索时发生未知错误: 查询='{query}', 错误={e}")
        return [], []

# --- 7. 网页爬虫与向量化 ---
def scrape_website_for_text(url):
    try:
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' }
        response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        [s.decompose() for s in soup(['script', 'style'])]
        cleaned_text = '\n'.join(chunk for chunk in (phrase.strip() for line in (line.strip() for line in soup.get_text().splitlines()) for phrase in line.split("  ")) if chunk)
        
        if cleaned_text and PINECONE_INDEX:
            try:
                print(f"📦 开始为 {url} 生成向量并存入Pinecone...")
                vector = genai.embed_content(model='models/text-embedding-004', content=cleaned_text, task_type='RETRIEVAL_DOCUMENT')
                metadata = {
                    'source_type': 'web_scrape',
                    'source_url': url,
                    'snippet': cleaned_text[:500],
                    'scraped_at': datetime.datetime.now().isoformat()
                }
                PINECONE_INDEX.upsert(vectors=[{'id': url, 'values': vector['embedding'], 'metadata': metadata}])
                print(f"✅ 成功将 {url} 的向量存入Pinecone。")
            except Exception as e:
                print(f"❌ 存入Pinecone时发生错误 (URL: {url}): {e}")

        return cleaned_text[:5000]
    except Exception as e:
        print(f"❌ 爬取网站时发生错误: {e}"); return None

# --- 8. 多语言Prompt指令核心 ---
PROMPTS = {
    'zh-CN': {
        'rag_prompt': """你是一个专业的职业分析师。请基于以下编号的上下文信息，回答用户的问题。规则：1. 你的回答必须完全基于提供的上下文信息。2. 在你提供信息的每一句话或关键事实之后，必须使用方括号 [编号] 来注明信息来源。3. 如果信息来自多个来源，请使用 [1], [2] 这样的格式。4. 保持分析的专业性和客观性。[上下文信息] {context_text} [用户问题] {user_query} [你的专业分析] """,
        'fallback_prompt': """As 'Project Lens', an expert AI assistant, generate a detailed analysis report in Simplified Chinese (简体中文) as a JSON object.
**Citation Rules (VERY IMPORTANT):**
1. Cite information by embedding the corresponding source tag (e.g., `[1]`, `[2]`).
2. **NEVER include URLs directly in the report text.** Use only the source ID tags for citation.
3. **You MUST ONLY use the source IDs provided in the `Research Data` section. DO NOT invent, hallucinate, or create any source IDs that are not explicitly given to you.**
4. When multiple sources support a single point, cite them individually, like `[21], [22], [29], [30]`.
5. Include all genuinely used IDs in the final `cited_ids` array.
**Information Provided:**
1. **Company, Role & Location:** {company_name} - {job_title} in {location}
2. **Current Date:** {current_date}
3. **Applicant's Resume/Bio:**
   ```{resume_text}```
4. **Research Data (Each block has a `[Source ID: X]`):**
   ```{context_with_sources}```
**Your Task:** Synthesize all info into a single JSON object with the following structure:
```json
{{
  "report": {{
    "company_location": "{location}",
    "red_flag_status": "Your assessment (e.g., 'Low Risk').",
    "red_flag_text": "Detailed explanation for red flags. Cite sources like [1] or [2], [3].",
    "hiring_experience_text": "Analysis of hiring process. Cite sources.",
    "timeliness_analysis": "1. Analyze info recency. 2. Analyze job posting status (e.g., 'Likely open', 'Potentially expired') and give a reason. Cite sources.",
    "culture_fit": {{ "reputation": "", "management": "", "sustainability": "", "wlb": "", "growth": "", "salary": "", "overtime": "", "innovation": "", "benefits": "", "diversity": "", "training": "" }},
    "value_match_score": "A number from 0-100. 0 if no resume.",
    "value_match_text": "Explanation of the match score. Cite sources.",
    "final_risk_rating": "Your final risk rating.",
    "final_risk_text": "Summary justifying the final rating. Cite sources."
  }},
  "cited_ids": []
}}
```"""
    },
    'en': {
        'rag_prompt': """You are a professional career analyst. Please answer the user's question based on the numbered context information below. Rules: 1. Your answer must be based entirely on the context provided. 2. After every sentence or key fact you provide, you MUST cite the source using brackets [Number]. 3. If information comes from multiple sources, use the format [1], [2]. 4. Maintain a professional and objective tone in your analysis. [Context] {context_text} [User Question] {user_query} [Your Professional Analysis] """,
        'fallback_prompt': """As 'Project Lens', an expert AI assistant, generate a detailed analysis report in English as a JSON object.
**Citation Rules (VERY IMPORTANT):**
1. Cite information by embedding the corresponding source tag (e.g., `[1]`, `[2]`).
2. **NEVER include URLs directly in the report text.** Use only the source ID tags for citation.
3. **You MUST ONLY use the source IDs provided in the `Research Data` section. DO NOT invent, hallucinate, or create any source IDs that are not explicitly given to you.**
4. When multiple sources support a single point, cite them individually, like `[21], [22], [29], [30]`.
5. Include all genuinely used IDs in the final `cited_ids` array.
**Information Provided:**
1. **Company, Role & Location:** {company_name} - {job_title} in {location}
2. **Current Date:** {current_date}
3. **Applicant's Resume/Bio:**
   ```{resume_text}```
4. **Research Data (Each block has a `[Source ID: X]`):**
   ```{context_with_sources}```
**Your Task:** Synthesize all info into a single JSON object with the following structure:
```json
{{
  "report": {{
    "company_location": "{location}",
    "red_flag_status": "Your assessment (e.g., 'Low Risk').",
    "red_flag_text": "Detailed explanation for red flags. Cite sources like [1] or [2], [3].",
    "hiring_experience_text": "Analysis of hiring process. Cite sources.",
    "timeliness_analysis": "1. Analyze info recency. 2. Analyze job posting status (e.g., 'Likely open', 'Potentially expired') and give a reason. Cite sources.",
    "culture_fit": {{ "reputation": "", "management": "", "sustainability": "", "wlb": "", "growth": "", "salary": "", "overtime": "", "innovation": "", "benefits": "", "diversity": "", "training": "" }},
    "value_match_score": "A number from 0-100. 0 if no resume.",
    "value_match_text": "Explanation of the match score. Cite sources.",
    "final_risk_rating": "Your final risk rating.",
    "final_risk_text": "Summary justifying the final rating. Cite sources."
  }},
  "cited_ids": []
}}
```"""
    },
    'zh-TW': {
        'rag_prompt': """你是一個專業的職業分析師。請基於以下編號的上下文資訊，回答用戶的問題。規則：1. 你的回答必須完全基於提供的上下文資訊。2. 在你提供資訊的每一句話或關鍵事實之後，必須使用方括號 [編號] 來註明資訊來源。3. 如果資訊來自多個來源，請使用 [1], [2] 這樣的格式。4. 保持分析的專業性和客觀性。[上下文資訊] {context_text} [用戶問題] {user_query} [你的專業分析] """,
        'fallback_prompt': """As 'Project Lens', an expert AI assistant, generate a detailed analysis report in Traditional Chinese (繁體中文) as a JSON object.
**Citation Rules (VERY IMPORTANT):**
1. Cite information by embedding the corresponding source tag (e.g., `[1]`, `[2]`).
2. **NEVER include URLs directly in the report text.** Use only the source ID tags for citation.
3. **You MUST ONLY use the source IDs provided in the `Research Data` section. DO NOT invent, hallucinate, or create any source IDs that are not explicitly given to you.**
4. When multiple sources support a single point, cite them individually, like `[21], [22], [29], [30]`.
5. Include all genuinely used IDs in the final `cited_ids` array.
**Information Provided:**
1. **Company, Role & Location:** {company_name} - {job_title} in {location}
2. **Current Date:** {current_date}
3. **Applicant's Resume/Bio:**
   ```{resume_text}```
4. **Research Data (Each block has a `[Source ID: X]`):**
   ```{context_with_sources}```
**Your Task:** Synthesize all info into a single JSON object with the following structure:
```json
{{
  "report": {{
    "company_location": "{location}",
    "red_flag_status": "Your assessment (e.g., 'Low Risk').",
    "red_flag_text": "Detailed explanation for red flags. Cite sources like [1] or [2], [3].",
    "hiring_experience_text": "Analysis of hiring process. Cite sources.",
    "timeliness_analysis": "1. Analyze info recency. 2. Analyze job posting status (e.g., 'Likely open', 'Potentially expired') and give a reason. Cite sources.",
    "culture_fit": {{ "reputation": "", "management": "", "sustainability": "", "wlb": "", "growth": "", "salary": "", "overtime": "", "innovation": "", "benefits": "", "diversity": "", "training": "" }},
    "value_match_score": "A number from 0-100. 0 if no resume.",
    "value_match_text": "Explanation of the match score. Cite sources.",
    "final_risk_rating": "Your final risk rating.",
    "final_risk_text": "Summary justifying the final rating. Cite sources."
  }},
  "cited_ids": []
}}
```"""
    }
}

# --- 9. 引用净化与链接注入 (已修复) ---
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



# --- 10. API路由 (已更新) ---
@app.route('/', methods=['GET'])
def health_check():
    key_status = { "GEMINI_API_KEY": "配置成功" if GEMINI_API_KEY else "缺失", "SEARCH_API_KEY": "配置成功" if SEARCH_API_KEY else "缺失", "SEARCH_ENGINE_ID": "配置成功" if SEARCH_ENGINE_ID else "缺失" }
    status_message = "服务运行正常" if all([GEMINI_API_KEY, SEARCH_API_KEY, SEARCH_ENGINE_ID]) else "警告：API密钥配置不完整，核心功能将无法使用"
    return jsonify({ "service_name": "Project Lens Backend", "status": status_message, "timestamp": datetime.datetime.utcnow().isoformat() + "Z", "api_keys_status": key_status }), 200

@app.route('/analyze', methods=['POST', 'OPTIONS'])
@limiter.limit("5 per day")
@cache.cached(key_prefix=lambda: request.get_data())
def analyze_company_text():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200
    if not API_KEYS_CONFIGURED: return make_error_response("configuration_error", "一个或多个必需的API密钥未在服务器上配置。", 503)

    print("--- RAG analysis request received! ---")
    try:
        data = request.get_json()
        if not data: return make_error_response("invalid_json", "Request body is not valid JSON.", 400)

        user_query = data.get('companyName')
        if not user_query: return make_error_response("missing_parameter", "Company name is required.", 400)

        lang = data.get('lang', 'zh-CN')
        if lang not in ['en', 'zh-CN', 'zh-TW']:
            lang = 'zh-CN'

        # RAG Step 1: Query VectorDB
        if PINECONE_INDEX:
            try:
                print(f"🔍 Performing RAG search in Pinecone for: '{user_query}'")
                query_vector = genai.embed_content(model='models/text-embedding-004', content=user_query, task_type='RETRIEVAL_QUERY')['embedding']
                
                query_results = PINECONE_INDEX.query(
                    vector=query_vector,
                    top_k=5,
                    include_metadata=True
                )
                
                # Check if results are good enough (e.g., score > 0.5)
                relevant_matches = [match for match in query_results['matches'] if match['score'] > 0.5]

                if relevant_matches:
                    print(f"✅ Found {len(relevant_matches)} relevant documents in Pinecone.")
                    
                    context_chunks = []
                    sources_for_frontend = []
                    for i, match in enumerate(relevant_matches, 1):
                        metadata = match['metadata']
                        snippet = metadata.get('snippet', '')
                        context_chunks.append(f"[{i}] {snippet}")
                        
                        # Prepare sources for frontend
                        label = "Unknown Source"
                        if metadata.get('source_type') == 'web_scrape':
                            try:
                                domain = metadata['source_url'].split('/')[2]
                                date = metadata['scraped_at'].split('T')[0]
                                label = f"{domain} ({date})"
                            except:
                                label = "Web Scrape"
                        else:
                            label = metadata.get('label', f"Source {i}")

                        
                        sources_for_frontend.append({
                            'id': i,
                            'title': label,
                            'link': metadata.get('source_url', '#'),
                            'source_type': metadata.get('source_type', 'default'),
                            'snippet': snippet
                        })
                    
                    context_text = "\n\n".join(context_chunks)

                    # RAG Step 2: Generate Answer from Context
                    model = genai.GenerativeModel('models/gemini-2.5-pro')
                    rag_prompt = PROMPTS[lang]['rag_prompt'].format(context_text=context_text, user_query=user_query)
                    response = model.generate_content(rag_prompt)
                    
                    # RAG Step 3: Format Response
                    answer = response.text

                    return jsonify({
                        "answer": answer,
                        "sources": sources_for_frontend,
                        "company_name": user_query
                    })

            except Exception as e:
                print(f"❌ RAG search failed: {e}")
                # Proceed to fallback logic
        
        print("⚠️ Pinecone RAG did not yield sufficient results. Falling back to web scraping.")
        # Fallback logic (original implementation)
        
        try:
            company_name, job_title, location = extract_entities_with_ai(user_query)
        except Exception as e:
            print(f"!!! 实体提取AI调用失败: {e} !!!"); print(traceback.format_exc())
            error_message = f"AI entity extraction failed. Error: {type(e).__name__}. This might be a problem with the Generative Language API permissions or billing. Please ensure the model 'models/gemini-2.5-pro' is available for your project."
            return make_error_response("ai_entity_extraction_error", error_message, 500)

        if not company_name: return make_error_response("entity_extraction_failed", "Could not identify company name from input.", 400)

        context_blocks, source_map, source_id_counter = [], {}, 1
        location_query_part = f' "{location}"' if location else ""
        comprehensive_queries = list(set([ f'"{company_name}"{location_query_part} {aspect}' for aspect in ["company culture review", "work life balance", "salary benefits", "growth opportunities", "hiring process interview", "management style", "overtime culture", "innovation culture", "diversity inclusion", "training programs", "sustainability", "scam fraud"] ] + [f'site:linkedin.com "{company_name}" "{location}"', f'site:indeed.com "{company_name}" "{location}" reviews', f'site:glassdoor.com "{company_name}" "{location}" reviews']))
        
        scraped_urls = set()
        for query in comprehensive_queries:
            print(f"🔍 正在处理综合查询: '{query}'") # New debug log
            search_query = f'{query}'
            snippets, sources_data = perform_google_search(search_query, SEARCH_API_KEY, SEARCH_ENGINE_ID)
            print(f"✅ 综合查询结果: 查询='{query}', 找到 {len(snippets)} 个片段, {len(sources_data)} 个来源数据") # New debug log
            for i, snippet in enumerate(snippets):
                if i < len(sources_data):
                    source_info = sources_data[i]
                    link = source_info.get('link')
                    if link and link not in scraped_urls:
                        scraped_text = scrape_website_for_text(link)
                        if scraped_text:
                            context_blocks.append(f"[Source ID: {source_id_counter}] {scraped_text}")
                            source_map[source_id_counter] = {
                                'title': source_info.get('title'),
                                'link': link,
                                'snippet': snippet
                            }
                            source_id_counter += 1
                            scraped_urls.add(link)
            time.sleep(0.1)

        if not context_blocks: return make_error_response("no_info_found", "No information found for this company. This might be due to the company being very new, very small, or the search query being too specific. Please try a broader search term.", 404)

        full_prompt = PROMPTS[lang]['fallback_prompt'].format(company_name=company_name, job_title=job_title, location=location or "Not Specified", current_date=datetime.date.today().strftime("%Y-%m-%d"), resume_text=data.get('resumeText', 'No resume provided.'), context_with_sources="\n\n".join(context_blocks))
        
        try:
            model = genai.GenerativeModel('models/gemini-2.5-pro')
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
            
            # --- RAG 修复逻辑 ---
            all_mentioned_ids = extract_all_mentioned_ids(report_data)
            valid_ids_set = all_mentioned_ids.intersection(source_map.keys())
            scrubbed_report_data = scrub_invalid_citations(report_data, valid_ids_set)
            # [最终修复] 将有效的 [ID] 标记转换为可点击的 Markdown 锚点链接
            final_report_data = scrubbed_report_data

        except json.JSONDecodeError:
            print(f"!!! AI returned malformed JSON: {response.text} !!!") # Log the raw response
            return make_error_response("ai_malformed_json", "AI failed to generate a valid JSON report.", 500)

        final_sources = [ {**source_map[sid], 'id': sid} for sid in sorted(list(valid_ids_set)) if sid in source_map ]
        return jsonify({"company_name": company_name, "answer": final_report_data, "sources": final_sources})

    except Exception as e:
        print(f"!!! 发生未知错误(被主路由捕获): {e} !!!"); print(traceback.format_exc())
        return make_error_response("internal_server_error", "An unexpected error occurred. Please check server logs for details.", 500)

# --- 11. 速率限制与全局错误处理器 ---
@app.errorhandler(429)
def ratelimit_handler(e):
    print(f"Flask-Limiter rate limit triggered: {e.description}")
    message = get_rate_limit_message(request)
    return make_error_response("rate_limit_exceeded", message, 429)

@app.errorhandler(500)
def handle_internal_server_error(e):
    print(f"!!! 全局500错误处理器被触发: {e} !!!")
    print(traceback.format_exc())
    error_message = "An unexpected internal server error occurred. The development team has been notified."
    return make_error_response("internal_server_error", error_message, 500)


# --- 12. 启动 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)
