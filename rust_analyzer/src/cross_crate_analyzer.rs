use crate::cargo_integration::ProjectMetadata;
use crate::error::Result;
use log::{debug, info};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use syn::{File, Item, UseGlob, UseGroup, UsePath, UseRename, UseTree};

/// 跨crate调用关系分析器
pub struct CrossCrateAnalyzer {
    /// 项目元数据
    project_metadata: ProjectMetadata,
    /// crate名称到路径的映射
    crate_paths: HashMap<String, PathBuf>,
    /// use语句映射：文件路径 -> 导入的符号
    use_statements: HashMap<PathBuf, Vec<UseStatement>>,
    /// 符号解析映射：符号名 -> 完整路径
    symbol_resolution: HashMap<String, String>,
}

/// Use语句信息
#[derive(Debug, Clone)]
pub struct UseStatement {
    /// 导入的路径
    pub path: String,
    /// 导入的符号名
    pub symbol: String,
    /// 别名（如果有）
    pub alias: Option<String>,
    /// 是否为glob导入
    pub is_glob: bool,
    /// 源文件
    pub source_file: PathBuf,
}

/// 跨crate调用信息
#[derive(Debug, Clone)]
pub struct CrossCrateCall {
    /// 调用者函数签名
    pub caller: String,
    /// 被调用者函数签名
    pub callee: String,
    /// 调用者所在的crate
    pub caller_crate: String,
    /// 被调用者所在的crate
    pub callee_crate: String,
    /// 调用类型
    pub call_type: CrossCrateCallType,
}

/// 跨crate调用类型
#[derive(Debug, Clone, PartialEq)]
pub enum CrossCrateCallType {
    /// 直接函数调用
    DirectCall,
    /// 方法调用
    MethodCall,
    /// 宏调用
    MacroCall,
    /// trait方法调用
    TraitMethodCall,
}

impl CrossCrateAnalyzer {
    pub fn new(project_metadata: ProjectMetadata) -> Self {
        let mut crate_paths = HashMap::new();

        // 构建crate名称到路径的映射
        for package in &project_metadata.packages {
            if package.is_workspace_member {
                crate_paths.insert(package.name.clone(), package.source_path.clone());
            }
        }

        let mut analyzer = Self {
            project_metadata,
            crate_paths,
            use_statements: HashMap::new(),
            symbol_resolution: HashMap::new(),
        };

        // 初始化时解析Cargo.lock文件获取更多依赖信息
        if let Err(e) = analyzer.parse_cargo_lock() {
            log::warn!("解析Cargo.lock失败: {e}");
        }

        analyzer
    }

    /// 解析Cargo.lock文件获取依赖信息
    fn parse_cargo_lock(&mut self) -> Result<()> {
        let cargo_lock_path = self.project_metadata.workspace_root.join("Cargo.lock");

        if !cargo_lock_path.exists() {
            debug!("Cargo.lock文件不存在: {}", cargo_lock_path.display());
            return Ok(());
        }

        let content = std::fs::read_to_string(&cargo_lock_path).map_err(|e| {
            crate::error::AnalyzerError::analysis_error(
                "Cargo.lock解析",
                format!("读取Cargo.lock失败: {e}"),
            )
        })?;

        // 简单解析Cargo.lock中的依赖信息
        for line in content.lines() {
            if line.starts_with("name = ")
                && let Some(name) = self.extract_quoted_value(line)
            {
                // 尝试解析依赖的源代码路径
                if let Some(source_path) = self.resolve_dependency_source_path(&name) {
                    self.crate_paths.insert(name.clone(), source_path);
                    debug!(
                        "找到外部依赖源代码: {} -> {:?}",
                        name,
                        self.crate_paths.get(&name)
                    );
                }
            }
        }

        info!(
            "Cargo.lock解析完成，共发现 {} 个crate路径",
            self.crate_paths.len()
        );
        Ok(())
    }

    /// 从引号中提取值
    fn extract_quoted_value(&self, line: &str) -> Option<String> {
        if let Some(start) = line.find('"')
            && let Some(end) = line[start + 1..].find('"')
        {
            return Some(line[start + 1..start + 1 + end].to_string());
        }
        None
    }

    /// 解析依赖的源代码路径
    fn resolve_dependency_source_path(&self, name: &str) -> Option<PathBuf> {
        // 1. 检查本地路径依赖
        if let Some(local_path) = self.check_local_dependency(name) {
            return Some(local_path);
        }

        // 2. 检查cargo registry缓存
        if let Some(registry_path) = self.check_registry_dependency(name) {
            return Some(registry_path);
        }

        None
    }

    /// 检查本地路径依赖
    fn check_local_dependency(&self, name: &str) -> Option<PathBuf> {
        // 检查workspace成员
        for package in &self.project_metadata.packages {
            if package.name == name && package.is_workspace_member {
                return Some(package.source_path.clone());
            }
        }

        // 检查相对路径依赖
        let potential_paths = vec![
            self.project_metadata.workspace_root.join(name),
            self.project_metadata.workspace_root.join("..").join(name),
            self.project_metadata.workspace_root.join("deps").join(name),
        ];

        for path in potential_paths {
            if path.exists() && path.join("Cargo.toml").exists() {
                return Some(path);
            }
        }

        None
    }

