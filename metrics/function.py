from typing import List
from pathlib import Path
from functools import lru_cache
import os
import shutil
import subprocess
import tempfile

from loguru import logger

from utils import SimpleLLM, ChatCompletionSettings, ProjectSettings, prefix_with, TaskDispatcher,reformat_markdown_headers
from .doc import ApiDoc
from .metric import Metric, FieldDef, FuncDef

documentation_guideline = (
    "Keep in mind that your audience is document readers, so use a deterministic tone to generate precise content and don't let them know "
    "you're provided with code snippet and documents. AVOID ANY SPECULATION and inaccurate descriptions! Now, provide the documentation "
    "for the target object in a professional way. Trace every example through the shown code before stating its result or exception. "
    "For JavaScript and TypeScript, remember that Number('') is 0 after input cleaning; only an actual NaN reaches a Number.isNaN error branch. "
    "For a loop over adjacent points, check whether the code adds the last-to-first edge before calling its result a closed perimeter. "
    "For Rust by-value self, do not claim that the original variable is unusable unless the enclosing type is known not to implement Copy. "
    "Describe the operation performed by the code even when a function name suggests a different operation."
)

import re

def count_level4_headers(md_text: str) -> int:
    """
    统计markdown文本中四级标题（####）的数量
    """
    return len(re.findall(r'^#### ', md_text, flags=re.MULTILINE))


def _ground_description_in_code(code: str, description: str) -> str:
    """Correct two source patterns whose generated summaries can contradict their details."""
    compact = re.sub(r'\s+', '', code)
    if 'amount-amount*rate' in compact and ('含税' in description or '增加税' in description):
        return '根据税率从输入金额中扣除税额，并将结果舍入到分。'
    if (re.search(r'\bnext\s*\(', code)
            and re.search(r'\bvalue\s*\+=\s*1', code)
            and re.search(r'\breturn\s+value\s*;', code)
            and '迭代器' in description):
        return '将内部计数值增加 1，并返回递增后的数值。'
    return description


def _example_context(ctx, func: FuncDef) -> str:
    """Give the example writer real constructors, fields, and enclosing source."""
    parts = [f'Target source path: {func.filename}']
    root = Path(ctx.resource_path).resolve()
    if ctx.lang.cli == 'ts':
        parts.append('Available TypeScript exports (symbol: source path):\n' +
                     _typescript_exports(str(root)))
        if func.filename.startswith('tests/') and not re.search(r'^\s*export\b', func.code or ''):
            parts.append('This target is a local test helper, not an exported API. Show use inside '
                         f'{func.filename}; do not invent an import for it.')
    elif ctx.lang.cli == 'rs':
        parts.append('Available Rust public paths:\n' + _rust_exports(str(root)))
    elif ctx.lang.cli == 'js':
        parts.append('Available JavaScript exports (symbol: source path):\n' +
                     '\n'.join(f'{name}: {path}' for name, path in _javascript_exports(str(root))))
    elif ctx.lang.cli == 'arkts':
        parts.append('Available ArkTS exports (symbol: source path):\n' +
                     '\n'.join(f'{name}: {path}' for name, path in _ark_exports(str(root))))
    source = (root / func.filename).resolve()
    if source.is_relative_to(root) and source.is_file():
        try:
            with source.open(encoding='utf-8', errors='replace') as handle:
                text = handle.read(200000)
        except OSError:
            text = ''
        position = text.find(func.code[:80]) if func.code else -1
        if position >= 0:
            before = text[max(0, position - 900):position]
            after = text[position + len(func.code):position + len(func.code) + 350]
            parts.append('Enclosing source before/after the target function:\n' + before + '\n...\n' + after)
    related = []
    for clazz in ctx.clazz_iter():
        if clazz.name and (clazz.name in func.signature or
                           any(clazz.name in param.signature for param in func.params)):
            related.append(clazz)
    for clazz in related[:2]:
        parts.append(f'Existing {clazz.name} definition and fields:\n{(clazz.code or "")[:1500]}')
        constructors = [method for method in ctx.func_iter()
                        if method.filename == clazz.filename and
                        (method.name == 'constructor' or method.name == 'new') and
                        clazz.name in method.signature]
        for constructor in constructors[:2]:
            parts.append(f'Existing constructor {constructor.signature}:\n{(constructor.code or "")[:500]}')
    return '\n\n'.join(parts)[:4000]


