"""
Rust特有的Function文档生成器
利用Rust扩展信息生成更精确的函数文档
"""
from typing import List

from loguru import logger

from utils import SimpleLLM, ChatCompletionSettings, ProjectSettings, TaskDispatcher
from .doc import ApiDoc
from .metric import Metric, FuncDef
from .rust_extensions import RustExtensionManager, create_enhanced_rust_prompt_for_function

rust_function_documentation_guideline = (
    "You are generating documentation for a Rust function. "
    "Focus on Rust-specific features like ownership, borrowing, lifetimes, async/await, error handling, "
    "and memory safety. Explain the function's purpose, parameters, return values, and any potential panics or errors. "
    "Include usage examples with proper Rust syntax. "
    "Keep in mind that your audience is Rust developers, so use precise technical language and avoid speculation."
)


class RustFunctionMetric(Metric):
    """为Rust函数生成增强文档的度量器"""

    def eva(self, ctx):
        callgraph = ctx.callgraph
        logger.info(f'[RustFunctionMetric] gen enhanced Rust doc for functions, functions count: {len(callgraph)}')

        # 生成文档
        def gen(signature: str):
            # 检查是否已有文档
            if ctx.load_function_doc(signature):
                logger.debug(f'[RustFunctionMetric] load existing doc for {signature}')
                return
            
            func_def: FuncDef = ctx.func(signature)
            if not func_def:
                logger.warning(f'[RustFunctionMetric] function not found: {signature}')
                return

            # 获取Rust扩展信息
            rust_ext = RustExtensionManager.get_func_extension(func_def)
            
            # 获取相关函数的文档（用于上下文）
            referencer = list(
                filter(lambda s: s is not None,
                       map(lambda s: ctx.load_function_doc(s), callgraph.successors(signature)))
            )[:8]  # 限制数量避免上下文过长
            
            referenced = list(
                filter(lambda s: s is not None,
                       map(lambda s: ctx.load_function_doc(s), callgraph.predecessors(signature)))
            )[:4]

            # 使用与原metrics一致的提示构建方式，并增加Rust特有信息
            from .function import _FunctionPromptBuilder, documentation_guideline
            
            # 构建标准提示（保持与原metrics一致）
            standard_prompt = _FunctionPromptBuilder().parameters(func_def.params).code(func_def.code).referencer(
                referencer).referenced(referenced).lang(ctx.lang.markdown).name(signature).build()
            
            # 在原始prompt基础上添加Rust特有信息
            rust_enhanced_prompt = self._enhance_prompt_with_rust_info(standard_prompt, func_def, rust_ext, ctx)
            
            # 调用LLM生成文档
            llm = SimpleLLM(ChatCompletionSettings())
            res = llm.add_system_msg(rust_enhanced_prompt).add_user_msg(documentation_guideline).ask()
            res = f'### {signature}\n' + res
            
            # 保存文档
            doc = ApiDoc.from_chapter(res)
            ctx.save_function_doc(signature, doc)
            logger.info(f'[RustFunctionMetric] generated enhanced doc for {signature}')

        # 使用线程池并行处理
        TaskDispatcher(ProjectSettings.llm_thread_pool).map(callgraph, gen).run()

    def _enhance_prompt_with_rust_info(self, base_prompt: str, func_def: FuncDef, rust_ext, ctx) -> str:
        """在原始prompt基础上增加Rust特有信息"""
        rust_info = []
        
        # Rust特有特性
        if rust_ext:
            features = []
            if rust_ext.is_async:
                features.append("async function")
            if rust_ext.is_unsafe:
                features.append("unsafe function")
            if rust_ext.is_const:
                features.append("const function")
            
            if features:
                rust_info.append(f"Special Rust features: {', '.join(features)}")
            
            # 泛型和生命周期
            if rust_ext.generics:
                rust_info.append(f"Generic parameters: {', '.join(rust_ext.generics)}")
            if rust_ext.lifetimes:
                rust_info.append(f"Lifetime parameters: {', '.join(rust_ext.lifetimes)}")
            
            # 现有文档信息
            if rust_ext.doc_comment:
                rust_info.append(f"Existing documentation: {rust_ext.doc_comment}")
            
            if rust_ext.errors:
                rust_info.append(f"Known error conditions: {', '.join(rust_ext.errors)}")
        
        # 项目上下文
        if hasattr(ctx, 'rust_project_info') and ctx.rust_project_info:
            rust_info.append(f"Project: {ctx.rust_project_info.name} v{ctx.rust_project_info.version}")
            if ctx.rust_project_info.description:
                rust_info.append(f"Project description: {ctx.rust_project_info.description}")
        
        # 增强原始prompt
        if rust_info:
            rust_context = "\n\nAdditional Rust-specific context:\n" + "\n".join(f"- {info}" for info in rust_info)
            rust_context += "\n\nPlease consider these Rust-specific aspects when generating documentation, especially for ownership, borrowing, lifetimes, async/await, and error handling."
            enhanced_prompt = base_prompt + rust_context
        else:
            enhanced_prompt = base_prompt
            
        return enhanced_prompt

    def _build_rust_function_prompt(self, func_def: FuncDef, rust_ext, referencer, referenced, ctx):
        """构建Rust函数的详细提示"""
        prompt_parts = []
        
        # 基本信息
        prompt_parts.append(f"You are documenting a Rust function.")
        prompt_parts.append(f"Function name: {func_def.name}")
        prompt_parts.append(f"Full signature: {func_def.signature}")
        prompt_parts.append(f"File: {func_def.filename}")
        prompt_parts.append(f"Visibility: {'public' if func_def.visible else 'private'}")
        
        # Rust特有特性
        if rust_ext:
            features = []
            if rust_ext.is_async:
                features.append("async function")
            if rust_ext.is_unsafe:
                features.append("unsafe function")
            if rust_ext.is_const:
                features.append("const function")
            
            if features:
                prompt_parts.append(f"Special features: {', '.join(features)}")
            
            # 泛型和生命周期
            if rust_ext.generics:
                prompt_parts.append(f"Generic parameters: {', '.join(rust_ext.generics)}")
            if rust_ext.lifetimes:
                prompt_parts.append(f"Lifetime parameters: {', '.join(rust_ext.lifetimes)}")
            
            # 现有文档信息
            if rust_ext.doc_comment:
                prompt_parts.append(f"Existing documentation comments: {rust_ext.doc_comment}")
            
            if rust_ext.examples:
                prompt_parts.append("Existing code examples:")
                for example in rust_ext.examples:
                    prompt_parts.append(f"```rust\n{example}\n```")
            
            if rust_ext.errors:
                prompt_parts.append(f"Known error conditions: {', '.join(rust_ext.errors)}")
        
        # 参数信息
        if func_def.params:
            param_info = []
            for param in func_def.params:
                param_ext = RustExtensionManager.get_field_extension(param)
                if param_ext and param_ext.description:
                    param_info.append(f"- {param.name}: {param.signature} - {param_ext.description}")
                else:
                    param_info.append(f"- {param.name}: {param.signature}")
            
            prompt_parts.append("Parameters:")
            prompt_parts.extend(param_info)
        
        # 项目上下文
        if hasattr(ctx, 'rust_project_info') and ctx.rust_project_info:
            prompt_parts.append(f"Project context: {ctx.rust_project_info.name} v{ctx.rust_project_info.version}")
            if ctx.rust_project_info.description:
                prompt_parts.append(f"Project description: {ctx.rust_project_info.description}")
        
        # 相关函数上下文
        if referenced:
            prompt_parts.append("Functions that call this function:")
            for doc in referenced[:3]:  # 限制数量
                if doc.description:
                    desc = doc.description[:100] + "..." if len(doc.description) > 100 else doc.description
                    prompt_parts.append(f"- {doc.name}: {desc}")
                else:
                    prompt_parts.append(f"- {doc.name}: [No description available]")
        
        if referencer:
            prompt_parts.append("Functions called by this function:")
            for doc in referencer[:3]:  # 限制数量
                if doc.description:
                    desc = doc.description[:100] + "..." if len(doc.description) > 100 else doc.description
                    prompt_parts.append(f"- {doc.name}: {desc}")
                else:
                    prompt_parts.append(f"- {doc.name}: [No description available]")
        
        # 函数代码
        prompt_parts.append(f"Function implementation:")
        prompt_parts.append(f"```rust\n{func_def.code}\n```")
        
        # 文档生成指令
        prompt_parts.append(
            "\nPlease generate comprehensive documentation for this Rust function that includes:\n"
            "1. A clear description of what the function does\n"
            "2. Detailed parameter explanations with Rust-specific type information\n"
            "3. Return value description\n"
            "4. Error conditions and panic scenarios\n"
            "5. Memory safety and ownership implications\n"
            "6. Usage examples with proper Rust syntax\n"
            "7. Notes about async behavior, unsafe operations, or const evaluation if applicable\n"
        )
        
        return "\n\n".join(prompt_parts)


