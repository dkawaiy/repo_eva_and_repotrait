"""
core/profiler.py

定义画像器（Profiler）的核心逻辑。
画像器负责将新软件的原始功能列表与现有的领域模型进行比对和映射，
找出已匹配的功能和未匹配的“新”功能（Delta）。
"""
import json
from pydantic import BaseModel, Field
from typing import List
from loguru import logger

from .model import DomainModel, SoftwareProfile, FeatureMapping
from utils import SimpleLLM, ChatCompletionSettings
from prompts import profiler_prompts
from pathlib import Path


# --- 核心类 ---

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
        # 遵循现有模式，在需要时创建LLM实例
        self.llm = SimpleLLM(ChatCompletionSettings())

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
        raw_features_json = json.dumps(normalized_raw_features, indent=2, ensure_ascii=False)

        user_prompt = profiler_prompts.get_user_prompt(
            domain_model_json=domain_model_json,
            raw_features_json=raw_features_json
        )
        
        logger.debug("Sending profiling request to LLM.")
        
        # 遵循 core/model.py 中的调用逻辑
        llm_response = self.llm.add_system_msg(profiler_prompts.SYSTEM_PROMPT).add_user_msg(user_prompt).ask()

        try:
            # 解析LLM返回的结果
            cleaned_response = llm_response.strip().strip('```json').strip('```').strip()
            result_data = json.loads(cleaned_response)
            
            # 先创建实例，再进行验证
            profile = SoftwareProfile(
                software_name=software_name,
                **result_data
            )
            logger.info(f"Successfully parsed profiling result for '{software_name}'.")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse LLM response for '{software_name}': {e}")
            logger.debug(f"Invalid LLM response received:\n{llm_response}")
            # 在解析失败时返回一个空画像
            return SoftwareProfile(
                software_name=software_name,
                unmapped_features=normalized_raw_features
            )

        # LLM 可能返回空结果；若无任何映射且无未映射，则回退为“全部未映射”。
        if not profile.mapped_features and not profile.unmapped_features and normalized_raw_features:
            logger.warning(
                f"LLM returned empty profiling result for '{software_name}', "
                "fallback to all raw features as unmapped"
            )
            profile.unmapped_features = normalized_raw_features

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