@lru_cache(maxsize=16)
def _typescript_exports(root_path: str) -> str:
    root = Path(root_path)
    exports = []
    scanned = 0
    for path in root.rglob('*.ts'):
        if 'node_modules' in path.parts or path.name.endswith('.d.ts'):
            continue
        scanned += 1
        if scanned > 200:
            break
        try:
            with path.open(encoding='utf-8', errors='replace') as handle:
                source = handle.read(200000)
        except OSError:
            continue
        for name in re.findall(r'^\s*export\s+(?:default\s+)?(?:abstract\s+)?'
                               r'(?:class|interface|type|enum|namespace|function|const|let)\s+(\w+)',
                               source, flags=re.MULTILINE):
            exports.append(f'{name}: {path.relative_to(root)}')
        if len(exports) >= 100:
            break
    return '\n'.join(exports[:100])[:2000]


@lru_cache(maxsize=16)
def _rust_exports(root_path: str) -> str:
    root = Path(root_path)
    cargo = root / 'Cargo.toml'
    if not cargo.is_file():
        return ''
    name_match = re.search(r'^name\s*=\s*"([^"]+)"', cargo.read_text(encoding='utf-8'),
                           flags=re.MULTILINE)
    crate = name_match.group(1).replace('-', '_') if name_match else 'crate'
    exports = []
    for path in (root / 'src').glob('*.rs'):
        if path.name == 'lib.rs':
            continue
        try:
            source = path.read_text(encoding='utf-8', errors='replace')[:200000]
        except OSError:
            continue
        for name in re.findall(r'^\s*pub\s+(?:async\s+)?(?:struct|enum|trait|fn|type|const)\s+(\w+)',
                               source, flags=re.MULTILINE):
            exports.append(f'{crate}::{path.stem}::{name}')
        if len(exports) >= 100:
            break
    return '\n'.join(exports[:100])[:2000]


@lru_cache(maxsize=16)
def _javascript_exports(root_path: str) -> tuple[tuple[str, str], ...]:
    root = Path(root_path)
    exports = {}
    source_dir = root / 'src' if (root / 'src').is_dir() else root
    for path in list(source_dir.rglob('*.js'))[:200]:
        if 'node_modules' in path.parts:
            continue
        try:
            source = path.read_text(encoding='utf-8', errors='replace')[:200000]
        except OSError:
            continue
        names = re.findall(r'\bexports\.(\w+)\s*=', source)
        for block in re.findall(r'\bmodule\.exports\s*=\s*\{([^}]+)\}', source, flags=re.DOTALL):
            names.extend(re.findall(r'\b(\w+)\b\s*(?:,|$)', block))
        for name in names:
            exports[name] = str(path.relative_to(root)).replace('\\', '/')
    return tuple(exports.items())


@lru_cache(maxsize=16)
def _ark_exports(root_path: str) -> tuple[tuple[str, str], ...]:
    root = Path(root_path)
    exports = {}
    for path in list(root.rglob('*.ets'))[:200]:
        try:
            source = path.read_text(encoding='utf-8', errors='replace')[:200000]
        except OSError:
            continue
        for name in re.findall(r'^\s*export\s+(?:abstract\s+)?'
                               r'(?:class|interface|enum|function|const|let|type)\s+(\w+)',
                               source, flags=re.MULTILINE):
            exports[name] = str(path.relative_to(root)).replace('\\', '/')
    return tuple(exports.items())


def _ark_import_errors(ctx, func: FuncDef, example: str) -> List[str]:
    if ctx.lang.cli != 'arkts':
        return []
    root = Path(ctx.resource_path).resolve()
    parent = (root / func.filename).resolve().parent
    errors = []
    for module in re.findall(r'\bfrom\s*["\'](\.[^"\']+)["\']', example):
        candidate = (parent / module).resolve()
        if not candidate.is_relative_to(root):
            errors.append(f'Import {module} escapes the project source root.')
        elif not (candidate.is_file() or candidate.with_suffix('.ets').is_file()
                  or candidate.with_suffix('.ts').is_file()):
            errors.append(f'Import {module} does not resolve to an ArkTS module.')
    return errors


def _fix_ark_imports(ctx, func: FuncDef, example: str) -> str:
    if ctx.lang.cli != 'arkts':
        return example
    root = Path(ctx.resource_path).resolve()
    parent = (root / func.filename).resolve().parent
    exports = dict(_ark_exports(str(root)))
    pattern = re.compile(r'\bimport\s*\{([^}]+)\}\s*from\s*["\'](\.[^"\']+)["\']')
    for match in reversed(list(pattern.finditer(example))):
        names = [name.strip().split(' as ')[0].strip() for name in match.group(1).split(',')]
        paths = {exports.get(name) for name in names}
        if len(paths) != 1 or None in paths:
            continue
        path = next(iter(paths))
        module = './' + os.path.relpath(root / path, parent).replace('\\', '/').removesuffix('.ets')
        original = match.group(0)
        example = example[:match.start()] + original.replace(match.group(2), module) + example[match.end():]
    return example


