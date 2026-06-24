"""
core/evolver.py

定义领域模型演化器 (Evolver) 的核心逻辑。
Evolver 负责根据画像产生的“未映射功能”（Deltas），
通过大语言模型（LLM）的决策，来更新和扩展现有的领域模型。
"""
import json
import uuid
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from loguru import logger

from .model import DomainModel, SoftwareProfile, Feature
from utils import SimpleLLM, ChatCompletionSettings
from .json_utils import safe_parse_json
from prompts import evolver_prompts
from pathlib import Path


class EvolutionDecision(BaseModel):
    """
    表示LLM对单个未映射功能做出的演化决策。
    """
    action: str = Field(..., description="操作类型 (ADD_NEW_FEATURE, ADD_SUB_FEATURE, MERGE_FEATURE, IGNORE)")
    raw_feature: str = Field(..., description="正在处理的原始功能描述")
    reasoning: str = Field(..., description="做出此决策的理由")
    
    # ADD_NEW_FEATURE / ADD_SUB_FEATURE
    new_feature_name: Optional[str] = None
    new_feature_description: Optional[str] = None
    
    # ADD_SUB_FEATURE
    parent_feature_id: Optional[str] = None
    
    # MERGE_FEATURE
    target_feature_id: Optional[str] = None
    updated_description: Optional[str] = None


class Evolver:
    """
    领域模型演化器 (Evolver)
    
    负责接收一个画像结果（SoftwareProfile），特别是其中的未映射功能列表，
    然后决策如何将这些新知识融入到现有的领域模型中，从而实现模型的演进。
    这是一个写操作，会直接修改并保存领域模型。
    """
    def __init__(self, domain_model: DomainModel):
        """
        初始化演化器。
        :param domain_model: 需要被演进的领域功能模型实例。
        """
        logger.info("Initializing Evolver.")
        self.model = domain_model
        self.llm = SimpleLLM(ChatCompletionSettings())

    def evolve(self, profile: SoftwareProfile) -> bool:
        """
        根据软件画像的未映射功能列表，执行领域模型的演化。
        这是该类的核心入口点。

        :param profile: 包含未映射功能的软件画像。
        :return: 如果模型发生了演化，则返回 True，否则返回 False。
        """
        if not profile.unmapped_features:
            logger.info(f"No unmapped features found for '{profile.software_name}'. Model evolution is not required.")
            return False

        logger.info(f"Starting model evolution based on unmapped features from '{profile.software_name}'...")

        domain_model_json = self.model.model_dump_json(indent=2)
        unmapped_features_json = json.dumps(profile.unmapped_features, indent=2, ensure_ascii=False)

        user_prompt = evolver_prompts.get_user_prompt(
            domain_model_json=domain_model_json,
            unmapped_features_json=unmapped_features_json
        )

        logger.debug("Sending evolution request to LLM.")
        llm_response = self.llm.add_system_msg(evolver_prompts.SYSTEM_PROMPT).add_user_msg(user_prompt).ask()

        try:
            decisions_data = safe_parse_json(llm_response, component='evolver')
            decisions = [EvolutionDecision.model_validate(d) for d in decisions_data]
            logger.info(f"Successfully parsed {len(decisions)} evolution decisions from LLM.")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse LLM response for evolution: {e}")
            logger.debug(f"Invalid LLM response received:\n{llm_response}")
            return False

        # 应用决策
        changed = self._apply_decisions(decisions)

        if changed:
            # 更新模型版本并保存
            self._update_model_version()
            model_path = Path(f"models/{self.model.name}_domain_model.json")
            self.model.save_model(model_path)
            logger.success(f"Domain model '{self.model.name}' has evolved. New version: {self.model.version}. Saved to {model_path}")
        else:
            logger.info("No changes were applied to the domain model after evaluation.")

        return changed

    def _apply_decisions(self, decisions: List[EvolutionDecision]) -> bool:
        """
        将LLM返回的决策列表逐一应用到领域模型上。
        """
        changed = False
        for decision in decisions:
            logger.info(f"Applying action '{decision.action}' for raw feature: '{decision.raw_feature}'")
            
            if decision.action == "ADD_NEW_FEATURE":
                if not decision.new_feature_name or not decision.new_feature_description:
                    logger.warning(
                        f"  [!] Invalid ADD_NEW_FEATURE decision for raw feature '{decision.raw_feature}': "
                        f"missing new_feature_name or new_feature_description. Skipping."
                    )
                    continue
                new_feature = Feature(
                    id=f"feat_{uuid.uuid4().hex[:8]}",
                    name=decision.new_feature_name,
                    description=decision.new_feature_description,
                    sub_features=[]
                )
                self.model.features.append(new_feature)
                logger.debug(f"  (+) Added new top-level feature: '{new_feature.name}' (ID: {new_feature.id})")
                changed = True

            elif decision.action == "ADD_SUB_FEATURE":
                if not decision.parent_feature_id or not decision.new_feature_name or not decision.new_feature_description:
                    logger.warning(
                        f"  [!] Invalid ADD_SUB_FEATURE decision for raw feature '{decision.raw_feature}': "
                        "missing parent_feature_id, new_feature_name or new_feature_description. Skipping."
                    )
                    continue
                parent_feature = self.model.find_feature_by_id(decision.parent_feature_id)
                if parent_feature:
                    new_sub_feature = Feature(
                        id=f"feat_{uuid.uuid4().hex[:8]}",
                        name=decision.new_feature_name,
                        description=decision.new_feature_description,
                        parent_id=parent_feature.id,
                        sub_features=[]
                    )
                    if parent_feature.sub_features is None:
                        parent_feature.sub_features = []
                    parent_feature.sub_features.append(new_sub_feature)
                    logger.debug(f"  (->) Added new sub-feature '{new_sub_feature.name}' to '{parent_feature.name}' (ID: {parent_feature.id})")
                    changed = True
                else:
                    logger.warning(f"  [!] Could not find parent feature with ID '{decision.parent_feature_id}' to add sub-feature. Skipping.")

            elif decision.action == "MERGE_FEATURE":
                if not decision.target_feature_id or not decision.updated_description:
                    logger.warning(
                        f"  [!] Invalid MERGE_FEATURE decision for raw feature '{decision.raw_feature}': "
                        "missing target_feature_id or updated_description. Skipping."
                    )
                    continue
                target_feature = self.model.find_feature_by_id(decision.target_feature_id)
                if target_feature:
                    original_desc = target_feature.description
                    target_feature.description = decision.updated_description
                    logger.debug(f"  (M) Merged into feature '{target_feature.name}' (ID: {target_feature.id}).")
                    logger.trace(f"      Old desc: {original_desc}")
                    logger.trace(f"      New desc: {target_feature.description}")
                    changed = True
                else:
                    logger.warning(f"  [!] Could not find target feature with ID '{decision.target_feature_id}' to merge. Skipping.")

            elif decision.action == "IGNORE":
                logger.debug(f"  (I) Ignored raw feature based on reasoning: {decision.reasoning}")
            
            else:
                logger.warning(f"  [!] Unknown action '{decision.action}'. Skipping.")
        
        return changed

    def _update_model_version(self):
        """
        简单地将模型版本号的修订号加一。
        """
        try:
            parts = self.model.version.split('.')
            major = int(parts[0])
            minor = int(parts[1])
            patch = int(parts[2])
            patch += 1
            self.model.version = f"{major}.{minor}.{patch}"
        except (ValueError, IndexError):
            logger.warning(f"Could not parse version '{self.model.version}'. Setting to '1.0.1'.")
            self.model.version = "1.0.1"


