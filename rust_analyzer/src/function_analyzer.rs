use crate::error::{AnalyzerError, Result};
use crate::output::{MethodOutput, ParameterInfo};
use crate::utils::{extract_module_path, generate_id, normalize_visibility};

use log::debug;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use syn::{
    Expr, ExprCall, ExprMethodCall, ExprPath, File, FnArg, ImplItem, Item, ItemConst, ItemFn,
    ItemImpl, ItemMacro, ItemStatic, Pat, PatType, ReturnType, Type, Visibility, spanned::Spanned,
    visit::Visit,
};

/// 分析Rust函数定义和调用关系的核心组件
pub struct FunctionAnalyzer {
    /// 当前分析的模块路径
    current_module: String,
    /// 当前文件中发现的所有函数调用
    function_calls: HashSet<String>,
    /// 源代码内容，用于准确计算行号
    source_content: String,
    /// 行号映射缓存
    line_map: HashMap<usize, u32>,
}

impl Default for FunctionAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl FunctionAnalyzer {
    pub fn new() -> Self {
        Self {
            current_module: String::new(),
            function_calls: HashSet::new(),
            source_content: String::new(),
            line_map: HashMap::new(),
        }
    }

    /// 获取项目根目录
    fn get_project_root(&self) -> Option<std::path::PathBuf> {
        // 寻找包含 Cargo.toml 的目录作为项目根目录
        std::env::current_dir().ok().and_then(|mut dir| {
            loop {
                if dir.join("Cargo.toml").exists() {
                    return Some(dir);
                }
                if !dir.pop() {
                    break;
                }
            }
            None
        })
    }

    /// 获取相对文件路径（保留目录结构以避免文件名冲突）
    fn get_relative_path(&self, file_path: &std::path::Path) -> String {
        if let Some(project_root) = self.get_project_root() {
            file_path
                .strip_prefix(&project_root)
                .unwrap_or(file_path)
                .to_string_lossy()
                .to_string()
        } else {
            file_path.to_string_lossy().to_string()
        }
    }

    /// 分析单个文件中的所有函数
    pub fn analyze_file(
        &mut self,
        file_path: &PathBuf,
        parsed_file: &File,
        project_root: &PathBuf,
    ) -> Result<Vec<MethodOutput>> {
        self.current_module = extract_module_path(file_path, project_root);
        debug!(
            "分析文件中的函数: {} (模块: {})",
            file_path.display(),
            self.current_module
        );

        // 读取源代码内容用于行号计算
        self.source_content = std::fs::read_to_string(file_path).map_err(|e| {
            AnalyzerError::analysis_error(
                format!("读取文件 {}", file_path.display()),
                e.to_string(),
            )
        })?;

        // 构建行号映射
        self.build_line_map();

        let mut methods = Vec::new();

        // 分析顶层函数
        for item in &parsed_file.items {
            match item {
                Item::Fn(item_fn) => {
                    if let Ok(method) = self.analyze_function(item_fn, file_path, None) {
                        methods.push(method);
                    }
                }
                Item::Impl(item_impl) => {
                    // 分析impl块中的方法
                    let impl_methods = self.analyze_impl_block(item_impl, file_path)?;
                    methods.extend(impl_methods);
                }
                Item::Trait(item_trait) => {
                    // 分析trait中的方法定义
                    for trait_item in &item_trait.items {
                        if let syn::TraitItem::Fn(trait_fn) = trait_item
                            && let Ok(method) = self.analyze_trait_method(
                                trait_fn,
                                file_path,
                                &item_trait.ident.to_string(),
                            )
                        {
                            methods.push(method);
                        }
                    }
                }
                Item::Macro(item_macro) => {
                    // 分析宏定义
                    if let Ok(method) = self.analyze_macro_definition(item_macro, file_path) {
                        methods.push(method);
                    }
                }
                Item::Const(item_const) => {
                    // 分析常量定义
                    if let Ok(method) = self.analyze_const_definition(item_const, file_path) {
                        methods.push(method);
                    }
                }
                Item::Static(item_static) => {
                    // 分析静态变量定义
                    if let Ok(method) = self.analyze_static_definition(item_static, file_path) {
                        methods.push(method);
                    }
                }
                _ => {}
            }
        }

        debug!(
            "在文件 {} 中发现 {} 个函数",
            file_path.display(),
            methods.len()
        );
        Ok(methods)
    }