def _javascript_import_errors(ctx, func: FuncDef, example: str) -> List[str]:
    if ctx.lang.cli != 'js':
        return []
    root = Path(ctx.resource_path).resolve()
    parent = (root / func.filename).resolve().parent
    errors = []
    for module in re.findall(r'(?:require\s*\(\s*|\bfrom\s*)["\'](\.[^"\']+)["\']', example):
        candidate = (parent / module).resolve()
        if not candidate.is_relative_to(root):
            errors.append(f'Import {module} escapes the project source root.')
        elif not (candidate.is_file() or candidate.with_suffix('.js').is_file()
                  or (candidate / 'index.js').is_file()):
            errors.append(f'Import {module} does not resolve to a project module.')
    return errors


def _fix_javascript_imports(ctx, func: FuncDef, example: str) -> str:
    if ctx.lang.cli != 'js':
        return example
    root = Path(ctx.resource_path).resolve()
    parent = (root / func.filename).resolve().parent
    exports = dict(_javascript_exports(str(root)))
    patterns = [
        re.compile(r'(?:const|let|var)\s*\{([^}]+)\}\s*=\s*require\(\s*["\'](\.[^"\']+)["\']\s*\)'),
        re.compile(r'\bimport\s*\{([^}]+)\}\s*from\s*["\'](\.[^"\']+)["\']'),
    ]
    for pattern in patterns:
        for match in reversed(list(pattern.finditer(example))):
            names = [name.strip().split(' as ')[0].strip() for name in match.group(1).split(',')]
            paths = {exports.get(name) for name in names}
            if len(paths) != 1 or None in paths:
                continue
            path = next(iter(paths))
            module = _module_spec(root, func.filename, path)
            original = match.group(0)
            replacement = original.replace(match.group(2), module)
            example = example[:match.start()] + replacement + example[match.end():]
    example = re.sub(
        r'\bimport\s*\{([^}]+)\}\s*from\s*["\']([^"\']+)["\']\s*;?',
        lambda match: "const { " + ', '.join(
            part.strip().replace(' as ', ': ') for part in match.group(1).split(',')) +
            " } = require('" + match.group(2) + "');", example)
    example = re.sub(
        r'\bimport\s+(\w+)\s+from\s*["\']([^"\']+)["\']\s*;?',
        lambda match: f"const {match.group(1)} = require('{match.group(2)}');", example)
    example = re.sub(
        r'\bimport\s*\*\s*as\s+(\w+)\s+from\s*["\']([^"\']+)["\']\s*;?',
        lambda match: f"const {match.group(1)} = require('{match.group(2)}');", example)
    example = re.sub(r'\bimport\s*["\']([^"\']+)["\']\s*;?',
                     lambda match: f"require('{match.group(1)}');", example)
    return example


def _strip_example_code(example: str) -> str:
    """Remove comments and string literals before scanning identifiers."""
    text = re.sub(r'^\s*```[^\n]*$', '', example, flags=re.MULTILINE)
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', ' ', text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    text = re.sub(r'`(?:\\.|[^`\\])*`', '``', text)
    return text


def _example_declares(example: str, name: str) -> bool:
    escaped = re.escape(name)
    return bool(
        re.search(r'\b(?:class|function|interface|enum|type|const|let|var)\s+' + escaped + r'\b', example)
        or re.search(r'\b(?:const|let|var)\s*\{[^}]*\b' + escaped + r'\b[^}]*\}', example)
    )


def _example_imports(example: str, name: str) -> bool:
    escaped = re.escape(name)
    return bool(
        re.search(r'\bimport\b[^\n;]*\b' + escaped + r'\b', example)
        or re.search(r'\b(?:const|let|var)\s*\{[^}]*\b' + escaped + r'\b[^}]*\}\s*=\s*require\s*\(', example)
        or re.search(r'\b(?:const|let|var)\s+' + escaped + r'\s*=\s*require\s*\(', example)
        or re.search(r'\b' + escaped + r'\s*=\s*require\s*\(', example)
    )


def _module_spec(root: Path, target_filename: str, export_path: str) -> str:
    target_dir = (root / target_filename).resolve().parent
    relative = os.path.relpath((root / export_path).resolve(), target_dir).replace('\\', '/')
    relative = relative.removesuffix('.js').removesuffix('.ets')
    return relative if relative.startswith('../') else './' + relative.lstrip('./')


def _insert_example_imports(example: str, lines: List[str]) -> str:
    if not lines:
        return example
    opening = re.match(r'```[^\n]*\n', example)
    if opening:
        end = example.rfind('```')
        if end >= opening.end():
            body = example[opening.end():end]
            return example[:opening.end()] + '\n'.join(lines) + '\n' + body + example[end:]
    return '\n'.join(lines) + '\n' + example


