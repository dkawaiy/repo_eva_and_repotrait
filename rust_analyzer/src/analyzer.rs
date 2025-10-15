use crate::cargo_doc_integration::CargoDocIntegrator;
use crate::cargo_integration::CargoAnalyzer;
use crate::cross_crate_analyzer::CrossCrateAnalyzer;
use crate::doc_analyzer::DocAnalyzer;
use crate::doc_output::DocOutputGenerator;
use crate::error::{AnalyzerError, Result};
use crate::function_analyzer::FunctionAnalyzer;
use crate::output::{MethodOutput, OutputGenerator, StructOutput};
use crate::type_analyzer::TypeAnalyzer;
use crate::utils::{find_rust_files, is_test_file};

use log::{debug, info, warn};
use std::collections::HashMap;
use std::path::PathBuf;
use syn::File;

pub struct RustAnalyzer {
    source_path: PathBuf,
    output_path: PathBuf,
    verbose: bool,
    cargo_analyzer: CargoAnalyzer,
    function_analyzer: FunctionAnalyzer,
    type_analyzer: TypeAnalyzer,
    output_generator: OutputGenerator,
}

impl RustAnalyzer {
    pub fn new(source_path: PathBuf, output_path: PathBuf) -> Result<Self> {
        let cargo_analyzer = CargoAnalyzer::new(&source_path)?;
        let function_analyzer = FunctionAnalyzer::new();
        let type_analyzer = TypeAnalyzer::new();
        let output_generator = OutputGenerator::new(output_path.clone());

        Ok(Self {
            source_path,
            output_path,
            verbose: false,
            cargo_analyzer,
            function_analyzer,
            type_analyzer,
            output_generator,
        })
    }

    pub fn set_verbose(&mut self, verbose: bool) {
        self.verbose = verbose;
    }

    pub fn analyze(&mut self) -> Result<()> {
        info!("开始Rust代码分析");

        info!("分析项目元数据");
        let project_metadata = self.cargo_analyzer.analyze()?;
        debug!("workspace中发现 {} 个包", project_metadata.packages.len());

        info!("发现Rust源文件...");
        let rust_files = find_rust_files(&self.source_path);
        info!("发现 {} 个Rust源文件", rust_files.len());

        if rust_files.is_empty() {
            return Err(AnalyzerError::analysis_error(
                "file_discovery",
                "项目中未找到Rust源文件",
            ));
        }

        info!("解析源文件...");
        let mut parsed_files = HashMap::new();
        let mut parse_errors = Vec::new();

        for file_path in &rust_files {
            // 跳过测试文件
            if is_test_file(file_path) {
                debug!("跳过测试文件: {}", file_path.display());
                continue;
            }

            match self.parse_file(file_path) {
                Ok(parsed_file) => {
                    parsed_files.insert(file_path.clone(), parsed_file);
                }
                Err(e) => {
                    warn!("解析失败 {}: {}", file_path.display(), e);
                    parse_errors.push((file_path.clone(), e));
                }
            }
        }

        info!("成功解析 {} 个文件", parsed_files.len());
        if !parse_errors.is_empty() {
            warn!("解析失败 {} 个文件", parse_errors.len());
        }

        info!("分析函数...");
        let mut all_methods = Vec::new();

        for (file_path, parsed_file) in &parsed_files {
            let methods =
                self.function_analyzer
                    .analyze_file(file_path, parsed_file, &self.source_path)?;
            all_methods.extend(methods);
        }

        info!("发现 {} 个函数", all_methods.len());

        info!("分析类型...");
        let mut all_structs = Vec::new();

        for (file_path, parsed_file) in &parsed_files {
            let structs =
                self.type_analyzer
                    .analyze_file(file_path, parsed_file, &self.source_path)?;
            all_structs.extend(structs);
        }

        info!("发现 {} 个类型", all_structs.len());

        info!("解析交叉引用...");
        self.resolve_cross_references(&mut all_methods, &all_structs)?;

        info!("开始跨crate调用关系分析...");
        let mut cross_crate_analyzer = CrossCrateAnalyzer::new(project_metadata.clone());

        let parsed_files_vec: Vec<(PathBuf, syn::File)> = parsed_files.into_iter().collect();
        cross_crate_analyzer.analyze_use_statements(&parsed_files_vec)?;

        // 构建函数调用映射
        let function_calls: HashMap<String, Vec<String>> = all_methods
            .iter()
            .map(|m| (m.signature.clone(), m.callees.clone()))
            .collect();

        let cross_crate_calls = cross_crate_analyzer.resolve_cross_crate_calls(&function_calls)?;
        cross_crate_analyzer.update_method_callees(&mut all_methods, &cross_crate_calls)?;

        info!("开始文档分析...");
        let mut doc_analyzer = DocAnalyzer::new();
        let mut all_docs = HashMap::new();

        for (file_path, parsed_file) in &parsed_files_vec {
            let file_docs = doc_analyzer.analyze_file(file_path, parsed_file, &self.source_path)?;
            all_docs.extend(file_docs);
        }

        info!("生成输出文件...");
        self.output_generator
            .generate_methods_output(&all_methods)?;
        self.output_generator
            .generate_structs_output(&all_structs)?;
        self.output_generator
            .generate_statistics(&all_methods, &all_structs)?;

        info!("开始cargo doc集成...");
        let mut cargo_doc_integrator = CargoDocIntegrator::new(self.source_path.clone());
        let all_docs = if let Err(e) = cargo_doc_integrator.integrate_cargo_doc() {
            warn!("cargo doc集成失败: {e}");
            std::collections::HashMap::new()
        } else {
            info!("cargo doc集成成功");
            // 从cargo doc integrator获取解析的文档信息
            self.extract_doc_info_from_cargo_doc(&cargo_doc_integrator)?
        };

        info!("生成文档输出...");
        let doc_generator = DocOutputGenerator::new(self.output_path.clone());
        doc_generator.generate_documentation(
            &all_methods,
            &all_structs,
            &all_docs,
            &project_metadata,
        )?;

        info!("分析完成");
        Ok(())
    }

