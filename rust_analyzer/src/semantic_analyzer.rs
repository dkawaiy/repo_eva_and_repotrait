use crate::error::{AnalyzerError, Result};
use log::info;
use petgraph::graph::{DiGraph, NodeIndex};
use petgraph::visit::EdgeRef;
use proc_macro2::TokenStream;

use rustc_hash::FxHashMap;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use syn::{
    Expr, ExprCall, ExprMethodCall, File, GenericArgument, ItemFn, ItemImpl, ItemStruct, ItemTrait,
    Macro, Path, PathArguments, Type, visit::Visit,
};

/// 语义分析,提供类型推导、宏展开和trait解析
pub struct SemanticAnalyzer {
    /// 类型推导上下文
    type_context: TypeInferenceContext,
    /// 宏展开器
    macro_expander: MacroExpander,
    /// Trait解析器
    trait_resolver: TraitResolver,
    /// 调用图
    call_graph: CallGraph,
    /// 当前分析的文件
    current_file: Option<PathBuf>,
}

/// 语义分析结果
#[derive(Debug, Clone)]
pub struct SemanticAnalysisResult {
    /// 文件路径
    pub file_path: PathBuf,
    /// 类型定义
    pub type_definitions: Vec<InferredType>,
    /// 函数调用
    pub function_calls: Vec<FunctionCall>,
    /// 宏调用
    pub macro_calls: Vec<MacroCall>,
    /// Trait调用
    pub trait_calls: Vec<TraitCall>,
    /// 异步函数
    pub async_functions: Vec<AsyncFunction>,
    /// Await点
    pub await_points: Vec<AwaitPoint>,
    /// 异步调用图
    pub async_call_graph: AsyncCallGraph,
}

/// 类型推导上下文
#[derive(Debug, Clone)]
pub struct TypeInferenceContext {
    /// 类型定义映射
    type_definitions: FxHashMap<String, TypeDefinition>,
    /// 变量类型映射
    variable_types: FxHashMap<String, InferredType>,
    /// 函数签名映射
    function_signatures: FxHashMap<String, FunctionSignature>,
    /// 泛型参数映射
    generic_params: FxHashMap<String, GenericParameter>,
    /// 作用域栈
    scope_stack: Vec<Scope>,
}

/// 推导的类型信息
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InferredType {
    /// 类型名称
    pub name: String,
    /// 完整类型路径
    pub full_path: String,
    /// 泛型参数
    pub generic_args: Vec<InferredType>,
    /// 是否是引用类型
    pub is_reference: bool,
    /// 是否是可变引用
    pub is_mutable: bool,
    /// 生命周期信息
    pub lifetime: Option<String>,
    /// 置信度 (0.0-1.0)
    pub confidence: f32,
    /// 类型种类
    pub type_kind: TypeKind,
    /// 关联类型信息
    pub associated_types: Vec<AssociatedTypeInfo>,
    /// 高阶类型绑定的生命周期
    pub higher_ranked_lifetimes: Vec<String>,
    /// const泛型参数
    pub const_params: Vec<ConstGenericParam>,
}

/// 关联类型信息
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AssociatedTypeInfo {
    pub trait_name: String,
    pub type_name: String,
    pub resolved_type: Option<Box<InferredType>>,
}

/// Const泛型参数
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConstGenericParam {
    pub name: String,
    pub value: Option<ConstValue>,
    pub param_type: String,
}

/// 常量值
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ConstValue {
    Integer(i64),
    String(String),
    Boolean(bool),
    Usize(usize),
    Float(f64),
}

/// 类型定义
#[derive(Debug, Clone)]
pub struct TypeDefinition {
    /// 类型名称
    pub name: String,
    /// 类型种类
    pub kind: TypeKind,
    /// 字段或变体
    pub fields: Vec<FieldDefinition>,
    /// 方法
    pub methods: Vec<MethodDefinition>,
    /// 泛型参数
    pub generic_params: Vec<GenericParameter>,
    /// 实现的trait
    pub implemented_traits: Vec<String>,
}

/// 类型种类
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum TypeKind {
    Struct,
    Enum,
    Union,
    Trait,
    TypeAlias,
    /// 函数类型
    Function {
        params: Vec<InferredType>,
        return_type: Box<InferredType>,
        is_async: bool,
    },
    /// 元组类型
    Tuple(Vec<InferredType>),
    /// 数组类型
    Array {
        element_type: Box<InferredType>,
        size: Option<usize>,
    },
    /// 切片类型
    Slice(Box<InferredType>),
    /// 引用类型
    Reference {
        inner: Box<InferredType>,
        mutable: bool,
        lifetime: Option<String>,
    },
    /// 指针类型
    Pointer {
        inner: Box<InferredType>,
        mutable: bool,
    },
    /// 泛型参数
    Generic {
        name: String,
        bounds: Vec<TraitBound>,
    },
    /// 关联类型
    AssociatedType {
        trait_name: String,
        type_name: String,
    },
    /// 高阶类型
    HigherRanked {
        lifetimes: Vec<String>,
        inner: Box<InferredType>,
    },
    /// Trait对象
    TraitObject {
        traits: Vec<String>,
        lifetime: Option<String>,
    },
    /// 投影类型
    Projection {
        base: Box<InferredType>,
        associated_type: String,
    },
    /// Never类型
    Never,
    /// 基本类型
    Primitive(String),
}

/// 字段定义
#[derive(Debug, Clone)]
pub struct FieldDefinition {
    pub name: String,
    pub field_type: InferredType,
    pub visibility: Visibility,
}

/// 方法定义
#[derive(Debug, Clone)]
pub struct MethodDefinition {
    pub name: String,
    pub signature: FunctionSignature,
    pub receiver_type: Option<ReceiverType>,
    pub visibility: Visibility,
}

/// 函数签名
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct FunctionSignature {
    pub name: String,
    pub parameters: Vec<Parameter>,
    pub return_type: Option<InferredType>,
    pub generic_params: Vec<GenericParameter>,
    pub where_clause: Vec<WhereClause>,
}

/// 参数定义
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Parameter {
    pub name: String,
    pub param_type: InferredType,
    pub is_self: bool,
}

/// 泛型参数
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GenericParameter {
    pub name: String,
    pub bounds: Vec<TraitBound>,
    pub default: Option<InferredType>,
}

/// Trait约束
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TraitBound {
    pub trait_path: String,
    pub generic_args: Vec<InferredType>,
}

/// Where子句
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct WhereClause {
    pub type_param: String,
    pub bounds: Vec<TraitBound>,
}

/// 接收者类型
#[derive(Debug, Clone, PartialEq)]
pub enum ReceiverType {
    SelfValue,
    SelfRef,
    SelfMutRef,
}

/// 可见性
#[derive(Debug, Clone, PartialEq)]
pub enum Visibility {
    Public,
    Private,
    Crate,
    Super,
    InPath(String),
}

/// 作用域
#[derive(Debug, Clone)]
pub struct Scope {
    /// 作用域中的变量
    variables: FxHashMap<String, InferredType>,
    /// 作用域类型
    scope_type: ScopeType,
}

/// 作用域类型
#[derive(Debug, Clone, PartialEq)]
pub enum ScopeType {
    Function,
    Block,
    Loop,
    Match,
    Closure,
}

/// 宏展开器
pub struct MacroExpander {
    /// 宏定义映射
    macro_definitions: FxHashMap<String, MacroDefinition>,
    /// 展开缓存
    expansion_cache: FxHashMap<String, TokenStream>,
    /// 声明式宏规则
    declarative_macros: FxHashMap<String, DeclarativeMacro>,
    /// 过程宏信息
    procedural_macros: FxHashMap<String, ProceduralMacro>,
    /// 宏展开深度限制
    max_expansion_depth: usize,
    /// 当前展开深度
    current_depth: usize,
}

/// 宏定义
#[derive(Debug, Clone)]
pub struct MacroDefinition {
    pub name: String,
    pub rules: Vec<MacroRule>,
    pub is_proc_macro: bool,
}

/// 声明式宏（macro_rules!）
#[derive(Debug, Clone)]
pub struct DeclarativeMacro {
    pub name: String,
    pub rules: Vec<MacroRule>,
    pub visibility: Visibility,
    pub hygiene_context: HygieneContext,
}

/// 过程宏
#[derive(Debug, Clone)]
pub struct ProceduralMacro {
    pub name: String,
    pub macro_type: ProcMacroType,
    pub crate_name: String,
    pub function_name: String,
}

/// 过程宏类型
#[derive(Debug, Clone, PartialEq)]
pub enum ProcMacroType {
    /// 派生宏 #[derive(MyMacro)]
    Derive,
    /// 属性宏 #[my_macro]
    Attribute,
    /// 函数式宏 my_macro!()
    Function,
}

/// 宏卫生性上下文
#[derive(Debug, Clone)]
pub struct HygieneContext {
    /// 宏定义的作用域
    definition_scope: String,
    /// 宏调用的作用域
    call_scope: Option<String>,
    /// 透明标识符
    transparent_identifiers: Vec<String>,
}

/// 宏规则
#[derive(Debug, Clone)]
pub struct MacroRule {
    pub pattern: TokenStream,
    pub expansion: TokenStream,
}

/// 宏模式
#[derive(Debug, Clone)]
pub enum MacroPattern {
    /// 字面量模式
    Literal(String),
    /// 变量模式
    Variable {
        name: String,
        kind: MacroVariableKind,
    },
    /// 重复模式
    Repetition {
        pattern: Box<MacroPattern>,
        separator: Option<String>,
        min_count: usize,
        max_count: Option<usize>,
    },
    /// 组合模式
    Group(Vec<MacroPattern>),
}

