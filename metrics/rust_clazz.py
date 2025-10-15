"""
Rust特有的Class/Type文档生成器
利用Rust扩展信息生成更精确的类型文档
"""
from typing import List

from loguru import logger

from utils import SimpleLLM, ChatCompletionSettings, ProjectSettings, TaskDispatcher
from .doc import ClazzDoc
from .metric import Metric, ClazzDef
from .rust_extensions import RustExtensionManager

rust_type_documentation_guideline = (
    "You are generating documentation for a Rust type (struct, enum, trait, or union). "
    "Focus on Rust-specific concepts like ownership semantics, trait implementations, "
    "generic parameters, lifetime parameters, memory layout, and usage patterns. "
    "Explain the type's purpose, fields, methods, and how it fits into Rust's type system. "
    "Include usage examples with proper Rust syntax showing construction, manipulation, and destruction. "
    "Keep in mind that your audience is Rust developers, so use precise technical language."
)


class RustClazzMetric(Metric):
    """为Rust类型生成增强文档的度量器"""

    def eva(self, ctx):
        callgraph = ctx.clazz_callgraph
        logger.info(f'[RustClazzMetric] gen enhanced Rust doc for types, types count: {len(callgraph)}')

        # 生成文档
        def gen(signature: str):
            max_retries = 3
            retry_count = 0
            
            try:
                # 检查是否已有文档
                if ctx.load_clazz_doc(signature):
                    logger.debug(f'[RustClazzMetric] load existing doc for {signature}')
                    return
                
                clazz_def: ClazzDef = ctx.clazz(signature)
                if not clazz_def:
                    logger.warning(f'[RustClazzMetric] type not found: {signature}')
                    return

                # 获取Rust扩展信息
                rust_ext = RustExtensionManager.get_clazz_extension(clazz_def)
                
                while retry_count < max_retries:
                    try:
                        # 获取相关类型的文档（用于上下文）
                        referenced = list(
                            filter(lambda s: s is not None,
                                   map(lambda s: ctx.load_clazz_doc(s), callgraph.predecessors(signature)))
                        )[:5]  # 限制数量避免上下文过长
                        
                        # 获取相关函数文档
                        functions = list(
                            filter(lambda s: s is not None,
                                   map(lambda s: ctx.load_function_doc(s.signature), clazz_def.functions))
                        )[:8]  # 限制数量

                        # 使用与原metrics一致的提示构建方式，并增加Rust特有信息
                        from .clazz import ClazzPromptBuilder, documentation_guideline
                        
                        # 构建标准提示（保持与原metrics一致）
                        standard_prompt = ClazzPromptBuilder().attributes(clazz_def.fields).code(clazz_def.code).functions(
                            functions).referenced(referenced).lang(ctx.lang.markdown).name(signature).build()
                        
                        # 在原始prompt基础上添加Rust特有信息
                        rust_enhanced_prompt = self._enhance_prompt_with_rust_info(standard_prompt, clazz_def, rust_ext, ctx)
                        
                        # 调用LLM生成文档
                        llm = SimpleLLM(ChatCompletionSettings())
                        res = llm.add_system_msg(rust_enhanced_prompt).add_user_msg(documentation_guideline).ask()
                        res = f'### {signature}\n' + res
                        
                        # 保存文档
                        doc = ClazzDoc.from_chapter(res)
                        ctx.save_clazz_doc(signature, doc)
                        logger.info(f'[RustClazzMetric] generated enhanced doc for {signature}')
                        return  # 成功，退出重试循环
                        
                    except (ConnectionError, TimeoutError, Exception) as e:
                        retry_count += 1
                        error_type = type(e).__name__
                        
                        if "RemoteProtocolError" in str(e) or "ConnectionError" in str(e) or "TimeoutError" in str(e):
                            # 网络相关错误，进行重试
                            if retry_count < max_retries:
                                wait_time = retry_count * 2  # 指数退避
                                logger.warning(f'[RustClazzMetric] Network error for {signature} (attempt {retry_count}/{max_retries}): {error_type}. Retrying in {wait_time}s...')
                                import time
                                time.sleep(wait_time)
                                continue
                            else:
                                logger.error(f'[RustClazzMetric] Network error for {signature} after {max_retries} attempts: {error_type}. Trying fallback...')
                        else:
                            # 其他错误，记录并尝试降级处理
                            logger.warning(f'[RustClazzMetric] Error for {signature} (attempt {retry_count}/{max_retries}): {error_type}. Message: {str(e)[:200]}')
                            if retry_count < max_retries:
                                continue
                            else:
                                logger.error(f'[RustClazzMetric] Failed to generate doc for {signature} after {max_retries} attempts. Trying fallback...')
                        
                        # 如果所有重试都失败，尝试使用简化的prompt作为后备方案
                        if retry_count >= max_retries:
                            try:
                                logger.info(f'[RustClazzMetric] Attempting fallback generation for {signature}')
                                # 使用简化的prompt，不包含上下文信息
                                from .clazz import ClazzPromptBuilder, documentation_guideline
                                simple_prompt = ClazzPromptBuilder().code(clazz_def.code).name(signature).build()
                                
                                llm = SimpleLLM(ChatCompletionSettings())
                                res = llm.add_system_msg(simple_prompt).add_user_msg(documentation_guideline).ask()
                                res = f'### {signature}\n' + res
                                
                                doc = ClazzDoc.from_chapter(res)
                                ctx.save_clazz_doc(signature, doc)
                                logger.info(f'[RustClazzMetric] generated fallback doc for {signature}')
                                return
                            except Exception as fallback_e:
                                logger.error(f'[RustClazzMetric] Fallback also failed for {signature}: {type(fallback_e).__name__}')
                                # 创建一个最小的占位文档，避免完全失败
                                try:
                                    placeholder_doc = f'### {signature}\n\n**Type:** {clazz_def.name}\n\n**Note:** Documentation generation failed due to technical issues. Please refer to the source code for details.\n\n```rust\n{clazz_def.code[:500]}{"..." if len(clazz_def.code) > 500 else ""}\n```'
                                    doc = ClazzDoc.from_chapter(placeholder_doc)
                                    ctx.save_clazz_doc(signature, doc)
                                    logger.info(f'[RustClazzMetric] saved placeholder doc for {signature}')
                                except Exception as placeholder_e:
                                    logger.error(f'[RustClazzMetric] Even placeholder creation failed for {signature}: {type(placeholder_e).__name__}')
                            break
                        
            except Exception as outer_e:
                # 捕获任何其他意外异常
                logger.error(f'[RustClazzMetric] Unexpected error processing {signature}: {type(outer_e).__name__}: {str(outer_e)[:200]}')
                # 不重新抛出异常，让其他任务继续执行

        # 使用线程池并行处理
        TaskDispatcher(ProjectSettings.llm_thread_pool).map(callgraph, gen).run()

    def _enhance_prompt_with_rust_info(self, base_prompt: str, clazz_def: ClazzDef, rust_ext, ctx) -> str:
        """在原始prompt基础上增加Rust特有信息"""
        rust_info = []
        
        # Rust特有特性
        if rust_ext:
            type_features = []
            if rust_ext.is_copy:
                type_features.append("Copy")
            if rust_ext.is_clone:
                type_features.append("Clone")
            if rust_ext.is_send:
                type_features.append("Send")
            if rust_ext.is_sync:
                type_features.append("Sync")
            
            if type_features:
                rust_info.append(f"Derived/Implemented traits: {', '.join(type_features)}")
            
            # 泛型和生命周期
            if rust_ext.generics:
                rust_info.append(f"Generic parameters: {', '.join(rust_ext.generics)}")
            if rust_ext.lifetimes:
                rust_info.append(f"Lifetime parameters: {', '.join(rust_ext.lifetimes)}")
            
            # 类型种类
            if rust_ext.type_kind:
                rust_info.append(f"Type kind: {rust_ext.type_kind}")
            
            # 现有文档信息
            if rust_ext.doc_comment:
                rust_info.append(f"Existing documentation: {rust_ext.doc_comment}")
        
        # 项目上下文
        if hasattr(ctx, 'rust_project_info') and ctx.rust_project_info:
            rust_info.append(f"Project: {ctx.rust_project_info.name} v{ctx.rust_project_info.version}")
            if ctx.rust_project_info.description:
                rust_info.append(f"Project description: {ctx.rust_project_info.description}")
        
        # 增强原始prompt
        if rust_info:
            rust_context = "\n\nAdditional Rust-specific context:\n" + "\n".join(f"- {info}" for info in rust_info)
            rust_context += "\n\nPlease consider these Rust-specific aspects when generating documentation, especially for ownership semantics, trait implementations, memory safety, and type system integration."
            enhanced_prompt = base_prompt + rust_context
        else:
            enhanced_prompt = base_prompt
            
        return enhanced_prompt

    def _build_rust_type_prompt(self, clazz_def: ClazzDef, rust_ext, referenced, functions, ctx):
        """构建Rust类型的详细提示"""
        prompt_parts = []
        
        # 基本信息
        prompt_parts.append(f"You are documenting a Rust type.")
        prompt_parts.append(f"Type name: {clazz_def.name}")
        prompt_parts.append(f"Full name: {clazz_def.signature}")
        prompt_parts.append(f"File: {clazz_def.filename}")
        
        # Rust类型特有信息
        if rust_ext:
            prompt_parts.append(f"Type kind: {rust_ext.type_kind}")
            
            # 泛型和生命周期
            if rust_ext.generics:
                prompt_parts.append(f"Generic parameters: {', '.join(rust_ext.generics)}")
            if rust_ext.lifetimes:
                prompt_parts.append(f"Lifetime parameters: {', '.join(rust_ext.lifetimes)}")
            
            # 实现的trait
            if rust_ext.implemented_traits:
                prompt_parts.append(f"Implemented traits: {', '.join(rust_ext.implemented_traits)}")
            
            # derive宏
            if rust_ext.derives:
                prompt_parts.append(f"Derived traits: {', '.join(rust_ext.derives)}")
            
            # 属性宏
            if rust_ext.attributes:
                prompt_parts.append(f"Attributes: {', '.join(rust_ext.attributes)}")
            
            # 现有文档信息
            if rust_ext.doc_comment:
                prompt_parts.append(f"Existing documentation comments: {rust_ext.doc_comment}")
            
            if rust_ext.examples:
                prompt_parts.append("Existing code examples:")
                for example in rust_ext.examples:
                    prompt_parts.append(f"```rust\n{example}\n```")
        
        # 字段信息
        if clazz_def.fields:
            field_info = []
            for field in clazz_def.fields:
                field_ext = RustExtensionManager.get_field_extension(field)
                if field_ext:
                    visibility = field_ext.visibility if field_ext.visibility != 'private' else ''
                    description = f" - {field_ext.description}" if field_ext.description else ""
                    mutability = " (mutable)" if field_ext.is_mutable else ""
                    field_info.append(f"- {visibility} {field.name}: {field.signature}{mutability}{description}")
                else:
                    field_info.append(f"- {field.name}: {field.signature}")
            
            prompt_parts.append("Fields:")
            prompt_parts.extend(field_info)
        
        # 方法信息
        if clazz_def.functions:
            method_names = []
            for func in clazz_def.functions:
                func_ext = RustExtensionManager.get_func_extension(func)
                if func_ext:
                    features = []
                    if func_ext.is_async:
                        features.append("async")
                    if func_ext.is_unsafe:
                        features.append("unsafe")
                    if func_ext.is_const:
                        features.append("const")
                    
                    feature_str = f" ({', '.join(features)})" if features else ""
                    method_names.append(f"{func.name}{feature_str}")
                else:
                    method_names.append(func.name)
            
            prompt_parts.append(f"Methods: {', '.join(method_names)}")
        
        # 项目上下文
        if hasattr(ctx, 'rust_project_info') and ctx.rust_project_info:
            prompt_parts.append(f"Project context: {ctx.rust_project_info.name} v{ctx.rust_project_info.version}")
            if ctx.rust_project_info.description:
                prompt_parts.append(f"Project description: {ctx.rust_project_info.description}")
        
        # 相关类型上下文
        if referenced:
            prompt_parts.append("Related types:")
            for doc in referenced[:3]:  # 限制数量
                prompt_parts.append(f"- {doc.name}: {doc.description[:100]}..." if len(doc.description) > 100 else f"- {doc.name}: {doc.description}")
        
        # 相关函数上下文
        if functions:
            prompt_parts.append("Associated functions:")
            for doc in functions[:5]:  # 限制数量
                prompt_parts.append(f"- {doc.name}: {doc.description[:100]}..." if len(doc.description) > 100 else f"- {doc.name}: {doc.description}")
        
        # 类型代码
        prompt_parts.append(f"Type definition:")
        prompt_parts.append(f"```rust\n{clazz_def.code}\n```")
        
        # 文档生成指令
        prompt_parts.append(
            "\nPlease generate comprehensive documentation for this Rust type that includes:\n"
            "1. A clear description of the type's purpose and design\n"
            "2. Detailed field explanations with ownership and borrowing implications\n"
            "3. Method overview and their usage patterns\n"
            "4. Generic parameters and lifetime relationships\n"
            "5. Trait implementations and their significance\n"
            "6. Memory layout and performance characteristics\n"
            "7. Construction and destruction patterns\n"
            "8. Usage examples with proper Rust syntax\n"
            "9. Common pitfalls and best practices\n"
        )
        
        return "\n\n".join(prompt_parts)


