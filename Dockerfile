# -----------------------------------------------------------------------------
# 「职场透镜」后端施工图纸 (Project Lens Backend Dockerfile)
# 版本: 4.0 - Flask 最终修正版
# 描述: 参照「灵感方舟」的成功配置，使用正确的Gunicorn命令启动Flask应用。
# -----------------------------------------------------------------------------

# 1. 使用官方的、轻量级的Python 3.9镜像作为基础
FROM python:3.9-slim

# 2. 设置环境变量，让Python运行更稳定
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 3. 在容器内创建一个工作目录
WORKDIR /app

# 4. 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 复制你项目的所有其他文件到工作目录
COPY . .

# 6. 【关键修正】设置正确的启动命令
#    使用和「灵感方舟」完全一致的Gunicorn启动命令来运行Flask应用。
#    它会自动监听Cloud Run通过 $PORT 环境变量指定的正确端口。
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app