/// 宏变量类型
#[derive(Debug, Clone, PartialEq)]
pub enum MacroVariableKind {
    /// 表达式 $e:expr
    Expression,
    /// 标识符 $i:ident
    Identifier,
    /// 类型 $t:ty
    Type,
    /// 模式 $p:pat
    Pattern,
    /// 语句 $s:stmt
    Statement,
    /// 块 $b:block
    Block,
    /// 项 $i:item
    Item,
    /// 元信息 $m:meta
    Meta,
    /// 路径 $p:path
    Path,
    /// 字面量 $l:literal
    Literal,
    /// 生命周期 $lt:lifetime
    Lifetime,
    /// 可见性 $v:vis
    Visibility,
    /// Token树 $tt:tt
    TokenTree,
}

/// Trait解析器
pub struct TraitResolver {
    /// Trait定义映射
    trait_definitions: FxHashMap<String, TraitDefinition>,
    /// Impl块映射
    impl_blocks: FxHashMap<String, Vec<ImplBlock>>,
    /// Trait对象映射
    trait_objects: FxHashMap<String, TraitObject>,
}

/// Trait定义
#[derive(Debug, Clone)]
pub struct TraitDefinition {
    pub name: String,
    pub methods: Vec<TraitMethod>,
    pub associated_types: Vec<AssociatedType>,
    pub super_traits: Vec<String>,
    pub generic_params: Vec<GenericParameter>,
}

/// Trait方法
#[derive(Debug, Clone)]
pub struct TraitMethod {
    pub name: String,
    pub signature: FunctionSignature,
    pub has_default_impl: bool,
}

/// 关联类型
#[derive(Debug, Clone)]
pub struct AssociatedType {
    pub name: String,
    pub bounds: Vec<TraitBound>,
    pub default: Option<InferredType>,
}

/// Impl块
#[derive(Debug, Clone)]
pub struct ImplBlock {
    pub target_type: InferredType,
    pub trait_path: Option<String>,
    pub methods: Vec<MethodDefinition>,
    pub associated_types: Vec<AssociatedTypeImpl>,
    pub generic_params: Vec<GenericParameter>,
    pub where_clause: Vec<WhereClause>,
}

/// 关联类型实现
#[derive(Debug, Clone)]
pub struct AssociatedTypeImpl {
    pub name: String,
    pub concrete_type: InferredType,
}

/// Trait对象
#[derive(Debug, Clone)]
pub struct TraitObject {
    pub trait_path: String,
    pub generic_args: Vec<InferredType>,
    pub lifetime: Option<String>,
}

/// 调用图
pub struct CallGraph {
    /// 图结构
    graph: DiGraph<CallNode, CallEdge>,
    /// 节点映射
    node_map: FxHashMap<String, NodeIndex>,
}

/// 调用图节点
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallNode {
    pub function_name: String,
    pub signature: FunctionSignature,
    pub location: SourceLocation,
}

/// 调用图边
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallEdge {
    pub call_type: CallType,
    pub location: SourceLocation,
}

/// 调用类型
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum CallType {
    DirectCall,
    MethodCall,
    TraitMethodCall,
    ClosureCall,
    MacroCall,
}

/// 源码位置
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SourceLocation {
    pub file: PathBuf,
    pub line: usize,
    pub column: usize,
}

impl Default for SemanticAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl SemanticAnalyzer {
    /// 创建新的语义分析器
    pub fn new() -> Self {
        Self {
            type_context: TypeInferenceContext::new(),
            macro_expander: MacroExpander::new(),
            trait_resolver: TraitResolver::new(),
            call_graph: CallGraph::new(),
            current_file: None,
        }
    }

    /// 分析文件
    pub fn analyze_file(
        &mut self,
        file_path: &PathBuf,
        syntax_tree: &File,
    ) -> Result<SemanticAnalysisResult> {
        info!("开始语义分析文件: {}", file_path.display());
        self.current_file = Some(file_path.clone());

        // 收集类型定义
        {
            let mut definition_collector = DefinitionCollector::new(&mut self.type_context);
            definition_collector.visit_file(syntax_tree);
        }

        // 分析函数调用和类型推断
        let function_calls = {
            let mut type_inferrer = TypeInferrer::new(&mut self.type_context, &mut self.call_graph);
            type_inferrer.visit_file(syntax_tree);
            type_inferrer.get_function_calls()
        };

        // 分析宏调用
        let macro_calls = self.macro_expander.analyze_macros(syntax_tree)?;

        // 分析trait调用
        let trait_calls = self
            .trait_resolver
            .resolve_trait_calls(syntax_tree, &self.type_context)?;

        // 分析异步函数
        let async_functions = self.analyze_async_functions(syntax_tree)?;

        // 分析await点
        let await_points = self.analyze_await_points(syntax_tree)?;

        // 构建异步调用图
        let async_call_graph = self.build_async_call_graph(&async_functions);

        // 获取类型定义
        let type_definitions = self.type_context.get_all_types();

        Ok(SemanticAnalysisResult {
            file_path: file_path.clone(),
            type_definitions,
            function_calls,
            macro_calls,
            trait_calls,
            async_functions,
            await_points,
            async_call_graph,
        })
    }

    /// 分析异步函数
    pub fn analyze_async_functions(&mut self, file: &syn::File) -> Result<Vec<AsyncFunction>> {
        let mut async_functions = Vec::new();
        let mut async_visitor = AsyncFunctionVisitor::new(&mut async_functions);
        async_visitor.visit_file(file);
        Ok(async_functions)
    }

    /// 分析await点
    pub fn analyze_await_points(&mut self, file: &syn::File) -> Result<Vec<AwaitPoint>> {
        let mut await_points = Vec::new();
        let mut await_visitor = AwaitPointVisitor::new(&mut await_points);
        await_visitor.visit_file(file);
        Ok(await_points)
    }

    /// 构建异步调用图
    pub fn build_async_call_graph(&mut self, async_functions: &[AsyncFunction]) -> AsyncCallGraph {
        let mut graph = AsyncCallGraph::new();

        for func in async_functions {
            graph.add_async_function(func);

            // 分析异步调用关系
            for callee in &func.async_calls {
                graph.add_async_call(&func.name, callee);
            }
        }

        graph
    }
}

/// 函数调用信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionCall {
    pub caller: String,
    pub callee: String,
    pub call_type: CallType,
    pub arguments: Vec<InferredType>,
    pub return_type: Option<InferredType>,
    pub location: SourceLocation,
    pub confidence: f32,
    pub is_async: bool,
    pub await_points: Vec<AwaitPoint>,
}

/// Await点信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AwaitPoint {
    pub location: SourceLocation,
    pub future_type: InferredType,
    pub error_propagation: bool,
}

/// 异步函数信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AsyncFunction {
    pub name: String,
    pub signature: FunctionSignature,
    pub future_type: InferredType,
    pub await_points: Vec<AwaitPoint>,
    pub async_calls: Vec<String>,
    pub error_handling: AsyncErrorHandling,
}

/// 异步错误处理方式
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum AsyncErrorHandling {
    /// 使用 ? 操作符传播错误
    QuestionMark,
    /// 使用 match 处理错误
    Match,
    /// 使用 unwrap/expect
    Unwrap,
    /// 自定义错误处理
    Custom,
}

/// Trait调用信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraitCall {
    pub trait_name: String,
    pub method_name: String,
    pub receiver_type: InferredType,
    pub arguments: Vec<InferredType>,
    pub return_type: Option<InferredType>,
    pub location: SourceLocation,
    pub is_dynamic_dispatch: bool,
}

/// 宏调用信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MacroCall {
    pub macro_name: String,
    pub expansion: String,
    pub generated_calls: Vec<FunctionCall>,
    pub location: SourceLocation,
}

/// 调用图导出格式
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallGraphExport {
    pub nodes: Vec<CallNode>,
    pub edges: Vec<(usize, usize, CallEdge)>,
}

impl Default for TypeInferenceContext {
    fn default() -> Self {
        Self::new()
    }
}

impl TypeInferenceContext {
    pub fn new() -> Self {
        Self {
            type_definitions: FxHashMap::default(),
            variable_types: FxHashMap::default(),
            function_signatures: FxHashMap::default(),
            generic_params: FxHashMap::default(),
            scope_stack: Vec::new(),
        }
    }

    pub fn get_all_types(&self) -> Vec<InferredType> {
        self.variable_types.values().cloned().collect()
    }

    pub fn push_scope(&mut self, scope_type: ScopeType) {
        self.scope_stack.push(Scope {
            variables: FxHashMap::default(),
            scope_type,
        });
    }

    pub fn pop_scope(&mut self) {
        self.scope_stack.pop();
    }

    pub fn add_variable(&mut self, name: String, var_type: InferredType) {
        if let Some(current_scope) = self.scope_stack.last_mut() {
            current_scope
                .variables
                .insert(name.clone(), var_type.clone());
        }
        self.variable_types.insert(name, var_type);
    }

    pub fn get_variable_type(&self, name: &str) -> Option<&InferredType> {
        // 从最内层作用域开始查找
        for scope in self.scope_stack.iter().rev() {
            if let Some(var_type) = scope.variables.get(name) {
                return Some(var_type);
            }
        }
        self.variable_types.get(name)
    }

    pub fn add_type_definition(&mut self, name: String, definition: TypeDefinition) {
        self.type_definitions.insert(name, definition);
    }

    pub fn get_type_definition(&self, name: &str) -> Option<&TypeDefinition> {
        self.type_definitions.get(name)
    }

    pub fn add_function_signature(&mut self, name: String, signature: FunctionSignature) {
        self.function_signatures.insert(name, signature);
    }

