import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from loguru import logger


def _json_payload(content: Any) -> str | bytes:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    if isinstance(content, str):
        return content
    if hasattr(content, "model_dump_json"):
        return content.model_dump_json(exclude_none=True)
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return json.dumps(content, ensure_ascii=False, default=str)


# 发送请求，重试5次
def post(url: str, content: Any, retry: int = 5):
    err = None
    payload = _json_payload(content)
    while retry > 0:
        retry -= 1
        try:
            res = requests.post(url,
                                data=payload,
                                headers={'Content-Type': 'application/json'})
            logger.info(f'Callback request sent to {url}, status:{res.status_code}, reason:{res.reason}, message:{res.text}')
            if res.status_code >= 400:
                err = Exception(f'HTTP {res.status_code}: {res.text}')
                logger.error(f'Callback failed with status {res.status_code}: {res.text}')
                if retry > 0:
                    time.sleep(1)
                    continue
                raise err
            return res
        except Exception as e:
            err = e
            logger.error(f'Callback request to {url} fail, err={e}')
            if retry > 0:
                time.sleep(1)
    raise Exception(f'Callback Request Failed, err={err}')


def fetch_file(url: str, local_path: Path) -> bool:
    """
    从给定的 URL 获取文件并保存到本地。
    如果文件已存在，则不会重新下载。

    :param url: 文件的远程 URL。
    :param local_path: 本地保存路径。
    :return: 如果文件是新下载的，返回 True；如果文件已存在，返回 False。
    """
    if local_path.exists():
        logger.info(f"File already exists at {local_path}. Skipping download.")
        return False
    
    logger.info(f"Fetching file from {url} to {local_path}...")
    try:
        # 确保目标目录存在
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        response = requests.get(url, stream=True)
        response.raise_for_status()  # 如果请求失败 (如 404)，则抛出异常
        
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logger.success(f"Successfully downloaded and saved file to {local_path}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch file from {url}. Error: {e}")
        # 如果下载失败，清理可能已创建的不完整文件
        if local_path.exists():
            os.remove(local_path)
        raise