    /// 分析单个函数定义
    fn analyze_function(
        &mut self,
        item_fn: &ItemFn,
        file_path: &PathBuf,
        impl_type: Option<&str>,
    ) -> Result<MethodOutput> {
        let function_name = item_fn.sig.ident.to_string();
        let _full_name = if let Some(type_name) = impl_type {
            generate_id(
                &self.current_module,
                &format!("{type_name}::{function_name}"),
            )
        } else {
            generate_id(&self.current_module, &function_name)
        };

        // 提取函数签名
        let signature = self.generate_function_signature(&item_fn.sig, impl_type);

        // 分析参数
        let params = self.extract_parameters(&item_fn.sig.inputs)?;

        // 提取返回类型
        let return_type = self.extract_return_type(&item_fn.sig.output);

        // 分析可见性
        let modifier = self.extract_modifiers(&item_fn.vis, &item_fn.sig);

        // 提取源代码行号
        let (begin_line, end_line) = self.extract_line_numbers(&item_fn.span());

        // 分析函数体中的调用关系
        self.function_calls.clear();
        self.visit_block(&item_fn.block);
        let callees: Vec<String> = self.function_calls.iter().cloned().collect();

        // 获取相对文件路径（保留目录结构）
        let filename = self.get_relative_path(file_path);

        Ok(MethodOutput {
            name: function_name,
            signature,
            begin_line,
            end_line,
            filename,
            modifier,
            params,
            return_type,
            callees,
        })
    }

    /// 分析impl块中的所有方法
    fn analyze_impl_block(
        &mut self,
        item_impl: &ItemImpl,
        file_path: &PathBuf,
    ) -> Result<Vec<MethodOutput>> {
        let mut methods = Vec::new();

        // 提取impl的目标类型名称
        let impl_type_name = self.extract_type_name(&item_impl.self_ty);

        for impl_item in &item_impl.items {
            if let ImplItem::Fn(method) = impl_item
                && let Ok(method_output) = self.analyze_function(
                    &ItemFn {
                        attrs: method.attrs.clone(),
                        vis: method.vis.clone(),
                        sig: method.sig.clone(),
                        block: Box::new(method.block.clone()),
                    },
                    file_path,
                    Some(&impl_type_name),
                )
            {
                methods.push(method_output);
            }
        }

        Ok(methods)
    }

    /// 分析trait方法
    fn analyze_trait_method(
        &mut self,
        trait_fn: &syn::TraitItemFn,
        file_path: &PathBuf,
        trait_name: &str,
    ) -> Result<MethodOutput> {
        let function_name = trait_fn.sig.ident.to_string();
        let _full_name = generate_id(
            &self.current_module,
            &format!("{trait_name}::{function_name}"),
        );

        let signature = self.generate_function_signature(&trait_fn.sig, Some(trait_name));
        let params = self.extract_parameters(&trait_fn.sig.inputs)?;
        let return_type = self.extract_return_type(&trait_fn.sig.output);
        let modifier = format!("TRAIT_{}", normalize_visibility(&Visibility::Inherited));
        let (begin_line, end_line) = self.extract_line_numbers(&trait_fn.span());

        let filename = self.get_relative_path(file_path);

        // trait方法通常没有实现体，所以callees为空
        let callees = if let Some(default_impl) = &trait_fn.default {
            self.function_calls.clear();
            self.visit_block(default_impl);
            self.function_calls.iter().cloned().collect()
        } else {
            Vec::new()
        };

        Ok(MethodOutput {
            name: function_name,
            signature,
            begin_line,
            end_line,
            filename,
            modifier,
            params,
            return_type,
            callees,
        })
    }

    /// 生成函数签名字符串
    fn generate_function_signature(&self, sig: &syn::Signature, impl_type: Option<&str>) -> String {
        let return_type = match &sig.output {
            ReturnType::Default => "()".to_string(),
            ReturnType::Type(_, ty) => self.type_to_string(ty),
        };

        let params: Vec<String> = sig
            .inputs
            .iter()
            .map(|input| match input {
                FnArg::Receiver(_) => "self".to_string(),
                FnArg::Typed(pat_type) => self.type_to_string(&pat_type.ty),
            })
            .collect();

        let function_name = if let Some(type_name) = impl_type {
            format!("{}::{}", type_name, sig.ident)
        } else {
            sig.ident.to_string()
        };

        format!("{} {}({})", return_type, function_name, params.join(", "))
    }