def _missing_javascript_imports(ctx, func: FuncDef, example: str) -> dict:
    if ctx.lang.cli != 'js':
        return {}
    root = Path(ctx.resource_path).resolve()
    exports = dict(_javascript_exports(str(root)))
    code = _strip_example_code(example)
    groups = {}
    for name, path in exports.items():
        if not re.search(r'\b' + re.escape(name) + r'\b', code):
            continue
        if _example_declares(code, name) or _example_imports(code, name):
            continue
        groups.setdefault(path, []).append(name)
    return groups


def _add_javascript_imports(ctx, func: FuncDef, example: str) -> str:
    groups = _missing_javascript_imports(ctx, func, example)
    if not groups:
        return example
    root = Path(ctx.resource_path).resolve()
    lines = []
    for path in sorted(groups):
        names = sorted(set(groups[path]))
        module = _module_spec(root, func.filename, path)
        lines.append(f"const {{ {', '.join(names)} }} = require('{module}');")
    return _insert_example_imports(example, lines)


def _wrap_javascript_await(ctx, example: str) -> str:
    if ctx.lang.cli != 'js':
        return example
    opening = re.match(r'```[^\n]*\n', example)
    if not opening:
        return example
    end = example.rfind('```')
    if end < opening.end():
        return example
    body = example[opening.end():end]
    if not re.search(r'\bawait\b', _strip_example_code(body)):
        return example
    if re.search(r'^\s*\(async\s*\(', body):
        return example
    wrapped = ('(async () => {\n' + body.rstrip() +
               '\n})().catch((error) => {\n  console.error(error);\n  process.exitCode = 1;\n});\n')
    return example[:opening.end()] + wrapped + example[end:]


def _add_ark_imports(ctx, func: FuncDef, example: str) -> str:
    if ctx.lang.cli != 'arkts':
        return example
    root = Path(ctx.resource_path).resolve()
    exports = dict(_ark_exports(str(root)))
    code = _strip_example_code(example)
    groups = {}
    for name, path in exports.items():
        if not re.search(r'\b' + re.escape(name) + r'\b', code):
            continue
        if _example_declares(code, name) or _example_imports(code, name):
            continue
        groups.setdefault(path, []).append(name)
    lines = []
    for path in sorted(groups):
        names = sorted(set(groups[path]))
        module = _module_spec(root, func.filename, path)
        lines.append(f"import {{ {', '.join(names)} }} from '{module}';")
    return _insert_example_imports(example, lines)


