pub mod analyzer;
pub mod cargo_doc_integration;
pub mod cargo_integration;
pub mod cli;
pub mod cross_crate_analyzer;
pub mod doc_analyzer;
pub mod doc_output;
pub mod error;
pub mod function_analyzer;
pub mod output;
pub mod semantic_analyzer;
pub mod type_analyzer;
pub mod utils;

pub use analyzer::RustAnalyzer;
pub use error::{AnalyzerError, Result};
pub use semantic_analyzer::{
    CallGraphExport, CallType, FunctionCall, InferredType, MacroCall, SemanticAnalysisResult,
    SemanticAnalyzer, SourceLocation, TraitCall,
};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub const NAME: &str = env!("CARGO_PKG_NAME");

pub const DESCRIPTION: &str = env!("CARGO_PKG_DESCRIPTION");

/// 配置
#[derive(Debug, Clone)]
pub struct AnalysisConfig {
    /// 是否启用详细日志
    pub verbose: bool,
    /// 是否包含私有项目
    pub include_private: bool,
    /// 是否跳过依赖分析
    pub skip_deps: bool,
    /// 函数调用链分析的最大深度
    pub max_depth: usize,
    /// 文件过滤模式
    pub filter_pattern: Option<String>,
    /// 是否排除测试文件
    pub exclude_tests: bool,
    /// 是否启用语义分析
    pub enable_semantic_analysis: bool,
    /// 是否启用借用检查器集成
    pub enable_borrow_checker: bool,
    /// 是否启用增量分析
    pub enable_incremental: bool,
}

impl Default for AnalysisConfig {
    fn default() -> Self {
        Self {
            verbose: false,
            include_private: false,
            skip_deps: false,
            max_depth: 10,
            filter_pattern: None,
            exclude_tests: true,
            enable_semantic_analysis: true,
            enable_borrow_checker: false,
            enable_incremental: false,
        }
    }
}

/// 分析结果
#[derive(Debug, Clone)]
pub struct AnalysisResult {
    /// 基础分析结果
    pub basic_analysis: BasicAnalysisResult,
    /// 语义分析结果（如果启用）
    pub semantic_analysis: Option<SemanticAnalysisResult>,
    /// 借用检查结果（如果启用）
    pub borrow_check_result: Option<BorrowCheckResult>,
    /// 代码质量分析结果（如果启用）
    pub quality_analysis: Option<QualityAnalysisResult>,
}

/// 基础分析结果
#[derive(Debug, Clone)]
pub struct BasicAnalysisResult {
    /// 分析的文件数量
    pub file_count: usize,
    /// 发现的函数数量
    pub function_count: usize,
    /// 发现的类型数量
    pub type_count: usize,
    /// 分析耗时（毫秒）
    pub analysis_time_ms: u64,
}

/// 借用检查结果
#[derive(Debug, Clone)]
pub struct BorrowCheckResult {
    /// 借用冲突
    pub borrow_conflicts: Vec<BorrowConflict>,
    /// 生命周期问题
    pub lifetime_issues: Vec<LifetimeIssue>,
    /// 内存安全问题
    pub safety_issues: Vec<SafetyIssue>,
}

/// 借用冲突
#[derive(Debug, Clone)]
pub struct BorrowConflict {
    /// 冲突位置
    pub location: SourceLocation,
    /// 冲突描述
    pub description: String,
    /// 严重程度
    pub severity: Severity,
}

/// 生命周期问题
#[derive(Debug, Clone)]
pub struct LifetimeIssue {
    /// 问题位置
    pub location: SourceLocation,
    /// 问题描述
    pub description: String,
    /// 建议修复方案
    pub suggestion: Option<String>,
}

/// 内存安全问题
#[derive(Debug, Clone)]
pub struct SafetyIssue {
    /// 问题位置
    pub location: SourceLocation,
    /// 问题类型
    pub issue_type: SafetyIssueType,
    /// 问题描述
    pub description: String,
}

/// 内存安全问题类型
#[derive(Debug, Clone, PartialEq)]
pub enum SafetyIssueType {
    /// 悬垂指针
    DanglingPointer,
    /// 使用后释放
    UseAfterFree,
    /// 双重释放
    DoubleFree,
    /// 缓冲区溢出
    BufferOverflow,
    /// 未初始化内存访问
    UninitializedMemory,
}

/// 代码质量分析结果
#[derive(Debug, Clone)]
pub struct QualityAnalysisResult {
    /// 复杂度度量
    pub complexity_metrics: ComplexityMetrics,
    /// 代码风格问题
    pub style_issues: Vec<StyleIssue>,
    /// 性能问题
    pub performance_issues: Vec<PerformanceIssue>,
    /// 安全漏洞
    pub security_vulnerabilities: Vec<SecurityVulnerability>,
}

/// 复杂度度量
#[derive(Debug, Clone)]
pub struct ComplexityMetrics {
    /// 圈复杂度
    pub cyclomatic_complexity: f64,
    /// 认知复杂度
    pub cognitive_complexity: f64,
    /// 代码行数
    pub lines_of_code: usize,
    /// 函数平均长度
    pub average_function_length: f64,
}

/// 代码风格问题
#[derive(Debug, Clone)]
pub struct StyleIssue {
    /// 问题位置
    pub location: SourceLocation,
    /// 问题类型
    pub issue_type: String,
    /// 问题描述
    pub description: String,
    /// 严重程度
    pub severity: Severity,
}

/// 性能问题
#[derive(Debug, Clone)]
pub struct PerformanceIssue {
    /// 问题位置
    pub location: SourceLocation,
    /// 问题类型
    pub issue_type: String,
    /// 问题描述
    pub description: String,
    /// 性能影响评估
    pub impact: PerformanceImpact,
}

/// 性能影响程度
#[derive(Debug, Clone, PartialEq, PartialOrd)]
pub enum PerformanceImpact {
    Low,
    Medium,
    High,
    Critical,
}

/// 安全漏洞
#[derive(Debug, Clone)]
pub struct SecurityVulnerability {
    /// 漏洞位置
    pub location: SourceLocation,
    /// 漏洞类型
    pub vulnerability_type: String,
    /// 漏洞描述
    pub description: String,
    /// 严重程度
    pub severity: Severity,
    /// CVSS评分
    pub cvss_score: Option<f64>,
}

/// 严重程度
#[derive(Debug, Clone, PartialEq, PartialOrd)]
pub enum Severity {
    Info,
    Warning,
    Error,
    Critical,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_default_config() {
        let config = AnalysisConfig::default();
        assert!(!config.verbose);
        assert!(!config.include_private);
        assert!(!config.skip_deps);
        assert_eq!(config.max_depth, 10);
        assert!(config.exclude_tests);
        assert!(config.enable_semantic_analysis);
    }

    #[test]
    fn test_version_info() {
        assert!(!VERSION.is_empty());
        assert!(!NAME.is_empty());
        assert!(!DESCRIPTION.is_empty());
    }

    #[test]
    fn test_severity_ordering() {
        assert!(Severity::Info < Severity::Warning);
        assert!(Severity::Warning < Severity::Error);
        assert!(Severity::Error < Severity::Critical);
    }

    #[test]
    fn test_performance_impact_ordering() {
        assert!(PerformanceImpact::Low < PerformanceImpact::Medium);
        assert!(PerformanceImpact::Medium < PerformanceImpact::High);
        assert!(PerformanceImpact::High < PerformanceImpact::Critical);
    }
}
