import requests
import json

API_KEY = "AIzaSyA54ADPb5KBb8eTEppiDPMKRHwzSelYWOA"
CSE_ID = "b6faa9dfdc9e64138"
QUERY = "Google company culture"

url = "https://www.googleapis.com/customsearch/v1"
params = {'key': API_KEY, 'cx': CSE_ID, 'q': QUERY, 'num': 2}

try:
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    search_results = response.json()

    if 'items' in search_results:
        print(f"✅ Google Custom Search API测试成功！找到 {len(search_results['items'])} 条结果。")
        for i, item in enumerate(search_results['items']):
            print(f"  - 结果 {i+1}: {item.get('title')} ({item.get('link')})")
    else:
        print(f"⚠️ Google Custom Search API测试成功，但未找到结果。原始响应: {search_results}")
except requests.exceptions.RequestException as e:
    print(f"❌ Google Custom Search API请求失败: {e}")
except Exception as e:
    print(f"❌ Google Custom Search API测试时发生未知错误: {e}")
