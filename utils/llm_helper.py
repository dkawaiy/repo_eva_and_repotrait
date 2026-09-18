import json
import time
from threading import BoundedSemaphore
from typing import Callable

from httpx import ReadTimeout
from loguru import logger
from openai import OpenAI, Stream
from openai.types.chat import ChatCompletionChunk

from .settings import ChatCompletionSettings, ProjectSettings


class LLMDeadlineExceeded(TimeoutError):
    pass


_LLM_SEMAPHORE = BoundedSemaphore(max(1, ProjectSettings.llm_concurrency))


# 通用的LLM代理
class SimpleLLM:
    def __init__(self, setting: ChatCompletionSettings):
        self._setting = setting
        self._llm = OpenAI(
            api_key=self._setting.openai_api_key,
            base_url=self._setting.openai_base_url,
            timeout=self._setting.request_timeout,
            # Retrying is handled below so that the total number of attempts is
            # explicit and bounded.
            max_retries=1,
        )
        self._history = []
        self._language_msg_added = False

    def add_system_msg(self, content: str):
        self._history.append({'role': 'system', 'content': content})
        return self

    def add_user_msg(self, content: str):
        self._history.append({'role': 'user', 'content': content})
        return self

    def _add_response(self, content: str):
        self._history.append({'role': 'assistant', 'content': content})
        return self

    def _add_language_msg(self):
        if self._language_msg_added:
            return
        self.add_user_msg(f'You must output in {self._setting.language} though the prompt is written in English.'
                          "You can write with some English words in the analysis and description "
                          "to enhance the document's readability because you do not need to translate the function name or variable name into the target language.\n")
        self._language_msg_added = True

    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        if isinstance(error, LLMDeadlineExceeded):
            return False
        if isinstance(error, ReadTimeout):
            return True
        if error.__class__.__name__ in {'APITimeoutError', 'APIConnectionError', 'RateLimitError',
                                        'InternalServerError'}:
            return True
        return getattr(error, 'status_code', None) in {408, 409, 429, 500, 502, 503, 504}

    def ask(self, post_processor: Callable[[str], str] = None) -> str:
        self._add_language_msg()
        attempts = max(1, self._setting.max_attempts)
        for attempt in range(1, attempts + 1):
            response = None
            try:
                with _LLM_SEMAPHORE:
                    started_at = time.monotonic()
                    response = self._llm.chat.completions.create(
                        model=self._setting.model,
                        messages=self._history,
                        temperature=self._setting.temperature,
                        stream=True,
                        max_tokens=self._setting.max_output_tokens,
                        extra_body={"enable_thinking": False},
                        stream_options={'include_usage': True}
                    )
                    res = self._get_stream_response(response, started_at)
                if post_processor:
                    res = post_processor(res)
                self._add_response(res)
                return res
            except Exception as e:
                retryable = self._is_retryable_error(e)
                if not retryable or attempt >= attempts:
                    logger.error(f"[SimpleLLM] Error in chat call after attempt {attempt}/{attempts}: {e}")
                    raise
                delay = min(2 ** (attempt - 1), 8)
                logger.warning(
                    f"[SimpleLLM] Retryable error in chat call on attempt {attempt}/{attempts}: {e}; "
                    f"retrying in {delay}s"
                )
                time.sleep(delay)
            finally:
                if response is not None:
                    response.close()
        raise RuntimeError('[SimpleLLM] exhausted attempts without a result')

    def _get_stream_response(self, response: Stream[ChatCompletionChunk], started_at: float) -> str:
        thinking_parts = []
        answer_parts = []
        usage = None
        chat_id = None
        finish_reason = None

        for chunk in response:
            elapsed = time.monotonic() - started_at
            if elapsed > self._setting.request_deadline:
                raise LLMDeadlineExceeded(
                    f'stream exceeded {self._setting.request_deadline}s end-to-end deadline'
                )

            chat_id = chunk.id or chat_id
            if chunk.choices:
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content is not None:
                    thinking_parts.append(delta.reasoning_content)
                elif delta.content:
                    answer_parts.append(delta.content)

            if chunk.usage:
                usage = chunk.usage

        answer_content = ''.join(answer_parts)
        thinking_chars = sum(map(len, thinking_parts))
        elapsed = time.monotonic() - started_at
        usage_text = 'unavailable'
        if usage is not None:
            usage_text = f'prompt {usage.prompt_tokens}, response {usage.completion_tokens}'
        logger.debug(
            f'[SimpleLLM] chat {chat_id}: token usage({usage_text}), elapsed={elapsed:.2f}s, '
            f'finish_reason={finish_reason}, prompt_chars={sum(len(msg["content"]) for msg in self._history)}, '
            f'thinking_chars={thinking_chars}, response_chars={len(answer_content)}, '
            f'response_preview={answer_content[:2000]!r}'
        )
        if finish_reason == 'length':
            logger.warning(
                f'[SimpleLLM] chat {chat_id} reached the configured output limit '
                f'({self._setting.max_output_tokens} tokens)'
            )
        return answer_content

    def add_file(self, path: str):
        try:
            # TODO file-extract 可能是qwen-long专用
            response = self._llm.files.create(file=open(path, 'rb'), purpose='file-extract')
            self.add_system_msg(f'fileid://{response.id}')
            return self
        except Exception as e:
            logger.error(f"[SimpleLLM] Error in add file: {e}")
            raise e


# TODO, 未接入流式API，不支持debug
class ToolsLLM(SimpleLLM):
    def __init__(self, setting: ChatCompletionSettings, tools, tools_map):
        self._tools = tools
        self._toolsMap = tools_map
        super().__init__(setting)

    def ask(self, post_processor: Callable[[str], str] = None) -> str:
        try:
            response = self._llm.chat.completions.create(
                model=self._setting.model,
                messages=self._history,
                temperature=self._setting.temperature,
                max_tokens=self._setting.max_output_tokens,
                tools=self._tools
            )
            logger.info(
                f'[ToolsLLM] chat {response.id}: token usage(prompt {response.usage.prompt_tokens}, response {response.usage.completion_tokens})')
            if response.choices[0].message.tool_calls:
                logger.info(f'[ToolsLLM] chat {response.id}: tool call{response.choices[0].message.tool_calls}')
                for tool_call in response.choices[0].message.tool_calls:
                    self._history.append(response.choices[0].message)
                    f = self._toolsMap.get(tool_call.function.name)
                    arguments = json.loads(tool_call.function.arguments)
                    r = f(**arguments)
                    self._history.append({'role': 'tool', 'content': r})
                    logger.info(
                        f"[ToolsLLM] chat {response.id}: tool call(name {f}, arguments {arguments}), result {r}")
                return self.ask()
            res = response.choices[0].message.content
            if post_processor:
                res = post_processor(res)
            self._add_response(res)
            return res
        except Exception as e:
            logger.error(f"[ToolsLLM] Error in chat call: {e}")
            raise e

    def debug(self) -> str:
        raise NotImplementedError
