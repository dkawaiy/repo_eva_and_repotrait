use crate::doc_analyzer::DocInfo;
use crate::output::{MethodOutput, StructOutput};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

/// 文档整合输出结构，专为LLM文档生成设计
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DocumentationOutput {
    /// 项目元数据
    pub project: ProjectDocumentation,
    /// 模块文档
    pub modules: Vec<ModuleDocumentation>,
    /// 函数文档
    pub functions: Vec<FunctionDocumentation>,
    /// 类型文档
    pub types: Vec<TypeDocumentation>,
    /// 宏文档
    pub macros: Vec<MacroDocumentation>,
    /// 常量文档
    pub constants: Vec<ConstantDocumentation>,
}

/// 项目级文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectDocumentation {
    /// 项目名称
    pub name: String,
    /// 项目版本
    pub version: String,
    /// 项目描述
    pub description: Option<String>,
    /// 作者信息
    pub authors: Vec<String>,
    /// 许可证
    pub license: Option<String>,
    /// 仓库地址
    pub repository: Option<String>,
    /// README内容
    pub readme: Option<String>,
    /// workspace信息
    pub workspace_info: Option<WorkspaceDocumentation>,
}

/// workspace文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceDocumentation {
    /// workspace成员
    pub members: Vec<String>,
    /// 成员包文档
    pub member_docs: HashMap<String, PackageDocumentation>,
}

/// 包文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PackageDocumentation {
    /// 包名
    pub name: String,
    /// 包描述
    pub description: Option<String>,
    /// 包版本
    pub version: String,
    /// 主要功能
    pub main_features: Vec<String>,
}

/// 模块文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModuleDocumentation {
    /// 模块路径
    pub path: String,
    /// 模块名称
    pub name: String,
    /// 模块文档注释
    pub doc_comment: Option<String>,
    /// 模块中的公共项目
    pub public_items: Vec<String>,
    /// 子模块
    pub submodules: Vec<String>,
    /// 使用示例
    pub examples: Vec<String>,
}

/// 函数文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionDocumentation {
    /// 函数签名
    pub signature: String,
    /// 函数名称
    pub name: String,
    /// 文档注释
    pub doc_comment: Option<String>,
    /// 参数文档
    pub parameters: Vec<ParameterDocumentation>,
    /// 返回值文档
    pub return_doc: Option<String>,
    /// 使用示例
    pub examples: Vec<String>,
    /// 错误处理
    pub errors: Vec<String>,
    /// 相关函数
    pub related_functions: Vec<String>,
    /// 源代码位置
    pub location: LocationInfo,
}

/// 参数文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParameterDocumentation {
    /// 参数名
    pub name: String,
    /// 参数类型
    pub param_type: String,
    /// 参数描述
    pub description: Option<String>,
}

/// 类型文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypeDocumentation {
    /// 类型名称
    pub name: String,
    /// 完整类型名
    pub fullname: String,
    /// 类型种类
    pub type_kind: String, // struct, enum, trait, union
    /// 文档注释
    pub doc_comment: Option<String>,
    /// 字段文档
    pub fields: Vec<FieldDocumentation>,
    /// 方法文档
    pub methods: Vec<String>, // 引用到函数文档
    /// 实现的trait
    pub implemented_traits: Vec<String>,
    /// 使用示例
    pub examples: Vec<String>,
    /// 源代码位置
    pub location: LocationInfo,
}

/// 字段文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldDocumentation {
    /// 字段名
    pub name: String,
    /// 字段类型
    pub field_type: String,
    /// 字段描述
    pub description: Option<String>,
    /// 可见性
    pub visibility: String,
}

/// 宏文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MacroDocumentation {
    /// 宏名称
    pub name: String,
    /// 宏类型
    pub macro_type: String, // declarative, procedural, derive
    /// 文档注释
    pub doc_comment: Option<String>,
    /// 使用示例
    pub examples: Vec<String>,
    /// 源代码位置
    pub location: LocationInfo,
}

