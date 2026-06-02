use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(
    name = "rust_analyzer",
    version = env!("CARGO_PKG_VERSION"),
    about = "分析Rust代码并生成结构化JSON输出",
    long_about = "使用AST解析分析Rust代码库，提取函数、类型及其关系。\
                  生成与现有输出兼容的methods.jsonl和structs.jsonl文件。"
)]
pub struct CliArgs {
    #[arg(
        short = 's',
        long = "source",
        value_name = "SOURCE_PATH",
        help = "包含Rust项目的源代码目录"
    )]
    pub source_path: PathBuf,

    #[arg(
        short = 'o',
        long = "output",
        value_name = "OUTPUT_PATH",
        help = "methods.jsonl和structs.jsonl的输出目录"
    )]
    pub output_path: PathBuf,

    /// 启用详细日志
    #[arg(short = 'v', long = "verbose", help = "启用详细输出用于调试")]
    pub verbose: bool,

    /// 跳过依赖分析
    #[arg(long = "skip-deps", help = "跳过外部依赖分析")]
    pub skip_dependencies: bool,

    /// 包含私有项
    #[arg(long = "include-private", help = "在输出中包含私有函数和类型")]
    pub include_private: bool,

    /// 调用图分析最大深度
    #[arg(
        long = "max-depth",
        value_name = "DEPTH",
        default_value = "10",
        help = "分析函数调用链的最大深度"
    )]
    pub max_depth: usize,

    /// 文件过滤模式
    #[arg(
        long = "filter",
        value_name = "PATTERN",
        help = "只分析匹配此glob模式的文件"
    )]
    pub file_filter: Option<String>,

    /// 排除测试文件
    #[arg(long = "exclude-tests", help = "排除测试文件和测试模块")]
    pub exclude_tests: bool,
}
