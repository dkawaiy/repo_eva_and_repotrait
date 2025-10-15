use crate::error::{AnalyzerError, Result};
use log::{debug, info, warn};
use scraper::{ElementRef, Html, Selector};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;

pub struct CargoDocIntegrator {
    /// 项目根目录
    project_root: PathBuf,
    /// 生成的文档目录
    doc_output_dir: PathBuf,
    /// 解析的文档信息
    parsed_docs: HashMap<String, CargoDocInfo>,
}

/// Cargo doc生成的文档信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CargoDocInfo {
    /// 模块名称
    pub module_name: String,
    /// 文档HTML内容
    pub html_content: String,
    /// 提取的结构化信息
    pub structured_info: StructuredDocInfo,
}

/// 结构化文档信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StructuredDocInfo {
    /// 模块级文档
    pub module_doc: Option<String>,
    /// 函数文档
    pub functions: Vec<FunctionDocInfo>,
    /// 类型文档
    pub types: Vec<TypeDocInfo>,
    /// 宏文档
    pub macros: Vec<MacroDocInfo>,
    /// 常量文档
    pub constants: Vec<ConstantDocInfo>,
}

/// 函数文档信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionDocInfo {
    pub name: String,
    pub signature: String,
    pub description: Option<String>,
    pub parameters: Vec<ParameterDoc>,
    pub return_info: Option<String>,
    pub examples: Vec<String>,
    pub panics: Vec<String>,
    pub errors: Vec<String>,
    pub safety: Option<String>,
}

/// 参数文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParameterDoc {
    pub name: String,
    pub type_info: String,
    pub description: Option<String>,
}

/// 类型文档信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypeDocInfo {
    pub name: String,
    pub kind: String, // struct, enum, trait, union
    pub description: Option<String>,
    pub fields: Vec<FieldDoc>,
    pub methods: Vec<String>,
    pub implementations: Vec<String>,
    pub examples: Vec<String>,
}

/// 字段文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldDoc {
    pub name: String,
    pub type_info: String,
    pub description: Option<String>,
}

/// 宏文档信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MacroDocInfo {
    pub name: String,
    pub description: Option<String>,
    pub examples: Vec<String>,
}

/// 常量文档信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConstantDocInfo {
    pub name: String,
    pub type_info: String,
    pub value: Option<String>,
    pub description: Option<String>,
}

impl CargoDocIntegrator {
    pub fn new(project_root: PathBuf) -> Self {
        let doc_output_dir = project_root.join("target").join("doc");

        Self {
            project_root,
            doc_output_dir,
            parsed_docs: HashMap::new(),
        }
    }

    /// 生成cargo doc文档
    pub fn generate_cargo_doc(&self) -> Result<()> {
        info!("开始生成cargo doc文档...");

        let mut cmd = Command::new("cargo");
        cmd.arg("doc")
            .arg("--no-deps")
            .arg("--document-private-items")
            .current_dir(&self.project_root);

        debug!("执行命令: {cmd:?}");

        let output = cmd
            .output()
            .map_err(|e| AnalyzerError::analysis_error("执行cargo doc失败", e.to_string()))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(AnalyzerError::analysis_error(
                "cargo doc执行失败",
                stderr.to_string(),
            ));
        }

        let stdout = String::from_utf8_lossy(&output.stdout);
        info!("cargo doc生成成功: {stdout}");

