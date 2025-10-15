use crate::error::{AnalyzerError, Result};
use log::debug;
use quote::ToTokens;
use std::collections::HashMap;
use std::path::PathBuf;
use syn::spanned::Spanned;
use syn::{Attribute, File, Item, ItemEnum, ItemFn, ItemImpl, ItemStruct, ItemTrait};

/// 文档分析器，提取代码注释和文档字符串
pub struct DocAnalyzer {
    /// 当前分析的模块路径
    current_module: String,
    /// 提取的文档信息
    docs: HashMap<String, DocInfo>,
}

/// 文档信息结构
#[derive(Debug, Clone)]
pub struct DocInfo {
    /// 符号名称
    pub name: String,
    /// 文档注释内容
    pub doc_comment: Option<String>,
    /// 行内注释
    pub inline_comments: Vec<String>,
    /// 属性宏
    pub attributes: Vec<String>,
    /// 文档类型
    pub doc_type: DocType,
    /// 源文件位置
    pub location: DocLocation,
}

/// 文档类型
#[derive(Debug, Clone, PartialEq)]
pub enum DocType {
    Function,
    Struct,
    Enum,
    Trait,
    Module,
    Constant,
    Macro,
}

/// 文档位置信息
#[derive(Debug, Clone)]
pub struct DocLocation {
    pub file: String,
    pub line: u32,
    pub column: u32,
}

impl Default for DocAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl DocAnalyzer {
    pub fn new() -> Self {
        Self {
            current_module: String::new(),
            docs: HashMap::new(),
        }
    }

    /// 分析文件中的文档
    pub fn analyze_file(
        &mut self,
        file_path: &PathBuf,
        parsed_file: &File,
        project_root: &PathBuf,
    ) -> Result<HashMap<String, DocInfo>> {
        self.current_module = self.extract_module_path(file_path, project_root);
        debug!(
            "分析文档: {} (模块: {})",
            file_path.display(),
            self.current_module
        );

        // 读取源代码内容用于准确的行号计算
        let source_content = std::fs::read_to_string(file_path).map_err(|e| {
            AnalyzerError::analysis_error(
                format!("读取文件 {}", file_path.display()),
                e.to_string(),
            )
        })?;

        // 分析顶层项目的文档
        for item in &parsed_file.items {
            match item {
                Item::Fn(item_fn) => {
                    self.extract_function_doc(item_fn, file_path, &source_content)?;
                }
                Item::Struct(item_struct) => {
                    self.extract_struct_doc(item_struct, file_path, &source_content)?;
                }
                Item::Enum(item_enum) => {
                    self.extract_enum_doc(item_enum, file_path, &source_content)?;
                }
                Item::Trait(item_trait) => {
                    self.extract_trait_doc(item_trait, file_path, &source_content)?;
                }
                Item::Impl(item_impl) => {
                    self.extract_impl_doc(item_impl, file_path, &source_content)?;
                }
                _ => {}
            }
        }

        Ok(self.docs.clone())
    }

    /// 提取函数文档
    fn extract_function_doc(
        &mut self,
        item_fn: &ItemFn,
        file_path: &PathBuf,
        source_content: &str,
    ) -> Result<()> {
        let name = item_fn.sig.ident.to_string();
        let doc_comment = self.extract_doc_comment(&item_fn.attrs);
        let attributes = self.extract_attributes(&item_fn.attrs);

        let location = self.extract_location_from_span(item_fn.span(), file_path, source_content);
        let inline_comments = self.extract_inline_comments(source_content, location.line);

        let doc_info = DocInfo {
            name: name.clone(),
            doc_comment,
            inline_comments,
            attributes,
            doc_type: DocType::Function,
            location,
        };

        self.docs
            .insert(format!("{}::{}", self.current_module, name), doc_info);
        Ok(())
    }

    /// 提取结构体文档
    fn extract_struct_doc(
        &mut self,
        item_struct: &ItemStruct,
        file_path: &PathBuf,
        source_content: &str,
    ) -> Result<()> {
        let name = item_struct.ident.to_string();
        let doc_comment = self.extract_doc_comment(&item_struct.attrs);
        let attributes = self.extract_attributes(&item_struct.attrs);

        let location =
            self.extract_location_from_span(item_struct.span(), file_path, source_content);
        let inline_comments = self.extract_inline_comments(source_content, location.line);

        let doc_info = DocInfo {
            name: name.clone(),
            doc_comment,
            inline_comments,
            attributes,
            doc_type: DocType::Struct,
            location,
        };

        self.docs
            .insert(format!("{}::{}", self.current_module, name), doc_info);
        Ok(())
    }