    /// 提取函数参数信息
    fn extract_parameters(
        &self,
        inputs: &syn::punctuated::Punctuated<FnArg, syn::Token![,]>,
    ) -> Result<Vec<ParameterInfo>> {
        let mut params = Vec::new();

        for input in inputs {
            match input {
                FnArg::Receiver(_) => {
                    // self参数不包含在输出中
                    continue;
                }
                FnArg::Typed(PatType { pat, ty, .. }) => {
                    let param_name = self.extract_pattern_name(pat);
                    let param_type = self.type_to_string(ty);

                    params.push(ParameterInfo {
                        name: param_name,
                        param_type,
                    });
                }
            }
        }

        Ok(params)
    }

    /// 从模式中提取参数名称
    fn extract_pattern_name(&self, pat: &Pat) -> String {
        match pat {
            Pat::Ident(pat_ident) => pat_ident.ident.to_string(),
            Pat::Reference(pat_ref) => self.extract_pattern_name(&pat_ref.pat),
            Pat::Type(pat_type) => self.extract_pattern_name(&pat_type.pat),
            _ => "_".to_string(),
        }
    }

    /// 提取返回类型
    fn extract_return_type(&self, output: &ReturnType) -> String {
        match output {
            ReturnType::Default => "()".to_string(),
            ReturnType::Type(_, ty) => self.type_to_string(ty),
        }
    }

    /// 将类型转换为字符串表示
    fn type_to_string(&self, ty: &Type) -> String {
        match ty {
            Type::Path(type_path) => type_path
                .path
                .segments
                .iter()
                .map(|seg| seg.ident.to_string())
                .collect::<Vec<_>>()
                .join("::"),
            Type::Reference(type_ref) => {
                let mutability = if type_ref.mutability.is_some() {
                    "mut "
                } else {
                    ""
                };
                format!("&{}{}", mutability, self.type_to_string(&type_ref.elem))
            }
            Type::Tuple(type_tuple) => {
                let elements: Vec<String> = type_tuple
                    .elems
                    .iter()
                    .map(|elem| self.type_to_string(elem))
                    .collect();
                format!("({})", elements.join(", "))
            }
            Type::Array(type_array) => {
                format!("[{}; _]", self.type_to_string(&type_array.elem))
            }
            Type::Slice(type_slice) => {
                format!("[{}]", self.type_to_string(&type_slice.elem))
            }
            _ => "unknown".to_string(),
        }
    }

    /// 提取类型名称
    fn extract_type_name(&self, ty: &Type) -> String {
        match ty {
            Type::Path(type_path) => type_path
                .path
                .segments
                .last()
                .map(|seg| seg.ident.to_string())
                .unwrap_or_else(|| "Unknown".to_string()),
            _ => "Unknown".to_string(),
        }
    }

    /// 提取修饰符信息
    fn extract_modifiers(&self, vis: &Visibility, sig: &syn::Signature) -> String {
        let mut modifiers = Vec::new();

        // 可见性修饰符
        modifiers.push(normalize_visibility(vis));

        // 异步修饰符
        if sig.asyncness.is_some() {
            modifiers.push("ASYNC".to_string());
        }

        // 不安全修饰符
        if sig.unsafety.is_some() {
            modifiers.push("UNSAFE".to_string());
        }

        // 外部函数修饰符
        if sig.abi.is_some() {
            modifiers.push("EXTERN".to_string());
        }

        modifiers.join("_")
    }

    /// 构建行号映射
    fn build_line_map(&mut self) {
        self.line_map.clear();
        let mut line_number = 1u32;
        let mut byte_offset = 0usize;

        self.line_map.insert(0, 1);

        for (i, byte) in self.source_content.bytes().enumerate() {
            if byte == b'\n' {
                line_number += 1;
                self.line_map.insert(i + 1, line_number);
            }
            byte_offset = i + 1;
        }

        // 确保文件末尾有映射
        self.line_map.entry(byte_offset).or_insert(line_number);
    }

    /// 从字节偏移量获取行号
    fn byte_offset_to_line(&self, offset: usize) -> u32 {
        // 查找最接近的行号映射
        let mut best_line = 1u32;
        let mut best_offset = 0usize;

        for (&map_offset, &line) in &self.line_map {
            if map_offset <= offset && map_offset > best_offset {
                best_offset = map_offset;
                best_line = line;
            }
        }

        best_line
    }

    /// 提取行号信息
    fn extract_line_numbers(&self, span: &proc_macro2::Span) -> (u32, u32) {
        // 尝试从span获取行号信息
        let start = span.start();
        let end = span.end();

        let start_line = start.line as u32;
        let end_line = end.line as u32;

        // 如果获取到有效的行号信息，使用它们
        if start_line > 0 && end_line > 0 {
            (start_line, end_line)
        } else {
            // 如果span信息不可用，尝试通过其他方式估算
            // 这是一个fallback，在某些环境下span可能不提供准确信息
            (1, 1)
        }
    }
}