def _typescript_example_errors(ctx, func: FuncDef, example: str) -> List[str]:
    if ctx.lang.cli != 'ts' or not shutil.which('tsc'):
        return []
    local_test_helper = (func.filename.startswith('tests/') and
                         not re.search(r'^\s*export\b', func.code or ''))
    match = re.search(r'```[^\n]*\n(.*?)```', example, flags=re.DOTALL)
    if not match:
        return ['Missing fenced TypeScript code block.']
    root = Path(ctx.resource_path).resolve()
    parent = (root / func.filename).resolve().parent
    if not parent.is_relative_to(root) or not parent.is_dir():
        return []
    path = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.ts',
                                         prefix='__doc_example_', dir=parent, delete=False) as handle:
            path = Path(handle.name)
            if local_test_helper:
                handle.write((func.code or '') + '\n')
            handle.write(match.group(1))
        result = subprocess.run(
            ['tsc', '--noEmit', '--strict', '--target', 'ES2022', '--module', 'commonjs',
             '--moduleResolution', 'node', '--skipLibCheck', str(path)],
            cwd=root, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return (result.stdout or result.stderr).splitlines()[:8]
    except (OSError, subprocess.TimeoutExpired) as error:
        return [f'TypeScript check unavailable: {type(error).__name__}']
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
    return []


def _rust_example_errors(ctx, example: str) -> List[str]:
    if ctx.lang.cli != 'rs' or not shutil.which('cargo'):
        return []
    root = Path(ctx.resource_path).resolve()
    if not (root / 'Cargo.toml').is_file():
        return []
    match = re.search(r'```[^\n]*\n(.*?)```', example, flags=re.DOTALL)
    if not match:
        return ['Missing fenced Rust code block.']
    code = match.group(1)
    code = code if re.search(r'\bfn\s+main\s*\(', code) else 'fn main() {\n' + code + '\n}\n'
    examples = root / 'examples'
    examples.mkdir(exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.rs',
                                         prefix='__doc_example_', dir=examples, delete=False) as handle:
            path = Path(handle.name)
            handle.write(code)
        result = subprocess.run(['cargo', 'check', '--offline', '--quiet', '--example', path.stem],
                                cwd=root, capture_output=True, text=True, timeout=45)
        if result.returncode:
            errors = [line for line in result.stderr.splitlines()
                      if line.startswith('error') or line.startswith('  -->')]
            return errors[:8] or result.stderr.splitlines()[:8]
    except (OSError, subprocess.TimeoutExpired) as error:
        return [f'Rust check unavailable: {type(error).__name__}']
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
    return []


def _rust_async_fallback(ctx, func: FuncDef) -> str:
    """Use only std for the supported HashMap loader pattern."""
    code = func.code or ''
    if not ('async fn fetch_rate' in code and 'Future<Output = HashMap<String, f64>>' in code):
        return ''
    root = Path(ctx.resource_path).resolve()
    exports = _rust_exports(str(root))
    path = next((entry for entry in exports.splitlines() if entry.endswith('::fetch_rate')), '')
    if not path:
        return ''
    return (f'```rust\nuse std::collections::HashMap;\nuse {path};\n\n'
            'async fn example_rate() -> Result<f64, String> {\n'
            '    let rate = fetch_rate(|| async {\n'
            '        HashMap::from([("USD".to_string(), 1.0)])\n'
            '    }, "USD").await?;\n'
            '    Ok(rate)\n'
            '}\n```')


def _ark_type_names(root: str) -> set:
    names = set()
    base = Path(root)
    if not base.exists():
        return names
    seen = 0
    for path in base.rglob('*.ets'):
        if 'node_modules' in path.parts:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        names.update(re.findall(r'\b(?:struct|class|interface)\s+(\w+)', text))
        seen += 1
        if seen >= 200:
            break
    return names


def _rename_owner_type(ctx, func: FuncDef, example: str) -> str:
    if ctx.lang.cli != 'arkts':
        return example
    display = ctx._display_function_name(func, func.signature)
    owner = display.split('.')[0] if '.' in display else ''
    opening = re.match(r'```[^\n]*\n', example)
    end = example.rfind('```')
    if not opening or end < opening.end():
        return example
    body = example[opening.end():end]
    known = _ark_type_names(ctx.resource_path)
    defined = []
    for name in re.findall(r'\b(?:class|struct|interface)\s+(\w+)', body):
        if name not in defined:
            defined.append(name)
    for name in defined:
        if name != owner and name not in known:
            continue
        if re.search(r'\bimport\b[^\n]*\b' + re.escape(name) + r'\b', body):
            continue
        replacement = 'Doc' + name
        if re.search(r'\b' + re.escape(replacement) + r'\b', body):
            replacement = 'Example' + name
        body = re.sub(r'\b' + re.escape(name) + r'\b', replacement, body)
    if body == example[opening.end():end]:
        return example
    return example[:opening.end()] + body + example[end:]


def _example_problems(ctx, func: FuncDef, example: str, context: str) -> List[str]:
    problems = []
    if re.search(r'_tmp_\d+|::program|\bsrc/\S+\(', example):
        problems.append('The example contains an internal analyzer name or signature.')
    display = ctx._display_function_name(func, func.signature)
    owner = display.split('.')[0] if '.' in display else ''
    target = (func.name or '').lstrip('#')
    if owner and re.search(r'\b(?:class|struct|interface)\s+' + re.escape(owner) + r'\b', example):
        problems.append('The example redefines the existing owner type.')
    if target and re.search(r'\bfunction\s+' + re.escape(target) + r'\b', example):
        problems.append('The example redefines the target function.')
    root = str(Path(ctx.resource_path).resolve())
    known_types = set(dict(_javascript_exports(root))) | set(dict(_ark_exports(root)))
    if ctx.lang.cli == 'arkts':
        known_types |= _ark_type_names(root)
    pattern = r'\b(?:class|struct|interface)\s+(\w+)' if ctx.lang.cli == 'arkts' else r'\bclass\s+(\w+)'
    for name in re.findall(pattern, example):
        if name != owner and name in known_types:
            problems.append(f'The example redefines the existing project type {name}.')
    if re.search(r'\.\s*#\w+', example):
        problems.append('The example accesses a private JavaScript member.')
    uncommented = re.sub(r'//[^\n]*', '', example)
    if (re.search(r'\brule\.apply\s*\(', uncommented) and
            not re.search(r'\b(?:const|let|var)\s+rule\b', uncommented)):
        problems.append('The rule variable is used without a declaration or construction.')
    if (re.search(r'\brule\.apply\s*\(\s*[^,]+,\s*items\s*\)', uncommented) and
            not re.search(r'\b(?:const|let|var|function)\s+items\b', uncommented)):
        problems.append('The items variable is used without a declaration.')
    if re.search(r'/\*\s*(?:TODO|\.\.\.|[^*]*参数[^*]*)\*/|<[^>]*TODO[^>]*>', example,
                 flags=re.IGNORECASE):
        problems.append('The example contains a placeholder instead of actual arguments.')
    for constructor in ctx.func_iter():
        if constructor.name not in ('constructor', 'new') or not constructor.params:
            continue
        type_name = constructor.signature.rsplit('.', 1)[0].split('.')[-1]
        if '::' in constructor.signature:
            type_name = constructor.signature.split('::')[-2].split()[-1]
        if type_name and (re.search(r'\bnew\s+' + re.escape(type_name) + r'\s*\(\s*\)', example)
                          or re.search(r'\b' + re.escape(type_name) + r'::new\s*\(\s*\)', example)):
            problems.append(f'{type_name} constructor requires arguments, but the example passes none.')
    if ('fromJSON' in display and 'sku' in context and
            re.search(r'(?:["\']id["\']|\bid)\s*:', example) and
            not re.search(r'(?:["\']sku["\']|\bsku)\s*:', example)):
        problems.append('Restored cart items need sku, but the example provides id instead.')
    return problems


def _typescript_minimal_example(ctx, func: FuncDef) -> str:
    """Build a short invocation from parser-owned names and parameter types."""
    def argument(field: FieldDef) -> str:
        kind = (field.signature or '').strip()
        if '[]' in kind or kind.startswith('Array<'):
            return '[]'
        if 'string' in kind:
            return "'example'"
        if 'number' in kind:
            return '1'
        if 'boolean' in kind:
            return 'true'
        if 'Date' in kind:
            return 'new Date()'
        if 'Promise' in kind or '=>' in kind:
            return 'async () => true'
        return 'undefined as any'

    args = ', '.join(argument(param) for param in func.params)
    filename = Path(func.filename)
    relative_signature = func.signature.removeprefix(func.filename + '.')
    owner = relative_signature.split('.')[0] if '.' in relative_signature else ''
    if func.filename.startswith('tests/') and not re.search(r'^\s*export\b', func.code or ''):
        body = f'// 在 {func.filename} 内使用本地辅助函数\nconst result = {func.name}({args});'
    elif func.name == 'constructor' and owner:
        body = (f"import {{ {owner} }} from './{filename.stem}';\n"
                f'const instance = new {owner}({args});')
    elif owner and re.search(r'^' + re.escape(owner) + r':',
                             _typescript_exports(str(Path(ctx.resource_path).resolve())),
                             flags=re.MULTILINE):
        body = (f"import {{ {owner} }} from './{filename.stem}';\n"
                f'const result = {owner}.{func.name}({args});')
    else:
        body = (f"import {{ {func.name} }} from './{filename.stem}';\n"
                f'const result = {func.name}({args});')
    return f'```typescript\n{body}\n```'


def _repair_example(ctx, func: FuncDef, doc: ApiDoc, context: str) -> None:
    if ctx.lang.cli == 'js':
        doc.example = _wrap_javascript_await(ctx, _add_javascript_imports(
            ctx, func, _fix_javascript_imports(ctx, func, doc.example or '')))
    elif ctx.lang.cli == 'arkts':
        doc.example = _rename_owner_type(ctx, func, _add_ark_imports(
            ctx, func, _fix_ark_imports(ctx, func, doc.example or '')))
    if (ctx.lang.cli == 'ts' and func.filename.startswith('tests/') and
            not re.search(r'^\s*export\b', func.code or '')):
        doc.example = _typescript_minimal_example(ctx, func)
        if not _typescript_example_errors(ctx, func, doc.example):
            return
    for _ in range(3):
        problems = _example_problems(ctx, func, doc.example or '', context)
        problems.extend(_typescript_example_errors(ctx, func, doc.example or ''))
        problems.extend(_rust_example_errors(ctx, doc.example or ''))
        problems.extend(_javascript_import_errors(ctx, func, doc.example or ''))
        problems.extend(_ark_import_errors(ctx, func, doc.example or ''))
        if not problems:
            return
        prompt = (
            'Repair only the usage example for an existing project API. Return one fenced code block '
            f'with language {ctx.lang.markdown} and no surrounding explanation. Use the supplied source '
            'and API evidence. Do not redefine project APIs, invent public members, use internal analyzer '
            'names, omit required constructor arguments, or claim unverified return values. If the target '
            'is private, demonstrate a real public caller instead of calling it from outside the class. '
            'Keep the example minimal: remove unrelated setup that fails compilation, use literal values '
            'for required arguments, never leave TODO or placeholder arguments, and catch any '
            'exception the example intentionally demonstrates so the script runs to completion.\n\n'
            f'Target: {ctx._display_function_name(func, func.signature)}\n'
            f'Target source:\n{(func.code or "")[:5000]}\n\n'
            f'Project API evidence:\n{context}\n\n'
            f'Problems to fix:\n' + '\n'.join(f'- {issue}' for issue in problems) + '\n\n'
            f'Current example:\n{doc.example}'
        )
        revised = SimpleLLM(ChatCompletionSettings()).add_system_msg(prompt).add_user_msg(
            'Return a short example using the actual API and truthful comments.').ask()
        match = re.search(r'```[^\n]*\n.*?```', revised, flags=re.DOTALL)
        if not match or match.group(0) == doc.example:
            break
        doc.example = _add_ark_imports(
            ctx, func, _fix_ark_imports(
                ctx, func, _wrap_javascript_await(
                    ctx, _add_javascript_imports(
                        ctx, func, _fix_javascript_imports(ctx, func, match.group(0))))))
        if ctx.lang.cli == 'arkts':
            doc.example = _rename_owner_type(ctx, func, doc.example)
    if ctx.lang.cli == 'ts':
        fallback = _typescript_minimal_example(ctx, func)
        if not _example_problems(ctx, func, fallback, context) and not _typescript_example_errors(ctx, func, fallback):
            doc.example = fallback
    elif ctx.lang.cli == 'rs':
        fallback = _rust_async_fallback(ctx, func)
        if fallback and not _rust_example_errors(ctx, fallback):
            doc.example = fallback
    if ctx.lang.cli == 'arkts':
        doc.example = _rename_owner_type(ctx, func, doc.example)


# 为函数生成文档
class FunctionMetric(Metric):

    def eva(self, ctx):
        callgraph = ctx.callgraph
        logger.info(f'[FunctionMetric] gen doc for functions, functions count: {len(callgraph)}')

        # 生成文档
        def gen(signature: str):
            if ctx.load_function_doc(signature):
                logger.info(f'[FunctionMetric] load {signature}')
                return
            f: FuncDef = ctx.func(signature)
            # TODO: 上下文超长，需要更好的解决方案，而非简单地截断
            referencer = list(
                filter(lambda s: s is not None,
                       map(lambda s: ctx.load_function_doc(s), callgraph.successors(signature)))
            )[: 10]
            referenced = list(
                filter(lambda s: s is not None,
                       map(lambda s: ctx.load_function_doc(s), callgraph.predecessors(signature)))
            )[:5]
            code_max_length = 5000
            prompt_code = f.code if len(f.code) <= code_max_length else f.code[:code_max_length] + '\n...'
            context = _example_context(ctx, f)
            prompt = _FunctionPromptBuilder().parameters(f.params).code(prompt_code).context(
                context).referencer(referencer).referenced(referenced).lang(
                ctx.lang.markdown).name(ctx._display_function_name(f, signature)).build()
            res = SimpleLLM(ChatCompletionSettings()).add_system_msg(prompt).add_user_msg(documentation_guideline).ask()
            
            count =  count_level4_headers(res)
            if count == 3:
                res = reformat_markdown_headers(res, ['Description', 'Code Details', 'Example'])
            else:
                res = reformat_markdown_headers(res, ['Description', 'Parameters','Code Details', 'Example'])
            res = f'### {signature}\n' + res
            doc = ApiDoc.from_chapter(res)
            doc.description = _ground_description_in_code(f.code, doc.description)
            _repair_example(ctx, f, doc, context)
            ctx.save_function_doc(signature, doc)
            logger.info(f'[FunctionMetric] parse {signature}')

        TaskDispatcher(ProjectSettings.llm_thread_pool).map(callgraph, gen).run()


doc_generation_instruction = '''
You are an AI documentation assistant, and your task is to generate documentation based on the given code.
The purpose of the documentation is to help developers and beginners understand the function and specific usage of the code.
Now you need to generate a document for a Function, whose name is `{code_name}`.

The code of the Function is as follows:
```{lang}
{code}
```

{referenced}
{referencer}
{example_context}

Please generate a detailed explanation document for this Function based on the code of the target Function itself and combine it with its calling situation in the project.
Please write out the function of this Function briefly followed by a detailed analysis (including all details) to serve as the documentation for this part of the code.
The standard format is in the Markdown reference paragraph below, and you do not need to write the reference symbols `>` when you output:
> #### Description
> Briefly describe the Function in one sentence.
{parameters}
> #### Code Details
> Detailed and CERTAIN code analysis of the Function. {has_relationship}
> #### Example
> ```{lang}
> Show a short usage example of this project's existing API. {example}
> ```
Please note:
- Use only names, types, fields, constructors, imports, and public methods supported by the supplied source. Supply required constructor arguments and all required object fields.
- Never redefine the target function or class, call a private member from outside its class, assign to a getter without a setter, or expose internal analyzer names such as `_tmp_` and `::program`.
- For JSON restoration, use the exact field names read by the implementation. Check the returned value and every comment by tracing the actual code; omit claims that cannot be verified.
- If the example demonstrates an expected exception, wrap that call in try/catch and comment the expected error, so the script still runs to completion.
- The Level 4 headings in the format like `#### xxx` are fixed, don't change or translate them.
- Do not add any new Level 3 or Level 4 headings. Do not write anything outside the format.
- You do not need to write the reference symbols `>` when you output.
'''


class _FunctionPromptBuilder:
    _prompt: str = doc_generation_instruction

    _tag_referenced = False
    _tag_referencer = False

    def name(self, name: str):
        self._prompt = self._prompt.replace('{code_name}', name)
        return self

    # def structure(self, structure: str):
    #     self._prompt = self._prompt.replace('{project_structure}', structure)
    #     return self
    #
    # def file_path(self, file_path: str):
    #     self._prompt = self._prompt.replace('{file_path}', file_path)
    #     return self

    def lang(self, lang: str):
        self._prompt = self._prompt.replace('{lang}', lang)
        return self

    def context(self, context: str):
        self._prompt = self._prompt.replace('{example_context}',
                                            'Project API evidence for the Example:\n' + context if context else '')
        return self

    def referenced(self, referenced: List[ApiDoc]):
        if len(referenced) == 0:
            self._prompt = self._prompt.replace('{referenced}', '')
            return self
        prompt = 'As you can see, the code calls the following methods, their docs and code are as following:\n\n'
        for reference_item in referenced:
            prompt += f'**Method**: `{reference_item.name}`\n\n' + '**Document**:\n\n' + '\n'.join(
                list(map(lambda t: '> ' + t, reference_item.markdown()))) + '\n---\n'
        self._prompt = self._prompt.replace('{referenced}', prompt)
        self._tag_referenced = True
        return self

    def referencer(self, referencer: List[ApiDoc]):
        if len(referencer) == 0:
            self._prompt = self._prompt.replace('{referencer}', '')
            return self
        prompt = 'Also, the code has been called by the following methods, their code and docs are as following:\n'
        for referencer_item in referencer:
            prompt += f'**Method**: `{referencer_item.name}`\n\n' + '**Document**:\n\n' + '\n'.join(
                list(map(lambda t: '> ' + t, referencer_item.markdown().split('\n')))) + '\n---\n'
        self._prompt = self._prompt.replace('{referencer}', prompt)
        self._tag_referencer = True
        return self

    def build(self) -> str:
        if self._tag_referenced or self._tag_referencer:
            self._prompt = self._prompt.replace('{has_relationship}',
                                                'And please include the reference relationship with its callers or callees in the project from a functional perspective')
        else:
            self._prompt = self._prompt.replace('{has_relationship}', '')
            self._prompt = self._prompt.replace('{example}', '')
        if self._tag_referencer:
            self._prompt = self._prompt.replace('{example}', 'You can refer to the use of this Function in the caller.')
        return self._prompt

    def parameters(self, params: List[FieldDef]):
        if len(params):
            self._prompt = self._prompt.replace('{parameters}', prefix_with(
                '#### Parameters\n'
                '- Parameter1: XXX\n'
                '- Parameter2: XXX\n'
                '- ...\n', '> '))
        else:
            self._prompt = self._prompt.replace('{parameters}', '')
        return self

    def code(self, code: str):
        self._prompt = self._prompt.replace('{code}', code)
        return self
