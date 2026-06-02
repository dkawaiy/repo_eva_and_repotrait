// 替换 CommonJS 的 require 为 ES模块的 import
import fs from 'fs';
import path from 'path';
import ts from 'typescript';
import iconv from 'iconv-lite';
import { execSync } from 'child_process';

// 输出文件路径
let methodsOutputPath;
let structsOutputPath;

// 辅助类定义（完全不变）
class FieldDefTS {
    constructor(name, signature, access) {
        this.name = name;
        this.signature = signature;
        this.access = access || 'public';
    }
}

class FuncDefTS {
    constructor(signature, name, code, filename, visible, access, params) {
        this.signature = signature;
        this.name = name;
        this.code = code;
        this.filename = filename;
        this.visible = visible;
        this.access = access;
        this.params = params;
    }
}

class ClazzDefTS {
    constructor(signature, name, code, fields, functions, filename, visible) {
        this.signature = signature;
        this.name = name;
        this.code = code;
        this.fields = fields;
        this.functions = functions;
        this.filename = filename;
        this.visible = visible;
    }
}

class DiGraph {
    constructor() {
        this.nodes = new Map();
        this.edges = new Map();
    }

    addNode(signature, attr) {
        this.nodes.set(signature, { attr });
        if (!this.edges.has(signature)) {
            this.edges.set(signature, new Set());
        }
    }

    addEdge(source, target) {
        if (this.nodes.has(source) && this.nodes.has(target)) {
            this.edges.get(source).add(target);
        }
    }

    hasNode(signature) {
        return this.nodes.has(signature);
    }

    getNode(signature) {
        return this.nodes.get(signature);
    }
}

class EvaContextTS {
    constructor(resourcePath, outputPath) {
        this.resource_path = resourcePath;
        this.output_path = outputPath;
        this.callgraph = new DiGraph();
        this.clazz_callgraph = new DiGraph();
    }

    func(signature) {
        const node = this.callgraph.getNode(signature);
        return node ? node.attr : undefined;
    }

    clazz(signature) {
        const node = this.clazz_callgraph.getNode(signature);
        return node ? node.attr : undefined;
    }
}

// 递归收集TS文件（完全不变）
function collectTsFiles(dir) {
    let tsFiles = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    
    for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            tsFiles = [...tsFiles, ...collectTsFiles(fullPath)];
        } else if (entry.isFile() && (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx') || entry.name.endsWith('.d.ts'))) {
            tsFiles.push(fullPath);
        }
    }
    return tsFiles;
}

// 主函数（完全不变）
function main() {
    const [resourcePath, outputPath] = process.argv.slice(2);
    if (!resourcePath || !outputPath) {
        console.error('错误：请提供源代码路径和输出路径');
        process.exit(1);
    }

    if (!fs.existsSync(outputPath)) {
        fs.mkdirSync(outputPath, { recursive: true });
    }

    methodsOutputPath = path.join(outputPath, 'methods.jsonl');
    structsOutputPath = path.join(outputPath, 'structs.jsonl');

    if (fs.existsSync(methodsOutputPath)) fs.unlinkSync(methodsOutputPath);
    if (fs.existsSync(structsOutputPath)) fs.unlinkSync(structsOutputPath);

    let tsFiles = [];
    const stats = fs.statSync(resourcePath);
    if (stats.isFile()) {
        if (resourcePath.endsWith('.ts') || resourcePath.endsWith('.tsx') || resourcePath.endsWith('.d.ts')) {
            tsFiles = [resourcePath];
        } else {
            console.error(`错误：输入文件不是TS文件 - ${resourcePath}`);
            process.exit(1);
        }
    } else if (stats.isDirectory()) {
        tsFiles = collectTsFiles(resourcePath);
        if (tsFiles.length === 0) {
            console.error(`错误：目录中未找到TS文件 - ${resourcePath}`);
            writeDefaultData(outputPath);
            process.exit(0);
        }
    } else {
        console.error(`错误：输入路径不是文件或目录 - ${resourcePath}`);
        process.exit(1);
    }

    console.log(`开始分析 TypeScript 代码：共 ${tsFiles.length} 个文件`);
    
    // 文件处理进度统计
    const totalFiles = tsFiles.length;
    let processedFiles = 0;
    const progressInterval = 100;

    tsFiles.forEach(tsFilePath => {
        try {
            const relPath = path.relative(resourcePath, tsFilePath);
            const content = fs.readFileSync(tsFilePath, 'utf8');
            const sourceFile = ts.createSourceFile(
                tsFilePath,
                content,
                ts.ScriptTarget.ES2020,
                true
            );

            const methods = [];
            const structs = [];
            
            ts.forEachChild(sourceFile, node => {
                if (ts.isClassDeclaration(node) && node.name) {
                    extractClassInfo(node, relPath, content, structs, methods);
                } else if (ts.isInterfaceDeclaration(node) && node.name) {
                    extractInterfaceInfo(node, relPath, content, structs);
                } else if (ts.isFunctionDeclaration(node) && node.name) {
                    extractFunctionInfo(node, relPath, content, methods);
                }
            });

            appendToFile(methodsOutputPath, methods);
            appendToFile(structsOutputPath, structs);

            // 更新进度
            processedFiles++;
            if (processedFiles % progressInterval === 0 || processedFiles === totalFiles) {
                const progress = ((processedFiles / totalFiles) * 100).toFixed(2);
                console.log(`[文件进度] 已处理 ${processedFiles}/${totalFiles} 个文件（${progress}%）`);
            }

        } catch (error) {
            console.error(`分析文件失败 ${tsFilePath}：${error.message}`);
        }
    });

    checkOutputFiles(methodsOutputPath, structsOutputPath);

    const ctx = new EvaContextTS(resourcePath, outputPath);
    loadCallgraph(ctx);
    loadClazzCallgraph(ctx, outputPath);

    console.log('分析完成');
}

