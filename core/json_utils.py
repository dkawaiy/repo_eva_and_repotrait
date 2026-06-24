import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from loguru import logger


def _strip_code_fence(s: str) -> str:
    # remove ```json ... ``` or ``` ... ``` fences
    return re.sub(r'```(?:json)?\n?', '', s).replace('```', '')


def _find_balanced_json(s: str) -> Optional[str]:
    # find the first balanced JSON object/array in string
    start_idx = None
    stack = []
    for i, ch in enumerate(s):
        if start_idx is None and ch in '{[':
            start_idx = i
            stack.append(ch)
            continue
        if start_idx is not None:
            if ch in '{[':
                stack.append(ch)
            elif ch in '}]':
                if not stack:
                    return None
                opening = stack.pop()
                if (opening == '{' and ch != '}') or (opening == '[' and ch != ']'):
                    # mismatch, give up on this region
                    return None
                if not stack:
                    return s[start_idx:i+1]
    return None


def _remove_control_chars(s: str) -> str:
    # keep common whitespace, remove other control chars
    return ''.join(ch if (ord(ch) >= 32 or ch in '\n\r\t') else ' ' for ch in s)


def safe_parse_json(raw: str, component: str = 'llm') -> Any:
    """
    Try multiple heuristics to parse JSON from LLM responses.
    Returns parsed Python object or raises json.JSONDecodeError.
    """
    s = raw or ''
    s = s.strip()
    # quick attempt
    try:
        return json.loads(s)
    except Exception:
        pass

    # strip markdown fences and try again
    cleaned = _strip_code_fence(s).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # attempt to find balanced JSON substring
    try:
        candidate = _find_balanced_json(s)
        if candidate:
            return json.loads(candidate)
    except Exception:
        pass

    # remove control characters and try
    try:
        candidate = _remove_control_chars(cleaned)
        return json.loads(candidate)
    except Exception:
        pass

    # attempt to auto-close unmatched braces/brackets simplistically
    try:
        open_braces = cleaned.count('{') - cleaned.count('}')
        open_brackets = cleaned.count('[') - cleaned.count(']')
        candidate = cleaned + ('}' * open_braces) + (']' * open_brackets)
        return json.loads(candidate)
    except Exception:
        pass

    # 最后，保存原始响应以便人工回溯并抛出异常
    try:
        out_dir = Path('logs/llm_responses')
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        fname = out_dir / f"{component}_{ts}.txt"
        fname.write_text(raw, encoding='utf-8')
        logger.debug(f"Saved unparsable LLM response to {fname}")
    except Exception:
        logger.exception("Failed to save unparsable LLM response")

    # let caller handle the decode error
    raise json.JSONDecodeError('Could not parse LLM JSON response', raw, 0)