    /// 检查registry依赖（crates.io缓存）
    fn check_registry_dependency(&self, name: &str) -> Option<PathBuf> {
        if let Some(home_dir) = dirs::home_dir() {
            let registry_cache = home_dir.join(".cargo").join("registry").join("src");

            // 检查不同的registry源
            let registry_sources = vec![
                "index.crates.io-6f17d22bba15001f",
                "github.com-1ecc6299db9ec823",
            ];

            for source in registry_sources {
                let source_path = registry_cache.join(source);
                if source_path.exists() {
                    // 查找匹配的crate（可能有多个版本）
                    if let Ok(entries) = std::fs::read_dir(&source_path) {
                        for entry in entries.flatten() {
                            let entry_name = entry.file_name().to_string_lossy().to_string();
                            if entry_name.starts_with(&format!("{name}-")) {
                                let crate_path = entry.path();
                                if crate_path.is_dir() && crate_path.join("Cargo.toml").exists() {
                                    return Some(crate_path);
                                }
                            }
                        }
                    }
                }
            }
        }
        None
    }

    /// 分析所有文件的use语句
    pub fn analyze_use_statements(&mut self, files: &[(PathBuf, File)]) -> Result<()> {
        info!("开始分析use语句和导入关系");

        for (file_path, parsed_file) in files {
            let mut file_uses = Vec::new();

            for item in &parsed_file.items {
                if let Item::Use(item_use) = item {
                    let use_statements = self.extract_use_statements(&item_use.tree, file_path)?;
                    file_uses.extend(use_statements);
                }
            }

            if !file_uses.is_empty() {
                debug!(
                    "在文件 {} 中发现 {} 个use语句",
                    file_path.display(),
                    file_uses.len()
                );
                self.use_statements.insert(file_path.clone(), file_uses);
            }
        }

        // 构建符号解析映射
        self.build_symbol_resolution()?;

        info!(
            "use语句分析完成，共处理 {} 个文件",
            self.use_statements.len()
        );
        Ok(())
    }

    /// 从use树中提取use语句
    fn extract_use_statements(
        &self,
        use_tree: &UseTree,
        file_path: &PathBuf,
    ) -> Result<Vec<UseStatement>> {
        let mut statements = Vec::new();
        self.extract_use_tree_recursive(use_tree, String::new(), file_path, &mut statements)?;
        Ok(statements)
    }

    /// 递归提取use树
    fn extract_use_tree_recursive(
        &self,
        use_tree: &UseTree,
        current_path: String,
        file_path: &PathBuf,
        statements: &mut Vec<UseStatement>,
    ) -> Result<()> {
        match use_tree {
            UseTree::Path(UsePath { ident, tree, .. }) => {
                let new_path = if current_path.is_empty() {
                    ident.to_string()
                } else {
                    format!("{current_path}::{ident}")
                };
                self.extract_use_tree_recursive(tree, new_path, file_path, statements)?;
            }
            UseTree::Name(name) => {
                let _full_path = if current_path.is_empty() {
                    name.ident.to_string()
                } else {
                    format!("{}::{}", current_path, name.ident)
                };
                statements.push(UseStatement {
                    path: current_path.clone(),
                    symbol: name.ident.to_string(),
                    alias: None,
                    is_glob: false,
                    source_file: file_path.clone(),
                });
            }
            UseTree::Rename(UseRename { ident, rename, .. }) => {
                let _full_path = if current_path.is_empty() {
                    ident.to_string()
                } else {
                    format!("{current_path}::{ident}")
                };
                statements.push(UseStatement {
                    path: current_path.clone(),
                    symbol: ident.to_string(),
                    alias: Some(rename.to_string()),
                    is_glob: false,
                    source_file: file_path.clone(),
                });
            }
            UseTree::Glob(UseGlob { .. }) => {
                statements.push(UseStatement {
                    path: current_path.clone(),
                    symbol: "*".to_string(),
                    alias: None,
                    is_glob: true,
                    source_file: file_path.clone(),
                });
            }
            UseTree::Group(UseGroup { items, .. }) => {
                for item in items {
                    self.extract_use_tree_recursive(
                        item,
                        current_path.clone(),
                        file_path,
                        statements,
                    )?;
                }
            }
        }
        Ok(())
    }

    /// 构建符号解析映射
    fn build_symbol_resolution(&mut self) -> Result<()> {
        info!("构建符号解析映射");

        for use_statements in self.use_statements.values() {
            for use_stmt in use_statements {
                if use_stmt.is_glob {
                    continue; // 暂时跳过glob导入的处理
                }

                let symbol_name = use_stmt.alias.as_ref().unwrap_or(&use_stmt.symbol);
                let full_path = if use_stmt.path.is_empty() {
                    use_stmt.symbol.clone()
                } else {
                    format!("{}::{}", use_stmt.path, use_stmt.symbol)
                };

                // 解析到具体的crate
                let resolved_path = self.resolve_symbol_to_crate(&full_path)?;
                self.symbol_resolution
                    .insert(symbol_name.clone(), resolved_path);
            }
        }

        debug!(
            "符号解析映射构建完成，共 {} 个符号",
            self.symbol_resolution.len()
        );
        Ok(())
    }