    pub fn get_function_signature(&self, name: &str) -> Option<&FunctionSignature> {
        self.function_signatures.get(name)
    }

    /// 解析高阶类型 (Higher-Ranked Types)
    pub fn analyze_higher_ranked_type(&mut self, type_expr: &syn::Type) -> InferredType {
        match type_expr {
            syn::Type::BareFn(bare_fn) => {
                // 处理 for<'a> fn(&'a str) -> &'a str 这样的类型
                let lifetimes: Vec<String> = bare_fn
                    .lifetimes
                    .as_ref()
                    .map(|lt_params| {
                        lt_params
                            .lifetimes
                            .iter()
                            .map(|lt| match lt {
                                syn::GenericParam::Lifetime(lifetime_param) => {
                                    lifetime_param.lifetime.ident.to_string()
                                }
                                _ => "unknown".to_string(),
                            })
                            .collect()
                    })
                    .unwrap_or_default();

                let params = bare_fn
                    .inputs
                    .iter()
                    .map(|arg| self.infer_type_from_syn(&arg.ty))
                    .collect();

                let return_type = match &bare_fn.output {
                    syn::ReturnType::Default => InferredType {
                        name: "()".to_string(),
                        full_path: "()".to_string(),
                        type_kind: TypeKind::Tuple(vec![]),
                        ..Default::default()
                    },
                    syn::ReturnType::Type(_, ty) => self.infer_type_from_syn(ty),
                };

                InferredType {
                    name: "fn".to_string(),
                    full_path: "fn".to_string(),
                    type_kind: TypeKind::Function {
                        params,
                        return_type: Box::new(return_type),
                        is_async: false,
                    },
                    higher_ranked_lifetimes: lifetimes,
                    ..Default::default()
                }
            }
            _ => self.infer_type_from_syn(type_expr),
        }
    }

    /// 解析关联类型投影
    pub fn resolve_associated_type_projection(&mut self, path: &syn::Path) -> Option<InferredType> {
        // 处理 <T as Iterator>::Item 这样的关联类型
        if let Some(segment) = path.segments.last()
            && let syn::PathArguments::AngleBracketed(args) = &segment.arguments
        {
            for arg in &args.args {
                if let syn::GenericArgument::AssocType(assoc_type) = arg {
                    return Some(InferredType {
                        name: assoc_type.ident.to_string(),
                        full_path: format!("{}::{}", segment.ident, assoc_type.ident),
                        type_kind: TypeKind::AssociatedType {
                            trait_name: segment.ident.to_string(),
                            type_name: assoc_type.ident.to_string(),
                        },
                        ..Default::default()
                    });
                }
            }
        }
        None
    }

    /// 分析const泛型参数
    pub fn analyze_const_generic(&mut self, const_param: &syn::ConstParam) -> ConstGenericParam {
        let value = const_param.default.as_ref().and_then(|expr| match expr {
            syn::Expr::Lit(lit) => match &lit.lit {
                syn::Lit::Int(int_lit) => {
                    int_lit.base10_parse::<i64>().ok().map(ConstValue::Integer)
                }
                syn::Lit::Str(str_lit) => Some(ConstValue::String(str_lit.value())),
                syn::Lit::Bool(bool_lit) => Some(ConstValue::Boolean(bool_lit.value)),
                _ => None,
            },
            _ => None,
        });

        ConstGenericParam {
            name: const_param.ident.to_string(),
            value,
            param_type: self.type_to_string(&const_param.ty),
        }
    }

    /// 处理类型别名透明化
    pub fn resolve_type_alias(&mut self, alias_name: &str) -> Option<InferredType> {
        // 查找类型别名定义并返回其实际类型
        if let Some(type_def) = self.type_definitions.get(alias_name) {
            if type_def.kind == TypeKind::TypeAlias {
                // 这里需要从类型定义中提取实际类型
                // 简化实现：返回一个标记为类型别名的类型
                Some(InferredType {
                    name: alias_name.to_string(),
                    full_path: alias_name.to_string(),
                    type_kind: TypeKind::TypeAlias,
                    ..Default::default()
                })
            } else {
                None
            }
        } else {
            None
        }
    }

    /// 从syn::Type推导类型信息
    pub fn infer_type_from_syn(&mut self, ty: &syn::Type) -> InferredType {
        match ty {
            syn::Type::Path(type_path) => self.analyze_type_path(&type_path.path),
            syn::Type::Reference(type_ref) => self.analyze_reference_type(type_ref),
            syn::Type::Tuple(type_tuple) => self.analyze_tuple_type(type_tuple),
            syn::Type::Array(type_array) => self.analyze_array_type(type_array),
            syn::Type::Slice(type_slice) => self.analyze_slice_type(type_slice),
            syn::Type::Ptr(type_ptr) => self.analyze_pointer_type(type_ptr),
            syn::Type::BareFn(_bare_fn) => self.analyze_higher_ranked_type(ty),
            syn::Type::TraitObject(trait_obj) => self.analyze_trait_object(trait_obj),
            syn::Type::Never(_) => InferredType {
                name: "!".to_string(),
                full_path: "!".to_string(),
                type_kind: TypeKind::Never,
                ..Default::default()
            },
            _ => InferredType {
                name: "unknown".to_string(),
                full_path: "unknown".to_string(),
                type_kind: TypeKind::Primitive("unknown".to_string()),
                ..Default::default()
            },
        }
    }

    fn analyze_type_path(&mut self, path: &syn::Path) -> InferredType {
        let name = path.segments.last().unwrap().ident.to_string();
        let full_path = path
            .segments
            .iter()
            .map(|seg| seg.ident.to_string())
            .collect::<Vec<_>>()
            .join("::");

        // 检查是否是关联类型投影
        if let Some(projected) = self.resolve_associated_type_projection(path) {
            return projected;
        }

        InferredType {
            name: name.clone(),
            full_path,
            type_kind: TypeKind::Primitive(name),
            ..Default::default()
        }
    }

    fn analyze_reference_type(&mut self, type_ref: &syn::TypeReference) -> InferredType {
        let inner = self.infer_type_from_syn(&type_ref.elem);
        let lifetime = type_ref.lifetime.as_ref().map(|lt| lt.ident.to_string());

        InferredType {
            name: format!(
                "&{}{}",
                if type_ref.mutability.is_some() {
                    "mut "
                } else {
                    ""
                },
                inner.name
            ),
            full_path: format!(
                "&{}{}",
                if type_ref.mutability.is_some() {
                    "mut "
                } else {
                    ""
                },
                inner.full_path
            ),
            type_kind: TypeKind::Reference {
                inner: Box::new(inner),
                mutable: type_ref.mutability.is_some(),
                lifetime,
            },
            is_reference: true,
            is_mutable: type_ref.mutability.is_some(),
            ..Default::default()
        }
    }

    fn analyze_tuple_type(&mut self, type_tuple: &syn::TypeTuple) -> InferredType {
        let elements: Vec<InferredType> = type_tuple
            .elems
            .iter()
            .map(|elem| self.infer_type_from_syn(elem))
            .collect();

        let name = format!(
            "({})",
            elements
                .iter()
                .map(|t| &t.name)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        );

        InferredType {
            name: name.clone(),
            full_path: name,
            type_kind: TypeKind::Tuple(elements),
            ..Default::default()
        }
    }

    fn analyze_array_type(&mut self, type_array: &syn::TypeArray) -> InferredType {
        let element_type = self.infer_type_from_syn(&type_array.elem);
        let size = match &type_array.len {
            syn::Expr::Lit(lit) => {
                if let syn::Lit::Int(int_lit) = &lit.lit {
                    int_lit.base10_parse::<usize>().ok()
                } else {
                    None
                }
            }
            _ => None,
        };

        InferredType {
            name: format!(
                "[{}; {}]",
                element_type.name,
                size.map_or("N".to_string(), |s| s.to_string())
            ),
            full_path: format!(
                "[{}; {}]",
                element_type.full_path,
                size.map_or("N".to_string(), |s| s.to_string())
            ),
            type_kind: TypeKind::Array {
                element_type: Box::new(element_type),
                size,
            },
            ..Default::default()
        }
    }

    fn analyze_slice_type(&mut self, type_slice: &syn::TypeSlice) -> InferredType {
        let element_type = self.infer_type_from_syn(&type_slice.elem);

        InferredType {
            name: format!("[{}]", element_type.name),
            full_path: format!("[{}]", element_type.full_path),
            type_kind: TypeKind::Slice(Box::new(element_type)),
            ..Default::default()
        }
    }

    fn analyze_pointer_type(&mut self, type_ptr: &syn::TypePtr) -> InferredType {
        let inner = self.infer_type_from_syn(&type_ptr.elem);
        let mutable = type_ptr.mutability.is_some();

        InferredType {
            name: format!("*{} {}", if mutable { "mut" } else { "const" }, inner.name),
            full_path: format!(
                "*{} {}",
                if mutable { "mut" } else { "const" },
                inner.full_path
            ),
            type_kind: TypeKind::Pointer {
                inner: Box::new(inner),
                mutable,
            },
            ..Default::default()
        }
    }

    fn analyze_trait_object(&mut self, trait_obj: &syn::TypeTraitObject) -> InferredType {
        let traits: Vec<String> = trait_obj
            .bounds
            .iter()
            .filter_map(|bound| {
                if let syn::TypeParamBound::Trait(trait_bound) = bound {
                    Some(trait_bound.path.segments.last()?.ident.to_string())
                } else {
                    None
                }
            })
            .collect();

        let lifetime = trait_obj.bounds.iter().find_map(|bound| {
            if let syn::TypeParamBound::Lifetime(lt) = bound {
                Some(lt.ident.to_string())
            } else {
                None
            }
        });

        InferredType {
            name: format!("dyn {}", traits.join(" + ")),
            full_path: format!("dyn {}", traits.join(" + ")),
            type_kind: TypeKind::TraitObject { traits, lifetime },
            ..Default::default()
        }
    }

