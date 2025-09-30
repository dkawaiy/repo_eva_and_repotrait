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

    arkts = 'arkts','ArkTs',"Arkts"

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
    header_texts = [h.group(2) for h in headers]
    flag = all(h in header_texts for h in new_headers)
    if flag:
        return md_text

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

def embedding_replace_headers(md_text: str, target_headers: List[str], 
                            model_name: str = 'all-MiniLM-L6-v2') -> str:
    """
    用embedding语义相似度将markdown中的四级标题替换为target_headers中的标题（按标题语义匹配）
    """
    model = SentenceTransformer(model_name)
    
    #  1. 使用 split 分割文本，保留标题（用于后续替换）
    blocks = re.split(r'(#### .+?\n)', md_text)
    
    # 提取所有原始标题的文本（去掉 '#### ' 和 '\n'）
    raw_header_texts = []
    header_positions = []  # 记录标题在 blocks 中的位置（奇数索引）
    
    for i in range(1, len(blocks), 2):
        # 去掉 '#### ' 和末尾换行
        title = blocks[i][5:].strip()  # [5:] 去掉 '#### '，strip() 去换行和空格
        raw_header_texts.append(title)
        header_positions.append(i)
    
    if not raw_header_texts or not target_headers:
        return md_text

    #  2. 计算原始标题 和 目标标题 的 embedding
    raw_embeds = model.encode(raw_header_texts)      # (N, 384)
    target_embeds = model.encode(target_headers)     # (M, 384)

    used = set()  # 记录已匹配的原始标题索引（在 raw_header_texts 中的索引）
    
    #  3. 为每个目标标题找最相似的原始标题（未被使用的）
    for target_idx, target_embed in enumerate(target_embeds):
        sims = np.dot(raw_embeds, target_embed) / (
            np.linalg.norm(raw_embeds, axis=1) * np.linalg.norm(target_embed) + 1e-8
        )
        
        best_raw_idx = -1
        for _ in range(len(sims)):
            candidate = int(np.argmax(sims))
            if candidate not in used:
                best_raw_idx = candidate
                break
            sims[candidate] = -1  # 排除已使用
        
        if best_raw_idx != -1:
            # 找到匹配：更新 blocks 中对应位置的标题
            block_index = header_positions[best_raw_idx]  # 在 blocks 中的位置
            blocks[block_index] = f'#### {target_headers[target_idx]}\n'
            used.add(best_raw_idx)

    return ''.join(blocks)