/// 常量文档
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConstantDocumentation {
    /// 常量名
    pub name: String,
    /// 常量类型
    pub const_type: String,
    /// 常量值
    pub value: Option<String>,
    /// 文档注释
    pub doc_comment: Option<String>,
    /// 源代码位置
    pub location: LocationInfo,
}

/// 位置信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocationInfo {
    /// 文件名
    pub file: String,
    /// 开始行号
    pub start_line: u32,
    /// 结束行号
    pub end_line: u32,
}

/// 文档输出生成器
pub struct DocOutputGenerator {
    output_path: PathBuf,
}

impl DocOutputGenerator {
    pub fn new(output_path: PathBuf) -> Self {
        Self { output_path }
    }

    /// 生成完整的文档输出
    pub fn generate_documentation(
        &self,
        methods: &[MethodOutput],
        structs: &[StructOutput],
        docs: &HashMap<String, DocInfo>,
        project_info: &crate::cargo_integration::ProjectMetadata,
    ) -> crate::error::Result<()> {
        let documentation =
            self.build_documentation_output(methods, structs, docs, project_info)?;

        // 生成documentation.json文件
        let doc_file = self.output_path.join("documentation.json");
        let json_content = serde_json::to_string_pretty(&documentation).map_err(|e| {
            crate::error::AnalyzerError::output_error(format!("JSON序列化失败: {e}"))
        })?;

        std::fs::write(&doc_file, json_content).map_err(|e| {
            crate::error::AnalyzerError::output_error(format!(
                "写入文档文件失败: {}: {}",
                doc_file.display(),
                e
            ))
        })?;

        Ok(())
    }

    /// 构建文档输出结构
    fn build_documentation_output(
        &self,
        methods: &[MethodOutput],
        structs: &[StructOutput],
        docs: &HashMap<String, DocInfo>,
        project_info: &crate::cargo_integration::ProjectMetadata,
    ) -> crate::error::Result<DocumentationOutput> {
        // 构建项目文档
        let project = ProjectDocumentation {
            name: project_info.name.clone(),
            version: project_info.version.clone(),
            description: project_info.description.clone(),
            authors: project_info.authors.clone(),
            license: project_info.license.clone(),
            repository: project_info.repository.clone(),
            readme: self.read_readme_file(&project_info.workspace_root)?,
            workspace_info: if project_info.is_workspace {
                Some(self.build_workspace_documentation(project_info)?)
            } else {
                None
            },
        };

        // 构建函数文档
        let functions = self.build_function_documentation(methods, docs)?;

        // 构建类型文档
        let types = self.build_type_documentation(structs, docs)?;

        // 构建模块文档
        let modules = self.build_module_documentation(methods, structs, docs)?;

        // 构建宏文档
        let macros = self.build_macro_documentation(methods, docs)?;

        // 构建常量文档
        let constants = self.build_constant_documentation(methods, docs)?;

        Ok(DocumentationOutput {
            project,
            modules,
            functions,
            types,
            macros,
            constants,
        })
    }

    /// 构建workspace文档
    fn build_workspace_documentation(
        &self,
        project_info: &crate::cargo_integration::ProjectMetadata,
    ) -> crate::error::Result<WorkspaceDocumentation> {
        let members: Vec<String> = project_info
            .packages
            .iter()
            .filter(|p| p.is_workspace_member)
            .map(|p| p.name.clone())
            .collect();

        let member_docs: HashMap<String, PackageDocumentation> = project_info
            .packages
            .iter()
            .filter(|p| p.is_workspace_member)
            .map(|p| {
                (
                    p.name.clone(),
                    PackageDocumentation {
                        name: p.name.clone(),
                        description: self.extract_package_description(p),
                        version: p.version.clone(),
                        main_features: self.analyze_main_features(p),
                    },
                )
            })
            .collect();

        Ok(WorkspaceDocumentation {
            members,
            member_docs,
        })
    }