/// 实现syn::visit::Visit trait来遍历AST并收集函数调用
impl<'ast> Visit<'ast> for FunctionAnalyzer {
    fn visit_expr_call(&mut self, call: &'ast ExprCall) {
        // 处理函数调用表达式
        match &*call.func {
            Expr::Path(expr_path) => {
                let function_name = self.resolve_path_to_function_name(&expr_path.path);
                if !function_name.is_empty() {
                    self.function_calls.insert(function_name);
                }
            }
            Expr::Field(field_expr) => {
                // 处理字段访问的函数调用，如 obj.field()
                if let syn::Member::Named(ident) = &field_expr.member {
                    let field_name = ident.to_string();
                    self.function_calls.insert(field_name);
                }
            }
            _ => {}
        }

        // 继续遍历子表达式
        syn::visit::visit_expr_call(self, call);
    }

    fn visit_expr_method_call(&mut self, method_call: &'ast ExprMethodCall) {
        // 处理方法调用表达式
        let _method_name = method_call.method.to_string();

        // 尝试解析接收者类型以获得更准确的方法名
        let qualified_method = self.resolve_method_call(method_call);
        self.function_calls.insert(qualified_method);

        // 继续遍历子表达式
        syn::visit::visit_expr_method_call(self, method_call);
    }

    fn visit_expr_path(&mut self, path: &'ast ExprPath) {
        // 处理路径表达式（可能是函数引用）
        let path_string = self.resolve_path_to_function_name(&path.path);

        // 检查是否为函数引用（不是类型或常量）
        if self.is_likely_function_reference(&path_string) {
            self.function_calls.insert(path_string);
        }

        syn::visit::visit_expr_path(self, path);
    }

    fn visit_expr_macro(&mut self, mac: &'ast syn::ExprMacro) {
        // 处理宏调用
        let macro_name = mac
            .mac
            .path
            .segments
            .iter()
            .map(|seg| seg.ident.to_string())
            .collect::<Vec<_>>()
            .join("::");

        if !macro_name.is_empty() {
            self.function_calls.insert(format!("{macro_name}!"));
        }

        syn::visit::visit_expr_macro(self, mac);
    }
}

impl FunctionAnalyzer {
    /// 解析路径为函数名
    fn resolve_path_to_function_name(&self, path: &syn::Path) -> String {
        let segments: Vec<String> = path
            .segments
            .iter()
            .map(|seg| seg.ident.to_string())
            .collect();

        if segments.is_empty() {
            return String::new();
        }

        // 构建完整路径
        let full_path = segments.join("::");

        // 如果是相对路径，添加当前模块前缀
        if !full_path.starts_with("crate::")
            && !full_path.starts_with("std::")
            && !full_path.starts_with("super::")
            && !full_path.starts_with("self::")
        {
            if segments.len() == 1 {
                // 单个标识符，可能是当前模块的函数
                format!("{}::{}", self.current_module, full_path)
            } else {
                // 多段路径，保持原样
                full_path
            }
        } else {
            full_path
        }
    }

    /// 解析方法调用
    fn resolve_method_call(&self, method_call: &syn::ExprMethodCall) -> String {
        let method_name = method_call.method.to_string();

        // 尝试从接收者推断类型
        match &*method_call.receiver {
            Expr::Path(path_expr) => {
                let receiver_path = self.resolve_path_to_function_name(&path_expr.path);
                if !receiver_path.is_empty() {
                    format!("{receiver_path}::{method_name}")
                } else {
                    method_name
                }
            }
            Expr::Field(field_expr) => {
                if let syn::Member::Named(ident) = &field_expr.member {
                    format!("{ident}::{method_name}")
                } else {
                    method_name
                }
            }
            _ => method_name,
        }
    }

    /// 检查路径是否可能是函数引用
    fn is_likely_function_reference(&self, path: &str) -> bool {
        if path.is_empty() {
            return false;
        }

        // 排除明显的类型名（通常以大写字母开头）
        let last_segment = path.split("::").last().unwrap_or(path);

        // 函数名通常以小写字母开头，或者是特殊的函数名模式
        let first_char = last_segment.chars().next().unwrap_or('A');
        let is_function_like = first_char.is_lowercase()
            || last_segment.starts_with('_')
            || last_segment == "new"
            || last_segment == "default"
            || last_segment.ends_with("_new")
            || last_segment.ends_with("_default");

        // 排除常见的常量模式（全大写）
        let is_constant = last_segment.chars().all(|c| c.is_uppercase() || c == '_');

        is_function_like && !is_constant
    }

