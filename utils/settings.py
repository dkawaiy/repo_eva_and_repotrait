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
    # Limit concurrent calls to the external model independently of the
    # general-purpose worker pool.  A large CPU count must not translate into
    # dozens of simultaneous long-running LLM streams.
    llm_concurrency: int = config('LLM_CONCURRENCY', cast=int, default=4)
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
    request_timeout: int = config('MODEL_TIMEOUT', cast=int, default=60)
    # The HTTP read timeout above is not an end-to-end timeout for streaming
    # responses: it is reset whenever another chunk arrives. The end-to-end
    # deadline bounds pathological streams independently from output length.
    request_deadline: int = config('MODEL_DEADLINE', cast=int, default=180)
    # This is the single output-size limit. Do not add a second character or
    # byte cap while consuming the stream: that can return partial JSON even
    # when the model itself has not reached its token limit.
    max_output_tokens: int = config('MODEL_MAX_OUTPUT_TOKENS', cast=int, default=65536)
    max_attempts: int = config('MODEL_MAX_ATTEMPTS', cast=int, default=2)
    model: str = config('MODEL')
    temperature: float = config('MODEL_TEMPERATURE', cast=float, default=0)
    language: str = config('MODEL_LANGUAGE', default='Chinese')
    history_max: int = config('HISTORY_MAX', cast=int, default=-1)


class RagSettings:
    use_gpu: bool = config('USE_GPU', default=False, cast=lambda x: bool(x))
    #tokenizer: Any = config('TOKENIZER', default='Amu/tao-8k', cast=lambda x: AutoTokenizer.from_pretrained(x))
    #model: Any = config('TOKENIZER_MODEL', default='Amu/tao-8k', cast=lambda x: AutoModel.from_pretrained(x))
    dim: int = config('TOKENIZER_DIM', cast=int, default=384)


logger.add('logs/application.log', level=ProjectSettings.log_level, rotation='1 day', retention='7 days',
           encoding='utf-8', filter=lambda record: not record['message'].startswith(('[SimpleLLM]', '[ToolsLLM]')))
logger.add('logs/llm.log', level=LogLevel.DEBUG, rotation='1 day', retention='3 days',
           encoding='utf-8', filter=lambda record: record['message'].startswith(('[SimpleLLM]', '[ToolsLLM]')))
