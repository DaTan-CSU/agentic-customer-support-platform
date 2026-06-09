# Slim Python image — we need ~3.11+ for openai-agents + tencentcloud sdk.
FROM python:3.11-slim

# Avoid prompts and keep the build minimal.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so they layer-cache across code edits.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Source. The compose file mounts the project read-write in dev, but the
# image still ships a self-contained copy so `docker run` works alone.
COPY . ./

EXPOSE 8000 9001

# main.py's startup hook spawns jd_realbackend on 9001 inside the same
# container, so a single CMD is enough. --host 0.0.0.0 to be reachable
# from the compose network.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