    fn type_to_string(&self, ty: &syn::Type) -> String {
        match ty {
            syn::Type::Path(path) => path.path.segments.last().unwrap().ident.to_string(),
            syn::Type::Reference(r) => format!("&{}", self.type_to_string(&r.elem)),
            _ => "unknown".to_string(),
        }
    }
}

impl Default for MacroExpander {
    fn default() -> Self {
        Self::new()
    }
}

impl MacroExpander {
    pub fn new() -> Self {
        Self {
            macro_definitions: FxHashMap::default(),
            expansion_cache: FxHashMap::default(),
            declarative_macros: FxHashMap::default(),
            procedural_macros: FxHashMap::default(),
            max_expansion_depth: 128,
            current_depth: 0,
        }
    }

    pub fn analyze_macros(&mut self, syntax_tree: &File) -> Result<Vec<MacroCall>> {
        let mut macro_calls = Vec::new();
        let mut visitor = MacroVisitor::new(&mut macro_calls, self);
        visitor.visit_file(syntax_tree);
        Ok(macro_calls)
    }

    pub fn expand_macro(&mut self, macro_call: &Macro) -> Result<TokenStream> {
        let macro_name = macro_call
            .path
            .segments
            .last()
            .ok_or_else(|| AnalyzerError::SemanticError("Invalid macro path".to_string()))?
            .ident
            .to_string();

        // 检查缓存
        let cache_key = format!("{}:{}", macro_name, macro_call.tokens);
        if let Some(cached) = self.expansion_cache.get(&cache_key) {
            return Ok(cached.clone());
        }

        // 检查展开深度
        if self.current_depth >= self.max_expansion_depth {
            return Err(AnalyzerError::SemanticError(
                "Macro expansion depth exceeded".to_string(),
            ));
        }

        // 尝试展开宏
        let expanded = self
            .try_expand_declarative_macro(&macro_name, &macro_call.tokens)
            .or_else(|| self.try_expand_procedural_macro(&macro_name, &macro_call.tokens))
            .or_else(|| self.try_expand_builtin_macro(&macro_name, &macro_call.tokens))
            .or_else(|| self.try_expand_custom_macro(&macro_name, &macro_call.tokens))
            .unwrap_or_else(|| macro_call.tokens.clone());

        // 缓存结果
        self.expansion_cache.insert(cache_key, expanded.clone());
        Ok(expanded)
    }

