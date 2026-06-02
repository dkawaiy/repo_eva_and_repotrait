use crate::error::{AnalyzerError, Result};
use crate::output::{AttributeInfo, StructOutput};
use crate::utils::{extract_module_path, generate_id, normalize_visibility};

use log::debug;
use std::collections::HashMap;
use std::path::PathBuf;
use syn::{
    Fields, File, GenericParam, Generics, ImplItem, Item, ItemEnum, ItemStruct, ItemTrait,
    TraitItem, Type,
};

/// 分析Rust类型定义（struct、enum、trait）的核心组件
pub struct TypeAnalyzer {
    /// 当前分析的模块路径
    current_module: String,
    /// 类型到其实现方法的映射
    type_implementations: HashMap<String, Vec<String>>,
    /// 源代码内容，用于行号计算
    source_content: String,
}

impl Default for TypeAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl TypeAnalyzer {
    pub fn new() -> Self {
        Self {
            current_module: String::new(),
            type_implementations: HashMap::new(),
            source_content: String::new(),
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

    /// 分析单个文件中的所有类型定义
    pub fn analyze_file(
        &mut self,
        file_path: &PathBuf,
        parsed_file: &File,
        project_root: &PathBuf,
    ) -> Result<Vec<StructOutput>> {
        self.current_module = extract_module_path(file_path, project_root);
        debug!(
            "分析文件中的类型: {} (模块: {})",
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

        let mut types = Vec::new();

        // 首先收集所有impl块的信息
        self.collect_implementations(parsed_file);

        // 分析各种类型定义
        for item in &parsed_file.items {
            match item {
                Item::Struct(item_struct) => {
                    if let Ok(struct_output) = self.analyze_struct(item_struct, file_path) {
                        types.push(struct_output);
                    }
                }
                Item::Enum(item_enum) => {
                    if let Ok(enum_output) = self.analyze_enum(item_enum, file_path) {
                        types.push(enum_output);
                    }
                }
                Item::Trait(item_trait) => {
                    if let Ok(trait_output) = self.analyze_trait(item_trait, file_path) {
                        types.push(trait_output);
                    }
                }
                Item::Union(item_union) => {
                    if let Ok(union_output) = self.analyze_union(item_union, file_path) {
                        types.push(union_output);
                    }
                }
                _ => {}
            }
        }

        debug!(
            "在文件 {} 中发现 {} 个类型定义",
            file_path.display(),
            types.len()
        );
        Ok(types)
    }

    /// 收集所有impl块的实现信息
    fn collect_implementations(&mut self, parsed_file: &File) {
        self.type_implementations.clear();

        for item in &parsed_file.items {
            if let Item::Impl(item_impl) = item {
                let type_name = self.extract_impl_target_type(&item_impl.self_ty);
                let full_type_name = generate_id(&self.current_module, &type_name);

                let methods: Vec<String> = item_impl
                    .items
                    .iter()
                    .filter_map(|impl_item| {
                        if let ImplItem::Fn(method) = impl_item {
                            Some(method.sig.ident.to_string())
                        } else {
                            None
                        }
                    })
                    .collect();

                self.type_implementations
                    .entry(full_type_name)
                    .or_default()
                    .extend(methods);
            }
        }
    }

    /// 分析struct定义
    fn analyze_struct(
        &self,
        item_struct: &ItemStruct,
        file_path: &PathBuf,
    ) -> Result<StructOutput> {
        let struct_name = item_struct.ident.to_string();
        let full_name = generate_id(&self.current_module, &struct_name);

        // 分析字段
        let attributes = self.extract_struct_fields(&item_struct.fields)?;

        // 获取实现的方法
        let methods = self
            .type_implementations
            .get(&full_name)
            .cloned()
            .unwrap_or_default();

        // 提取泛型信息
        let _generics_info = self.extract_generics(&item_struct.generics);

        // 获取相对文件路径
        let filename = self.get_relative_path(file_path);

        // 提取行号信息
        let begin_line = self.extract_line_number_from_span(&item_struct.ident.span());

        Ok(StructOutput {
            name: struct_name,
            fullname: full_name,
            filename,
            begin_line,
            methods,
            attributes,
        })
    }

    /// 分析enum定义
    fn analyze_enum(&self, item_enum: &ItemEnum, file_path: &PathBuf) -> Result<StructOutput> {
        let enum_name = item_enum.ident.to_string();
        let full_name = generate_id(&self.current_module, &enum_name);

        // 将enum变体作为属性处理
        let mut attributes = Vec::new();
        for variant in &item_enum.variants {
            let variant_name = variant.ident.to_string();
            let variant_type = match &variant.fields {
                Fields::Named(fields_named) => {
                    let field_types: Vec<String> = fields_named
                        .named
                        .iter()
                        .map(|field| self.type_to_string(&field.ty))
                        .collect();
                    format!("{{ {} }}", field_types.join(", "))
                }
                Fields::Unnamed(fields_unnamed) => {
                    let field_types: Vec<String> = fields_unnamed
                        .unnamed
                        .iter()
                        .map(|field| self.type_to_string(&field.ty))
                        .collect();
                    format!("({})", field_types.join(", "))
                }
                Fields::Unit => "()".to_string(),
            };

            attributes.push(AttributeInfo {
                name: variant_name,
                attr_type: variant_type,
                modifier: "VARIANT".to_string(),
            });
        }

        let methods = self
            .type_implementations
            .get(&full_name)
            .cloned()
            .unwrap_or_default();

        let filename = self.get_relative_path(file_path);

        Ok(StructOutput {
            name: enum_name,
            fullname: full_name,
            filename,
            begin_line: self.extract_line_number_from_span(&item_enum.ident.span()),
            methods,
            attributes,
        })
    }

    /// 分析trait定义
    fn analyze_trait(&self, item_trait: &ItemTrait, file_path: &PathBuf) -> Result<StructOutput> {
        let trait_name = item_trait.ident.to_string();
        let full_name = generate_id(&self.current_module, &trait_name);

        // 提取trait方法作为属性
        let mut attributes = Vec::new();
        let mut methods = Vec::new();

        for trait_item in &item_trait.items {
            match trait_item {
                TraitItem::Fn(trait_fn) => {
                    let method_name = trait_fn.sig.ident.to_string();
                    methods.push(method_name.clone());

                    // 将trait方法也作为属性记录
                    let method_signature = self.generate_trait_method_signature(&trait_fn.sig);
                    attributes.push(AttributeInfo {
                        name: method_name,
                        attr_type: method_signature,
                        modifier: "TRAIT_METHOD".to_string(),
                    });
                }
                TraitItem::Type(trait_type) => {
                    let type_name = trait_type.ident.to_string();
                    attributes.push(AttributeInfo {
                        name: type_name,
                        attr_type: "AssociatedType".to_string(),
                        modifier: "ASSOCIATED_TYPE".to_string(),
                    });
                }
                TraitItem::Const(trait_const) => {
                    let const_name = trait_const.ident.to_string();
                    let const_type = self.type_to_string(&trait_const.ty);
                    attributes.push(AttributeInfo {
                        name: const_name,
                        attr_type: const_type,
                        modifier: "TRAIT_CONST".to_string(),
                    });
                }
                _ => {}
            }
        }

        let filename = self.get_relative_path(file_path);

        Ok(StructOutput {
            name: trait_name,
            fullname: full_name,
            filename,
            begin_line: self.extract_line_number_from_span(&item_trait.ident.span()),
            methods,
            attributes,
        })
    }

    /// 分析union定义
    fn analyze_union(
        &self,
        item_union: &syn::ItemUnion,
        file_path: &PathBuf,
    ) -> Result<StructOutput> {
        let union_name = item_union.ident.to_string();
        let full_name = generate_id(&self.current_module, &union_name);

        // 分析union字段
        let mut attributes = Vec::new();
        for field in &item_union.fields.named {
            if let Some(field_name) = &field.ident {
                let field_type = self.type_to_string(&field.ty);
                let modifier = normalize_visibility(&field.vis);

                attributes.push(AttributeInfo {
                    name: field_name.to_string(),
                    attr_type: field_type,
                    modifier,
                });
            }
        }

        let methods = self
            .type_implementations
            .get(&full_name)
            .cloned()
            .unwrap_or_default();

        let filename = self.get_relative_path(file_path);

        Ok(StructOutput {
            name: union_name,
            fullname: full_name,
            filename,
            begin_line: self.extract_line_number_from_span(&item_union.ident.span()),
            methods,
            attributes,
        })
    }

    /// 提取struct字段信息
    fn extract_struct_fields(&self, fields: &Fields) -> Result<Vec<AttributeInfo>> {
        let mut attributes = Vec::new();

        match fields {
            Fields::Named(fields_named) => {
                for field in &fields_named.named {
                    if let Some(field_name) = &field.ident {
                        let field_type = self.type_to_string(&field.ty);
                        let modifier = normalize_visibility(&field.vis);

                        attributes.push(AttributeInfo {
                            name: field_name.to_string(),
                            attr_type: field_type,
                            modifier,
                        });
                    }
                }
            }
            Fields::Unnamed(fields_unnamed) => {
                for (index, field) in fields_unnamed.unnamed.iter().enumerate() {
                    let field_name = format!("_{index}");
                    let field_type = self.type_to_string(&field.ty);
                    let modifier = normalize_visibility(&field.vis);

                    attributes.push(AttributeInfo {
                        name: field_name,
                        attr_type: field_type,
                        modifier,
                    });
                }
            }
            Fields::Unit => {
                // Unit struct没有字段
            }
        }

        Ok(attributes)
    }

    /// 提取impl块的目标类型
    fn extract_impl_target_type(&self, ty: &Type) -> String {
        match ty {
            Type::Path(type_path) => type_path
                .path
                .segments
                .iter()
                .map(|seg| seg.ident.to_string())
                .collect::<Vec<_>>()
                .join("::"),
            _ => "Unknown".to_string(),
        }
    }

    /// 将类型转换为字符串表示
    fn type_to_string(&self, ty: &Type) -> String {
        match ty {
            Type::Path(type_path) => type_path
                .path
                .segments
                .iter()
                .map(|seg| {
                    let mut segment = seg.ident.to_string();
                    if !seg.arguments.is_empty() {
                        segment.push_str("<...>");
                    }
                    segment
                })
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
            Type::Ptr(type_ptr) => {
                let mutability = if type_ptr.mutability.is_some() {
                    "mut "
                } else {
                    "const "
                };
                format!("*{}{}", mutability, self.type_to_string(&type_ptr.elem))
            }
            _ => "unknown".to_string(),
        }
    }

    /// 生成trait方法签名
    fn generate_trait_method_signature(&self, sig: &syn::Signature) -> String {
        let return_type = match &sig.output {
            syn::ReturnType::Default => "()".to_string(),
            syn::ReturnType::Type(_, ty) => self.type_to_string(ty),
        };

        let params: Vec<String> = sig
            .inputs
            .iter()
            .map(|input| match input {
                syn::FnArg::Receiver(_) => "self".to_string(),
                syn::FnArg::Typed(pat_type) => self.type_to_string(&pat_type.ty),
            })
            .collect();

        format!("{} {}({})", return_type, sig.ident, params.join(", "))
    }

    /// 提取泛型信息
    fn extract_generics(&self, generics: &Generics) -> String {
        if generics.params.is_empty() {
            return String::new();
        }

        let params: Vec<String> = generics
            .params
            .iter()
            .map(|param| match param {
                GenericParam::Type(type_param) => type_param.ident.to_string(),
                GenericParam::Lifetime(lifetime_param) => lifetime_param.lifetime.to_string(),
                GenericParam::Const(const_param) => const_param.ident.to_string(),
            })
            .collect();

        format!("<{}>", params.join(", "))
    }

    /// 从span提取行号
    fn extract_line_number_from_span(&self, span: &proc_macro2::Span) -> u32 {
        // 尝试从span获取行号信息
        let start = span.start();
        let line = start.line as u32;

        // 如果获取到有效的行号信息，使用它
        if line > 0 {
            line
        } else {
            // 如果span信息不可用，返回默认值
            1
        }
    }
}