    /// 构建函数文档
    fn build_function_documentation(
        &self,
        methods: &[MethodOutput],
        docs: &HashMap<String, DocInfo>,
    ) -> crate::error::Result<Vec<FunctionDocumentation>> {
        let mut function_docs = Vec::new();

        for method in methods {
            let doc_info = docs.get(&method.signature);

            let parameters: Vec<ParameterDocumentation> = method
                .params
                .iter()
                .map(|p| ParameterDocumentation {
                    name: p.name.clone(),
                    param_type: p.param_type.clone(),
                    description: self.extract_parameter_description(
                        &doc_info.and_then(|d| d.doc_comment.clone()),
                        &p.name,
                    ),
                })
                .collect();

            function_docs.push(FunctionDocumentation {
                signature: method.signature.clone(),
                name: method.name.clone(),
                doc_comment: doc_info.and_then(|d| d.doc_comment.clone()),
                parameters,
                return_doc: self
                    .extract_return_description(&doc_info.and_then(|d| d.doc_comment.clone())),
                examples: self.extract_examples_from_doc_comment(
                    &doc_info.and_then(|d| d.doc_comment.as_ref()),
                ),
                errors: self.extract_error_info(&doc_info.and_then(|d| d.doc_comment.clone())),
                related_functions: method.callees.clone(),
                location: LocationInfo {
                    file: method.filename.clone(),
                    start_line: method.begin_line,
                    end_line: method.end_line,
                },
            });
        }

        Ok(function_docs)
    }

    /// 构建类型文档
    fn build_type_documentation(
        &self,
        structs: &[StructOutput],
        docs: &HashMap<String, DocInfo>,
    ) -> crate::error::Result<Vec<TypeDocumentation>> {
        let mut type_docs = Vec::new();

        for struct_def in structs {
            let doc_info = docs.get(&struct_def.fullname);

            let fields: Vec<FieldDocumentation> = struct_def
                .attributes
                .iter()
                .map(|attr| FieldDocumentation {
                    name: attr.name.clone(),
                    field_type: attr.attr_type.clone(),
                    description: self.extract_field_description(
                        &doc_info.and_then(|d| d.doc_comment.clone()),
                        &attr.name,
                    ),
                    visibility: attr.modifier.clone(),
                })
                .collect();

            type_docs.push(TypeDocumentation {
                name: struct_def.name.clone(),
                fullname: struct_def.fullname.clone(),
                type_kind: self.determine_type_kind(struct_def),
                doc_comment: doc_info.and_then(|d| d.doc_comment.clone()),
                fields,
                methods: struct_def.methods.clone(),
                implemented_traits: self.extract_implemented_traits(struct_def),
                examples: self.extract_examples_from_doc_comment(
                    &doc_info.and_then(|d| d.doc_comment.as_ref()),
                ),
                location: LocationInfo {
                    file: struct_def.filename.clone(),
                    start_line: struct_def.begin_line,
                    end_line: self.extract_end_line(struct_def),
                },
            });
        }

        Ok(type_docs)
    }

    /// 读取README文件
    fn read_readme_file(
        &self,
        workspace_root: &std::path::Path,
    ) -> crate::error::Result<Option<String>> {
        let readme_files = ["README.md", "README.rst", "README.txt", "README"];

        for readme_name in &readme_files {
            let readme_path = workspace_root.join(readme_name);
            if readme_path.exists() {
                match std::fs::read_to_string(&readme_path) {
                    Ok(content) => {
                        log::debug!("成功读取README文件: {}", readme_path.display());
                        return Ok(Some(content));
                    }
                    Err(e) => {
                        log::warn!("读取README文件失败 {}: {}", readme_path.display(), e);
                    }
                }
            }
        }

        Ok(None)
    }

