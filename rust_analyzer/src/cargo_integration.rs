use crate::error::{AnalyzerError, Result};

use cargo_metadata::{DependencyKind, Metadata, MetadataCommand, Package};
use log::{debug, info};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// Cargo项目元数据信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectMetadata {
    /// 项目名称
    pub name: String,
    /// 项目版本
    pub version: String,
    /// 项目描述
    pub description: Option<String>,
    /// 项目作者
    pub authors: Vec<String>,
    /// 项目许可证
    pub license: Option<String>,
    /// 项目主页
    pub homepage: Option<String>,
    /// 项目仓库
    pub repository: Option<String>,
    /// workspace中的所有包
    pub packages: Vec<PackageInfo>,
    /// 依赖关系图
    pub dependencies: HashMap<String, Vec<DependencyInfo>>,
    /// 构建目标信息
    pub targets: Vec<TargetInfo>,
    /// 特性标志
    pub features: HashMap<String, Vec<String>>,

    pub is_workspace: bool,
    pub workspace_root: PathBuf,
    pub workspace_config: Option<WorkspaceConfig>,
}

/// 包信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PackageInfo {
    /// 包名称
    pub name: String,
    /// 包版本
    pub version: String,
    /// 包路径
    pub manifest_path: PathBuf,
    /// 包源代码路径
    pub source_path: PathBuf,
    /// 包的依赖
    pub dependencies: Vec<DependencyInfo>,
    /// 包的构建目标
    pub targets: Vec<TargetInfo>,
    /// 包的特性
    pub features: HashMap<String, Vec<String>>,
    /// 是否为workspace成员
    pub is_workspace_member: bool,
}

/// workspace配置
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceConfig {
    /// workspace成员
    pub members: Vec<String>,
    /// 排除的成员
    pub exclude: Vec<String>,
    /// workspace级别的依赖
    pub dependencies: HashMap<String, DependencyInfo>,
    /// workspace级别的元数据
    pub metadata: HashMap<String, toml::Value>,
}

/// 依赖信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyInfo {
    /// 依赖名称
    pub name: String,
    /// 依赖版本要求
    pub version_req: String,
    /// 实际版本
    pub version: Option<String>,
    /// 依赖类型（normal, dev, build）
    pub kind: String,
    /// 是否可选
    pub optional: bool,
    /// 启用的特性
    pub features: Vec<String>,
    /// 依赖来源
    pub source: Option<String>,
}

/// 构建目标信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetInfo {
    /// 目标名称
    pub name: String,
    /// 目标类型（bin, lib, test, bench, example）
    pub kind: Vec<String>,
    /// 源文件路径
    pub src_path: PathBuf,
    /// 是否为文档测试
    pub doctest: bool,
    /// 目标版本
    pub edition: String,
}

/// Cargo集成分析器
pub struct CargoAnalyzer {
    /// 项目根目录
    project_root: PathBuf,
    /// Cargo元数据
    metadata: Option<Metadata>,
}

impl CargoAnalyzer {
    /// 创建新的Cargo分析器
    pub fn new(project_root: &Path) -> Result<Self> {
        let project_root = project_root.to_path_buf();

        // 验证是否为有效的Rust项目
        let cargo_toml = project_root.join("Cargo.toml");
        if !cargo_toml.exists() {
            return Err(AnalyzerError::invalid_project(format!(
                "Cargo.toml not found in {}",
                project_root.display()
            )));
        }

        Ok(Self {
            project_root,
            metadata: None,
        })
    }

    /// 分析Cargo项目元数据
    pub fn analyze(&mut self) -> Result<ProjectMetadata> {
        info!("分析Cargo项目元数据: {}", self.project_root.display());

        // 获取Cargo元数据
        let metadata = self.load_metadata()?;
        self.metadata = Some(metadata.clone());

        // 解析项目信息
        let project_metadata = self.parse_project_metadata(&metadata)?;

        info!(
            "成功分析项目元数据: {} v{}",
            project_metadata.name, project_metadata.version
        );
        debug!(
            "发现 {} 个包, {} 个依赖",
            project_metadata.packages.len(),
            project_metadata.dependencies.len()
        );

        Ok(project_metadata)
    }

    /// 加载Cargo元数据
    fn load_metadata(&self) -> Result<Metadata> {
        debug!("加载Cargo元数据从: {}", self.project_root.display());

        let metadata = MetadataCommand::new()
            .manifest_path(self.project_root.join("Cargo.toml"))
            .exec()
            .map_err(AnalyzerError::CargoMetadata)?;

        debug!("成功加载元数据，包含 {} 个包", metadata.packages.len());
        Ok(metadata)
    }

