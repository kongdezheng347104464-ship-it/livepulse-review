FROM python:3.11-slim

WORKDIR /app

# 国内网络可在构建时传入镜像: --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX_URL=https://pypi.org/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# torch 先单独装 2.2.2: linux/arm64 上新版 torch 会拖进整套 CUDA(数 GB,无 GPU 时无用)。
# sentence-transformers 只要求 torch>=1.11.0,2.2.2 满足且与 numpy<2 的 pin 一致。
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} torch==2.2.2
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
