#!/bin/bash

# 构建脚本

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查依赖
check_dependencies() {
    log_info "检查构建依赖..."
    
    # 检查Rust工具链
    if ! command -v rustc &> /dev/null; then
        log_error "Rust编译器未安装，请先安装Rust工具链"
        log_info "安装命令: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        exit 1
    fi
    
    if ! command -v cargo &> /dev/null; then
        log_error "Cargo包管理器未找到"
        exit 1
    fi
    
    # 检查Git（用于版本信息）
    if ! command -v git &> /dev/null; then
        log_warning "Git未安装，将使用默认版本信息"
    fi
    
    # 显示工具链版本
    log_info "Rust版本: $(rustc --version)"
    log_info "Cargo版本: $(cargo --version)"
    
    log_success "依赖检查完成"
}

# 清理构建产物
clean_build() {
    log_info "清理之前的构建产物..."
    
    if [ -d "target" ]; then
        rm -rf target
        log_info "已删除target目录"
    fi
    
    if [ -f "Cargo.lock" ]; then
        rm -f Cargo.lock
        log_info "已删除Cargo.lock文件"
    fi
    
    log_success "清理完成"
}

# 运行测试
run_tests() {
    log_info "运行单元测试..."
    
    if cargo test --verbose; then
        log_success "所有测试通过"
    else
        log_error "测试失败，构建中止"
        exit 1
    fi
}

# 运行代码检查
run_checks() {
    log_info "运行代码质量检查..."
    
    # 检查代码格式
    if cargo fmt --check; then
        log_success "代码格式检查通过"
    else
        log_warning "代码格式不符合标准，正在自动格式化..."
        cargo fmt
        log_info "代码已自动格式化"
    fi
    
    if cargo check --all-targets --all-features; then
        log_success "cargo check 检查通过"
    else
        log_error "Cargo check失败，请修复警告后重新构建"
        exit 1
    fi
}

# 构建发布版本
build_release() {
    log_info "开始构建发布版本..."
    
    # 设置构建环境变量
    export RUSTFLAGS="-C target-cpu=native"
    
    if cargo build --release --verbose; then
        log_success "发布版本构建成功"
    else
        log_error "发布版本构建失败"
        exit 1
    fi
    
    # 验证二进制文件
    local binary_path="target/release/rust_analyzer"
    if [ -f "$binary_path" ]; then
        local file_size=$(du -h "$binary_path" | cut -f1)
        log_info "二进制文件大小: $file_size"
        log_info "二进制文件路径: $binary_path"
        
        # 测试二进制文件是否可执行
        if "$binary_path" --version &> /dev/null; then
            log_success "二进制文件验证通过"
        else
            log_error "二进制文件无法正常执行"
            exit 1
        fi
    else
        log_error "二进制文件未生成"
        exit 1
    fi
}

# 生成文档
generate_docs() {
    log_info "生成项目文档..."
    
    if cargo doc --no-deps --document-private-items; then
        log_success "文档生成成功"
        log_info "文档路径: target/doc/rust_analyzer/index.html"
    else
        log_warning "文档生成失败，但不影响构建"
    fi
}

# 主函数
main() {
    log_info "开始构建Rust代码分析器..."
    log_info "构建时间: $(date)"
    
    # 检查是否在正确的目录
    if [ ! -f "Cargo.toml" ]; then
        log_error "未找到Cargo.toml文件，请在项目根目录运行此脚本"
        exit 1
    fi
    
    # 解析命令行参数
    CLEAN=false
    SKIP_TESTS=false
    SKIP_CHECKS=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --clean)
                CLEAN=true
                shift
                ;;
            --skip-tests)
                SKIP_TESTS=true
                shift
                ;;
            --skip-checks)
                SKIP_CHECKS=true
                shift
                ;;
            --help)
                echo "用法: $0 [选项]"
                echo "选项:"
                echo "  --clean       清理之前的构建产物"
                echo "  --skip-tests  跳过单元测试"
                echo "  --skip-checks 跳过代码质量检查"
                echo "  --help        显示此帮助信息"
                exit 0
                ;;
            *)
                log_error "未知选项: $1"
                exit 1
                ;;
        esac
    done
    
    # 执行构建流程
    check_dependencies
    
    if [ "$CLEAN" = true ]; then
        clean_build
    fi
    
    if [ "$SKIP_CHECKS" = false ]; then
        run_checks
    fi
    
    if [ "$SKIP_TESTS" = false ]; then
        run_tests
    fi
    
    build_release
    generate_docs
    
    log_success "构建完成！"
    log_info "可执行文件: target/release/rust_analyzer"
    log_info "使用方法: ./target/release/rust_analyzer --help"
}

# 错误处理
trap 'log_error "构建过程中发生错误，退出码: $?"' ERR

# 执行主函数
main "$@"