        Ok(())
    }

    /// 解析生成的HTML文档
    pub fn parse_generated_docs(&mut self) -> Result<()> {
        info!("开始解析生成的HTML文档...");

        if !self.doc_output_dir.exists() {
            return Err(AnalyzerError::analysis_error(
                "文档目录不存在",
                format!("路径: {}", self.doc_output_dir.display()),
            ));
        }

        let html_files = self.find_html_files(&self.doc_output_dir)?;
        info!("找到 {} 个HTML文档文件", html_files.len());

        for html_file in html_files {
            match self.parse_html_file(&html_file) {
                Ok(doc_info) => {
                    self.parsed_docs
                        .insert(doc_info.module_name.clone(), doc_info);
                }
                Err(e) => {
                    warn!("解析HTML文件失败 {}: {}", html_file.display(), e);
                }
            }
        }

        info!("HTML文档解析完成，共解析 {} 个模块", self.parsed_docs.len());
        Ok(())
    }

    /// 查找所有HTML文件
    fn find_html_files(&self, dir: &Path) -> Result<Vec<PathBuf>> {
        let mut html_files = Vec::new();

        if dir.is_dir() {
            for entry in std::fs::read_dir(dir)
                .map_err(|e| AnalyzerError::analysis_error("读取目录失败", e.to_string()))?
            {
                let entry = entry
                    .map_err(|e| AnalyzerError::analysis_error("读取目录项失败", e.to_string()))?;

                let path = entry.path();
                if path.is_dir() {
                    html_files.extend(self.find_html_files(&path)?);
                } else if path.extension().and_then(|s| s.to_str()) == Some("html")
                    && let Some(file_name) = path.file_name().and_then(|s| s.to_str())
                    && !file_name.starts_with("index.")
                    && !file_name.starts_with("search")
                    && !file_name.starts_with("help.")
                {
                    html_files.push(path);
                }
            }
        }

        Ok(html_files)
    }

    /// 解析单个HTML文件
    fn parse_html_file(&self, html_file: &Path) -> Result<CargoDocInfo> {
        debug!("解析HTML文件: {}", html_file.display());

        let html_content = std::fs::read_to_string(html_file)
            .map_err(|e| AnalyzerError::analysis_error("读取HTML文件失败", e.to_string()))?;

        let module_name = self.extract_module_name_from_path(html_file)?;
        let structured_info = self.parse_html_content(&html_content)?;

        Ok(CargoDocInfo {
            module_name,
            html_content,
            structured_info,
        })
    }

    fn extract_module_name_from_path(&self, html_file: &Path) -> Result<String> {
        let relative_path = html_file.strip_prefix(&self.doc_output_dir).map_err(|_| {
            AnalyzerError::analysis_error("无法计算相对路径", html_file.display().to_string())
        })?;

        let module_name = relative_path
            .with_extension("")
            .to_string_lossy()
            .replace(['/', '\\'], "::");

        Ok(module_name)
    }

    /// 解析HTML内容提取结构化信息
    fn parse_html_content(&self, html_content: &str) -> Result<StructuredDocInfo> {
        let document = Html::parse_document(html_content);

        let mut structured_info = StructuredDocInfo {
            module_doc: None,
            functions: Vec::new(),
            types: Vec::new(),
            macros: Vec::new(),
            constants: Vec::new(),
        };

        structured_info.module_doc = self.extract_module_doc(&document)?;
        structured_info.functions = self.extract_function_docs(&document)?;
        structured_info.types = self.extract_type_docs(&document)?;
        structured_info.macros = self.extract_macro_docs(&document)?;
        structured_info.constants = self.extract_constant_docs(&document)?;

        Ok(structured_info)
    }

    /// 提取模块级文档
    fn extract_module_doc(&self, document: &Html) -> Result<Option<String>> {
        let docblock_selector = Selector::parse("div.docblock")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        if let Some(docblock) = document.select(&docblock_selector).next() {
            let text = self.extract_text_from_element(&docblock);
            Ok(Some(text))
        } else {
            Ok(None)
        }
    }

    /// 提取函数文档
    fn extract_function_docs(&self, document: &Html) -> Result<Vec<FunctionDocInfo>> {
        let mut functions = Vec::new();

        let fn_selector = Selector::parse("h4 code")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        let link_selector = Selector::parse("a")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        for element in document.select(&fn_selector) {
            let code_text = self.extract_text_from_element(&element);

            if code_text.contains("fn ")
                && let Some(link) = element.select(&link_selector).next()
            {
                let fn_name = self.extract_text_from_element(&link);

                let function_doc = FunctionDocInfo {
                    name: fn_name,
                    signature: code_text,
                    description: self.extract_following_docblock(document, &element)?,
                    parameters: Vec::new(),
                    return_info: None,
                    examples: Vec::new(),
                    panics: Vec::new(),
                    errors: Vec::new(),
                    safety: None,
                };
                functions.push(function_doc);
            }
        }

        Ok(functions)
    }

    /// 提取类型文档
    fn extract_type_docs(&self, document: &Html) -> Result<Vec<TypeDocInfo>> {
        let mut types = Vec::new();

        let struct_selector = Selector::parse("h3 code")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        let link_selector = Selector::parse("a")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        for element in document.select(&struct_selector) {
            let code_text = self.extract_text_from_element(&element);

            if (code_text.contains("struct ")
                || code_text.contains("enum ")
                || code_text.contains("trait "))
                && let Some(link) = element.select(&link_selector).next()
            {
                let type_name = self.extract_text_from_element(&link);
                let kind = if code_text.contains("struct ") {
                    "struct"
                } else if code_text.contains("enum ") {
                    "enum"
                } else if code_text.contains("trait ") {
                    "trait"
                } else {
                    "unknown"
                };

                let type_doc = TypeDocInfo {
                    name: type_name,
                    kind: kind.to_string(),
                    description: self.extract_following_docblock(document, &element)?,
                    fields: Vec::new(),
                    methods: Vec::new(),
                    implementations: Vec::new(),
                    examples: Vec::new(),
                };
                types.push(type_doc);
            }
        }

        Ok(types)
    }

    /// 提取宏文档
    fn extract_macro_docs(&self, document: &Html) -> Result<Vec<MacroDocInfo>> {
        let mut macros = Vec::new();

        let macro_selector = Selector::parse("h4 code")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        let link_selector = Selector::parse("a")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        for element in document.select(&macro_selector) {
            let code_text = self.extract_text_from_element(&element);

            if code_text.contains("macro_rules!")
                && let Some(link) = element.select(&link_selector).next()
            {
                let macro_name = self.extract_text_from_element(&link);

                let macro_doc = MacroDocInfo {
                    name: macro_name,
                    description: self.extract_following_docblock(document, &element)?,
                    examples: Vec::new(),
                };
                macros.push(macro_doc);
            }
        }

        Ok(macros)
    }

    /// 提取常量文档
    fn extract_constant_docs(&self, document: &Html) -> Result<Vec<ConstantDocInfo>> {
        let mut constants = Vec::new();

        let const_selector = Selector::parse("h4 code")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        let link_selector = Selector::parse("a")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        for element in document.select(&const_selector) {
            let code_text = self.extract_text_from_element(&element);

            if (code_text.contains("const ") || code_text.contains("static "))
                && let Some(link) = element.select(&link_selector).next()
            {
                let const_name = self.extract_text_from_element(&link);

                let constant_doc = ConstantDocInfo {
                    name: const_name,
                    type_info: code_text.clone(),
                    value: None,
                    description: self.extract_following_docblock(document, &element)?,
                };
                constants.push(constant_doc);
            }
        }

        Ok(constants)
    }

    /// 从HTML元素中提取纯文本
    fn extract_text_from_element(&self, element: &ElementRef) -> String {
        element
            .text()
            .collect::<Vec<_>>()
            .join(" ")
            .trim()
            .to_string()
    }

    /// 提取元素后面的文档块
    fn extract_following_docblock(
        &self,
        document: &Html,
        _element: &ElementRef,
    ) -> Result<Option<String>> {
        let docblock_selector = Selector::parse("div.docblock")
            .map_err(|e| AnalyzerError::analysis_error("CSS选择器解析失败", format!("{e:?}")))?;

        if let Some(docblock) = document.select(&docblock_selector).next() {
            Ok(Some(self.extract_text_from_element(&docblock)))
        } else {
            Ok(None)
        }
    }

    /// 获取解析的文档信息
    pub fn get_parsed_docs(&self) -> &HashMap<String, CargoDocInfo> {
        &self.parsed_docs
    }

    /// 完整的cargo doc集成流程
    pub fn integrate_cargo_doc(&mut self) -> Result<()> {
        info!("开始完整的cargo doc集成流程...");
        self.generate_cargo_doc()?;
        self.parse_generated_docs()?;
        info!("cargo doc集成完成");
        Ok(())
    }

    /// 从已解析的 HTML 中查找函数的源文件与起始行号（基于 rustdoc 的源码链接）
    pub fn find_function_source_location(
        &self,
        module_name: &str,
        func_name: &str,
    ) -> Option<(String, u32)> {
        let doc = self.parsed_docs.get(module_name)?;
        Self::find_item_source_location_in_html(&doc.html_content, |code_text| {
            code_text.contains("fn ") && code_text.contains(func_name)
        })
    }

    /// 从已解析的 HTML 中查找类型（struct/enum/trait）的源文件与起始行号
    pub fn find_type_source_location(
        &self,
        module_name: &str,
        type_name: &str,
    ) -> Option<(String, u32)> {
        let doc = self.parsed_docs.get(module_name)?;
        Self::find_item_source_location_in_html(&doc.html_content, |code_text| {
            (code_text.contains("struct ")
                || code_text.contains("enum ")
                || code_text.contains("trait "))
                && code_text.contains(type_name)
        })
    }

    /// 在一段 rustdoc HTML 中，依据判定条件找到对应项，并解析其“源码链接”的文件名与起始行号
    fn find_item_source_location_in_html<F>(
        html_content: &str,
        predicate: F,
    ) -> Option<(String, u32)>
    where
        F: Fn(&str) -> bool,
    {
        use regex::Regex;

        let document = Html::parse_document(html_content);
        let h_selector = Selector::parse("h3, h4").ok()?;
        let code_selector = Selector::parse("code").ok()?;
        let a_selector = Selector::parse("a").ok()?;

        // 典型 rustdoc 源码链接形如：
        // href="../src/<crate>/<path>.rs.html#123-150" 或 "../src/<crate>/<path>.rs.html#123"
        let re = Regex::new(r"/src/([^#]+)\.html#(\d+)(?:-\d+)?$").ok()?;

        for header in document.select(&h_selector) {
            // 找到标题中的 code 文本以判定是否为目标项
            if let Some(code) = header.select(&code_selector).next() {
                let code_text = code.text().collect::<Vec<_>>().join(" ").trim().to_string();
                if !predicate(&code_text) {
                    continue;
                }
                // 同一标题或其邻近区域通常包含“源码”链接
                for link in header.select(&a_selector) {
                    if let Some(href) = link.value().attr("href") {
                        if href.contains("/src/") {
                            if let Some(caps) = re.captures(href) {
                                let path_with_rs = caps.get(1)?.as_str(); // e.g. crate/module/file.rs
                                let line: u32 = caps.get(2)?.as_str().parse().ok()?;
                                // 仅返回文件名（保守处理），避免引入仓库外路径
                                let file = path_with_rs
                                    .rsplit('/')
                                    .next()
                                    .unwrap_or(path_with_rs)
                                    .to_string();
                                return Some((file, line));
                            }
                        }
                    }
                }
            }
        }
        None
    }
}