def create_rust_aware_function_prompt_builder():
    """创建Rust感知的函数提示构建器"""
    class RustFunctionPromptBuilder:
        def __init__(self):
            self.parts = []
        
        def function_info(self, func_def: FuncDef):
            self.parts.append(f"Function: {func_def.name}")
            self.parts.append(f"Signature: {func_def.signature}")
            return self
        
        def rust_features(self, rust_ext):
            if rust_ext:
                features = []
                if rust_ext.is_async:
                    features.append("async")
                if rust_ext.is_unsafe:
                    features.append("unsafe")  
                if rust_ext.is_const:
                    features.append("const")
                
                if features:
                    self.parts.append(f"Rust features: {', '.join(features)}")
            return self
        
        def parameters(self, params: List):
            if params:
                param_strs = []
                for param in params:
                    param_ext = RustExtensionManager.get_field_extension(param)
                    if param_ext and param_ext.description:
                        param_strs.append(f"{param.name}: {param.signature} // {param_ext.description}")
                    else:
                        param_strs.append(f"{param.name}: {param.signature}")
                
                self.parts.append("Parameters:")
                self.parts.extend([f"  {p}" for p in param_strs])
            return self
        
        def code(self, code: str):
            self.parts.append(f"Implementation:\n```rust\n{code}\n```")
            return self
        
        def context_functions(self, callers, callees):
            if callers:
                self.parts.append("Called by: " + ", ".join([f.name for f in callers[:5]]))
            if callees:
                self.parts.append("Calls: " + ", ".join([f.name for f in callees[:5]]))
            return self
        
        def build(self):
            return "\n\n".join(self.parts)
    
    return RustFunctionPromptBuilder()