def determine_rust_type_category(clazz_def: ClazzDef, rust_ext) -> str:
    """确定Rust类型的分类"""
    if rust_ext and rust_ext.type_kind:
        return rust_ext.type_kind
    
    # 从代码中推断类型
    code_lower = clazz_def.code.lower()
    if 'enum' in code_lower:
        return 'enum'
    elif 'trait' in code_lower:
        return 'trait'
    elif 'union' in code_lower:
        return 'union'
    else:
        return 'struct'


def extract_rust_type_features(clazz_def: ClazzDef) -> dict:
    """从代码中提取Rust类型特性"""
    features = {
        'is_generic': False,
        'has_lifetimes': False,
        'is_copy': False,
        'is_clone': False,
        'derives': []
    }
    
    code = clazz_def.code
    
    # 检查泛型
    if '<' in code and '>' in code:
        features['is_generic'] = True
    
    # 检查生命周期
    if "'" in code:
        features['has_lifetimes'] = True
    
    # 检查derive宏
    if '#[derive(' in code:
        import re
        derive_match = re.search(r'#\[derive\(([^)]+)\)\]', code)
        if derive_match:
            derives = [d.strip() for d in derive_match.group(1).split(',')]
            features['derives'] = derives
            features['is_copy'] = 'Copy' in derives
            features['is_clone'] = 'Clone' in derives
    
    return features


