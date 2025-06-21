import os
from concurrent.futures.thread import ThreadPoolExecutor
from enum import StrEnum
from typing import Any

from decouple import config
from loguru import logger
from transformers import AutoTokenizer, AutoModel


class LogLevel(StrEnum):
    DEBUG = 'DEBUG'
    INFO = 'INFO'
    WARNING = 'WARNING'
    ERROR = 'ERROR'
    CRITICAL = 'CRITICAL'


class ProjectSettings:
    log_level: LogLevel = config('LOG_LEVEL', default=LogLevel.INFO)
    llm_thread_pool: ThreadPoolExecutor = config(
        'THREADS',
        cast=lambda x: ThreadPoolExecutor(int(x)),
        default=os.cpu_count() * 4)
    test = []


class ChatCompletionSettings:
    # 环境变量写入密钥
    openai_api_key: str = config('OPENAI_API_KEY')
    # .env文件配置
    openai_base_url: str = config('OPENAI_BASE_URL')
    request_timeout: int = config('MODEL_TIMEOUT', cast=int, default=30)
    model: str = config('MODEL')
    temperature: float = config('MODEL_TEMPERATURE', cast=float, default=0)
    language: str = config('MODEL_LANGUAGE', default='Chinese')
    history_max: int = config('HISTORY_MAX', cast=int, default=-1)


class RagSettings:
    tokenizer: Any = config('TOKENIZER', default='Amu/tao-8k', cast=lambda x: AutoTokenizer.from_pretrained(x))
    model: Any = config('TOKENIZER_MODEL', default='Amu/tao-8k', cast=lambda x: AutoModel.from_pretrained(x))
    dim: int = config('TOKENIZER_DIM', cast=int, default=1024)


logger.add('logs/application.log', level=ProjectSettings.log_level, rotation='1 day', retention='7 days',
           encoding='utf-8', filter=lambda record: not record['message'].startswith(('[SimpleLLM]', '[ToolsLLM]')))
logger.add('logs/llm.log', level=LogLevel.DEBUG, rotation='1 day', retention='3 days',
           encoding='utf-8', filter=lambda record: record['message'].startswith(('[SimpleLLM]', '[ToolsLLM]')))