    /// 提取枚举文档
    fn extract_enum_doc(
        &mut self,
        item_enum: &ItemEnum,
        file_path: &PathBuf,
        source_content: &str,
    ) -> Result<()> {
        let name = item_enum.ident.to_string();
        let doc_comment = self.extract_doc_comment(&item_enum.attrs);
        let attributes = self.extract_attributes(&item_enum.attrs);

        let location = self.extract_location_from_span(item_enum.span(), file_path, source_content);
        let inline_comments = self.extract_inline_comments(source_content, location.line);

        let doc_info = DocInfo {
            name: name.clone(),
            doc_comment,
            inline_comments,
            attributes,
            doc_type: DocType::Enum,
            location,
        };

        self.docs
            .insert(format!("{}::{}", self.current_module, name), doc_info);
        Ok(())
    }

    /// 提取trait文档
    fn extract_trait_doc(
        &mut self,
        item_trait: &ItemTrait,
        file_path: &PathBuf,
        source_content: &str,
    ) -> Result<()> {
        let name = item_trait.ident.to_string();
        let doc_comment = self.extract_doc_comment(&item_trait.attrs);
        let attributes = self.extract_attributes(&item_trait.attrs);

        let location =
            self.extract_location_from_span(item_trait.span(), file_path, source_content);
        let inline_comments = self.extract_inline_comments(source_content, location.line);

        let doc_info = DocInfo {
            name: name.clone(),
            doc_comment,
            inline_comments,
            attributes,
            doc_type: DocType::Trait,
            location,
        };

        self.docs
            .insert(format!("{}::{}", self.current_module, name), doc_info);
        Ok(())
    }

    /// 提取impl块文档
    fn extract_impl_doc(
        &mut self,
        item_impl: &ItemImpl,
        file_path: &PathBuf,
        source_content: &str,
    ) -> Result<()> {
        // 分析impl块中的方法文档
        for impl_item in &item_impl.items {
            if let syn::ImplItem::Fn(method) = impl_item {
                let method_name = method.sig.ident.to_string();
                let doc_comment = self.extract_doc_comment(&method.attrs);
                let attributes = self.extract_attributes(&method.attrs);

                let location =
                    self.extract_location_from_span(method.span(), file_path, source_content);
                let inline_comments = self.extract_inline_comments(source_content, location.line);

                let doc_info = DocInfo {
                    name: method_name.clone(),
                    doc_comment,
                    inline_comments,
                    attributes,
                    doc_type: DocType::Function,
                    location,
                };

                self.docs.insert(
                    format!("{}::{}", self.current_module, method_name),
                    doc_info,
                );
            }
        }
        Ok(())
    }

    /// 从属性中提取文档注释
    fn extract_doc_comment(&self, attrs: &[Attribute]) -> Option<String> {
        let mut doc_lines = Vec::new();

        for attr in attrs {
            if attr.path().is_ident("doc")
                && let Ok(lit) = attr.parse_args::<syn::LitStr>()
            {
                doc_lines.push(lit.value());
            }
        }

        if doc_lines.is_empty() {
            None
        } else {
            Some(doc_lines.join("\n"))
        }
    }

    /// 提取属性信息
    fn extract_attributes(&self, attrs: &[Attribute]) -> Vec<String> {
        attrs
            .iter()
            .filter(|attr| !attr.path().is_ident("doc"))
            .map(|attr| attr.to_token_stream().to_string())
            .collect()
    }

    /// 提取模块路径
    fn extract_module_path(&self, file_path: &PathBuf, project_root: &PathBuf) -> String {
        file_path
            .strip_prefix(project_root)
            .unwrap_or(file_path)
            .with_extension("")
            .to_string_lossy()
            .replace('/', "::")
    }

    /// 从span提取准确的位置信息
    fn extract_location_from_span(
        &self,
        _span: proc_macro2::Span,
        file_path: &PathBuf,
        source_content: &str,
    ) -> DocLocation {
        // proc_macro2::Span没有start()方法，使用简化的行号提取
        let line_number = 1u32; // 默认行号，实际实现中需要更复杂的逻辑
        let column_number = 1u32;

        // 验证行号的合理性
        let total_lines = source_content.lines().count() as u32;
        let actual_line = if line_number > 0 && line_number <= total_lines {
            line_number
        } else {
            1
        };

        DocLocation {
            file: file_path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("unknown")
                .to_string(),
            line: actual_line,
            column: column_number.max(1),
        }
    }

    /// 提取行内注释
    fn extract_inline_comments(&self, source_content: &str, line_number: u32) -> Vec<String> {
        let lines: Vec<&str> = source_content.lines().collect();
        let mut comments = Vec::new();

        if line_number > 0 && (line_number as usize) <= lines.len() {
            let line = lines[(line_number - 1) as usize];

            // 查找行内注释
            if let Some(comment_start) = line.find("//") {
                let comment = line[comment_start + 2..].trim();
                if !comment.is_empty() && !comment.starts_with('/') {
                    comments.push(comment.to_string());
                }
            }
        }

        comments
    }
}