// 提取类信息（完全不变）
function extractClassInfo(classNode, filePath, content, structs, methods) {
    const className = classNode.name.getText();
    const classSignature = `${filePath}.${className}`;
    
    const classCode = buildClassCode({
        content,
        start: classNode.getStart(),
        end: classNode.getEnd(),
        name: className
    });

    const attributes = [];
    classNode.members.forEach(member => {
        if (ts.isPropertyDeclaration(member) && member.name && member.type) {
            attributes.push({
                name: member.name.getText(),
                type: member.type.getText(),
                modifier: getAccessModifier(member)
            });
        }
    });

    const classMethods = [];
    classNode.members.forEach(member => {
        if (ts.isMethodDeclaration(member) && member.name) {
            const methodInfo = extractFunctionInfo(member, filePath, content, methods, className);
            if (methodInfo) classMethods.push(methodInfo.signature);
        }
    });

    const isExported = classNode.modifiers?.some(m => 
        m.kind === ts.SyntaxKind.ExportKeyword
    );

    structs.push({
        signature: classSignature,
        name: className,
        code: classCode,
        filename: filePath,
        modifier: getAccessModifier(classNode),
        attributes,
        methods: classMethods,
        isExported: isExported
    });
}

// 提取接口信息（完全不变）
function extractInterfaceInfo(ifNode, filePath, content, structs) {
    const ifName = ifNode.name.getText();
    const ifSignature = `${filePath}.${ifName}`;
    
    const ifCode = buildClassCode({
        content,
        start: ifNode.getStart(),
        end: ifNode.getEnd(),
        name: ifName
    });

    const attributes = [];
    ifNode.members.forEach(member => {
        if (ts.isPropertySignature(member) && member.name && member.type) {
            attributes.push({
                name: member.name.getText(),
                type: member.type.getText(),
                modifier: 'public'
            });
        }
    });

    const isExported = ifNode.modifiers?.some(m => 
        m.kind === ts.SyntaxKind.ExportKeyword
    );

    structs.push({
        signature: ifSignature,
        name: ifName,
        code: ifCode,
        filename: filePath,
        modifier: 'public',
        attributes,
        methods: [],
        isExported: isExported
    });
}

