use anyhow::{Context, Result};
use clap::Parser;
use log::{error, info};
use std::error::Error;

mod analyzer;
mod cargo_doc_integration;
mod cargo_integration;
mod cli;
mod cross_crate_analyzer;
mod doc_analyzer;
mod doc_output;
mod error;
mod function_analyzer;
mod output;
mod semantic_analyzer;
mod type_analyzer;
mod utils;

use analyzer::RustAnalyzer;
use cli::CliArgs;

fn main() -> Result<()> {
    env_logger::Builder::from_default_env()
        .filter_level(log::LevelFilter::Info)
        .init();

    info!("启动Rust代码分析器 v{}", env!("CARGO_PKG_VERSION"));

    let args = CliArgs::parse();

    if !args.source_path.exists() {
        error!("源代码路径不存在: {}", args.source_path.display());
        std::process::exit(1);
    }

    if !args.source_path.is_dir() {
        error!("源代码路径必须是目录: {}", args.source_path.display());
        std::process::exit(1);
    }

    // 检查Cargo.toml
    let cargo_toml = args.source_path.join("Cargo.toml");
    if !cargo_toml.exists() {
        error!(
            "不是Rust项目: {} 中未找到Cargo.toml",
            args.source_path.display()
        );
        std::process::exit(1);
    }

    std::fs::create_dir_all(&args.output_path)
        .with_context(|| format!("创建输出目录失败: {}", args.output_path.display()))?;

    info!("分析Rust项目: {}", args.source_path.display());
    info!("输出目录: {}", args.output_path.display());

    // 初始化分析器
    let mut analyzer = RustAnalyzer::new(args.source_path.clone(), args.output_path.clone())?;

    // 设置详细级别
    analyzer.set_verbose(args.verbose);

    // 运行分析
    match analyzer.analyze() {
        Ok(_) => {
            info!("分析完成");
            info!("生成的文件:");
            info!("  - {}/methods.jsonl", args.output_path.display());
            info!("  - {}/structs.jsonl", args.output_path.display());
        }
        Err(e) => {
            error!("分析失败: {e}");

            let mut source = e.source();
            while let Some(err) = source {
                error!("  原因: {err}");
                source = err.source();
            }

            std::process::exit(1);
        }
    }

    Ok(())
}
