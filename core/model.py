"""
core/model.py

定义项目核心的数据模型，包括领域功能模型、软件功能画像等。
使用 Pydantic 进行数据建模和校验。
"""
import uuid
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Literal

# 1. 导入你项目中的类和我们之前定义的 prompt
from utils import ChatCompletionSettings
from utils import SimpleLLM
#from utils import parse_single_repository_file
from prompts.initialization_prompts import SYSTEM_PROMPT, get_user_prompt

from pydantic import BaseModel, Field


class Module_doc(BaseModel):
    name:str
    description:str
    example:str
    functions:List[str]

class Repository(BaseModel):
    repo_name: str
    modules: List[Module_doc]

def parse_single_repository_file(file_path) -> Repository:
    """
    将单个 Markdown 文件解析成一个 Repository 对象。
    - 文件中的每个 '### 模块名' 被视为一个模块的开始。
    - '#### Description' 下的内容被视为模块描述。
    """
    repo_name = file_path.stem
    modules: List[Module_doc] = []
    
    content = file_path.read_text(encoding='utf-8')

    # 兼容文件首行就是 '### ' 的场景：补一个前导换行后再按标题切分。
    # 否则首个模块会被丢失，导致 modules 为空。
    normalized_content = f"\n{content}"
    raw_modules = normalized_content.split('\n### ')
    
    for raw_module_text in raw_modules[1:]:
        if not raw_module_text.strip():
            continue
        
        # 使用 '#### Description' 作为新的分隔符来分离模块名和描述
        # 使用 split('\n#### Description', 1) 来确保只切分一次
        parts = raw_module_text.split('\n#### Description', 1)
        
        if len(parts) == 2:
            module_name = parts[0].strip()
            # .strip() 可以去除可能存在的前导/尾随空行
            module_description = parts[1].strip()
            
            # 如果Description标题下没有内容，也视为有效，只是描述为空
            if module_name:
                module = Module_doc(
                    name=module_name,
                    description=module_description,
                    example="",
                    functions=[]
                )
                modules.append(module)
        else:
            # 如果一个模块块里没有 '#### Description'，可以选择忽略或记录
            module_name_only = raw_module_text.splitlines()[0].strip()
            print(f"  [!] 警告: 在模块 '{module_name_only}' 中未找到 '#### Description' 标题，已跳过。")
            
    return Repository(repo_name=repo_name, modules=modules)

class Feature(BaseModel):
    """
    表示一个功能点或功能模块。
    功能点之间可以有层级关系，形成一个树状结构。
    """
    id: str = Field(..., description="功能的唯一标识符")
    name: str = Field(..., description="功能名称")
    description: str = Field(..., description="功能的详细描述")
    parent_id: Optional[str] = Field(None, description="父功能ID，用于构建层级关系")
    sub_features: List['Feature'] = Field(None, description="子功能列表")

    def to_dict(self) -> Dict[str, Any]:
        """将模型递归转换为字典"""
        return self.model_dump()


# Pydantic v2 自动处理前向引用，无需手动调用 update_forward_refs()
# Feature.model_rebuild()

class FeatureMapping(BaseModel):
    """表示单个原始功能点到标准领域模型功能的映射关系"""
    standard_id: str = Field(..., description="标准模型中的功能ID")
    standard_name: str = Field(..., description="标准模型中的功能名称")
    raw_feature_description: str = Field(..., description="新软件原始功能的完整描述")
    confidence: float = Field(..., description="匹配置信度，范围从 0.0 到 1.0")
    reasoning: str = Field(..., description="LLM给出的匹配理由")

