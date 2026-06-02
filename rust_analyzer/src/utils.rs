use std::path::{Path, PathBuf};
use walkdir::WalkDir;

/// 查找目录中的所有Rust源文件
pub fn find_rust_files(dir: &Path) -> Vec<PathBuf> {
    WalkDir::new(dir)
        .into_iter()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "rs"))
        .filter(|entry| {
            // 跳过构建目录和缓存目录
            !entry.path().components().any(|component| {
                matches!(
                    component.as_os_str().to_str(),
                    Some("target")
                        | Some(".git")
                        | Some("node_modules")
                        | Some(".benchwork")
                        | Some("cargo_home")
                )
            })
        })
        .map(|entry| entry.path().to_path_buf())
        .collect()
}

/// 从文件路径提取模块路径
pub fn extract_module_path(file_path: &Path, project_root: &Path) -> String {
    let relative_path = file_path.strip_prefix(project_root).unwrap_or(file_path);

    let mut components: Vec<&str> = relative_path
        .components()
        .filter_map(|comp| comp.as_os_str().to_str())
        .collect();

    // 移除src前缀
    if components.first() == Some(&"src") {
        components.remove(0);
    }

    // 移除.rs扩展名
    if let Some(last) = components.last_mut()
        && let Some(name) = last.strip_suffix(".rs")
    {
        *last = name;
    }

    // 处理特殊情况
    match components.last() {
        Some(&"main") => {
            if components.len() > 1 {
                components.pop();
                format!("crate::{}", components.join("::"))
            } else {
                "crate".to_string()
            }
        }
        Some(&"lib") => "crate".to_string(),
        Some(&"mod") => {
            components.pop();
            if components.is_empty() {
                "crate".to_string()
            } else {
                format!("crate::{}", components.join("::"))
            }
        }
        _ => {
            if components.is_empty() {
                "crate".to_string()
            } else {
                format!("crate::{}", components.join("::"))
            }
        }
    }
}

/// 清理类型名称
pub fn clean_type_name(type_str: &str) -> String {
    let mut cleaned = type_str.to_string();

    // 移除引用符号
    cleaned = cleaned.replace("&mut ", "").replace("&", "");

    // 移除生命周期
    cleaned = regex::Regex::new(r"'[a-zA-Z_][a-zA-Z0-9_]*\s*")
        .unwrap()
        .replace_all(&cleaned, "")
        .to_string();

    // 提取泛型主类型
    if let Some(pos) = cleaned.find('<') {
        cleaned = cleaned[..pos].to_string();
    }

    // 移除路径前缀
    if let Some(pos) = cleaned.rfind("::") {
        cleaned = cleaned[pos + 2..].to_string();
    }

    cleaned.trim().to_string()
}

/// 检查是否为测试文件
pub fn is_test_file(path: &Path) -> bool {
    path.components().any(|comp| {
        matches!(
            comp.as_os_str().to_str(),
            Some("tests") | Some("test") | Some("benches") | Some("examples")
        )
    }) || path
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| {
            name.contains("test") || name.contains("bench") || name.contains("example")
        })
}

/// 生成唯一标识符
pub fn generate_id(module_path: &str, name: &str) -> String {
    format!("{module_path}::{name}")
}

/// 标准化可见性修饰符
pub fn normalize_visibility(vis: &syn::Visibility) -> String {
    match vis {
        syn::Visibility::Public(_) => "PUBLIC".to_string(),
        syn::Visibility::Restricted(restricted) => {
            if restricted.path.is_ident("crate") {
                "CRATE".to_string()
            } else if restricted.path.is_ident("super") {
                "SUPER".to_string()
            } else {
                "RESTRICTED".to_string()
            }
        }
        syn::Visibility::Inherited => "PRIVATE".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_extract_module_path() {
        let project_root = PathBuf::from("/project");

        assert_eq!(
            extract_module_path(&PathBuf::from("/project/src/main.rs"), &project_root),
            "crate"
        );

        assert_eq!(
            extract_module_path(&PathBuf::from("/project/src/lib.rs"), &project_root),
            "crate"
        );

        assert_eq!(
            extract_module_path(&PathBuf::from("/project/src/utils/mod.rs"), &project_root),
            "crate::utils"
        );

        assert_eq!(
            extract_module_path(&PathBuf::from("/project/src/parser/ast.rs"), &project_root),
            "crate::parser::ast"
        );
    }

    #[test]
    fn test_clean_type_name() {
        assert_eq!(clean_type_name("&str"), "str");
        assert_eq!(clean_type_name("&mut String"), "String");
        assert_eq!(clean_type_name("Vec<String>"), "Vec");
        assert_eq!(clean_type_name("std::collections::HashMap"), "HashMap");
        assert_eq!(clean_type_name("'a str"), "str");
    }

    #[test]
    fn test_is_test_file() {
        assert!(is_test_file(&PathBuf::from("src/tests/mod.rs")));
        assert!(is_test_file(&PathBuf::from("tests/integration.rs")));
        assert!(is_test_file(&PathBuf::from("src/lib_test.rs")));
        assert!(!is_test_file(&PathBuf::from("src/lib.rs")));
        assert!(!is_test_file(&PathBuf::from("src/parser.rs")));
    }
}
