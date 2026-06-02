"""
Rust特有的数据结构扩展
为支持Rust语言特性而创建的额外数据结构
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class RustFuncDefExtension:
    """Rust函数定义的扩展信息"""
    # 文档相关
    doc_comment: Optional[str] = None
    examples: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    return_doc: Optional[str] = None
    
    # Rust特有特性
    is_async: bool = False
    is_unsafe: bool = False
    is_const: bool = False
    is_pub: bool = True
    generics: List[str] = field(default_factory=list)
    where_clause: Optional[str] = None
    
    # 生命周期参数
    lifetimes: List[str] = field(default_factory=list)
    
    # 特质绑定
    trait_bounds: List[str] = field(default_factory=list)


@dataclass
class RustClazzDefExtension:
    """Rust类型定义的扩展信息"""
    # 文档相关
    doc_comment: Optional[str] = None
    examples: List[str] = field(default_factory=list)
    
    # 类型种类
    type_kind: str = 'struct'  # struct, enum, trait, union
    
    # 实现的trait
    implemented_traits: List[str] = field(default_factory=list)
    
    # 泛型参数
    generics: List[str] = field(default_factory=list)
    where_clause: Optional[str] = None
    
    # 生命周期参数
    lifetimes: List[str] = field(default_factory=list)
    
    # derive宏
    derives: List[str] = field(default_factory=list)
    
    # 属性宏
    attributes: List[str] = field(default_factory=list)
    
    # Rust标准trait标识
    is_copy: bool = False
    is_clone: bool = False
    is_send: bool = False
    is_sync: bool = False


@dataclass
class RustFieldDefExtension:
    """Rust字段定义的扩展信息"""
    description: Optional[str] = None
    visibility: str = 'private'  # public, private, pub(crate), pub(super)
    is_mutable: bool = False
    default_value: Optional[str] = None


@dataclass
class RustModuleInfo:
    """Rust模块信息"""
    path: str
    name: str
    doc_comment: Optional[str] = None
    public_items: List[str] = field(default_factory=list)
    submodules: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    
    # Rust特有
    visibility: str = 'public'
    is_inline: bool = False
    cfg_attributes: List[str] = field(default_factory=list)


@dataclass
class RustProjectInfo:
    """Rust项目信息"""
    name: str = ''
    version: str = ''
    description: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    license: Optional[str] = None
    repository: Optional[str] = None
    readme: Optional[str] = None
    
    # Cargo特有
    edition: str = '2021'
    categories: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)
    dev_dependencies: Dict[str, str] = field(default_factory=dict)
    features: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class RustWorkspaceInfo:
    """Rust workspace信息"""
    members: List[str] = field(default_factory=list)
    member_docs: Dict[str, Any] = field(default_factory=dict)
    
    # workspace配置
    default_members: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    resolver: str = '2'


class RustExtensionManager:
    """管理Rust扩展信息的工具类"""
    
    @staticmethod
    def attach_func_extension(func_def, extension: RustFuncDefExtension):
        """为FuncDef附加Rust扩展信息"""
        func_def._rust_ext = extension
    
    @staticmethod
    def attach_clazz_extension(clazz_def, extension: RustClazzDefExtension):
        """为ClazzDef附加Rust扩展信息"""
        clazz_def._rust_ext = extension
    
    @staticmethod
    def attach_field_extension(field_def, extension: RustFieldDefExtension):
        """为FieldDef附加Rust扩展信息"""
        field_def._rust_ext = extension
    
    @staticmethod
    def get_func_extension(func_def) -> Optional[RustFuncDefExtension]:
        """获取FuncDef的Rust扩展信息"""
        return getattr(func_def, '_rust_ext', None)
    
    @staticmethod
    def get_clazz_extension(clazz_def) -> Optional[RustClazzDefExtension]:
        """获取ClazzDef的Rust扩展信息"""
        return getattr(clazz_def, '_rust_ext', None)
    
    @staticmethod
    def get_field_extension(field_def) -> Optional[RustFieldDefExtension]:
        """获取FieldDef的Rust扩展信息"""
        return getattr(field_def, '_rust_ext', None)


def create_enhanced_rust_prompt_for_function(func_def, rust_ext: Optional[RustFuncDefExtension] = None):
    """为Rust函数创建增强的LLM提示"""
    prompt_parts = []
    
    # 基本函数信息
    prompt_parts.append(f"Function: {func_def.name}")
    prompt_parts.append(f"Signature: {func_def.signature}")
    
    if rust_ext:
        # Rust特有特性
        features = []
        if rust_ext.is_async:
            features.append("async")
        if rust_ext.is_unsafe:
            features.append("unsafe")
        if rust_ext.is_const:
            features.append("const")
        if not rust_ext.is_pub:
            features.append("private")
        
        if features:
            prompt_parts.append(f"Features: {', '.join(features)}")
        
        # 泛型和生命周期
        if rust_ext.generics:
            prompt_parts.append(f"Generics: {', '.join(rust_ext.generics)}")
        if rust_ext.lifetimes:
            prompt_parts.append(f"Lifetimes: {', '.join(rust_ext.lifetimes)}")
        
        # 现有文档
        if rust_ext.doc_comment:
            prompt_parts.append(f"Existing documentation: {rust_ext.doc_comment}")
        if rust_ext.examples:
            prompt_parts.append(f"Existing examples:\n" + "\n".join(rust_ext.examples))
    
    # 代码
    prompt_parts.append(f"Code:\n```rust\n{func_def.code}\n```")
    
    return "\n\n".join(prompt_parts)


def create_enhanced_rust_prompt_for_type(clazz_def, rust_ext: Optional[RustClazzDefExtension] = None):
    """为Rust类型创建增强的LLM提示"""
    prompt_parts = []
    
    # 基本类型信息
    prompt_parts.append(f"Type: {clazz_def.name}")
    prompt_parts.append(f"Full name: {clazz_def.signature}")
    
    if rust_ext:
        # 类型种类
        prompt_parts.append(f"Type kind: {rust_ext.type_kind}")
        
        # 泛型和生命周期
        if rust_ext.generics:
            prompt_parts.append(f"Generics: {', '.join(rust_ext.generics)}")
        if rust_ext.lifetimes:
            prompt_parts.append(f"Lifetimes: {', '.join(rust_ext.lifetimes)}")
        
        # 实现的trait
        if rust_ext.implemented_traits:
            prompt_parts.append(f"Implemented traits: {', '.join(rust_ext.implemented_traits)}")
        
        # derive宏
        if rust_ext.derives:
            prompt_parts.append(f"Derived traits: {', '.join(rust_ext.derives)}")
        
        # 现有文档
        if rust_ext.doc_comment:
            prompt_parts.append(f"Existing documentation: {rust_ext.doc_comment}")
        if rust_ext.examples:
            prompt_parts.append(f"Existing examples:\n" + "\n".join(rust_ext.examples))
    
    # 字段信息
    if clazz_def.fields:
        field_info = []
        for field in clazz_def.fields:
            field_ext = RustExtensionManager.get_field_extension(field)
            if field_ext and field_ext.description:
                field_info.append(f"  {field.name}: {field.signature} - {field_ext.description}")
            else:
                field_info.append(f"  {field.name}: {field.signature}")
        prompt_parts.append(f"Fields:\n" + "\n".join(field_info))
    
    # 方法信息
    if clazz_def.functions:
        method_names = [f.name for f in clazz_def.functions]
        prompt_parts.append(f"Methods: {', '.join(method_names)}")
    
    # 代码
    prompt_parts.append(f"Code:\n```rust\n{clazz_def.code}\n```")
    
    return "\n\n".join(prompt_parts)