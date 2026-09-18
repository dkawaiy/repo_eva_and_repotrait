from enum import Enum
from typing import List, Optional, Tuple, Union
import json

from pydantic import BaseModel, Field, field_validator

from core.model import DomainModel, SoftwareProfile


class TaskStatus(str, Enum):
    """异步任务状态。"""

    received = "received"
    processing = "processing"
    succeeded = "succeeded"
    partial = "partial"  # 部分成功：结果可用，但存在已记录的问题（如部分画像/模块生成失败）
    failed = "failed"


class StageIssue(BaseModel):
    """阶段问题的记录。

    用于把“生成失败”等技术性问题与“业务上未匹配”区分开：
    - 技术性问题：进入 issues，并不会被当作新增领域知识去演化模型；
    - 业务未匹配：仍然记录在对应 SoftwareProfile.unmapped_features 中。
    """

    scope: str = Field(..., description="问题所属范围：软件名、领域模型名或任务 ID")
    stage: str = Field(
        ...,
        description="发生问题的阶段：metric/profile/module_draft/module_merge/module_enhance/domain_bootstrap/evolution/task",
    )
    message: str = Field(..., description="问题描述（技术性原因，不代表业务未匹配）")


class AutoProfileTaskRequest(BaseModel):
    """对外服务的异步任务请求：自动画像 + 自动演化 + 返回新模型。"""

    id: str = Field(..., description="任务 ID（由调用方生成，用于幂等与查询）")
    domain_model: Union[str, dict] = Field(..., description="领域模型JSON格式文本或对象")
    repo_urls: List[Tuple[str, str]] = Field(..., min_length=1, description="一批软件仓库源码的地址列表以及软件对应语言")
    callback: Optional[str] = Field(
        None,
        description="任务完成后后端向该 URL POST",
    )



class TaskAck(BaseModel):
    """任务回执：立即返回，表示已接收并进入后台处理。"""

    id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="任务状态，通常为 received")
    message: str = Field(..., description="提示信息")


class AutoProfileTaskResult(BaseModel):
    """任务最终结果：自动画像 +（如有未匹配则自动演化）+ 重画像 + 新模型。"""

    id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="最终状态：succeeded/partial/failed")
    message: str = Field(..., description="结果说明")
    domain_model_name: Optional[str] = Field(None, description="本次画像使用的领域模型名称")
    domain_model_version: Optional[str] = Field(None, description="本次画像使用的领域模型版本")
    profiles: List[SoftwareProfile] = Field(default_factory=list, description="画像结果（最终版本）")
    evolved_domain_model: Optional[DomainModel] = Field(None, description="最终领域模型（可能未变更）")
    evolve_applied: bool = Field(False, description="是否实际对模型做出了变更")
    metric_results: List[str] = Field(default_factory=list, description="各软件画像的度量结果")
    error: Optional[str] = Field(None, description="失败时的错误信息")
    issues: List[StageIssue] = Field(default_factory=list, description="阶段问题列表（生成失败等技术性问题的原因）")
    #input_docs: List[InputDocRef] = Field(default_factory=list, description="输入文档引用")

class AutoProfileTaskResult_t(BaseModel):
    """任务最终结果：自动画像 +（如有未匹配则自动演化）+ 重画像 + 新模型。"""

    id: str = Field(..., description="任务 ID")
    status: TaskStatus = Field(..., description="最终状态：succeeded/partial/failed")
    message: str = Field(..., description="结果说明")
    domain_model_name: Optional[str] = Field(None, description="本次画像使用的领域模型名称")
    domain_model_version: Optional[str] = Field(None, description="本次画像使用的领域模型版本")
    profiles: dict[str, SoftwareProfile] = Field(default_factory=dict, description="画像结果（最终版本）")
    metric_results: dict[str, dict] = Field(default_factory=dict, description="按软件标识索引的度量结果")
    evolved_domain_model: Optional[DomainModel] = Field(None, description="最终领域模型（可能未变更）")
    issues: List[StageIssue] = Field(default_factory=list, description="阶段问题列表（生成失败等技术性问题的原因）")

class InputDocRef(BaseModel):
    """单个输入文档的引用信息：用于把 doc_url 与 profiles 内 software_name 对齐。"""

    index: int = Field(..., ge=0, description="在 doc_urls 中的顺序")
    doc_url: str = Field(..., description="输入文档 URL")
    doc_id: str = Field(..., description="doc_url 的 hash（用于唯一标识）")
    software_name: str = Field(..., description="该文档对应的画像 software_name")


AutoProfileTaskResult.model_rebuild()