class RustTypePromptBuilder:
    """Rust类型文档提示构建器"""
    
    def __init__(self):
        self.parts = []
    
    def type_info(self, clazz_def: ClazzDef):
        self.parts.append(f"Type: {clazz_def.name}")
        self.parts.append(f"Full name: {clazz_def.signature}")
        return self
    
    def rust_features(self, rust_ext):
        if rust_ext:
            if rust_ext.type_kind != 'struct':
                self.parts.append(f"Type kind: {rust_ext.type_kind}")
            
            if rust_ext.implemented_traits:
                self.parts.append(f"Implements: {', '.join(rust_ext.implemented_traits)}")
            
            if rust_ext.derives:
                self.parts.append(f"Derives: {', '.join(rust_ext.derives)}")
        return self
    
    def fields(self, fields: List):
        if fields:
            field_strs = []
            for field in fields:
                field_ext = RustExtensionManager.get_field_extension(field)
                if field_ext and field_ext.description:
                    field_strs.append(f"{field.name}: {field.signature} // {field_ext.description}")
                else:
                    field_strs.append(f"{field.name}: {field.signature}")
            
            self.parts.append("Fields:")
            self.parts.extend([f"  {f}" for f in field_strs])
        return self
    
    def methods(self, functions: List):
        if functions:
            method_names = [f.name for f in functions]
            self.parts.append(f"Methods: {', '.join(method_names)}")
        return self
    
    def code(self, code: str):
        self.parts.append(f"Definition:\n```rust\n{code}\n```")
        return self
    
    def build(self):
        return "\n\n".join(self.parts)