    /// 从cargo doc integrator中提取文档信息
    fn extract_doc_info_from_cargo_doc(
        &self,
        cargo_doc_integrator: &CargoDocIntegrator,
    ) -> Result<std::collections::HashMap<String, crate::doc_analyzer::DocInfo>> {
        use crate::doc_analyzer::{DocInfo, DocLocation, DocType};

        let mut doc_map = std::collections::HashMap::new();
        let parsed_docs = cargo_doc_integrator.get_parsed_docs();

        for (module_name, cargo_doc_info) in parsed_docs {
            // 处理函数文档
            for func_doc in &cargo_doc_info.structured_info.functions {
                // 从 rustdoc HTML 中解析源码链接，提取文件名与行号
                let (file, line) = if let Some((file, line)) =
                    cargo_doc_integrator.find_function_source_location(module_name, &func_doc.name)
                {
                    (file, line)
                } else {
                    (module_name.clone(), 1)
                };

                let doc_info = DocInfo {
                    name: func_doc.name.clone(),
                    doc_comment: func_doc.description.clone(),
                    inline_comments: vec![],
                    attributes: vec![],
                    doc_type: DocType::Function,
                    location: DocLocation {
                        file,
                        line,
                        column: 1,
                    },
                };
                doc_map.insert(func_doc.signature.clone(), doc_info);
            }

            // 处理类型文档
            for type_doc in &cargo_doc_info.structured_info.types {
                // 从 rustdoc HTML 中解析源码链接，提取文件名与行号
                let (file, line) = if let Some((file, line)) =
                    cargo_doc_integrator.find_type_source_location(module_name, &type_doc.name)
                {
                    (file, line)
                } else {
                    (module_name.clone(), 1)
                };

                // 粗略根据 kind 设置 DocType
                let doc_type = match type_doc.kind.as_str() {
                    "struct" => DocType::Struct,
                    "enum" => DocType::Enum,
                    "trait" => DocType::Trait,
                    _ => DocType::Struct,
                };

                let doc_info = DocInfo {
                    name: type_doc.name.clone(),
                    doc_comment: type_doc.description.clone(),
                    inline_comments: vec![],
                    attributes: vec![],
                    doc_type: doc_type,
                    location: DocLocation {
                        file,
                        line,
                        column: 1,
                    },
                };
                doc_map.insert(type_doc.name.clone(), doc_info);
            }
        }

        Ok(doc_map)
    }

