use crate::error::{AnalyzerError, Result};

use log::{debug, info};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::PathBuf;

/// 方法输出结构
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MethodOutput {
    /// 函数名称
    pub name: String,
    /// 函数签名
    pub signature: String,
    /// 开始行号
    #[serde(rename = "beginLine")]
    pub begin_line: u32,
    /// 结束行号
    #[serde(rename = "endLine")]
    pub end_line: u32,
    /// 文件名
    pub filename: String,
    /// 修饰符
    pub modifier: String,
    /// 参数列表
    pub params: Vec<ParameterInfo>,
    /// 返回类型
    #[serde(rename = "returnType")]
    pub return_type: String,
    /// 调用的函数列表
    pub callees: Vec<String>,
}

/// 参数信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParameterInfo {
    /// 参数名称
    pub name: String,
    /// 参数类型
    #[serde(rename = "type")]
    pub param_type: String,
}

/// 结构体输出结构
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StructOutput {
    /// 类型名称
    pub name: String,
    /// 完整类型名称
    pub fullname: String,
    /// 文件名
    pub filename: String,
    /// 开始行号
    #[serde(rename = "beginLine")]
    pub begin_line: u32,
    /// 方法列表
    pub methods: Vec<String>,
    /// 属性列表
    pub attributes: Vec<AttributeInfo>,
}

/// 属性信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttributeInfo {
    /// 属性名称
    pub name: String,
    /// 属性类型
    #[serde(rename = "type")]
    pub attr_type: String,
    /// 修饰符
    pub modifier: String,
}

/// JSON输出生成器
pub struct OutputGenerator {
    /// 输出目录路径
    output_path: PathBuf,
}

impl OutputGenerator {
    /// 创建输出生成器
    pub fn new(output_path: PathBuf) -> Self {
        Self { output_path }
    }

    /// 生成methods.jsonl文件
    pub fn generate_methods_output(&self, methods: &[MethodOutput]) -> Result<()> {
        let methods_file = self.output_path.join("methods.jsonl");
        info!("生成methods.jsonl文件: {}", methods_file.display());

        let file = File::create(&methods_file)
            .map_err(|e| AnalyzerError::output_error(format!("无法创建methods.jsonl文件: {e}")))?;

        let mut writer = BufWriter::new(file);

        for method in methods {
            // 验证方法数据的完整性
            self.validate_method_output(method)?;

            // 序列化为JSON并写入文件
            let json_line = serde_json::to_string(method)
                .map_err(|e| AnalyzerError::output_error(format!("序列化方法数据失败: {e}")))?;

            writeln!(writer, "{json_line}")
                .map_err(|e| AnalyzerError::output_error(format!("写入methods.jsonl失败: {e}")))?;
        }

        writer.flush().map_err(|e| {
            AnalyzerError::output_error(format!("刷新methods.jsonl缓冲区失败: {e}"))
        })?;

        info!("成功生成methods.jsonl，包含 {} 个方法", methods.len());
        debug!("methods.jsonl文件路径: {}", methods_file.display());

        Ok(())
    }

    /// 生成structs.jsonl文件
    pub fn generate_structs_output(&self, structs: &[StructOutput]) -> Result<()> {
        let structs_file = self.output_path.join("structs.jsonl");
        info!("生成structs.jsonl文件: {}", structs_file.display());

        let file = File::create(&structs_file)
            .map_err(|e| AnalyzerError::output_error(format!("无法创建structs.jsonl文件: {e}")))?;

        let mut writer = BufWriter::new(file);

        for struct_def in structs {
            // 验证结构体数据的完整性
            self.validate_struct_output(struct_def)?;

            // 序列化为JSON并写入文件
            let json_line = serde_json::to_string(struct_def)
                .map_err(|e| AnalyzerError::output_error(format!("序列化结构体数据失败: {e}")))?;

            writeln!(writer, "{json_line}")
                .map_err(|e| AnalyzerError::output_error(format!("写入structs.jsonl失败: {e}")))?;
        }

        writer.flush().map_err(|e| {
            AnalyzerError::output_error(format!("刷新structs.jsonl缓冲区失败: {e}"))
        })?;

        info!("成功生成structs.jsonl，包含 {} 个类型", structs.len());
        debug!("structs.jsonl文件路径: {}", structs_file.display());

        Ok(())
    }

    /// 生成统计信息文件
    pub fn generate_statistics(
        &self,
        methods: &[MethodOutput],
        structs: &[StructOutput],
    ) -> Result<()> {
        let stats_file = self.output_path.join("analysis_stats.json");
        info!("生成分析统计信息: {}", stats_file.display());

        let stats = AnalysisStatistics::new(methods, structs);

        let file = File::create(&stats_file)
            .map_err(|e| AnalyzerError::output_error(format!("无法创建统计文件: {e}")))?;

        serde_json::to_writer_pretty(file, &stats)
            .map_err(|e| AnalyzerError::output_error(format!("写入统计信息失败: {e}")))?;

        info!("成功生成分析统计信息");
        Ok(())
    }

