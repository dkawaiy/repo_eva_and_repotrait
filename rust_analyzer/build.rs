use std::env;
use std::fs;
use std::path::Path;

fn main() {
    // 设置构建时的环境变量
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=src/");
    // 让 Git 提交变更也触发重跑
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/refs/heads");

    // 获取构建信息
    let target = env::var("TARGET").unwrap_or_else(|_| "unknown".to_string());
    let profile = env::var("PROFILE").unwrap_or_else(|_| "debug".to_string());
    let opt_level = env::var("OPT_LEVEL").unwrap_or_else(|_| "0".to_string());

    println!("cargo:rustc-env=BUILD_TARGET={target}");
    println!("cargo:rustc-env=BUILD_PROFILE={profile}");
    println!("cargo:rustc-env=BUILD_OPT_LEVEL={opt_level}");

    // 生成版本信息
    let version = env!("CARGO_PKG_VERSION");
    let git_hash = get_git_hash().unwrap_or_else(|| "unknown".to_string());
    let build_time = chrono::Utc::now()
        .format("%Y-%m-%d %H:%M:%S UTC")
        .to_string();

    println!("cargo:rustc-env=BUILD_VERSION={version}");
    println!("cargo:rustc-env=BUILD_GIT_HASH={git_hash}");
    println!("cargo:rustc-env=BUILD_TIME={build_time}");

    // 创建构建信息文件
    create_build_info_file(
        &target,
        &profile,
        &opt_level,
        version,
        &git_hash,
        &build_time,
    );
}

fn get_git_hash() -> Option<String> {
    use std::process::Command;

    let output = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()?;

    if output.status.success() {
        Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        None
    }
}

fn create_build_info_file(
    target: &str,
    profile: &str,
    opt_level: &str,
    version: &str,
    git_hash: &str,
    build_time: &str,
) {
    let out_dir = env::var("OUT_DIR").unwrap();
    let dest_path = Path::new(&out_dir).join("build_info.rs");

    let build_info = format!(
        r#"
/// 构建信息模块
pub mod build_info {{
    /// 版本号
    pub const VERSION: &str = "{version}";

    /// Git提交哈希
    pub const GIT_HASH: &str = "{git_hash}";

    /// 构建时间
    pub const BUILD_TIME: &str = "{build_time}";

    /// 构建目标
    pub const TARGET: &str = "{target}";

    /// 构建配置
    pub const PROFILE: &str = "{profile}";

    /// 优化级别
    pub const OPT_LEVEL: &str = "{opt_level}";

    /// 获取完整版本信息
    pub fn full_version() -> String {{
        format!("{{}} ({{}} {{}} {{}})", VERSION, GIT_HASH, PROFILE, BUILD_TIME)
    }}

    /// 获取简短版本信息
    pub fn short_version() -> String {{
        format!("{{}}-{{}}", VERSION, GIT_HASH)
    }}
}}
"#
    );

    fs::write(&dest_path, build_info).unwrap();
}
