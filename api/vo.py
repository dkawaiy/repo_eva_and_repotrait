from dataclasses import field
from dataclasses import field
from enum import Enum
from typing import Optional, List, Dict

from pydantic import BaseModel

from utils import LangEnum


class RATask(BaseModel):
    id: str  # ID
    repo: str  # 仓库OSS地址
    callback: str  # 回调URL
    language: str = LangEnum.cpp.render  # 语言
    name: str = ''


class RAStatus(Enum):
    received = 0
    success = 1
    fail = 2


class RAResult(BaseModel):
    id: str
    status: int
    message: str = ''
    score: Optional[int] = None
    result: Optional[str] = None


class EvaResult(BaseModel):
    functions: List[Dict]
    classes: List[Dict]
    modules: List[Dict]
    repo: List[Dict]


class CompReq(BaseModel):
    results: List[str]
    requestId: str
    callback: str
    names: List[str] = field(default_factory=lambda: ['software1', 'software2'])


class CompResult(BaseModel):
    requestId: str
    status: int
    message: str
    result: Optional[str] = None