    /// 解析项目元数据
    fn parse_project_metadata(&self, metadata: &Metadata) -> Result<ProjectMetadata> {
        // 确定根包
        let root_package = self.find_root_package(metadata)?;

        // 解析包信息
        let packages = self.parse_packages(metadata)?;

        // 解析依赖关系
        let dependencies = self.parse_dependencies(metadata)?;

        // 解析构建目标
        let targets = self.parse_targets(metadata)?;

        // 解析特性
        let features = self.parse_features(root_package);

        // 检查是否为workspace
        let is_workspace = metadata.workspace_members.len() > 1;

        Ok(ProjectMetadata {
            name: root_package.name.clone(),
            version: root_package.version.to_string(),
            description: root_package.description.clone(),
            authors: root_package.authors.clone(),
            license: root_package.license.clone(),
            homepage: root_package.homepage.clone(),
            repository: root_package.repository.clone(),
            packages,
            dependencies,
            targets,
            features,
            is_workspace,
            workspace_root: metadata.workspace_root.clone().into(),
            workspace_config: if is_workspace {
                Some(self.parse_workspace_config(metadata)?)
            } else {
                None
            },
        })
    }

    /// 查找根包
    fn find_root_package<'a>(&self, metadata: &'a Metadata) -> Result<&'a Package> {
        // 首先尝试通过manifest路径匹配
        let cargo_toml = self.project_root.join("Cargo.toml");

        for package in &metadata.packages {
            if package.manifest_path.as_std_path() == cargo_toml {
                return Ok(package);
            }
        }

        // 如果没有找到，使用workspace的第一个成员
        if let Some(member_id) = metadata.workspace_members.first() {
            for package in &metadata.packages {
                if &package.id == member_id {
                    return Ok(package);
                }
            }
        }

        // 最后尝试使用第一个包
        metadata
            .packages
            .first()
            .ok_or_else(|| AnalyzerError::invalid_project("No packages found in metadata"))
    }

    /// 解析所有包信息
    fn parse_packages(&self, metadata: &Metadata) -> Result<Vec<PackageInfo>> {
        let workspace_members: HashSet<_> = metadata.workspace_members.iter().collect();
        let mut packages = Vec::new();

        for package in &metadata.packages {
            let is_workspace_member = workspace_members.contains(&package.id);

            // 解析包的依赖
            let dependencies = self.parse_package_dependencies(package);

            // 解析包的构建目标
            let targets = self.parse_package_targets(package);

            // 解析包的特性
            let features = self.parse_features(package);

            // 确定源代码路径
            let source_path = package
                .manifest_path
                .parent()
                .map(|p| p.to_path_buf())
                .unwrap_or_else(|| package.manifest_path.clone());

            packages.push(PackageInfo {
                name: package.name.clone(),
                version: package.version.to_string(),
                manifest_path: package.manifest_path.clone().into(),
                source_path: source_path.into(),
                dependencies,
                targets,
                features,
                is_workspace_member,
            });
        }

        Ok(packages)
    }

    /// 解析包的依赖关系
    fn parse_package_dependencies(&self, package: &Package) -> Vec<DependencyInfo> {
        let mut dependencies = Vec::new();

        for dependency in &package.dependencies {
            let kind = match dependency.kind {
                DependencyKind::Normal => "normal",
                DependencyKind::Development => "dev",
                DependencyKind::Build => "build",
                _ => "unknown",
            };

            dependencies.push(DependencyInfo {
                name: dependency.name.clone(),
                version_req: dependency.req.to_string(),
                version: None, // 在实际实现中可以通过resolve图获取
                kind: kind.to_string(),
                optional: dependency.optional,
                features: dependency.features.clone(),
                source: dependency.source.as_ref().map(|s| s.to_string()),
            });
        }

        dependencies
    }

    /// 解析包的构建目标
    fn parse_package_targets(&self, package: &Package) -> Vec<TargetInfo> {
        let mut targets = Vec::new();

        for target in &package.targets {
            targets.push(TargetInfo {
                name: target.name.clone(),
                kind: target.kind.clone(),
                src_path: target.src_path.clone().into(),
                doctest: target.doctest,
                edition: target.edition.to_string(),
            });
        }

        targets
    }

    /// 解析全局依赖关系图
    fn parse_dependencies(
        &self,
        metadata: &Metadata,
    ) -> Result<HashMap<String, Vec<DependencyInfo>>> {
        let mut dependencies = HashMap::new();

        for package in &metadata.packages {
            let package_deps = self.parse_package_dependencies(package);
            dependencies.insert(package.name.clone(), package_deps);
        }

        Ok(dependencies)
    }

    /// 解析全局构建目标
    fn parse_targets(&self, metadata: &Metadata) -> Result<Vec<TargetInfo>> {
        let mut targets = Vec::new();

        for package in &metadata.packages {
            let package_targets = self.parse_package_targets(package);
            targets.extend(package_targets);
        }

        Ok(targets)
    }

    /// 解析特性标志
    fn parse_features(&self, package: &Package) -> HashMap<String, Vec<String>> {
        package
            .features
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect()
    }

    /// 获取包的源代码目录
    pub fn get_source_directories(&self) -> Result<Vec<PathBuf>> {
        let metadata = self
            .metadata
            .as_ref()
            .ok_or_else(|| AnalyzerError::config_error("Metadata not loaded"))?;

        let mut source_dirs = Vec::new();
        let workspace_members: HashSet<_> = metadata.workspace_members.iter().collect();

        for package in &metadata.packages {
            // 只包含workspace成员的源代码
            if workspace_members.contains(&package.id)
                && let Some(parent) = package.manifest_path.parent()
            {
                source_dirs.push(parent.to_path_buf());
            }
        }

        if source_dirs.is_empty() {
            source_dirs.push(self.project_root.clone().try_into().unwrap());
        }

        Ok(source_dirs.into_iter().map(|p| p.into()).collect())
    }

    /// 检查是否应该包含某个包的分析
    pub fn should_include_package(&self, package_name: &str) -> bool {
        if let Some(metadata) = &self.metadata {
            let workspace_members: HashSet<_> = metadata.workspace_members.iter().collect();

            for package in &metadata.packages {
                if package.name == package_name {
                    return workspace_members.contains(&package.id);
                }
            }
        }
        false
    }

    /// 获取包的特定目标类型
    pub fn get_targets_by_kind(&self, kind: &str) -> Result<Vec<TargetInfo>> {
        let metadata = self
            .metadata
            .as_ref()
            .ok_or_else(|| AnalyzerError::config_error("Metadata not loaded"))?;

        let mut targets = Vec::new();
        let workspace_members: HashSet<_> = metadata.workspace_members.iter().collect();

        for package in &metadata.packages {
            if workspace_members.contains(&package.id) {
                for target in &package.targets {
                    if target.kind.contains(&kind.to_string()) {
                        targets.push(TargetInfo {
                            name: target.name.clone(),
                            kind: target.kind.clone(),
                            src_path: target.src_path.clone().into(),
                            doctest: target.doctest,
                            edition: target.edition.to_string(),
                        });
                    }
                }
            }
        }

        Ok(targets)
    }

    /// 解析workspace配置
    fn parse_workspace_config(&self, metadata: &Metadata) -> Result<WorkspaceConfig> {
        let workspace_root_toml = metadata.workspace_root.join("Cargo.toml");

        // 读取workspace根目录的Cargo.toml
        let toml_content = std::fs::read_to_string(&workspace_root_toml).map_err(|e| {
            AnalyzerError::config_error(format!("无法读取workspace Cargo.toml: {e}"))
        })?;

        // 解析TOML内容
        let toml_value: toml::Value = toml::from_str(&toml_content).map_err(|e| {
            AnalyzerError::config_error(format!("解析workspace Cargo.toml失败: {e}"))
        })?;

        // 提取workspace配置
        let workspace_section = toml_value
            .get("workspace")
            .ok_or_else(|| AnalyzerError::config_error("未找到[workspace]配置段"))?;

        // 解析members
        let members = workspace_section
            .get("members")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str())
                    .map(|s| s.to_string())
                    .collect()
            })
            .unwrap_or_default();

        // 解析exclude
        let exclude = workspace_section
            .get("exclude")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str())
                    .map(|s| s.to_string())
                    .collect()
            })
            .unwrap_or_default();

        // 解析workspace级别的依赖
        let mut workspace_dependencies = HashMap::new();
        if let Some(deps) = workspace_section
            .get("dependencies")
            .and_then(|v| v.as_table())
        {
            for (name, dep_info) in deps {
                let dependency = self.parse_workspace_dependency(name, dep_info)?;
                workspace_dependencies.insert(name.clone(), dependency);
            }
        }

        // 解析workspace元数据
        let mut workspace_metadata = HashMap::new();
        if let Some(metadata_section) = workspace_section.get("metadata").and_then(|v| v.as_table())
        {
            for (key, value) in metadata_section {
                workspace_metadata.insert(key.clone(), value.clone());
            }
        }

        Ok(WorkspaceConfig {
            members,
            exclude,
            dependencies: workspace_dependencies,
            metadata: workspace_metadata,
        })
    }

    /// 解析workspace级别的依赖
    fn parse_workspace_dependency(
        &self,
        name: &str,
        dep_info: &toml::Value,
    ) -> Result<DependencyInfo> {
        let version = if let Some(version_str) = dep_info.as_str() {
            version_str.to_string()
        } else if let Some(table) = dep_info.as_table() {
            table
                .get("version")
                .and_then(|v| v.as_str())
                .unwrap_or("*")
                .to_string()
        } else {
            "*".to_string()
        };

        let source = if let Some(table) = dep_info.as_table() {
            if table.contains_key("path") {
                "path".to_string()
            } else if table.contains_key("git") {
                "git".to_string()
            } else {
                "registry".to_string()
            }
        } else {
            "registry".to_string()
        };

        Ok(DependencyInfo {
            name: name.to_string(),
            version_req: version.clone(),
            version: Some(version),
            source: Some(source),
            kind: "normal".to_string(),
            optional: dep_info
                .as_table()
                .and_then(|t| t.get("optional"))
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            features: dep_info
                .as_table()
                .and_then(|t| t.get("features"))
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str())
                        .map(|s| s.to_string())
                        .collect()
                })
                .unwrap_or_default(),
        })
    }
}
