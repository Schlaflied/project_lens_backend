# 🔬 职场透镜 (Project Lens) - 后端 / Backend

这是一个基于 Python Flask 框架开发的 AI 职业分析后端服务，旨在帮助用户快速评估公司文化、潜在风险和职位契合度。

This is an AI-powered career analysis backend service built with the Python Flask framework, designed to help users quickly assess company culture, potential risks, and job fit.

## 核心功能 / Core Features

* **AI 深度分析 / AI Deep Analysis:** 使用 **Google Gemini 2.5 Pro** 模型进行专业的公司风险评估和文化契合度分析。
* **RAG 增强搜索 / RAG Enhanced Search:** 结合 **Pinecone** 向量数据库和 **Google Custom Search API**，获取最新的、具有可点击引用的多源信息。
* **智能实体提取 / Smart Entity Extraction:** 自动从用户输入中提取公司名、职位和地点。
* **速率限制与缓存 / Rate Limiting & Caching:** 采用 Flask-Limiter 和 Flask-Caching 来控制接口调用频率（默认 5 次/天）和优化性能。
* **多语言支持 / Multilingual Support:** 支持简体中文 (zh-CN)、繁体中文 (zh-TW) 和英文 (en)。

## 技术栈 / Tech Stack

| 模块 / Module | 组件 / Component | 描述 / Description |
| :--- | :--- | :--- |
| **框架 / Framework** | Flask | 轻量级的 Python Web 框架。/ Lightweight Python web framework. |
| **AI 引擎 / AI Engine** | `google-generativeai` | 用于调用 Gemini 2.5 Pro 进行分析和嵌入。/ Used for calling Gemini 2.5 Pro for analysis and embeddings. |
| **向量数据库 / Vector DB** | Pinecone | 用于检索增强生成 (RAG) 流程。/ Used for the Retrieval-Augmented Generation (RAG) pipeline. |
| **部署 / Deployment** | Docker, Gunicorn | 容器化和生产环境 Web 服务器（针对 Cloud Run 优化）。/ Containerization and production web server (optimized for Cloud Run). |
| **爬虫 / Scraper** | `requests`, `beautifulsoup4` | 用于抓取 Google 搜索结果中的网页文本。/ Used to scrape web text from Google search results. |

## API 端点 / API Endpoints

| 方法 / Method | 路径 / Path | 描述 / Description |
| :--- | :--- | :--- |
| `GET` | `/` | Health Check. 检查服务状态和 API 密钥配置。 / Checks service health and API key configuration. |
| `POST` | `/analyze` | **核心分析接口**。接受 JSON 数据，返回详细的公司分析报告。 / **Core Analysis Endpoint**. Accepts JSON data and returns a detailed company analysis report. |

### `POST /analyze` 请求体示例 / Request Body Example

```json
{
  "companyName": "Tesla Software Engineer, Fremont CA",
  "resumeText": "Passionate developer with 5 years experience in machine learning and a focus on work-life balance.",
  "lang": "zh-CN"
}







## 部署配置 / Deployment Configuration

项目需要以下环境变量才能正常运行。/ The project requires the following environment variables to run correctly.

| 变量名 / Variable Name | 描述 / Description |
| :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API 密钥。/ Google Gemini API Key. |
| `SEARCH_API_KEY` | Google Custom Search API 密钥。/ Google Custom Search API Key. |
| `SEARCH_ENGINE_ID` | Google Custom Search Engine ID。/ Google Custom Search Engine ID. |
| `PINECONE_API_KEY` | Pinecone 向量数据库 API 密钥。/ Pinecone Vector Database API Key. |
| `PINECONE_ENVIRONMENT` | Pinecone 环境名称。/ Pinecone Environment Name. |
| `PORT` | 服务监听端口（如 `8080`），通常由 PaaS 平台（如 Cloud Run）自动注入。/ The service listening port (e.g., `8080`), usually injected automatically by PaaS platforms (like Cloud Run). |
