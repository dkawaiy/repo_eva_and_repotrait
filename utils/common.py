import os
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from functools import reduce

import networkx as nx
import requests
from loguru import logger

from .file_helper import resolve_archive

import re
from typing import List
from sentence_transformers import SentenceTransformer
import numpy as np
import re


def prefix_with(s: str, p: str) -> str:
    return reduce(lambda x, y: x + y, map(lambda k: p + k + '\n', s.splitlines()))


@dataclass
class _Lang:
    render: str
    markdown: str
    cli: str


class LangEnum(_Lang, Enum):
    cpp = 'C/C++', 'c++', 'cpp'

    # TODO: 其他语言
    # rust = 'Rust', 'rust', 'rs'
    javascript = 'JavaScript', 'javascript', 'js'

    # java = 'Java', 'java', 'java'
    # python = 'Python', 'python', 'py'

    @classmethod
    def from_cli(cls, cli: str):
        for lang in cls:
            if lang.cli == cli:
                return lang
        raise ValueError(f'Invalid language: {cli}')

    @classmethod
    def from_render(cls, render: str):
        for lang in cls:
            if lang.render == render:
                return lang
        raise ValueError(f'Invalid language: {render}')


# 去除有向图中的环，对于每个环，删除rank值最小的节点的入边
def remove_cycle(callgraph: nx.DiGraph):
    rank = nx.pagerank(callgraph)
    while not nx.is_directed_acyclic_graph(callgraph):
        cycle = list(nx.find_cycle(callgraph))
        edge_to_remove = min(cycle, key=lambda x: rank[x[1]])
        callgraph.remove_edge(*edge_to_remove)
    return callgraph


# 发送请求，重试5次
def post(url: str, content: str, retry: int = 5):
    err = None
    while retry > 0:
        retry -= 1
        try:
            res = requests.post(url,
                                data=content,
                                headers={'Content-Type': 'application/json'})
            logger.info(f'requests send, status:{res.status_code}, message:{res.text}')
            return
        except Exception as e:
            err = e
            logger.error(f'request fail, err={e}')
            time.sleep(1)
    raise Exception(f'Request Failed, err={err}')



def reformat_markdown_headers(md_text: str, new_headers: List[str]) -> str:
    """
    将markdown文本中的所有四级标题（####）依次替换为new_headers中的标签，顺序对应。
    :param md_text: 原始markdown文本
    :param new_headers: 新的四级标题标签列表
    :return: 替换后的markdown文本
    """
    # 匹配所有四级标题的位置
    header_pattern = re.compile(r'(#### )(.+?)(?=\n)')
    headers = list(header_pattern.finditer(md_text))
    if len(headers) != len(new_headers):
        return embedding_replace_headers(md_text, new_headers)
    # 逐个替换
    result = []
    last_idx = 0
    for match, new_header in zip(headers, new_headers):
        start, end = match.span(2)
        result.append(md_text[last_idx:start])
        result.append(new_header)
        last_idx = end
    result.append(md_text[last_idx:])
    return ''.join(result)

def reformat_markdown_with_headers(md_text: str, headers: List[str]) -> str:
    """
    替换markdown文档中所有三级标题为headers[0]，每个三级标题下的四级标题依次替换为headers[1:]。
    假设每个三级标题下四级标题数量一致，且顺序对应。
    :param md_text: 原始markdown文本
    :param headers: [新的三级标题, 新的四级标题1, 新的四级标题2, ...]
    :return: 替换后的markdown文本
    """
    # 分割出所有三级标题段
    parts = re.split(r'(^### .*$)', md_text, flags=re.MULTILINE)
    result = []
    for i in range(1, len(parts), 2):
        # parts[i] 是三级标题，parts[i+1] 是内容
        result.append(f'### {headers[0]}')
        content = parts[i+1]
        # 找到所有四级标题
        sub_headers = list(re.finditer(r'(#### )(.+?)(?=\n)', content))
        if len(sub_headers) != len(headers) - 1:
            content = embedding_replace_headers(md_text, headers[1:])
            result.append(content)
            continue
            raise ValueError("每个三级标题下的四级标题数量与给定标题列表不一致")
        last_idx = 0
        new_content = []
        for match, new_h in zip(sub_headers, headers[1:]):
            start, end = match.span(2)
            new_content.append(content[last_idx:start])
            new_content.append(new_h)
            last_idx = end
        new_content.append(content[last_idx:])
        result.append(''.join(new_content))
    # parts[0] 可能是开头的内容（无三级标题），保留
    if parts[0].strip():
        result.insert(0, parts[0])
    return ''.join(result)



def embedding_replace_headers(md_text: str, target_headers: List[str], model_name: str = 'all-MiniLM-L6-v2') -> str:
    """
    用embedding语义相似度将markdown中的四级标题依次替换为target_headers中的标题。
    :param md_text: 原始markdown文本
    :param target_headers: 目标四级标题列表
    :param model_name: embedding模型名
    :return: 替换后的markdown文本
    """
    model = SentenceTransformer(model_name)
    # 找到所有四级标题及其内容区块
    blocks = re.split(r'(#### .+?\n)', md_text)
    header_indices = [i for i in range(1, len(blocks), 2)]
    block_texts = [blocks[i+1] if i+1 < len(blocks) else "" for i in header_indices]
    # 计算每个区块内容的embedding
    block_embeds = model.encode(block_texts)
    # 计算每个目标标题的embedding
    header_embeds = model.encode(target_headers)
    used = set()
    for idx, header_embed in enumerate(header_embeds):
        # 计算与所有区块的相似度
        sims = np.dot(block_embeds, header_embed) / (np.linalg.norm(block_embeds, axis=1) * np.linalg.norm(header_embed) + 1e-8)
        # 找到未用过的最相似区块
        for _ in range(len(sims)):
            best_idx = int(np.argmax(sims))
            if header_indices[best_idx] not in used:
                blocks[header_indices[best_idx]] = f'#### {target_headers[idx]}\n'
                used.add(header_indices[best_idx])
                break
            else:
                sims[best_idx] = -1  # 已用过则跳过
    return ''.join(blocks)