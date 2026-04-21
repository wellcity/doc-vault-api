import os
from dotenv import load_dotenv

load_dotenv()

# PostgreSQL（向量 + 權限 + metadata）
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "docvault")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

# Embedding 設定
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "Qwen3-Embedding-8B-GGUF")

# Ollama（EMBEDDING_PROVIDER=ollama 時使用）
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

# 本地模型（EMBEDDING_PROVIDER=local 時使用）
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LOCAL_EMBEDDING_DIM = int(os.getenv("LOCAL_EMBEDDING_DIM", "384"))

DATA_DIR = os.getenv("DATA_DIR", "./processed")
IMAGE_DIR = os.getenv("IMAGE_DIR", "./processed/images")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "5002"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