// 提取函数信息（完全不变）
function extractFunctionInfo(funcNode, filePath, content, methods, className = '') {
    try {
        const funcName = funcNode.name?.getText();
        if (!funcName) return null;

        const funcSignature = className 
            ? `${filePath}.${className}.${funcName}`
            : `${filePath}.${funcName}`;

        const params = [];
        if (funcNode.parameters) {
            funcNode.parameters.forEach(param => {
                const paramName = param.name.getText();
                const paramType = param.type ? param.type.getText() : 'any';
                params.push({ 
                    name: paramName, 
                    type: paramType,
                    modifier: 'public'
                });
            });
        }

        const funcCode = content.substring(
            funcNode.getStart(),
            funcNode.getEnd()
        ).substring(0, 5000);

        const callees = [];
        ts.forEachChild(funcNode.body, child => {
            if (ts.isCallExpression(child) && ts.isIdentifier(child.expression)) {
                const calleeName = child.expression.getText();
                let fullCalleeSig;
                if (className) {
                    fullCalleeSig = `${filePath}.${className}.${calleeName}`;
                } else {
                    const fileFuncs = methods.filter(m => m.filename === filePath);
                    const matchedFunc = fileFuncs.find(f => f.name === calleeName);
                    fullCalleeSig = matchedFunc ? matchedFunc.signature : `${filePath}.${calleeName}`;
                }
                callees.push(fullCalleeSig);
            }
        });

        const isExported = funcNode.modifiers?.some(m => 
            m.kind === ts.SyntaxKind.ExportKeyword
        );

        const funcInfo = {
            signature: funcSignature,
            name: funcName,
            code: funcCode,
            filename: filePath,
            modifier: getAccessModifier(funcNode),
            isStatic: funcNode.modifiers?.some(m => m.kind === ts.SyntaxKind.StaticKeyword) || false,
            isExported: isExported,
            params,
            callees
        };

        methods.push(funcInfo);
        return funcInfo;
    } catch (error) {
        console.error(`提取函数信息失败：${error.message}`);
        return null;
    }
}

// 工具函数：获取访问修饰符（完全不变）
function getAccessModifier(node) {
    if (!node.modifiers) return 'public';
    
    if (node.modifiers.some(m => m.kind === ts.SyntaxKind.PrivateKeyword)) return 'private';
    if (node.modifiers.some(m => m.kind === ts.SyntaxKind.ProtectedKeyword)) return 'protected';
    return 'public';
}

// 工具函数：追加到JSONL文件（完全不变）
function appendToFile(filePath, dataList) {
    const validData = dataList.filter(data => 
        data.signature && data.name && data.code.trim() !== ''
    );
    if (validData.length === 0) return;
    const content = validData.map(data => JSON.stringify(data)).join('\n') + '\n';
    fs.appendFileSync(filePath, content, 'utf8');
}

// 校验输出文件并添加兜底数据（完全不变）
function checkOutputFiles(methodsPath, structsPath) {
    function countValidLines(path) {
        if (!fs.existsSync(path)) return 0;
        const content = fs.readFileSync(path, 'utf8');
        return content.split('\n').filter(line => {
            try {
                const data = JSON.parse(line);
                return data.signature && data.name;
            } catch (e) {
                return false;
            }
        }).length;
    }
    
    const methodCount = countValidLines(methodsPath);
    const structCount = countValidLines(structsPath);
    
    if (methodCount === 0) {
        console.warn('未生成有效函数数据，添加默认函数');
        fs.appendFileSync(methodsPath, JSON.stringify({
            signature: 'default.test',
            name: 'test',
            code: 'export function test() { return "default"; }',
            filename: 'default.ts',
            modifier: 'public',
            isExported: true,
            params: [],
            callees: []
        }) + '\n');
    }
    
    if (structCount === 0) {
        console.warn('未生成有效类数据，添加默认类');
        fs.appendFileSync(structsPath, JSON.stringify({
            signature: 'default.TestClass',
            name: 'TestClass',
            code: 'export class TestClass { id: number; }',
            filename: 'default.ts',
            modifier: 'public',
            isExported: true,
            attributes: [{ name: 'id', type: 'number', modifier: 'public' }],
            methods: []
        }) + '\n');
    }
}

