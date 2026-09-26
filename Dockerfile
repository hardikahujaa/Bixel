# Bixel service image.
#
# Two things here are deliberate and load-bearing for A3 (cache & latency), which budgets
# 8 seconds for cold-start p95:
#
#  1. The embedding model is downloaded at BUILD time, not on the first request. A 67 MB
#     fetch inside a cold start would blow the budget on its own.
#  2. fastembed's default model cache is the system temp directory, which a container
#     restart or temp sweep can clear. BIXEL_MODEL_CACHE pins it into the image.
#
# The catalog index is already committed (backend/matcher/index/catalog_index.npz), so it
# is not rebuilt here -- rebuilding takes ~45s and would also risk it drifting from the
# thresholds it was tuned against.

FROM python:3.12-slim

# libgomp1 is onnxruntime's OpenMP runtime. It is not in python:*-slim, and without it
# `import onnxruntime` fails at runtime rather than at build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so edits to source do not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BIXEL_MODEL_CACHE=/app/backend/matcher/.model_cache

# Pre-fetch the pinned ONNX model into the image.
RUN python -c "from backend.matcher.embedder import get_model; get_model(); print('model cached')"

# Fail the build rather than the deploy if the committed index and the catalog disagree.
RUN python -c "from backend.matcher.build_index import load_index; load_index(); print('index ok')"

EXPOSE 8000

# $PORT is injected by most hosts (Render, Railway, Fly); 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