    /// 构建模块文档
    fn build_module_documentation(
        &self,
        methods: &[crate::output::MethodOutput],
        structs: &[crate::output::StructOutput],
        _docs: &std::collections::HashMap<String, crate::doc_analyzer::DocInfo>,
    ) -> crate::error::Result<Vec<ModuleDocumentation>> {
        let mut module_map: std::collections::HashMap<String, ModuleDocumentation> =
            std::collections::HashMap::new();

        // 从函数中提取模块信息
        for method in methods {
            let module_path = self.extract_module_from_filename(&method.filename);
            let entry =
                module_map
                    .entry(module_path.clone())
                    .or_insert_with(|| ModuleDocumentation {
                        path: module_path.clone(),
                        name: self.extract_module_name(&module_path),
                        doc_comment: None,
                        public_items: Vec::new(),
                        submodules: Vec::new(),
                        examples: Vec::new(),
                    });

            if method.modifier.contains("PUBLIC") {
                entry.public_items.push(method.name.clone());
            }
        }

        // 从类型中提取模块信息
        for struct_def in structs {
            let module_path = self.extract_module_from_fullname(&struct_def.fullname);
            let entry =
                module_map
                    .entry(module_path.clone())
                    .or_insert_with(|| ModuleDocumentation {
                        path: module_path.clone(),
                        name: self.extract_module_name(&module_path),
                        doc_comment: None,
                        public_items: Vec::new(),
                        submodules: Vec::new(),
                        examples: Vec::new(),
                    });

            entry.public_items.push(struct_def.name.clone());
        }

        // 构建子模块关系
        let module_paths: Vec<String> = module_map.keys().cloned().collect();
        for module_path in module_paths {
            let parts: Vec<&str> = module_path.split("::").collect();
            if parts.len() > 1 {
                let parent_path = parts[..parts.len() - 1].join("::");
                let child_name = parts.last().unwrap().to_string();
                if let Some(parent_module) = module_map.get_mut(&parent_path) {
                    parent_module.submodules.push(child_name);
                }
            }
        }

        Ok(module_map.into_values().collect())
    }

    /// 从文件名中提取模块路径
    fn extract_module_from_filename(&self, filename: &str) -> String {
        use std::path::Path;

        let path = Path::new(filename);

        // 移除文件扩展名
        let stem = path.with_extension("");

        // 将路径转换为模块路径格式
        let mut module_parts = Vec::new();

        for component in stem.components() {
            match component {
                std::path::Component::Normal(name) => {
                    if let Some(name_str) = name.to_str() {
                        // 跳过一些不需要的目录
                        if name_str != "src" && name_str != "resource" {
                            module_parts.push(name_str.to_string());
                        }
                    }
                }
                _ => {}
            }
        }

        if module_parts.is_empty() {
            "root".to_string()
        } else {
            module_parts.join("::")
        }
    }

    /// 从完整名称中提取模块路径
    fn extract_module_from_fullname(&self, fullname: &str) -> String {
        if let Some(pos) = fullname.rfind("::") {
            fullname[..pos].to_string()
        } else {
            "root".to_string()
        }
    }

    /// 提取模块名称
    fn extract_module_name(&self, module_path: &str) -> String {
        if let Some(pos) = module_path.rfind("::") {
            module_path[pos + 2..].to_string()
        } else {
            module_path.to_string()
        }
    }

    /// 构建宏文档
    fn build_macro_documentation(
        &self,
        methods: &[crate::output::MethodOutput],
        docs: &std::collections::HashMap<String, crate::doc_analyzer::DocInfo>,
    ) -> crate::error::Result<Vec<MacroDocumentation>> {
        let mut macro_docs = Vec::new();

        for method in methods {
            if method.modifier.contains("MACRO") {
                let doc_info = docs.get(&method.signature);

                let macro_type = if method.name.ends_with("_derive") {
                    "derive".to_string()
                } else if method.signature.contains("proc_macro") {
                    "procedural".to_string()
                } else {
                    "declarative".to_string()
                };

                macro_docs.push(MacroDocumentation {
                    name: method.name.clone(),
                    macro_type,
                    doc_comment: doc_info.and_then(|d| d.doc_comment.clone()),
                    examples: self.extract_examples_from_doc_comment(
                        &doc_info.and_then(|d| d.doc_comment.as_ref()),
                    ),
                    location: LocationInfo {
                        file: method.filename.clone(),
                        start_line: method.begin_line,
                        end_line: method.end_line,
                    },
                });
            }
        }

        Ok(macro_docs)
    }