    fn try_expand_builtin_macro(&self, name: &str, tokens: &TokenStream) -> Option<TokenStream> {
        match name {
            "println" | "print" | "eprintln" | "eprint" => {
                // 简化的内置宏展开
                Some(quote::quote! { std::io::_print(format_args!(#tokens)) })
            }
            "vec" => {
                // vec!宏的简化展开
                Some(quote::quote! {
                    {
                        let mut temp_vec = Vec::new();
                        temp_vec.extend_from_slice(&[#tokens]);
                        temp_vec
                    }
                })
            }
            "format" => Some(quote::quote! { std::fmt::format(format_args!(#tokens)) }),
            _ => None,
        }
    }

    fn try_expand_declarative_macro(
        &self,
        name: &str,
        tokens: &TokenStream,
    ) -> Option<TokenStream> {
        if let Some(macro_def) = self.declarative_macros.get(name) {
            self.expand_declarative_macro_rules(macro_def, tokens)
        } else {
            None
        }
    }

    fn try_expand_procedural_macro(&self, name: &str, tokens: &TokenStream) -> Option<TokenStream> {
        if let Some(proc_macro) = self.procedural_macros.get(name) {
            self.expand_procedural_macro(proc_macro, tokens)
        } else {
            None
        }
    }

    fn expand_declarative_macro_rules(
        &self,
        macro_def: &DeclarativeMacro,
        input: &TokenStream,
    ) -> Option<TokenStream> {
        // 尝试匹配每个宏规则
        for rule in &macro_def.rules {
            if let Some(expanded) =
                self.try_match_and_expand_rule(rule, input, &macro_def.hygiene_context)
            {
                return Some(expanded);
            }
        }
        None
    }

    fn try_match_and_expand_rule(
        &self,
        rule: &MacroRule,
        input: &TokenStream,
        _hygiene: &HygieneContext,
    ) -> Option<TokenStream> {
        // 简化的模式匹配和展开
        // 实际实现需要完整的宏模式匹配器

        // 基本的字面量匹配
        if input.to_string().trim() == rule.pattern.to_string().trim() {
            Some(rule.expansion.clone())
        } else {
            // 尝试变量替换
            self.try_variable_substitution(rule, input)
        }
    }

    fn try_variable_substitution(
        &self,
        rule: &MacroRule,
        input: &TokenStream,
    ) -> Option<TokenStream> {
        // 简化的变量替换逻辑
        // 在实际实现中需要完整的模式匹配
        let pattern_str = rule.pattern.to_string();
        let _input_str = input.to_string();

        // 如果模式包含变量占位符，尝试替换
        if pattern_str.contains("$") {
            let expansion = rule.expansion.clone();
            // 这里需要实现完整的变量捕获和替换逻辑
            Some(expansion)
        } else {
            None
        }
    }

    fn expand_procedural_macro(
        &self,
        proc_macro: &ProceduralMacro,
        tokens: &TokenStream,
    ) -> Option<TokenStream> {
        match proc_macro.macro_type {
            ProcMacroType::Derive => self.expand_derive_macro(proc_macro, tokens),
            ProcMacroType::Attribute => self.expand_attribute_macro(proc_macro, tokens),
            ProcMacroType::Function => self.expand_function_macro(proc_macro, tokens),
        }
    }

    fn expand_derive_macro(
        &self,
        proc_macro: &ProceduralMacro,
        tokens: &TokenStream,
    ) -> Option<TokenStream> {
        // 简化的派生宏展开
        match proc_macro.name.as_str() {
            "Debug" => self.generate_debug_impl(tokens),
            "Clone" => self.generate_clone_impl(tokens),
            "PartialEq" => self.generate_partial_eq_impl(tokens),
            _ => None,
        }
    }

    fn generate_debug_impl(&self, tokens: &TokenStream) -> Option<TokenStream> {
        // 解析结构体定义并生成Debug实现
        // 这是一个简化版本
        if let Ok(item_struct) = syn::parse2::<syn::ItemStruct>(tokens.clone()) {
            let struct_name = &item_struct.ident;
            Some(quote::quote! {
                impl std::fmt::Debug for #struct_name {
                    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                        f.debug_struct(stringify!(#struct_name)).finish()
                    }
                }
            })
        } else {
            None
        }
    }

    fn generate_clone_impl(&self, tokens: &TokenStream) -> Option<TokenStream> {
        if let Ok(item_struct) = syn::parse2::<syn::ItemStruct>(tokens.clone()) {
            let struct_name = &item_struct.ident;
            Some(quote::quote! {
                impl Clone for #struct_name {
                    fn clone(&self) -> Self {
                        Self { ..*self }
                    }
                }
            })
        } else {
            None
        }
    }

    fn generate_partial_eq_impl(&self, tokens: &TokenStream) -> Option<TokenStream> {
        if let Ok(item_struct) = syn::parse2::<syn::ItemStruct>(tokens.clone()) {
            let struct_name = &item_struct.ident;
            Some(quote::quote! {
                impl PartialEq for #struct_name {
                    fn eq(&self, other: &Self) -> bool {
                        true // 简化实现
                    }
                }
            })
        } else {
            None
        }
    }

    fn expand_attribute_macro(
        &self,
        _proc_macro: &ProceduralMacro,
        tokens: &TokenStream,
    ) -> Option<TokenStream> {
        // 属性宏通常修改现有代码
        Some(tokens.clone())
    }

    fn expand_function_macro(
        &self,
        _proc_macro: &ProceduralMacro,
        _tokens: &TokenStream,
    ) -> Option<TokenStream> {
        // 函数式过程宏的展开
        None
    }

    fn try_expand_custom_macro(&self, name: &str, _tokens: &TokenStream) -> Option<TokenStream> {
        if let Some(macro_def) = self.macro_definitions.get(name) {
            // 简化的自定义宏展开
            // 在实际实现中，这里需要完整的宏匹配和展开逻辑
            macro_def.rules.first().map(|rule| rule.expansion.clone())
        } else {
            None
        }
    }
}

impl Default for TraitResolver {
    fn default() -> Self {
        Self::new()
    }
}

impl TraitResolver {
    pub fn new() -> Self {
        Self {
            trait_definitions: FxHashMap::default(),
            impl_blocks: FxHashMap::default(),
            trait_objects: FxHashMap::default(),
        }
    }

    pub fn resolve_trait_calls(
        &self,
        syntax_tree: &File,
        type_context: &TypeInferenceContext,
    ) -> Result<Vec<TraitCall>> {
        let mut trait_calls = Vec::new();
        let mut visitor = TraitCallVisitor::new(&mut trait_calls, self, type_context);
        visitor.visit_file(syntax_tree);
        Ok(trait_calls)
    }

    pub fn resolve_method_call(
        &self,
        receiver_type: &InferredType,
        method_name: &str,
        type_context: &TypeInferenceContext,
    ) -> Option<TraitCall> {
        // 查找直接实现的方法
        if let Some(type_def) = type_context.get_type_definition(&receiver_type.name) {
            for method in &type_def.methods {
                if method.name == method_name {
                    return Some(TraitCall {
                        trait_name: "inherent".to_string(),
                        method_name: method_name.to_string(),
                        receiver_type: receiver_type.clone(),
                        arguments: Vec::new(),
                        return_type: method.signature.return_type.clone(),
                        location: SourceLocation {
                            file: PathBuf::new(),
                            line: 0,
                            column: 0,
                        },
                        is_dynamic_dispatch: false,
                    });
                }
            }
        }

        // 查找trait实现的方法
        if let Some(impl_blocks) = self.impl_blocks.get(&receiver_type.name) {
            for impl_block in impl_blocks {
                if let Some(trait_path) = &impl_block.trait_path {
                    for method in &impl_block.methods {
                        if method.name == method_name {
                            return Some(TraitCall {
                                trait_name: trait_path.clone(),
                                method_name: method_name.to_string(),
                                receiver_type: receiver_type.clone(),
                                arguments: Vec::new(),
                                return_type: method.signature.return_type.clone(),
                                location: SourceLocation {
                                    file: PathBuf::new(),
                                    line: 0,
                                    column: 0,
                                },
                                is_dynamic_dispatch: self.is_dynamic_dispatch(trait_path),
                            });
                        }
                    }
                }
            }
        }

        None
    }

    fn is_dynamic_dispatch(&self, trait_path: &str) -> bool {
        // 简化的动态分发检测
        // 在实际实现中，需要更复杂的分析
        trait_path.contains("dyn ") || trait_path.starts_with("Box<dyn ")
    }
}

impl Default for CallGraph {
    fn default() -> Self {
        Self::new()
    }
}

impl CallGraph {
    pub fn new() -> Self {
        Self {
            graph: DiGraph::new(),
            node_map: FxHashMap::default(),
        }
    }

    pub fn add_node(
        &mut self,
        function_name: String,
        signature: FunctionSignature,
        location: SourceLocation,
    ) -> NodeIndex {
        if let Some(&existing_index) = self.node_map.get(&function_name) {
            return existing_index;
        }

        let node = CallNode {
            function_name: function_name.clone(),
            signature,
            location,
        };
        let index = self.graph.add_node(node);
        self.node_map.insert(function_name, index);
        index
    }

    pub fn add_edge(
        &mut self,
        caller: NodeIndex,
        callee: NodeIndex,
        call_type: CallType,
        location: SourceLocation,
    ) {
        let edge = CallEdge {
            call_type,
            location,
        };
        self.graph.add_edge(caller, callee, edge);
    }

    pub fn export(&self) -> CallGraphExport {
        let nodes = self.graph.node_weights().cloned().collect();
        let edges = self
            .graph
            .edge_references()
            .map(|edge| {
                (
                    edge.source().index(),
                    edge.target().index(),
                    edge.weight().clone(),
                )
            })
            .collect();

        CallGraphExport { nodes, edges }
    }
}

/// 定义收集器 - 第一遍遍历，收集所有类型和函数定义
struct DefinitionCollector<'a> {
    type_context: &'a mut TypeInferenceContext,
    current_impl_target: Option<String>,
}

impl<'a> DefinitionCollector<'a> {
    fn new(type_context: &'a mut TypeInferenceContext) -> Self {
        Self {
            type_context,
            current_impl_target: None,
        }
    }
}

impl<'a> Visit<'a> for DefinitionCollector<'a> {
    fn visit_item_struct(&mut self, item_struct: &'a ItemStruct) {
        let struct_name = item_struct.ident.to_string();

        let fields = item_struct
            .fields
            .iter()
            .map(|field| FieldDefinition {
                name: field
                    .ident
                    .as_ref()
                    .map(|i| i.to_string())
                    .unwrap_or_default(),
                field_type: self.type_from_syn_type(&field.ty),
                visibility: self.visibility_from_syn(&field.vis),
            })
            .collect();

        let type_def = TypeDefinition {
            name: struct_name.clone(),
            kind: TypeKind::Struct,
            fields,
            methods: Vec::new(),
            generic_params: self.extract_generic_params(&item_struct.generics),
            implemented_traits: Vec::new(),
        };

        self.type_context.add_type_definition(struct_name, type_def);
        syn::visit::visit_item_struct(self, item_struct);
    }

    fn visit_item_fn(&mut self, item_fn: &'a ItemFn) {
        let function_name = item_fn.sig.ident.to_string();
        let signature = self.function_signature_from_syn(&item_fn.sig);

        self.type_context
            .add_function_signature(function_name, signature);
        syn::visit::visit_item_fn(self, item_fn);
    }

    fn visit_item_trait(&mut self, item_trait: &'a ItemTrait) {
        let trait_name = item_trait.ident.to_string();

        let methods = item_trait
            .items
            .iter()
            .filter_map(|item| {
                if let syn::TraitItem::Fn(method) = item {
                    Some(TraitMethod {
                        name: method.sig.ident.to_string(),
                        signature: self.function_signature_from_syn(&method.sig),
                        has_default_impl: method.default.is_some(),
                    })
                } else {
                    None
                }
            })
            .collect();

        let _trait_def = TraitDefinition {
            name: trait_name,
            methods,
            associated_types: Vec::new(),
            super_traits: Vec::new(),
            generic_params: self.extract_generic_params(&item_trait.generics),
        };

        // 这里需要将trait定义添加到trait_resolver中
        // 但由于借用检查器的限制，我们暂时跳过
        syn::visit::visit_item_trait(self, item_trait);
    }

    fn visit_item_impl(&mut self, item_impl: &'a ItemImpl) {
        if let Type::Path(type_path) = &*item_impl.self_ty {
            let target_type_name = type_path
                .path
                .segments
                .last()
                .map(|seg| seg.ident.to_string())
                .unwrap_or_default();

            self.current_impl_target = Some(target_type_name);
        }

        syn::visit::visit_item_impl(self, item_impl);
        self.current_impl_target = None;
    }
}

impl<'a> DefinitionCollector<'a> {
    fn type_from_syn_type(&self, ty: &Type) -> InferredType {
        match ty {
            Type::Path(type_path) => {
                let name = type_path
                    .path
                    .segments
                    .last()
                    .map(|seg| seg.ident.to_string())
                    .unwrap_or_default();

                InferredType {
                    name: name.clone(),
                    full_path: self.path_to_string(&type_path.path),
                    generic_args: self.extract_generic_args(&type_path.path),
                    is_reference: false,
                    is_mutable: false,
                    lifetime: None,
                    confidence: 1.0,
                    type_kind: TypeKind::Primitive(name),
                    associated_types: Vec::new(),
                    higher_ranked_lifetimes: Vec::new(),
                    const_params: Vec::new(),
                }
            }
            Type::Reference(type_ref) => {
                let mut inner_type = self.type_from_syn_type(&type_ref.elem);
                inner_type.is_reference = true;
                inner_type.is_mutable = type_ref.mutability.is_some();
                inner_type.lifetime = type_ref.lifetime.as_ref().map(|lt| lt.ident.to_string());
                inner_type
            }
            _ => InferredType {
                name: "unknown".to_string(),
                full_path: "unknown".to_string(),
                generic_args: Vec::new(),
                is_reference: false,
                is_mutable: false,
                lifetime: None,
                confidence: 0.0,
                type_kind: TypeKind::Primitive("unknown".to_string()),
                associated_types: Vec::new(),
                higher_ranked_lifetimes: Vec::new(),
                const_params: Vec::new(),
            },
        }
    }

    fn path_to_string(&self, path: &Path) -> String {
        path.segments
            .iter()
            .map(|seg| seg.ident.to_string())
            .collect::<Vec<_>>()
            .join("::")
    }

    fn extract_generic_args(&self, path: &Path) -> Vec<InferredType> {
        path.segments
            .last()
            .and_then(|seg| {
                if let PathArguments::AngleBracketed(args) = &seg.arguments {
                    Some(
                        args.args
                            .iter()
                            .filter_map(|arg| {
                                if let GenericArgument::Type(ty) = arg {
                                    Some(self.type_from_syn_type(ty))
                                } else {
                                    None
                                }
                            })
                            .collect(),
                    )
                } else {
                    None
                }
            })
            .unwrap_or_default()
    }

    fn extract_generic_params(&self, generics: &syn::Generics) -> Vec<GenericParameter> {
        generics
            .params
            .iter()
            .filter_map(|param| {
                if let syn::GenericParam::Type(type_param) = param {
                    Some(GenericParameter {
                        name: type_param.ident.to_string(),
                        bounds: Vec::new(), // 简化实现
                        default: None,
                    })
                } else {
                    None
                }
            })
            .collect()
    }

    fn function_signature_from_syn(&self, sig: &syn::Signature) -> FunctionSignature {
        let parameters = sig
            .inputs
            .iter()
            .map(|input| match input {
                syn::FnArg::Receiver(receiver) => Parameter {
                    name: "self".to_string(),
                    param_type: InferredType {
                        name: "Self".to_string(),
                        full_path: "Self".to_string(),
                        generic_args: Vec::new(),
                        is_reference: receiver.reference.is_some(),
                        is_mutable: receiver.mutability.is_some(),
                        lifetime: None,
                        confidence: 1.0,
                        type_kind: TypeKind::Primitive("Self".to_string()),
                        associated_types: Vec::new(),
                        higher_ranked_lifetimes: Vec::new(),
                        const_params: Vec::new(),
                    },
                    is_self: true,
                },
                syn::FnArg::Typed(typed) => {
                    let name = if let syn::Pat::Ident(ident) = &*typed.pat {
                        ident.ident.to_string()
                    } else {
                        "unknown".to_string()
                    };

                    Parameter {
                        name,
                        param_type: self.type_from_syn_type(&typed.ty),
                        is_self: false,
                    }
                }
            })
            .collect();

        let return_type = match &sig.output {
            syn::ReturnType::Default => None,
            syn::ReturnType::Type(_, ty) => Some(self.type_from_syn_type(ty)),
        };

        FunctionSignature {
            name: sig.ident.to_string(),
            parameters,
            return_type,
            generic_params: self.extract_generic_params(&sig.generics),
            where_clause: Vec::new(),
        }
    }

    fn visibility_from_syn(&self, vis: &syn::Visibility) -> Visibility {
        match vis {
            syn::Visibility::Public(_) => Visibility::Public,
            syn::Visibility::Restricted(restricted) => {
                if restricted.path.is_ident("crate") {
                    Visibility::Crate
                } else if restricted.path.is_ident("super") {
                    Visibility::Super
                } else {
                    Visibility::InPath(self.path_to_string(&restricted.path))
                }
            }
            syn::Visibility::Inherited => Visibility::Private,
        }
    }
}

/// 类型推导器 - 第二遍遍历，进行类型推导和调用关系分析
struct TypeInferrer<'a> {
    type_context: &'a mut TypeInferenceContext,
    call_graph: &'a mut CallGraph,
    function_calls: Vec<FunctionCall>,
    current_function: Option<String>,
}

impl<'a> TypeInferrer<'a> {
    fn new(type_context: &'a mut TypeInferenceContext, call_graph: &'a mut CallGraph) -> Self {
        Self {
            type_context,
            call_graph,
            function_calls: Vec::new(),
            current_function: None,
        }
    }

    fn get_function_calls(self) -> Vec<FunctionCall> {
        self.function_calls
    }
}

impl<'a> Visit<'a> for TypeInferrer<'a> {
    fn visit_item_fn(&mut self, item_fn: &'a ItemFn) {
        let function_name = item_fn.sig.ident.to_string();
        self.current_function = Some(function_name.clone());

        // 为函数创建新的作用域
        self.type_context.push_scope(ScopeType::Function);

        // 添加参数到作用域
        for input in &item_fn.sig.inputs {
            match input {
                syn::FnArg::Receiver(_) => {
                    // self参数
                    let self_type = InferredType {
                        name: "Self".to_string(),
                        full_path: "Self".to_string(),
                        generic_args: Vec::new(),
                        is_reference: false,
                        is_mutable: false,
                        lifetime: None,
                        confidence: 1.0,
                        type_kind: TypeKind::Primitive("Self".to_string()),
                        associated_types: Vec::new(),
                        higher_ranked_lifetimes: Vec::new(),
                        const_params: Vec::new(),
                    };
                    self.type_context
                        .add_variable("self".to_string(), self_type);
                }
                syn::FnArg::Typed(typed) => {
                    if let syn::Pat::Ident(ident) = &*typed.pat {
                        let param_name = ident.ident.to_string();
                        let param_type = self.infer_type_from_syn(&typed.ty);
                        self.type_context.add_variable(param_name, param_type);
                    }
                }
            }
        }

        syn::visit::visit_item_fn(self, item_fn);

        self.type_context.pop_scope();
        self.current_function = None;
    }

    fn visit_expr_call(&mut self, call: &'a ExprCall) {
        if let Some(current_fn) = &self.current_function {
            let callee_name = self.extract_function_name_from_expr(&call.func);

            // 推导参数类型
            let argument_types: Vec<InferredType> = call
                .args
                .iter()
                .map(|arg| self.infer_type_from_expr(arg))
                .collect();

            // 推导返回类型
            let return_type = self.infer_return_type(&callee_name, &argument_types);

            let function_call = FunctionCall {
                caller: current_fn.clone(),
                callee: callee_name,
                call_type: CallType::DirectCall,
                arguments: argument_types,
                return_type,
                location: SourceLocation {
                    file: PathBuf::new(), // 需要从span获取
                    line: 0,
                    column: 0,
                },
                confidence: 0.8,
                is_async: false,
                await_points: Vec::new(),
            };

            self.function_calls.push(function_call);
        }

        syn::visit::visit_expr_call(self, call);
    }

    fn visit_expr_method_call(&mut self, method_call: &'a ExprMethodCall) {
        if let Some(current_fn) = &self.current_function {
            let receiver_type = self.infer_type_from_expr(&method_call.receiver);
            let method_name = method_call.method.to_string();

            // 推导参数类型
            let argument_types: Vec<InferredType> = method_call
                .args
                .iter()
                .map(|arg| self.infer_type_from_expr(arg))
                .collect();

            // 构造完整的方法调用名称
            let callee_name = format!("{}::{}", receiver_type.name, method_name);

            let function_call = FunctionCall {
                caller: current_fn.clone(),
                callee: callee_name,
                call_type: CallType::MethodCall,
                arguments: argument_types,
                return_type: None, // 需要通过trait解析来确定
                location: SourceLocation {
                    file: PathBuf::new(),
                    line: 0,
                    column: 0,
                },
                confidence: 0.7,
                is_async: false,
                await_points: Vec::new(),
            };

            self.function_calls.push(function_call);
        }

        syn::visit::visit_expr_method_call(self, method_call);
    }

    fn visit_local(&mut self, local: &'a syn::Local) {
        if let syn::Pat::Ident(ident) = &local.pat {
            let var_name = ident.ident.to_string();

            let var_type = if let Some(init) = &local.init {
                // 从初始化表达式推导类型
                self.infer_type_from_expr(&init.expr)
            } else {
                // 无法推导的类型
                InferredType {
                    name: "unknown".to_string(),
                    full_path: "unknown".to_string(),
                    generic_args: Vec::new(),
                    is_reference: false,
                    is_mutable: ident.mutability.is_some(),
                    lifetime: None,
                    confidence: 0.0,
                    type_kind: TypeKind::Primitive("unknown".to_string()),
                    associated_types: Vec::new(),
                    higher_ranked_lifetimes: Vec::new(),
                    const_params: Vec::new(),
                }
            };

            self.type_context.add_variable(var_name, var_type);
        }

        syn::visit::visit_local(self, local);
    }
}

impl<'a> TypeInferrer<'a> {
    fn infer_type_from_syn(&self, ty: &Type) -> InferredType {
        match ty {
            Type::Path(type_path) => {
                let name = type_path
                    .path
                    .segments
                    .last()
                    .map(|seg| seg.ident.to_string())
                    .unwrap_or_default();

                InferredType {
                    name: name.clone(),
                    full_path: self.path_to_string(&type_path.path),
                    generic_args: self.extract_generic_args(&type_path.path),
                    is_reference: false,
                    is_mutable: false,
                    lifetime: None,
                    confidence: 1.0,
                    type_kind: TypeKind::Primitive(name),
                    associated_types: Vec::new(),
                    higher_ranked_lifetimes: Vec::new(),
                    const_params: Vec::new(),
                }
            }
            Type::Reference(type_ref) => {
                let mut inner_type = self.infer_type_from_syn(&type_ref.elem);
                inner_type.is_reference = true;
                inner_type.is_mutable = type_ref.mutability.is_some();
                inner_type.lifetime = type_ref.lifetime.as_ref().map(|lt| lt.ident.to_string());
                inner_type
            }
            _ => InferredType {
                name: "unknown".to_string(),
                full_path: "unknown".to_string(),
                generic_args: Vec::new(),
                is_reference: false,
                is_mutable: false,
                lifetime: None,
                confidence: 0.0,
                type_kind: TypeKind::Primitive("unknown".to_string()),
                associated_types: Vec::new(),
                higher_ranked_lifetimes: Vec::new(),
                const_params: Vec::new(),
            },
        }
    }

    fn infer_type_from_expr(&self, expr: &Expr) -> InferredType {
        match expr {
            Expr::Path(path) => {
                let name = path
                    .path
                    .segments
                    .last()
                    .map(|seg| seg.ident.to_string())
                    .unwrap_or_default();

                // 尝试从变量类型映射中查找
                if let Some(var_type) = self.type_context.get_variable_type(&name) {
                    var_type.clone()
                } else {
                    InferredType {
                        name: name.clone(),
                        full_path: self.path_to_string(&path.path),
                        generic_args: Vec::new(),
                        is_reference: false,
                        is_mutable: false,
                        lifetime: None,
                        confidence: 0.5,
                        type_kind: TypeKind::Primitive(name),
                        associated_types: Vec::new(),
                        higher_ranked_lifetimes: Vec::new(),
                        const_params: Vec::new(),
                    }
                }
            }
            Expr::Lit(lit) => self.infer_type_from_literal(&lit.lit),
            Expr::Call(call) => {
                let function_name = self.extract_function_name_from_expr(&call.func);
                if let Some(signature) = self.type_context.get_function_signature(&function_name) {
                    signature
                        .return_type
                        .clone()
                        .unwrap_or_else(|| InferredType {
                            name: "()".to_string(),
                            full_path: "()".to_string(),
                            generic_args: Vec::new(),
                            is_reference: false,
                            is_mutable: false,
                            lifetime: None,
                            confidence: 0.8,
                            type_kind: TypeKind::Tuple(Vec::new()),
                            associated_types: Vec::new(),
                            higher_ranked_lifetimes: Vec::new(),
                            const_params: Vec::new(),
                        })
                } else {
                    InferredType {
                        name: "unknown".to_string(),
                        full_path: "unknown".to_string(),
                        generic_args: Vec::new(),
                        is_reference: false,
                        is_mutable: false,
                        lifetime: None,
                        confidence: 0.3,
                        type_kind: TypeKind::Primitive("unknown".to_string()),
                        associated_types: Vec::new(),
                        higher_ranked_lifetimes: Vec::new(),
                        const_params: Vec::new(),
                    }
                }
            }
            _ => InferredType {
                name: "unknown".to_string(),
                full_path: "unknown".to_string(),
                generic_args: Vec::new(),
                is_reference: false,
                is_mutable: false,
                lifetime: None,
                confidence: 0.1,
                type_kind: TypeKind::Primitive("unknown".to_string()),
                associated_types: Vec::new(),
                higher_ranked_lifetimes: Vec::new(),
                const_params: Vec::new(),
            },
        }
    }

    fn infer_type_from_literal(&self, lit: &syn::Lit) -> InferredType {
        let (name, confidence) = match lit {
            syn::Lit::Str(_) => ("&str", 1.0),
            syn::Lit::ByteStr(_) => ("&[u8]", 1.0),
            syn::Lit::Byte(_) => ("u8", 1.0),
            syn::Lit::Char(_) => ("char", 1.0),
            syn::Lit::Int(_) => ("i32", 0.8), // 可能是其他整数类型
            syn::Lit::Float(_) => ("f64", 0.8), // 可能是f32
            syn::Lit::Bool(_) => ("bool", 1.0),
            syn::Lit::Verbatim(_) => ("unknown", 0.0),
            _ => ("unknown", 0.0),
        };

        InferredType {
            name: name.to_string(),
            full_path: name.to_string(),
            generic_args: Vec::new(),
            is_reference: name.starts_with('&'),
            is_mutable: false,
            lifetime: None,
            confidence,
            type_kind: TypeKind::Primitive(name.to_string()),
            associated_types: Vec::new(),
            higher_ranked_lifetimes: Vec::new(),
            const_params: Vec::new(),
        }
    }

    fn extract_function_name_from_expr(&self, expr: &Expr) -> String {
        match expr {
            Expr::Path(path) => self.path_to_string(&path.path),
            _ => "unknown".to_string(),
        }
    }

    fn infer_return_type(
        &self,
        function_name: &str,
        _args: &[InferredType],
    ) -> Option<InferredType> {
        if let Some(signature) = self.type_context.get_function_signature(function_name) {
            signature.return_type.clone()
        } else {
            None
        }
    }

    fn path_to_string(&self, path: &Path) -> String {
        path.segments
            .iter()
            .map(|seg| seg.ident.to_string())
            .collect::<Vec<_>>()
            .join("::")
    }

    fn extract_generic_args(&self, path: &Path) -> Vec<InferredType> {
        path.segments
            .last()
            .and_then(|seg| {
                if let PathArguments::AngleBracketed(args) = &seg.arguments {
                    Some(
                        args.args
                            .iter()
                            .filter_map(|arg| {
                                if let GenericArgument::Type(ty) = arg {
                                    Some(self.infer_type_from_syn(ty))
                                } else {
                                    None
                                }
                            })
                            .collect(),
                    )
                } else {
                    None
                }
            })
            .unwrap_or_default()
    }
}

/// 宏访问者 - 分析宏调用
struct MacroVisitor<'a> {
    macro_calls: &'a mut Vec<MacroCall>,
    macro_expander: &'a mut MacroExpander,
}

impl<'a> MacroVisitor<'a> {
    fn new(macro_calls: &'a mut Vec<MacroCall>, macro_expander: &'a mut MacroExpander) -> Self {
        Self {
            macro_calls,
            macro_expander,
        }
    }
}

impl<'a> Visit<'a> for MacroVisitor<'a> {
    fn visit_macro(&mut self, mac: &'a Macro) {
        let macro_name = mac
            .path
            .segments
            .last()
            .map(|seg| seg.ident.to_string())
            .unwrap_or_default();

        // 尝试展开宏
        if let Ok(expanded) = self.macro_expander.expand_macro(mac) {
            // 分析展开后的代码中的函数调用
            let generated_calls = self.analyze_expanded_tokens(&expanded);

            let macro_call = MacroCall {
                macro_name,
                expansion: expanded.to_string(),
                generated_calls,
                location: SourceLocation {
                    file: PathBuf::new(),
                    line: 0,
                    column: 0,
                },
            };

            self.macro_calls.push(macro_call);
        }

        syn::visit::visit_macro(self, mac);
    }
}

impl<'a> MacroVisitor<'a> {
    fn analyze_expanded_tokens(&self, tokens: &TokenStream) -> Vec<FunctionCall> {
        // 简化的展开代码分析
        // 在实际实现中，需要解析展开后的TokenStream为AST并分析
        let mut calls = Vec::new();

        // 这里只是一个示例，实际需要更复杂的分析
        let token_string = tokens.to_string();
        if token_string.contains("println!") {
            calls.push(FunctionCall {
                caller: "macro_expansion".to_string(),
                callee: "std::io::_print".to_string(),
                call_type: CallType::MacroCall,
                arguments: Vec::new(),
                is_async: false,
                await_points: Vec::new(),
                return_type: Some(InferredType {
                    name: "()".to_string(),
                    full_path: "()".to_string(),
                    generic_args: Vec::new(),
                    is_reference: false,
                    is_mutable: false,
                    lifetime: None,
                    confidence: 0.9,
                    type_kind: TypeKind::Tuple(Vec::new()),
                    associated_types: Vec::new(),
                    higher_ranked_lifetimes: Vec::new(),
                    const_params: Vec::new(),
                }),
                location: SourceLocation {
                    file: PathBuf::new(),
                    line: 0,
                    column: 0,
                },
                confidence: 0.8,
            });
        }

        calls
    }
}

/// Trait调用访问者 - 分析trait方法调用
struct TraitCallVisitor<'a> {
    trait_calls: &'a mut Vec<TraitCall>,
    trait_resolver: &'a TraitResolver,
    type_context: &'a TypeInferenceContext,
}

impl<'a> TraitCallVisitor<'a> {
    fn new(
        trait_calls: &'a mut Vec<TraitCall>,
        trait_resolver: &'a TraitResolver,
        type_context: &'a TypeInferenceContext,
    ) -> Self {
        Self {
            trait_calls,
            trait_resolver,
            type_context,
        }
    }
}

impl<'a> Visit<'a> for TraitCallVisitor<'a> {
    fn visit_expr_method_call(&mut self, method_call: &'a ExprMethodCall) {
        let receiver_type = self.infer_receiver_type(&method_call.receiver);
        let method_name = method_call.method.to_string();

        // 尝试解析trait方法调用
        if let Some(trait_call) =
            self.trait_resolver
                .resolve_method_call(&receiver_type, &method_name, self.type_context)
        {
            self.trait_calls.push(trait_call);
        }

        syn::visit::visit_expr_method_call(self, method_call);
    }