// 当无TS文件时写入默认数据（完全不变）
function writeDefaultData(outputPath) {
    const methodsPath = path.join(outputPath, 'methods.jsonl');
    const structsPath = path.join(outputPath, 'structs.jsonl');
    
    fs.writeFileSync(methodsPath, JSON.stringify({
        signature: 'default.hello',
        name: 'hello',
        code: 'export function hello() { return "hello"; }',
        filename: 'default.ts',
        modifier: 'public',
        isExported: true,
        params: [],
        callees: []
    }) + '\n');
    
    fs.writeFileSync(structsPath, JSON.stringify({
        signature: 'default.DefaultClass',
        name: 'DefaultClass',
        code: 'export class DefaultClass { name: string; }',
        filename: 'default.ts',
        modifier: 'public',
        isExported: true,
        attributes: [{ name: 'name', type: 'string', modifier: 'public' }],
        methods: []
    }) + '\n');
}

// 其他工具方法（完全不变）
function fileEncoding(path) {
    const encodings = ['utf-8', 'gbk', 'utf-16', 'ISO-8859-1'];
    const fileBuffer = fs.readFileSync(path);

    for (const enc of encodings) {
        try {
            const decodedContent = iconv.decode(fileBuffer, enc);
            if (decodedContent.length > 0 || fileBuffer.length === 0) {
                return enc;
            }
        } catch (error) {
            continue;
        }
    }

    throw new Error(`UnicodeDecodeError: 无法识别文件编码：${path}`);
}

function loadCallgraph(ctx) {
    const visibleSets = getVisibleFunctions(ctx);
    console.log(`[loadCallgraph] 可见函数数量：${visibleSets.size}`);

    const methodsPath = path.join(ctx.output_path, 'methods.jsonl');
    if (!fs.existsSync(methodsPath)) {
        console.warn(`methods.jsonl 不存在：${methodsPath}`);
        return;
    }

    try {
        const enc = fileEncoding(methodsPath);
        const fileContent = iconv.decode(fs.readFileSync(methodsPath), enc);
        const lines = fileContent.split('\n').filter(line => line.trim());

        lines.forEach((line, index) => {
            try {
                const funcData = JSON.parse(line);
                
                const required = ['signature', 'name', 'code', 'filename', 'modifier', 'params', 'callees'];
                if (!required.every(field => funcData.hasOwnProperty(field))) {
                    console.warn(`跳过无效函数数据（行${index+1}）：缺少字段 ${required.filter(f => !funcData.hasOwnProperty(f))}`);
                    return;
                }

                const funcFullPath = path.join(ctx.resource_path, funcData.filename);
                if (!fs.existsSync(funcFullPath)) {
                    console.warn(`文件不存在，跳过函数（行${index+1}）：${funcFullPath}`);
                    return;
                }

                const params = funcData.params.map(p => 
                    new FieldDefTS(p.name, p.type, p.modifier || 'public')
                );

                const isVisible = visibleSets.has(funcData.name) && !funcData.isStatic;

                const funcDef = new FuncDefTS(
                    funcData.signature,
                    funcData.name,
                    funcData.code,
                    funcData.filename,
                    isVisible,
                    funcData.modifier,
                    params
                );

                ctx.callgraph.addNode(funcDef.signature, funcDef);

            } catch (error) {
                console.warn(`跳过无效JSON（行${index+1}）：${error.message}`);
            }
        });

        buildCallgraphEdges(ctx, methodsPath, enc);
        const edgeCount = Array.from(ctx.callgraph.edges.values()).reduce((sum, set) => sum + set.size, 0);
        console.log(`[loadCallgraph] 函数调用图：节点=${ctx.callgraph.nodes.size}，边=${edgeCount}`);

    } catch (error) {
        console.error(`加载函数调用图失败：${error.message}`);
    }
}