    /// 解析单个Rust源文件
    fn parse_file(&self, file_path: &PathBuf) -> Result<File> {
        debug!("解析文件: {}", file_path.display());

        let content = std::fs::read_to_string(file_path).map_err(|e| {
            AnalyzerError::analysis_error(
                format!("reading file {}", file_path.display()),
                e.to_string(),
            )
        })?;

        let parsed_file = syn::parse_file(&content).map_err(|e| {
            AnalyzerError::syntax_error(file_path.clone(), format!("Failed to parse syntax: {e}"))
        })?;

        Ok(parsed_file)
    }

    /// 解析函数和类型之间的交叉引用
    fn resolve_cross_references(
        &self,
        methods: &mut [MethodOutput],
        structs: &[StructOutput],
    ) -> Result<()> {
        debug!("解析 {} 个方法的交叉引用", methods.len());

        // 构建查找映射表
        let _struct_map: HashMap<String, &StructOutput> =
            structs.iter().map(|s| (s.name.clone(), s)).collect();

        let method_signatures: Vec<String> = methods.iter().map(|m| m.signature.clone()).collect();
        let method_map: HashMap<String, MethodOutput> = methods
            .iter()
            .map(|m| (m.signature.clone(), m.clone()))
            .collect();

        for (i, method) in methods.iter_mut().enumerate() {
            let signature = &method_signatures[i];
            method.callees = self.resolve_function_calls_cloned(signature, &method_map);
        }

        debug!("交叉引用解析完成");
        Ok(())
    }

    /// 解析特定方法的函数调用
    fn resolve_function_calls_cloned(
        &self,
        method_signature: &str,
        method_map: &HashMap<String, MethodOutput>,
    ) -> Vec<String> {
        if let Some(method) = method_map.get(method_signature) {
            let mut resolved_calls = Vec::new();

            for callee in &method.callees {
                // 精确匹配
                if method_map.contains_key(callee) {
                    resolved_calls.push(callee.clone());
                    continue;
                }

                // 模糊匹配
                let matching_methods: Vec<String> = method_map
                    .keys()
                    .filter(|&key| {
                        key.ends_with(&format!("::{callee}"))
                            || key.contains(&format!("{callee}::"))
                            || key.contains(callee)
                    })
                    .cloned()
                    .collect();

                if matching_methods.len() == 1 {
                    resolved_calls.push(matching_methods[0].clone());
                } else if !matching_methods.is_empty() {
                    // 选择最相似的匹配
                    let best_match = matching_methods.into_iter().min_by_key(|method_name| {
                        self.calculate_similarity_score(callee, method_name)
                    });

                    if let Some(best) = best_match {
                        resolved_calls.push(best);
                    }
                }
            }

            resolved_calls
        } else {
            Vec::new()
        }
    }

    /// 计算字符串相似度分数
    fn calculate_similarity_score(&self, target: &str, candidate: &str) -> usize {
        // 编辑距离算法
        let target_chars: Vec<char> = target.chars().collect();
        let candidate_chars: Vec<char> = candidate.chars().collect();

        let target_len = target_chars.len();
        let candidate_len = candidate_chars.len();

        if target_len == 0 {
            return candidate_len;
        }
        if candidate_len == 0 {
            return target_len;
        }

        let mut matrix = vec![vec![0; candidate_len + 1]; target_len + 1];

        // 初始化矩阵
        for i in 0..=target_len {
            matrix[i][0] = i;
        }
        for j in 0..=candidate_len {
            matrix[0][j] = j;
        }

        // 计算编辑距离
        for i in 1..=target_len {
            for j in 1..=candidate_len {
                let cost = if target_chars[i - 1] == candidate_chars[j - 1] {
                    0
                } else {
                    1
                };
                matrix[i][j] = std::cmp::min(
                    std::cmp::min(
                        matrix[i - 1][j] + 1, // 删除
                        matrix[i][j - 1] + 1, // 插入
                    ),
                    matrix[i - 1][j - 1] + cost, // 替换
                );
            }
        }

        matrix[target_len][candidate_len]
    }
}