    fn visit_expr_call(&mut self, call: &'a ExprCall) {
        // 检查是否是trait方法的UFCS调用 (Trait::method(receiver, args))
        if let Expr::Path(path) = &*call.func
            && path.path.segments.len() >= 2
        {
            let trait_name = path.path.segments[path.path.segments.len() - 2]
                .ident
                .to_string();
            let method_name = path.path.segments.last().unwrap().ident.to_string();

            // 如果第一个参数是接收者
            if let Some(first_arg) = call.args.first() {
                let receiver_type = self.infer_receiver_type(first_arg);

                let trait_call = TraitCall {
                    trait_name,
                    method_name,
                    receiver_type,
                    arguments: call
                        .args
                        .iter()
                        .skip(1)
                        .map(|arg| self.infer_argument_type(arg))
                        .collect(),
                    return_type: None, // 需要进一步推导
                    location: SourceLocation {
                        file: PathBuf::new(),
                        line: 0,
                        column: 0,
                    },
                    is_dynamic_dispatch: false,
                };

                self.trait_calls.push(trait_call);
            }
        }

        syn::visit::visit_expr_call(self, call);
    }
}

impl<'a> TraitCallVisitor<'a> {
    fn infer_receiver_type(&self, expr: &Expr) -> InferredType {
        match expr {
            Expr::Path(path) => {
                let name = path
                    .path
                    .segments
                    .last()
                    .map(|seg| seg.ident.to_string())
                    .unwrap_or_default();

                // 尝试从类型上下文中查找
                if let Some(var_type) = self.type_context.get_variable_type(&name) {
                    var_type.clone()
                } else {
                    InferredType {
                        name: name.clone(),
                        full_path: self.path_to_string(&path.path),
                        generic_args: Vec::new(),
                        is_reference: false,
                        is_mutable: false,
                        lifetime: None,
                        confidence: 0.5,
                        type_kind: TypeKind::Primitive(name.clone()),
                        associated_types: Vec::new(),
                        higher_ranked_lifetimes: Vec::new(),
                        const_params: Vec::new(),
                    }
                }
            }
            Expr::Reference(ref_expr) => {
                let mut inner_type = self.infer_receiver_type(&ref_expr.expr);
                inner_type.is_reference = true;
                inner_type.is_mutable = ref_expr.mutability.is_some();
                inner_type
            }
            _ => InferredType {
                name: "unknown".to_string(),
                full_path: "unknown".to_string(),
                generic_args: Vec::new(),
                is_reference: false,
                is_mutable: false,
                lifetime: None,
                confidence: 0.1,
                type_kind: TypeKind::Primitive("unknown".to_string()),
                associated_types: Vec::new(),
                higher_ranked_lifetimes: Vec::new(),
                const_params: Vec::new(),
            },
        }
    }