class SoftwareProfile(BaseModel):
    """
    表示单个软件的功能画像。
    包含软件的基本信息和从其文档中提取出的功能点列表。
    """
    software_name: str = Field(..., description="软件名称")
    version: Optional[str] = Field(None, description="软件版本")
    description: Optional[str] = Field(None, description="软件的简要描述")
    
    # 从 Profiler 移动过来的字段
    mapped_features: List[FeatureMapping] = Field([], description="成功映射到领域模型的功能列表")
    unmapped_features: List[str] = Field([], description="未能映射到领域模型的原始功能描述列表（演化的种子）")
    coverage_score: float = Field(0.0, description="本次画像对领域模型中已有功能的覆盖率")

    # 生成结果状态：用于区分“技术性生成失败”与“业务上确实未匹配”
    status: Literal["succeeded", "failed"] = Field(
        "succeeded",
        description="画像生成状态：succeeded=生成成功（unmapped_features 代表业务未匹配）；"
                    "failed=生成失败（技术问题，unmapped_features 不代表业务未匹配，不应参与演化）",
    )
    error: Optional[str] = Field(None, description="status=failed 时的错误信息")

    @property
    def is_valid(self) -> bool:
        """画像是否生成成功；只有生成成功的画像才允许参与领域模型演化。"""
        return self.status == "succeeded"

    def to_dict(self) -> Dict[str, Any]:
        """将模型递归转换为字典"""
        return self.model_dump()
    
    def save_profile(self, file_path: Path) -> None:
        """
        将当前软件画像保存到 JSON 文件。
        :param file_path: 保存文件的路径
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.model_dump(), f, indent=4, ensure_ascii=False)

class DomainModel(BaseModel):
    """
    领域功能模型。
    这是整个系统的核心，它汇集了领域内多个软件的功能，
    形成一个统一的、结构化的功能知识库。
    该模型是动态的，可以在处理新软件时不断演进。
    """
    name: str = Field(..., description="领域模型的名称")
    version: str = Field("1.0.0", description="领域模型的版本")
    features: List[Feature] = Field([], description="领域模型包含的所有功能特性，通常是树状结构")

    def save_model(self, file_path: Path) -> None:
        """
        将当前领域模型保存到 JSON 文件。
        :param file_path: 保存文件的路径
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.model_dump(), f, indent=4, ensure_ascii=False)

    @classmethod
    def load_model(cls, file_path: Path) -> 'DomainModel':
        """
        从 JSON 文件加载领域模型。
        :param file_path: 模型文件的路径
        :return: DomainModel的实例
        """
        if not file_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.model_validate(data)

    def find_feature_by_id(self, feature_id: str) -> Optional[Feature]:
        """
        根据ID在整个模型中递归查找功能。
        :param feature_id: 要查找的功能ID
        :return: 找到的Feature对象，如果不存在则返回None
        """
        def search(features: List[Feature]) -> Optional[Feature]:
            for feature in features:
                if feature.id == feature_id:
                    return feature
                found = search(feature.sub_features)
                if found:
                    return found
            return None
        return search(self.features)
    
    def initial(self, domain_name: str, repositories_path: str) -> bool:
        """
        输入一组仓库的模块文档，利用大模型初始化领域模型。
        此方法完全复用 utils/settings.py 和 utils/llm_helper.py 中的接口。
        """
        # 读取repositories_path路径下的所有仓库文档
        print(f"正在为 '{domain_name}' 领域创建初始模型...")
        self.name = domain_name
        # 如果/models目录下已经存在对应领域的模型文件，可以选择加载已有模型进行更新
        model_path = Path(f"models/{domain_name}_domain_model.json")
        if model_path.exists():
            existing_model = DomainModel.load_model(model_path)
            self.features = existing_model.features
            self.version = existing_model.version
            print(f"已加载现有的领域模型 '{domain_name}'，版本: {self.version}")
            return True
        # 1. 将 Pydantic 对象列表直接转换为 JSON 字符串
        # Pydantic 的 model_dump 会将对象递归转换为字典
        # 读取repositories_path路径下的所有仓库文档
        base_path = Path(repositories_path)
        repositories = []
        # base_path 下每个文件代表一个仓库
        for file_path in base_path.iterdir():
            if file_path.is_file() and file_path.suffix == '.md':
                repo = parse_single_repository_file(file_path)
                repositories.append(repo)
        repos_data = [repo.model_dump() for repo in repositories]
        documents_as_json = json.dumps(repos_data, indent=2, ensure_ascii=False)

        # 2. 从 prompts 目录加载并生成 Prompt
        system_prompt = SYSTEM_PROMPT
        user_prompt = get_user_prompt(domain_name, documents_as_json)

        # 3. 完全按照你的接口规范来调用 LLM
        print("正在调用 LLM 生成模型...")
        try:
            # 3.1. 初始化配置类
            settings = ChatCompletionSettings()
            # 3.2. 初始化 SimpleLLM
            llm = SimpleLLM(setting=settings)
            
            # 3.3. 添加消息并提问
            raw_response = llm.add_system_msg(system_prompt).add_user_msg(user_prompt).ask()

        except Exception as e:
            # 捕获可能的配置错误或API调用错误
            print(f"在初始化或调用LLM时发生错误: {e}")
            print("请确保你的 .env 文件或环境变量已正确配置 (OPENAI_API_KEY, OPENAI_BASE_URL, MODEL等)。")
            return

        if not raw_response:
            raise ValueError("LLM未能返回任何内容。")

        print("LLM 已返回结果，正在解析和填充模型...")

        # 4. 手动解析返回的JSON字符串
        try:
            # 清理可能的代码块标记
            cleaned_response = raw_response.strip().strip('```json').strip('```').strip()
            json_response = json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise ValueError(f"LLM返回的内容不是有效的JSON格式。收到内容:\n{raw_response}")

        if not isinstance(json_response, list):
            raise ValueError(f"LLM返回的JSON不是一个数组（列表）。收到的内容: {json_response}")

        # 5. 解析结果并填充到当前实例 (self)
        new_features = []
        for feature_data in json_response:
            if "name" not in feature_data or "description" not in feature_data:
                print(f"警告：跳过一个格式不完整的功能对象: {feature_data}")
                continue
            
            feature = Feature.model_validate({
                "id": f"feat_{uuid.uuid4().hex[:8]}",
                "name": feature_data.get("name"),
                "description": feature_data.get("description"),
                "sub_features": []
            })
            new_features.append(feature)

        self.features = new_features
        self.version = "1.0.0"
        self.save_model(model_path)
        print(f"初始模型创建成功，共生成 {len(self.features)} 个顶级功能。")
        return False