function loadClazzCallgraph(ctx, outputPath) {
    const structsPath = path.join(ctx.output_path, 'structs.jsonl');
    if (!fs.existsSync(structsPath)) {
        console.warn(`structs.jsonl 不存在：${structsPath}`);
        return;
    }

    let skipEmptyClassCount = 0;
    const skipLogInterval = 100;

    try {
        const enc = fileEncoding(structsPath);
        const fileContent = iconv.decode(fs.readFileSync(structsPath), enc);
        const lines = fileContent.split('\n').filter(line => line.trim());

        lines.forEach((line, index) => {
            try {
                const clazzData = JSON.parse(line);
                
                const required = ['signature', 'name', 'code', 'filename', 'modifier', 'attributes', 'methods'];
                if (!required.every(field => clazzData.hasOwnProperty(field))) {
                    console.warn(`跳过无效类数据（行${index+1}）：缺少字段 ${required.filter(f => !clazzData.hasOwnProperty(f))}`);
                    return;
                }

                const fields = clazzData.attributes.map(attr => 
                    new FieldDefTS(attr.name, attr.type, attr.modifier || 'public')
                );

                let functions = [];
                if (clazzData.methods && Array.isArray(clazzData.methods)) {
                    functions = clazzData.methods
                        .map(sig => ctx.func(sig))
                        .filter(Boolean);
                }

                if (functions.length === 0) {
                    const allFuncs = Array.from(ctx.callgraph.nodes.values()).map(n => n.attr);
                    const matchedFuncs = [];
                    
                    allFuncs.forEach(func => {
                        if (matchedFuncs.length >= 5) return;
                        
                        func.params.some(param => {
                            const pureType = trimType(param.signature);
                            if (pureType === clazzData.name) {
                                matchedFuncs.push(func);
                                return true;
                            }
                            return false;
                        });
                    });
                    
                    functions = matchedFuncs;
                }

                if (fields.length === 0 && functions.length === 0) {
                    skipEmptyClassCount++;
                    if (skipEmptyClassCount % skipLogInterval === 0) {
                        console.log(`[空类统计] 已跳过 ${skipEmptyClassCount} 个空类（当前仓库输出路径：${outputPath}）`);
                    }
                    return;
                }

                const clazzDef = new ClazzDefTS(
                    clazzData.signature,
                    clazzData.name,
                    clazzData.code,
                    fields,
                    functions,
                    clazzData.filename,
                    clazzData.modifier === 'public'
                );

                ctx.clazz_callgraph.addNode(clazzDef.signature, clazzDef);

            } catch (error) {
                console.warn(`跳过无效JSON（行${index+1}）：${error.message}`);
            }
        });

        buildClazzEdges(ctx);
        removeCycleFromGraph(ctx.clazz_callgraph);
        
        console.log(`[空类统计] 共跳过 ${skipEmptyClassCount} 个空类`);
        const edgeCount = Array.from(ctx.clazz_callgraph.edges.values()).reduce((sum, set) => sum + set.size, 0);
        console.log(`[loadClazzCallgraph] 类调用图：节点=${ctx.clazz_callgraph.nodes.size}，边=${edgeCount}`);

    } catch (error) {
        console.error(`加载类调用图失败：${error.message}`);
    }
}

function getVisibleFunctions(ctx) {
    const tagsPath = path.join(ctx.output_path, 'tags');
    const visibleFuncs = new Set();

    if (!fs.existsSync(tagsPath)) {
        console.log(`生成tags文件：${tagsPath}`);
        try {
            const ctagsCmd = [
                'ctags', '-R',
                '--languages=TypeScript',
                '--ts-kinds=f',
                `-f ${tagsPath}`,
                ctx.resource_path
            ].join(' ');

            execSync(ctagsCmd, { stdio: 'ignore' });
        } catch (error) {
            console.warn(`ctags执行失败：${error.message}，返回空集合`);
            return visibleFuncs;
        }
    }

    try {
        const enc = fileEncoding(tagsPath);
        const tagsContent = iconv.decode(fs.readFileSync(tagsPath), enc);
        
        tagsContent.split('\n').forEach(line => {
            line = line.trim();
            if (!line || line.startsWith('!')) return;
            
            const parts = line.split('\t');
            if (parts.length >= 4 && parts[3].startsWith('f')) {
                const funcName = parts[0];
                const fileName = parts[1];
                if (fileName.endsWith('.ts') || fileName.endsWith('.d.ts')) {
                    visibleFuncs.add(funcName);
                }
            }
        });
    } catch (error) {
        console.warn(`解析tags文件失败：${error.message}`);
    }

    return visibleFuncs;
}

