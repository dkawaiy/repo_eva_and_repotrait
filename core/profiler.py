"""
core/profiler.py

定义画像器（Profiler）的核心逻辑。
画像器负责将新软件的原始功能列表与现有的领域模型进行比对和映射，
找出已匹配的功能和未匹配的“新”功能（Delta）。
"""
import json
from pydantic import BaseModel, ConfigDict, Field
from typing import Dict, List, Optional
from loguru import logger

from .model import DomainModel, SoftwareProfile, FeatureMapping
from utils import SimpleLLM, ChatCompletionSettings
from .json_utils import safe_parse_json
from prompts import profiler_prompts
from pathlib import Path


# --- 核心类 ---

class ProfileDecision(BaseModel):
    """LLM 返回的紧凑映射决策；原文和标准名称由服务端回填。"""
    model_config = ConfigDict(extra="forbid")

    raw_id: str
    standard_id: Optional[str] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str


class CompactProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    decisions: List[ProfileDecision]


def _index_domain_features(domain_model: DomainModel) -> Dict[str, str]:
    """递归建立标准功能 ID 到名称的索引。"""
    feature_index: Dict[str, str] = {}

    def visit(features) -> None:
        for feature in features or []:
            feature_index[feature.id] = feature.name
            visit(feature.sub_features)

    visit(domain_model.features)
    return feature_index


def _build_profile_from_compact_response(
    software_name: str,
    raw_feature_by_id: Dict[str, str],
    feature_name_by_id: Dict[str, str],
    result_data,
) -> SoftwareProfile:
    """校验紧凑响应，并使用服务端可信数据恢复完整画像。"""
    compact = CompactProfileResponse.model_validate(result_data)
    decision_by_raw_id: Dict[str, ProfileDecision] = {}

    for decision in compact.decisions:
        if decision.raw_id not in raw_feature_by_id:
            raise ValueError(f"画像结果包含未知 raw_id: {decision.raw_id}")
        if decision.raw_id in decision_by_raw_id:
            raise ValueError(f"画像结果包含重复 raw_id: {decision.raw_id}")
        if decision.standard_id is not None and decision.standard_id not in feature_name_by_id:
            raise ValueError(f"画像结果包含未知 standard_id: {decision.standard_id}")
        decision_by_raw_id[decision.raw_id] = decision

    missing_raw_ids = set(raw_feature_by_id) - set(decision_by_raw_id)
    if missing_raw_ids:
        raise ValueError(f"画像结果缺少 raw_id: {sorted(missing_raw_ids)}")

    mapped_features: List[FeatureMapping] = []
    unmapped_features: List[str] = []
    for raw_id, raw_description in raw_feature_by_id.items():
        decision = decision_by_raw_id[raw_id]
        if decision.standard_id is None or decision.confidence < 0.8:
            unmapped_features.append(raw_description)
            continue
        mapped_features.append(FeatureMapping(
            standard_id=decision.standard_id,
            standard_name=feature_name_by_id[decision.standard_id],
            raw_feature_description=raw_description,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
        ))

    return SoftwareProfile(
        software_name=software_name,
        description=compact.description,
        mapped_features=mapped_features,
        unmapped_features=unmapped_features,
    )

class Profiler:
    """
    画像器 (Profiler)
    负责将新软件的原始功能列表与现有的领域模型进行比对和映射，
    找出已匹配的功能和未匹配的“新”功能。
    这是一个只读操作，不会修改领域模型。
    """
    def __init__(self, domain_model: DomainModel):
        """
        初始化画像器。
        :param domain_model: 当前的领域功能模型。
        """
        logger.info("Initializing Profiler.")
        self.model = domain_model
        # 领域模型画像输出较长，不限制端到端 deadline

    def profile(self, software_name: str, raw_features: List[str], flag = 0) -> SoftwareProfile:
        """
        对一个新软件执行画像操作。
        这是该类的核心入口点。
        它会调用大语言模型（LLM）来执行核心的匹配逻辑。

        :param software_name: 新软件的名称。
        :param raw_features: 从文档中提取的原始功能描述列表。
        :return: 一个包含映射结果和未映射功能的画像结果对象。
        """
        #先看下对应路径下是否已存在profile文件，存在则直接加载返回
        profile_path = Path(f"profiles/{self.model.name}/{software_name}_profile.json")
        if profile_path.exists() and flag == 0:
            logger.info(f"Profile for software '{software_name}' already exists at {profile_path}. Loading existing profile.")
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile_data = json.load(f)
            # 移除 profile_data 中的 software_name，以避免重复传递
            profile_data.pop('software_name', None)
            return SoftwareProfile(software_name=software_name, **profile_data)
        
        logger.info(f"Starting to profile software: '{software_name}'...")

        normalized_raw_features = [f.strip() for f in raw_features if isinstance(f, str) and f.strip()]

        # 领域模型为空时不走 LLM：直接将原始功能作为未映射功能，供后续演化使用。
        if not self.model.features:
            profile = SoftwareProfile(
                software_name=software_name,
                description=f"{software_name} 的功能待纳入领域模型。",
                mapped_features=[],
                unmapped_features=normalized_raw_features,
                coverage_score=0.0,
            )
            profile.save_profile(profile_path)
            logger.info(
                f"Domain model is empty, skip LLM profiling for '{software_name}', "
                f"unmapped count={len(profile.unmapped_features)}"
            )
            return profile
        
        domain_model_json = self.model.model_dump_json(indent=2)
        raw_feature_by_id = {
            f"rf_{index:04d}": description
            for index, description in enumerate(normalized_raw_features, start=1)
        }
        raw_features_payload = [
            {"raw_id": raw_id, "description": description}
            for raw_id, description in raw_feature_by_id.items()
        ]
        raw_features_json = json.dumps(raw_features_payload, indent=2, ensure_ascii=False)

        user_prompt = profiler_prompts.get_user_prompt(
            domain_model_json=domain_model_json,
            raw_features_json=raw_features_json
        )
        
        logger.debug("Sending profiling request to LLM.")
        
        # 遵循 core/model.py 中的调用逻辑
        llm = SimpleLLM(ChatCompletionSettings(), request_deadline=None)
        llm_response = llm.add_system_msg(profiler_prompts.SYSTEM_PROMPT).add_user_msg(user_prompt).ask()

        try:
            # 使用更耐受的解析器解析LLM返回的结果
            result_data = safe_parse_json(llm_response, component=f'profiler_{software_name}')
            profile = _build_profile_from_compact_response(
                software_name=software_name,
                raw_feature_by_id=raw_feature_by_id,
                feature_name_by_id=_index_domain_features(self.model),
                result_data=result_data,
            )
            logger.info(f"Successfully parsed profiling result for '{software_name}'.")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse LLM response for '{software_name}': {e}")
            logger.debug(f"Invalid LLM response received:\n{llm_response}")
            # 画像结果不完整时必须终止，不能把全部功能误当成未映射并触发模型演化。
            raise ValueError(f"画像结果解析或校验失败: {e}") from e

        # 计算覆盖率
        if self.model.features:
            mapped_ids = {m.standard_id for m in profile.mapped_features}
            total_features_in_model = len(self.model.features)
            if total_features_in_model > 0:
                profile.coverage_score = len(mapped_ids) / total_features_in_model
                logger.info(f"Feature coverage for '{software_name}': {profile.coverage_score:.2%}")

        # 保存画像文件
        profile_path = Path(f"profiles/{self.model.name}/{software_name}_profile.json")
        profile.save_profile(profile_path)
        logger.success(f"Profiling for '{software_name}' completed and saved to {profile_path}")
        
        return profile