    /// 验证方法输出数据的完整性
    fn validate_method_output(&self, method: &MethodOutput) -> Result<()> {
        if method.name.is_empty() {
            return Err(AnalyzerError::output_error("方法名称不能为空"));
        }

        if method.signature.is_empty() {
            return Err(AnalyzerError::output_error("方法签名不能为空"));
        }

        if method.filename.is_empty() {
            return Err(AnalyzerError::output_error("文件名不能为空"));
        }

        if method.begin_line == 0 {
            return Err(AnalyzerError::output_error("开始行号必须大于0"));
        }

        if method.end_line < method.begin_line {
            return Err(AnalyzerError::output_error("结束行号不能小于开始行号"));
        }

        // 验证参数信息
        for param in &method.params {
            if param.name.is_empty() {
                return Err(AnalyzerError::output_error("参数名称不能为空"));
            }
            if param.param_type.is_empty() {
                return Err(AnalyzerError::output_error("参数类型不能为空"));
            }
        }

        Ok(())
    }

    /// 验证结构体输出数据的完整性
    fn validate_struct_output(&self, struct_def: &StructOutput) -> Result<()> {
        if struct_def.name.is_empty() {
            return Err(AnalyzerError::output_error("结构体名称不能为空"));
        }

        if struct_def.fullname.is_empty() {
            return Err(AnalyzerError::output_error("完整名称不能为空"));
        }

        if struct_def.filename.is_empty() {
            return Err(AnalyzerError::output_error("文件名不能为空"));
        }

        if struct_def.begin_line == 0 {
            return Err(AnalyzerError::output_error("开始行号必须大于0"));
        }

        // 验证属性信息
        for attr in &struct_def.attributes {
            if attr.name.is_empty() {
                return Err(AnalyzerError::output_error("属性名称不能为空"));
            }
            if attr.attr_type.is_empty() {
                return Err(AnalyzerError::output_error("属性类型不能为空"));
            }
        }

        Ok(())
    }
}

/// 分析统计信息
#[derive(Debug, Serialize, Deserialize)]
pub struct AnalysisStatistics {
    /// 总方法数
    pub total_methods: usize,
    /// 总类型数
    pub total_types: usize,
    /// 公共方法数
    pub public_methods: usize,
    /// 私有方法数
    pub private_methods: usize,
    /// 异步方法数
    pub async_methods: usize,
    /// 不安全方法数
    pub unsafe_methods: usize,
    /// 平均方法参数数
    pub avg_method_params: f64,
    /// 最大调用深度
    pub max_call_depth: usize,
    /// 结构体数量
    pub struct_count: usize,
    /// 枚举数量
    pub enum_count: usize,
    /// trait数量
    pub trait_count: usize,
    /// 平均类型属性数
    pub avg_type_attributes: f64,
}

impl AnalysisStatistics {
    /// 从分析结果创建统计信息
    pub fn new(methods: &[MethodOutput], structs: &[StructOutput]) -> Self {
        let total_methods = methods.len();
        let total_types = structs.len();

        let public_methods = methods
            .iter()
            .filter(|m| m.modifier.contains("PUBLIC"))
            .count();

        let private_methods = methods
            .iter()
            .filter(|m| m.modifier.contains("PRIVATE"))
            .count();

        let async_methods = methods
            .iter()
            .filter(|m| m.modifier.contains("ASYNC"))
            .count();

        let unsafe_methods = methods
            .iter()
            .filter(|m| m.modifier.contains("UNSAFE"))
            .count();

        let total_params: usize = methods.iter().map(|m| m.params.len()).sum();

        let avg_method_params = if total_methods > 0 {
            total_params as f64 / total_methods as f64
        } else {
            0.0
        };

        let max_call_depth = methods.iter().map(|m| m.callees.len()).max().unwrap_or(0);

        // 统计不同类型的数量
        let mut struct_count = 0;
        let mut enum_count = 0;
        let mut trait_count = 0;

        for struct_def in structs {
            // 通过分析属性来区分类型
            let has_variants = struct_def
                .attributes
                .iter()
                .any(|attr| attr.modifier == "VARIANT");
            let has_trait_methods = struct_def
                .attributes
                .iter()
                .any(|attr| attr.modifier == "TRAIT_METHOD");

            if has_variants {
                enum_count += 1;
            } else if has_trait_methods {
                trait_count += 1;
            } else {
                struct_count += 1;
            }
        }

        let total_attributes: usize = structs.iter().map(|s| s.attributes.len()).sum();

        let avg_type_attributes = if total_types > 0 {
            total_attributes as f64 / total_types as f64
        } else {
            0.0
        };

        Self {
            total_methods,
            total_types,
            public_methods,
            private_methods,
            async_methods,
            unsafe_methods,
            avg_method_params,
            max_call_depth,
            struct_count,
            enum_count,
            trait_count,
            avg_type_attributes,
        }
    }
}