function buildClassCode(clazzData) {
    try {
        const rawClassCode = clazzData.content.substring(clazzData.start, clazzData.end);
        const lines = rawClassCode.split(/\r?\n/).map(line => line.trimEnd());
        
        const nonEmptyLines = lines.filter(line => line.trim() !== '');
        if (nonEmptyLines.length === 0) {
            console.warn(`类 ${clazzData.name} 无有效代码`);
            return '';
        }
        
        const indentCounts = nonEmptyLines.map(line => {
            const match = line.match(/^(\s*)/);
            return match ? match[1].length : 0;
        });
        const minIndent = Math.min(...indentCounts);
        
        const cleanedLines = lines
            .map(line => {
                const trimmed = minIndent > 0 ? line.substring(minIndent) : line;
                const commentIndex = trimmed.indexOf('//');
                return commentIndex > -1 ? trimmed.substring(0, commentIndex).trimEnd() : trimmed;
            })
            .filter(line => line.trim() !== '');
        
        let classCode = cleanedLines.join('\n').trim();
        const maxLength = 10000;
        if (classCode.length > maxLength) {
            classCode = classCode.substring(0, maxLength) + '\n// ...（代码过长，已截断）';
        }
        
        return classCode;
    } catch (error) {
        console.warn(`处理类 ${clazzData.name} 代码失败：${error.message}`);
        return '';
    }
}

function trimType(typeStr) {
    if (!typeStr || typeof typeStr !== 'string') {
        return '';
    }

    let cleaned = typeStr.replace(/\[\s*\]/g, '');
    const genericRegex = /<[^<>]*>/g;
    while (genericRegex.test(cleaned)) {
        cleaned = cleaned.replace(genericRegex, '');
    }
    cleaned = cleaned.split(/[|&]/)[0].trim();
    const basicTypes = new Set([
        'string', 'number', 'boolean', 'any', 'void', 'null', 'undefined',
        'object', 'symbol', 'bigint', 'unknown'
    ]);
    const match = cleaned.match(/([A-Z][a-zA-Z0-9_]*)/);

    if (match) {
        const className = match[1];
        return basicTypes.has(className.toLowerCase()) ? '' : className;
    }

    return '';
}

function buildCallgraphEdges(ctx, methodsPath, enc) {
    const fileContent = iconv.decode(fs.readFileSync(methodsPath), enc);
    const lines = fileContent.split('\n').filter(line => line.trim());

    lines.forEach(line => {
        try {
            const funcData = JSON.parse(line);
            const callerSig = funcData.signature;
            
            if (!ctx.callgraph.hasNode(callerSig)) return;
            
            funcData.callees.forEach(calleeSig => {
                if (ctx.callgraph.hasNode(calleeSig)) {
                    ctx.callgraph.addEdge(callerSig, calleeSig);
                }
            });
        } catch (error) {
            // 忽略无效行
        }
    });
}

function buildClazzEdges(ctx) {
    const clazzSignatures = Array.from(ctx.clazz_callgraph.nodes.keys());
    
    clazzSignatures.forEach(clazzSig => {
        const clazzNode = ctx.clazz_callgraph.getNode(clazzSig);
        const clazzDef = clazzNode.attr;

        clazzDef.fields.forEach(field => {
            const pureFieldType = trimType(field.signature);
            if (!pureFieldType) return;

            const targetSig = Array.from(ctx.clazz_callgraph.nodes.keys()).find(sig => {
                return ctx.clazz_callgraph.getNode(sig).attr.name === pureFieldType;
            });

            if (targetSig && targetSig !== clazzSig) {
                ctx.clazz_callgraph.addEdge(clazzSig, targetSig);
            }
        });
    });
}

function removeCycleFromGraph(graph) {
    const nodes = Array.from(graph.nodes.keys());
    const visited = new Set();
    const recStack = new Set();

    function hasCycle(node) {
        if (!visited.has(node)) {
            visited.add(node);
            recStack.add(node);

            const neighbors = Array.from(graph.edges.get(node) || []);
            for (const neighbor of neighbors) {
                if (!visited.has(neighbor) && hasCycle(neighbor)) {
                    return true;
                } else if (recStack.has(neighbor)) {
                    graph.edges.get(node).delete(neighbor);
                    return true;
                }
            }
        }
        recStack.delete(node);
        return false;
    }

    nodes.forEach(node => {
        if (!visited.has(node)) {
            hasCycle(node);
        }
    });
}

// 执行主函数
main();

// 替换 CommonJS 的 module.exports 为 ES模块的 export
export {
    FieldDefTS,
    FuncDefTS,
    ClazzDefTS,
    DiGraph,
    EvaContextTS
};