    /// 构建常量文档
    fn build_constant_documentation(
        &self,
        methods: &[crate::output::MethodOutput],
        docs: &std::collections::HashMap<String, crate::doc_analyzer::DocInfo>,
    ) -> crate::error::Result<Vec<ConstantDocumentation>> {
        let mut constant_docs = Vec::new();

        for method in methods {
            if method.modifier.contains("CONST") || method.modifier.contains("STATIC") {
                let doc_info = docs.get(&method.signature);

                // 从签名中提取值
                let value = self.extract_value_from_signature(&method.signature);

                constant_docs.push(ConstantDocumentation {
                    name: method.name.clone(),
                    const_type: method.return_type.clone(),
                    value,
                    doc_comment: doc_info.and_then(|d| d.doc_comment.clone()),
                    location: LocationInfo {
                        file: method.filename.clone(),
                        start_line: method.begin_line,
                        end_line: method.end_line,
                    },
                });
            }
        }

        Ok(constant_docs)
    }

    /// 从文档注释中提取示例代码
    fn extract_examples_from_doc_comment(&self, doc_comment: &Option<&String>) -> Vec<String> {
        let mut examples = Vec::new();

        if let Some(comment) = doc_comment {
            let lines: Vec<&str> = comment.lines().collect();
            let mut in_example = false;
            let mut current_example = Vec::new();

            for line in lines {
                let trimmed = line.trim();
                if trimmed.starts_with("```") {
                    if in_example {
                        // 结束示例
                        if !current_example.is_empty() {
                            examples.push(current_example.join("\n"));
                            current_example.clear();
                        }
                        in_example = false;
                    } else {
                        // 开始示例
                        in_example = true;
                    }
                } else if in_example {
                    current_example.push(line.to_string());
                }
            }
        }

        examples
    }

    /// 从签名中提取常量值
    fn extract_value_from_signature(&self, signature: &str) -> Option<String> {
        // 简化实现：尝试从签名中提取 = 后面的值
        if let Some(pos) = signature.find(" = ") {
            let value_part = &signature[pos + 3..];
            if let Some(end_pos) = value_part.find(';') {
                Some(value_part[..end_pos].trim().to_string())
            } else {
                Some(value_part.trim().to_string())
            }
        } else {
            None
        }
    }

    /// 从包的Cargo.toml提取描述
    fn extract_package_description(
        &self,
        package: &crate::cargo_integration::PackageInfo,
    ) -> Option<String> {
        // 读取包的Cargo.toml文件
        let cargo_toml_path = package.manifest_path.clone();

        match std::fs::read_to_string(&cargo_toml_path) {
            Ok(content) => match toml::from_str::<toml::Value>(&content) {
                Ok(toml_value) => toml_value
                    .get("package")
                    .and_then(|p| p.get("description"))
                    .and_then(|d| d.as_str())
                    .map(|s| s.to_string()),
                Err(_) => None,
            },
            Err(_) => None,
        }
    }

    /// 分析包的主要功能
    fn analyze_main_features(
        &self,
        package: &crate::cargo_integration::PackageInfo,
    ) -> Vec<String> {
        let mut features = Vec::new();

        // 从包的特性中提取
        for feature_name in package.features.keys() {
            if feature_name != "default" {
                features.push(feature_name.clone());
            }
        }

        // 从构建目标中推断功能
        for target in &package.targets {
            if target.kind.contains(&"bin".to_string()) {
                features.push(format!("可执行程序: {}", target.name));
            } else if target.kind.contains(&"lib".to_string()) {
                features.push("库".to_string());
            }
        }

        features
    }