    /// 将符号解析到具体的crate
    fn resolve_symbol_to_crate(&self, symbol_path: &str) -> Result<String> {
        // 检查是否为标准库
        if symbol_path.starts_with("std::")
            || symbol_path.starts_with("core::")
            || symbol_path.starts_with("alloc::")
        {
            return Ok(symbol_path.to_string());
        }

        // 检查是否为当前workspace的crate
        for crate_name in self.crate_paths.keys() {
            if symbol_path.starts_with(&format!("{crate_name}::")) || symbol_path == *crate_name {
                return Ok(symbol_path.to_string());
            }
        }

        // 检查是否为外部依赖
        for package in &self.project_metadata.packages {
            if symbol_path.starts_with(&format!("{}::", package.name))
                || symbol_path == package.name
            {
                return Ok(symbol_path.to_string());
            }
        }

        // 如果无法解析，返回原始路径
        Ok(symbol_path.to_string())
    }

    /// 解析跨crate调用关系
    pub fn resolve_cross_crate_calls(
        &self,
        function_calls: &HashMap<String, Vec<String>>,
    ) -> Result<Vec<CrossCrateCall>> {
        info!("开始解析跨crate调用关系");
        let mut cross_crate_calls = Vec::new();

        for (caller_signature, callees) in function_calls {
            let caller_crate = self.extract_crate_from_signature(caller_signature)?;

            for callee in callees {
                let callee_crate = self.extract_crate_from_signature(callee)?;

                // 只记录跨crate的调用
                if caller_crate != callee_crate {
                    let call_type = self.determine_call_type(callee);

                    cross_crate_calls.push(CrossCrateCall {
                        caller: caller_signature.clone(),
                        callee: callee.clone(),
                        caller_crate: caller_crate.clone(),
                        callee_crate,
                        call_type,
                    });
                }
            }
        }

        info!(
            "跨crate调用关系解析完成，发现 {} 个跨crate调用",
            cross_crate_calls.len()
        );
        Ok(cross_crate_calls)
    }

    /// 从函数签名中提取crate名称
    fn extract_crate_from_signature(&self, signature: &str) -> Result<String> {
        // 从签名中提取模块路径
        if let Some(first_part) = signature.split("::").next() {
            // 检查是否为已知的crate
            if self.crate_paths.contains_key(first_part) {
                return Ok(first_part.to_string());
            }

            // 检查是否为外部依赖
            for package in &self.project_metadata.packages {
                if package.name == first_part {
                    return Ok(first_part.to_string());
                }
            }
        }

        // 默认返回当前项目的主crate
        Ok(self.project_metadata.name.clone())
    }

    /// 确定调用类型
    fn determine_call_type(&self, callee: &str) -> CrossCrateCallType {
        if callee.ends_with('!') {
            CrossCrateCallType::MacroCall
        } else if callee.contains("::") && callee.contains('<') {
            CrossCrateCallType::TraitMethodCall
        } else if callee.contains("::") {
            CrossCrateCallType::DirectCall
        } else {
            CrossCrateCallType::MethodCall
        }
    }

    /// 获取所有跨crate调用的统计信息
    pub fn get_cross_crate_statistics(
        &self,
        cross_crate_calls: &[CrossCrateCall],
    ) -> HashMap<String, usize> {
        let mut stats = HashMap::new();

        for call in cross_crate_calls {
            let key = format!("{} -> {}", call.caller_crate, call.callee_crate);
            *stats.entry(key).or_insert(0) += 1;
        }

        stats
    }

    /// 更新方法输出中的跨crate调用信息
    pub fn update_method_callees(
        &self,
        methods: &mut [crate::output::MethodOutput],
        cross_crate_calls: &[CrossCrateCall],
    ) -> Result<()> {
        info!("更新方法输出中的跨crate调用信息");

        // 构建调用者到被调用者的映射
        let mut caller_to_callees: HashMap<String, Vec<String>> = HashMap::new();
        for call in cross_crate_calls {
            caller_to_callees
                .entry(call.caller.clone())
                .or_default()
                .push(call.callee.clone());
        }

        // 更新每个方法的callees
        for method in methods {
            if let Some(cross_crate_callees) = caller_to_callees.get(&method.signature) {
                // 合并现有的callees和跨crate的callees
                let mut all_callees: HashSet<String> = method.callees.iter().cloned().collect();
                all_callees.extend(cross_crate_callees.iter().cloned());
                method.callees = all_callees.into_iter().collect();
            }
        }

        info!("跨crate调用信息更新完成");
        Ok(())
    }
}