    fn infer_argument_type(&self, expr: &Expr) -> InferredType {
        // 简化的参数类型推导
        self.infer_receiver_type(expr)
    }

    fn path_to_string(&self, path: &Path) -> String {
        path.segments
            .iter()
            .map(|seg| seg.ident.to_string())
            .collect::<Vec<_>>()
            .join("::")
    }
}

/// 异步调用图
#[derive(Debug, Clone)]
pub struct AsyncCallGraph {
    /// 异步函数节点
    async_functions: FxHashMap<String, AsyncFunction>,
    /// 异步调用关系
    async_calls: Vec<(String, String)>,
}

impl Default for AsyncCallGraph {
    fn default() -> Self {
        Self::new()
    }
}

impl AsyncCallGraph {
    pub fn new() -> Self {
        Self {
            async_functions: FxHashMap::default(),
            async_calls: Vec::new(),
        }
    }

    pub fn add_async_function(&mut self, func: &AsyncFunction) {
        self.async_functions.insert(func.name.clone(), func.clone());
    }

    pub fn add_async_call(&mut self, caller: &str, callee: &str) {
        self.async_calls
            .push((caller.to_string(), callee.to_string()));
    }
}

/// 异步函数访问者
struct AsyncFunctionVisitor<'a> {
    async_functions: &'a mut Vec<AsyncFunction>,
    current_function: Option<String>,
}

