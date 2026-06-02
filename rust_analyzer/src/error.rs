use std::path::PathBuf;
use thiserror::Error;

/// 错误类型
#[derive(Error, Debug)]
pub enum AnalyzerError {
    #[error("IO错误: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON序列化错误: {0}")]
    Json(#[from] serde_json::Error),

    #[error("Cargo元数据错误: {0}")]
    CargoMetadata(#[from] cargo_metadata::Error),

    #[error("文件 {file} 语法解析错误: {message}")]
    SyntaxError { file: PathBuf, message: String },

    #[error("无效的Rust项目: {message}")]
    InvalidProject { message: String },

    #[error("文件未找到: {path}")]
    FileNotFound { path: PathBuf },

    #[error("分析错误 {context}: {message}")]
    AnalysisError { context: String, message: String },

    #[error("配置错误: {message}")]
    ConfigError { message: String },

    #[error("依赖解析错误: {message}")]
    DependencyError { message: String },

    #[error("输出生成错误: {message}")]
    OutputError { message: String },

    #[error("语义分析错误: {0}")]
    SemanticError(String),
}

impl AnalyzerError {
    pub fn syntax_error(file: PathBuf, message: impl Into<String>) -> Self {
        Self::SyntaxError {
            file,
            message: message.into(),
        }
    }

    pub fn invalid_project(message: impl Into<String>) -> Self {
        Self::InvalidProject {
            message: message.into(),
        }
    }

    pub fn file_not_found(path: PathBuf) -> Self {
        Self::FileNotFound { path }
    }

    pub fn analysis_error(context: impl Into<String>, message: impl Into<String>) -> Self {
        Self::AnalysisError {
            context: context.into(),
            message: message.into(),
        }
    }

    pub fn config_error(message: impl Into<String>) -> Self {
        Self::ConfigError {
            message: message.into(),
        }
    }

    pub fn dependency_error(message: impl Into<String>) -> Self {
        Self::DependencyError {
            message: message.into(),
        }
    }

    pub fn output_error(message: impl Into<String>) -> Self {
        Self::OutputError {
            message: message.into(),
        }
    }
}

pub type Result<T> = std::result::Result<T, AnalyzerError>;
