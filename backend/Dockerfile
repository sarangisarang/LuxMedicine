# The API, with the embedder and the extractor it needs to actually answer a question.
#
# `[embeddings]` is what makes this image large: multilingual-e5-large runs locally so
# clinical text is embedded inside the EU rather than posted to a third-party API. Torch is
# installed from PyTorch's CPU index FIRST — pip's default wheel carries the whole CUDA
# runtime (~2.5GB) for a container that will never see a GPU, and the CPU wheel is a
# fraction of it. The later install then sees torch already satisfied and leaves it alone.
#
# The model weights themselves (~2.2GB) are NOT baked in: they live in the hf-cache volume
# (see docker-compose.yml), so rebuilding the image does not re-download them.

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir -e ".[embeddings,gemini]"

EXPOSE 8000

# Migrate, then serve. The schema's guarantees (the append-only audit triggers, row-level
# security) live in the migrations, not in metadata.create_all — so a container that served
# before migrating would be serving a database with none of them.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