    /// 从文档注释中提取参数描述
    fn extract_parameter_description(
        &self,
        doc_comment: &Option<String>,
        param_name: &str,
    ) -> Option<String> {
        if let Some(comment) = doc_comment {
            let lines: Vec<&str> = comment.lines().collect();

            for (i, line) in lines.iter().enumerate() {
                let trimmed = line.trim();
                if trimmed.starts_with("# Parameters") || trimmed.starts_with("## Parameters") {
                    // 查找参数描述
                    for j in (i + 1)..lines.len() {
                        let param_line = lines[j].trim();
                        if (param_line.starts_with(&format!("- {param_name}"))
                            || param_line.starts_with(&format!("* {param_name}")))
                            && let Some(desc_start) = param_line.find(':')
                        {
                            return Some(param_line[desc_start + 1..].trim().to_string());
                        }
                        if param_line.starts_with("# ") || param_line.starts_with("## ") {
                            break;
                        }
                    }
                }
            }
        }
        None
    }

    /// 从文档注释中提取返回值描述
    fn extract_return_description(&self, doc_comment: &Option<String>) -> Option<String> {
        if let Some(comment) = doc_comment {
            let lines: Vec<&str> = comment.lines().collect();

            for (i, line) in lines.iter().enumerate() {
                let trimmed = line.trim();
                if (trimmed.starts_with("# Returns") || trimmed.starts_with("## Returns"))
                    && i + 1 < lines.len()
                {
                    return Some(lines[i + 1].trim().to_string());
                }
            }
        }
        None
    }

    /// 从文档注释中提取错误信息
    fn extract_error_info(&self, doc_comment: &Option<String>) -> Vec<String> {
        let mut errors = Vec::new();

        if let Some(comment) = doc_comment {
            let lines: Vec<&str> = comment.lines().collect();

            for (i, line) in lines.iter().enumerate() {
                let trimmed = line.trim();
                if trimmed.starts_with("# Errors") || trimmed.starts_with("## Errors") {
                    for j in (i + 1)..lines.len() {
                        let error_line = lines[j].trim();
                        if error_line.starts_with("- ") || error_line.starts_with("* ") {
                            errors.push(error_line[2..].to_string());
                        } else if error_line.starts_with("# ") || error_line.starts_with("## ") {
                            break;
                        }
                    }
                }
            }
        }

        errors
    }

    /// 从文档注释中提取字段描述
    fn extract_field_description(
        &self,
        doc_comment: &Option<String>,
        field_name: &str,
    ) -> Option<String> {
        if let Some(comment) = doc_comment {
            let lines: Vec<&str> = comment.lines().collect();

            for (i, line) in lines.iter().enumerate() {
                let trimmed = line.trim();
                if trimmed.starts_with("# Fields") || trimmed.starts_with("## Fields") {
                    for j in (i + 1)..lines.len() {
                        let field_line = lines[j].trim();
                        if (field_line.starts_with(&format!("- {field_name}"))
                            || field_line.starts_with(&format!("* {field_name}")))
                            && let Some(desc_start) = field_line.find(':')
                        {
                            return Some(field_line[desc_start + 1..].trim().to_string());
                        }
                        if field_line.starts_with("# ") || field_line.starts_with("## ") {
                            break;
                        }
                    }
                }
            }
        }
        None
    }

    /// 确定类型种类
    fn determine_type_kind(&self, struct_def: &crate::output::StructOutput) -> String {
        // 从fullname或其他信息推断类型
        if struct_def.fullname.contains("enum") {
            "enum".to_string()
        } else if struct_def.fullname.contains("trait") {
            "trait".to_string()
        } else if struct_def.fullname.contains("union") {
            "union".to_string()
        } else {
            "struct".to_string()
        }
    }

    /// 提取实现的trait
    fn extract_implemented_traits(&self, _struct_def: &crate::output::StructOutput) -> Vec<String> {
        // 需要分析impl块来确定实现的trait
        Vec::new()
    }

    /// 提取结束行号
    fn extract_end_line(&self, struct_def: &crate::output::StructOutput) -> u32 {
        // 简化实现：假设结构体至少占用3行
        struct_def.begin_line + 2
    }
}
