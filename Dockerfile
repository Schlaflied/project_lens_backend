<<<<<<< HEAD
# -----------------------------------------------------------------------------
# 「职场透镜」后端施工图纸 (Project Lens Backend Dockerfile)
# 版本: 5.0 - 最终稳定版
# 描述: 这是一个稳定且经过验证的配置，用于在云端环境中
#       (如 Google Cloud Run) 部署 Flask 应用。
# -----------------------------------------------------------------------------

# 1. 基础镜像：使用官方的、轻量级的Python 3.9镜像，保证环境纯净、体积小。
FROM python:3.9-slim

# 2. 环境变量：
#    - PYTHONDONTWRITEBYTECODE=1: 防止Python生成.pyc文件，保持容器整洁。
#    - PYTHONUNBUFFERED=1: 确保Python的输出（如print语句）能直接、实时地
#      显示在容器日志中，方便调试。
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 3. 工作目录：在容器内部创建一个名为 /app 的文件夹，并将其设置为工作目录。
WORKDIR /app

# 4. 安装依赖：
#    - 先只复制 requirements.txt 文件。
#    - 利用Docker的层缓存机制，只有当这个文件变化时，才会重新执行安装步骤。
#    - --no-cache-dir 参数告诉pip不要存储缓存，可以减小最终镜像的体积。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 复制项目代码：将你本地的所有项目文件（app.py等）复制到容器的 /app 目录中。
COPY . .

# 6. 启动命令：
#    - 使用 exec gunicorn ... 来启动应用。exec 会让 Gunicorn 进程
#      替换掉当前的 shell 进程，这是容器化应用的最佳实践。
#    - --bind :$PORT: 监听由云平台（如Cloud Run）通过 $PORT 环境变量
#      动态指定的端口。
#    - --workers 1 --threads 8: 这是一个适用于Cloud Run免费套餐的通用配置，
#      平衡了性能和资源消耗。
#    - --timeout 0: 禁用Gunicorn的超时，将超时管理完全交给Cloud Run平台，
#      防止因AI处理时间较长而被Gunicorn错误地终止。
#    - app:app: 告诉Gunicorn去运行名为 app.py 文件中的 Flask 实例 app。
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
=======
# -----------------------------------------------------------------------------
# 「职场透镜」后端施工图纸 (Project Lens Backend Dockerfile)
# 版本: 5.0 - 最终稳定版
# 描述: 这是一个稳定且经过验证的配置，用于在云端环境中
#       (如 Google Cloud Run) 部署 Flask 应用。
# -----------------------------------------------------------------------------

# 1. 基础镜像：使用官方的、轻量级的Python 3.9镜像，保证环境纯净、体积小。
FROM python:3.9-slim

# 2. 环境变量：
#    - PYTHONDONTWRITEBYTECODE=1: 防止Python生成.pyc文件，保持容器整洁。
#    - PYTHONUNBUFFERED=1: 确保Python的输出（如print语句）能直接、实时地
#      显示在容器日志中，方便调试。
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 3. 工作目录：在容器内部创建一个名为 /app 的文件夹，并将其设置为工作目录。
WORKDIR /app

# 4. 安装依赖：
#    - 先只复制 requirements.txt 文件。
#    - 利用Docker的层缓存机制，只有当这个文件变化时，才会重新执行安装步骤。
#    - --no-cache-dir 参数告诉pip不要存储缓存，可以减小最终镜像的体积。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 复制项目代码：将你本地的所有项目文件（app.py等）复制到容器的 /app 目录中。
COPY . .

# 6. 启动命令：
#    - 使用 exec gunicorn ... 来启动应用。exec 会让 Gunicorn 进程
#      替换掉当前的 shell 进程，这是容器化应用的最佳实践。
#    - --bind :$PORT: 监听由云平台（如Cloud Run）通过 $PORT 环境变量
#      动态指定的端口。
#    - --workers 1 --threads 8: 这是一个适用于Cloud Run免费套餐的通用配置，
#      平衡了性能和资源消耗。
#    - --timeout 0: 禁用Gunicorn的超时，将超时管理完全交给Cloud Run平台，
#      防止因AI处理时间较长而被Gunicorn错误地终止。
#    - app:app: 告诉Gunicorn去运行名为 app.py 文件中的 Flask 实例 app。
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
>>>>>>> d81ee58c85c868164b512c80a62e3c69b9a6c1b4