    /// 分析宏定义
    pub fn analyze_macro_definition(
        &mut self,
        item_macro: &ItemMacro,
        file_path: &PathBuf,
    ) -> Result<MethodOutput> {
        let macro_name = item_macro
            .ident
            .as_ref()
            .map(|ident| ident.to_string())
            .unwrap_or_else(|| "anonymous_macro".to_string());

        let signature = format!("macro {macro_name}!");
        let (begin_line, end_line) = self.extract_line_numbers(&item_macro.span());

        // 分析宏体中的调用关系
        self.function_calls.clear();

        let callees = self.analyze_macro_tokens(&item_macro.mac.tokens)?;

        let filename = self.get_relative_path(file_path);

        Ok(MethodOutput {
            name: macro_name,
            signature,
            begin_line,
            end_line,
            filename,
            modifier: "MACRO PUBLIC".to_string(),
            params: Vec::new(), // 宏参数处理复杂，暂时为空
            return_type: "TokenStream".to_string(),
            callees,
        })
    }

    /// 分析常量定义
    pub fn analyze_const_definition(
        &mut self,
        item_const: &ItemConst,
        file_path: &PathBuf,
    ) -> Result<MethodOutput> {
        let const_name = item_const.ident.to_string();
        let const_type = self.type_to_string(&item_const.ty);
        let signature = format!("const {const_name}: {const_type}");
        let (begin_line, end_line) = self.extract_line_numbers(&item_const.span());

        // 分析常量表达式中的调用关系
        self.function_calls.clear();
        self.visit_expr(&item_const.expr);
        let callees: Vec<String> = self.function_calls.iter().cloned().collect();

        let filename = self.get_relative_path(file_path);

        let modifier = format!("CONST {}", normalize_visibility(&item_const.vis));

        Ok(MethodOutput {
            name: const_name,
            signature,
            begin_line,
            end_line,
            filename,
            modifier,
            params: Vec::new(),
            return_type: const_type,
            callees,
        })
    }

    /// 分析静态变量定义
    pub fn analyze_static_definition(
        &mut self,
        item_static: &ItemStatic,
        file_path: &PathBuf,
    ) -> Result<MethodOutput> {
        let static_name = item_static.ident.to_string();
        let static_type = self.type_to_string(&item_static.ty);
        let mutability = match item_static.mutability {
            syn::StaticMutability::Mut(_) => "mut ",
            syn::StaticMutability::None => "",
            _ => "",
        };
        let signature = format!("static {mutability}{static_name}: {static_type}");
        let (begin_line, end_line) = self.extract_line_numbers(&item_static.span());

        // 分析静态变量表达式中的调用关系
        self.function_calls.clear();
        self.visit_expr(&item_static.expr);
        let callees: Vec<String> = self.function_calls.iter().cloned().collect();

        let filename = self.get_relative_path(file_path);

        let mut modifier = format!("STATIC {}", normalize_visibility(&item_static.vis));
        if matches!(item_static.mutability, syn::StaticMutability::Mut(_)) {
            modifier.push_str(" MUTABLE");
        }

        Ok(MethodOutput {
            name: static_name,
            signature,
            begin_line,
            end_line,
            filename,
            modifier,
            params: Vec::new(),
            return_type: static_type,
            callees,
        })
    }

    /// 分析宏token流中的调用关系
    fn analyze_macro_tokens(&mut self, tokens: &proc_macro2::TokenStream) -> Result<Vec<String>> {
        let mut callees = Vec::new();

        // 将token流转换为字符串进行简单分析
        let token_string = tokens.to_string();

        // 使用正则表达式查找可能的函数调用
        let call_regex = regex::Regex::new(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
            .map_err(|e| AnalyzerError::analysis_error("regex compilation", e.to_string()))?;

        for cap in call_regex.captures_iter(&token_string) {
            if let Some(func_name) = cap.get(1) {
                let name = func_name.as_str().to_string();
                if self.is_likely_function_reference(&name) {
                    callees.push(name);
                }
            }
        }

        // 查找宏调用
        let macro_regex = regex::Regex::new(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*!")
            .map_err(|e| AnalyzerError::analysis_error("regex compilation", e.to_string()))?;

        for cap in macro_regex.captures_iter(&token_string) {
            if let Some(macro_name) = cap.get(1) {
                callees.push(format!("{}!", macro_name.as_str()));
            }
        }

        Ok(callees)
    }
}