impl<'a> AsyncFunctionVisitor<'a> {
    fn new(async_functions: &'a mut Vec<AsyncFunction>) -> Self {
        Self {
            async_functions,
            current_function: None,
        }
    }
}

impl<'a> Visit<'a> for AsyncFunctionVisitor<'a> {
    fn visit_item_fn(&mut self, item_fn: &'a ItemFn) {
        if item_fn.sig.asyncness.is_some() {
            let function_name = item_fn.sig.ident.to_string();
            self.current_function = Some(function_name.clone());

            // 分析异步函数
            let mut await_points = Vec::new();
            let mut async_calls = Vec::new();
            let mut await_visitor = AwaitAnalyzer::new(&mut await_points, &mut async_calls);
            await_visitor.visit_block(&item_fn.block);

            // 推导Future类型
            let future_type = self.infer_future_type(&item_fn.sig);

            // 分析错误处理
            let error_handling = self.analyze_error_handling(&item_fn.block);

            let async_function = AsyncFunction {
                name: function_name,
                signature: self.extract_function_signature(&item_fn.sig),
                future_type,
                await_points,
                async_calls,
                error_handling,
            };

            self.async_functions.push(async_function);
        }

        syn::visit::visit_item_fn(self, item_fn);
        self.current_function = None;
    }
}

impl<'a> AsyncFunctionVisitor<'a> {
    fn infer_future_type(&self, sig: &syn::Signature) -> InferredType {
        // 分析async函数的返回类型，推导Future类型
        match &sig.output {
            syn::ReturnType::Default => InferredType {
                name: "Future<Output = ()>".to_string(),
                full_path: "std::future::Future<Output = ()>".to_string(),
                type_kind: TypeKind::Primitive("Future".to_string()),
                ..Default::default()
            },
            syn::ReturnType::Type(_, ty) => {
                let return_type = self.type_to_string(ty);
                InferredType {
                    name: format!("Future<Output = {return_type}>"),
                    full_path: format!("std::future::Future<Output = {return_type}>"),
                    type_kind: TypeKind::Primitive("Future".to_string()),
                    ..Default::default()
                }
            }
        }
    }

    fn analyze_error_handling(&self, block: &syn::Block) -> AsyncErrorHandling {
        // 简化的错误处理分析
        let block_str = quote::quote!(#block).to_string();

        if block_str.contains("?") {
            AsyncErrorHandling::QuestionMark
        } else if block_str.contains("match") && block_str.contains("Err") {
            AsyncErrorHandling::Match
        } else if block_str.contains("unwrap") || block_str.contains("expect") {
            AsyncErrorHandling::Unwrap
        } else {
            AsyncErrorHandling::Custom
        }
    }

    fn extract_function_signature(&self, sig: &syn::Signature) -> FunctionSignature {
        let parameters = sig
            .inputs
            .iter()
            .filter_map(|arg| match arg {
                syn::FnArg::Receiver(_) => Some(Parameter {
                    name: "self".to_string(),
                    param_type: InferredType {
                        name: "Self".to_string(),
                        full_path: "Self".to_string(),
                        type_kind: TypeKind::Primitive("Self".to_string()),
                        ..Default::default()
                    },
                    is_self: true,
                }),
                syn::FnArg::Typed(pat_type) => {
                    if let syn::Pat::Ident(pat_ident) = &*pat_type.pat {
                        Some(Parameter {
                            name: pat_ident.ident.to_string(),
                            param_type: self.type_from_syn(&pat_type.ty),
                            is_self: false,
                        })
                    } else {
                        None
                    }
                }
            })
            .collect();

        let return_type = match &sig.output {
            syn::ReturnType::Default => None,
            syn::ReturnType::Type(_, ty) => Some(self.type_from_syn(ty)),
        };

        FunctionSignature {
            name: sig.ident.to_string(),
            parameters,
            return_type,
            generic_params: Vec::new(), // 简化实现
            where_clause: Vec::new(),   // 简化实现
        }
    }

    fn type_from_syn(&self, ty: &syn::Type) -> InferredType {
        match ty {
            syn::Type::Path(type_path) => {
                let name = type_path.path.segments.last().unwrap().ident.to_string();
                InferredType {
                    name: name.clone(),
                    full_path: name.clone(),
                    type_kind: TypeKind::Primitive(name),
                    ..Default::default()
                }
            }
            _ => InferredType {
                name: "unknown".to_string(),
                full_path: "unknown".to_string(),
                type_kind: TypeKind::Primitive("unknown".to_string()),
                ..Default::default()
            },
        }
    }

    fn type_to_string(&self, ty: &syn::Type) -> String {
        match ty {
            syn::Type::Path(path) => path.path.segments.last().unwrap().ident.to_string(),
            syn::Type::Reference(r) => format!("&{}", self.type_to_string(&r.elem)),
            _ => "unknown".to_string(),
        }
    }
}

/// Await点访问者
struct AwaitPointVisitor<'a> {
    await_points: &'a mut Vec<AwaitPoint>,
}

impl<'a> AwaitPointVisitor<'a> {
    fn new(await_points: &'a mut Vec<AwaitPoint>) -> Self {
        Self { await_points }
    }
}

impl<'a> Visit<'a> for AwaitPointVisitor<'a> {
    fn visit_expr_await(&mut self, await_expr: &'a syn::ExprAwait) {
        // 分析.await表达式
        let future_type = self.infer_future_type(&await_expr.base);
        let error_propagation = self.has_error_propagation(await_expr);

        let await_point = AwaitPoint {
            location: SourceLocation {
                file: PathBuf::from("unknown"), // 需要从上下文获取
                line: 0,                        // 需要从span获取
                column: 0,
            },
            future_type,
            error_propagation,
        };

        self.await_points.push(await_point);
        syn::visit::visit_expr_await(self, await_expr);
    }
}

impl<'a> AwaitPointVisitor<'a> {
    fn infer_future_type(&self, expr: &syn::Expr) -> InferredType {
        // 简化的Future类型推导
        match expr {
            syn::Expr::Call(call) => {
                if let syn::Expr::Path(path) = &*call.func {
                    let function_name = path.path.segments.last().unwrap().ident.to_string();
                    InferredType {
                        name: format!("Future<{function_name}>"),
                        full_path: format!("std::future::Future<{function_name}>"),
                        type_kind: TypeKind::Primitive("Future".to_string()),
                        ..Default::default()
                    }
                } else {
                    self.default_future_type()
                }
            }
            _ => self.default_future_type(),
        }
    }

    fn has_error_propagation(&self, _await_expr: &syn::ExprAwait) -> bool {
        // 检查是否有?操作符
        // 这需要检查await表达式的父表达式
        false // 简化实现
    }

    fn default_future_type(&self) -> InferredType {
        InferredType {
            name: "Future<()>".to_string(),
            full_path: "std::future::Future<()>".to_string(),
            type_kind: TypeKind::Primitive("Future".to_string()),
            ..Default::default()
        }
    }
}

/// Await分析器
struct AwaitAnalyzer<'a> {
    await_points: &'a mut Vec<AwaitPoint>,
    async_calls: &'a mut Vec<String>,
}

impl<'a> AwaitAnalyzer<'a> {
    fn new(await_points: &'a mut Vec<AwaitPoint>, async_calls: &'a mut Vec<String>) -> Self {
        Self {
            await_points,
            async_calls,
        }
    }
}

impl<'a> Visit<'a> for AwaitAnalyzer<'a> {
    fn visit_expr_await(&mut self, await_expr: &'a syn::ExprAwait) {
        // 记录await点
        let await_point = AwaitPoint {
            location: SourceLocation {
                file: PathBuf::from("unknown"),
                line: 0,
                column: 0,
            },
            future_type: InferredType {
                name: "Future".to_string(),
                full_path: "std::future::Future".to_string(),
                type_kind: TypeKind::Primitive("Future".to_string()),
                ..Default::default()
            },
            error_propagation: false,
        };
        self.await_points.push(await_point);

        // 分析异步调用
        if let syn::Expr::Call(call) = &*await_expr.base
            && let syn::Expr::Path(path) = &*call.func
        {
            let function_name = path.path.segments.last().unwrap().ident.to_string();
            self.async_calls.push(function_name);
        }

        syn::visit::visit_expr_await(self, await_expr);
    }
}

impl Default for InferredType {
    fn default() -> Self {
        Self {
            name: String::new(),
            full_path: String::new(),
            generic_args: Vec::new(),
            is_reference: false,
            is_mutable: false,
            lifetime: None,
            confidence: 1.0,
            type_kind: TypeKind::Primitive("unknown".to_string()),
            associated_types: Vec::new(),
            higher_ranked_lifetimes: Vec::new(),
            const_params: Vec::new(),
        }
    }
}
