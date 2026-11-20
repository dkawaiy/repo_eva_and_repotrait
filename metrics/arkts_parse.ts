// cli_callgraph.ts
// 就是这个版本呢

//在windows运行的时候要把模块改成正确的路径
import * as fs from "fs";// 这一块目前还只能调用arkanalyzer目录下的脚本
import * as path from "path";
import { SceneConfig } from "/arkanalyzer/src/Config";
import { Scene } from "/arkanalyzer/src/Scene";
import { ArkFile } from "/arkanalyzer/src/core/model/ArkFile";
import { ArkClass } from "/arkanalyzer/src/core/model/ArkClass";
import { ArkMethod } from "/arkanalyzer/src/core/model/ArkMethod";
import { ArrayType, ClassType, Type, UnionType } from "/arkanalyzer/src/core/base/Type";
import * as ts from "typescript";

// 工具函数：生成方法签名
function generateFunctionSignature(m: ArkMethod): string {
    const name = m.getName();
    const returnType = getTypeFullName(m.getReturnType());
    const parameters = m.getParameters()
        .map(p => `${getTypeFullName(p.getType())}`)
        .join(", ");
    const owner = m.getDeclaringArkClass();
    const ownerName = owner.getName().startsWith('%') ? '' : `${owner.getName()}.`;
    return `${m.getDeclaringArkFile().getName()}:${ownerName}${name}(${parameters}) → ${returnType}`;
}

function classKey(cls: ArkClass): string {
    return cls.getSignature().toString();
}

function referencedClassKeys(type: Type): string[] {
    if (type instanceof ClassType) return [type.getClassSignature().toString()];
    if (type instanceof ArrayType) return referencedClassKeys(type.getBaseType());
    if (type instanceof UnionType) return type.getTypes().flatMap(referencedClassKeys);
    return [];
}

// 工具函数：获取类型全名
function getTypeFullName(type: any): string {
    const name = typeof type === "string" ? type : type.toString();
    return name.replace(/@[^\s<>]+:\s*/g, '');
}

// 工具函数：检查方法是否有效
// 工具函数：检查类是否有效
function isValidClass(cls: ArkClass): boolean {
    // 行号为 null 或小于等于 0
    const line = cls.getLine();
    if (line === null || line <= 0) {
        return false;
    }
    // 名称以 % 开头
    if (cls.getName().startsWith('%')) {
        return false;
    }
    // code 为空或全是空白
    const code = cls.getCode();
    if (!code || code.trim() === '') {
        return false;
    }
    return true;
}

function isValidMethod(method: ArkMethod): boolean {
    // 如果方法的起始行号为 null 或小于等于 0，则认为无效
    const line = method.getLine();
    if (line === null || line <= 0) {
        return false;
    }

    // 过滤掉名称以 % 开头的方法
    if (method.getName().startsWith('%')) {
        return false;
    }

    // 过滤掉 code 为空的方法
    const code = method.getCode();
    if (!code || code.trim() === '') {
        return false;
    }

    return true;
}



// 工具函数：获取方法调用的 callee 列表
/*
function getCallees(method: ArkMethod): string[] {
    if (!method.getCallOut) return [];
    const callees = method.getCallOut();
    if (!callees) return [];
    return callees.map(callee => callee.methodFullName || callee.getName());
}
*/

async function main() {
    const args = process.argv.slice(2);
    if (args.length < 2) {
        console.error("Usage: ts-node cli_callgraph.ts <project_dir> <output_file>");
        process.exit(1);
    }

    const projectDir = path.resolve(args[0]);
    const outputFile = path.resolve(args[1]);


    // 初始化场景配置
    const sceneConfig = new SceneConfig();
    sceneConfig.buildFromProjectDir(projectDir);

    const scene = new Scene();
    scene.buildSceneFromProjectDir(sceneConfig);
    scene.inferTypes();

    const files: ArkFile[] = scene.getFiles();
    const allClassInfos: any[] = [];

    const methods: ArkMethod[] = scene.getMethods();
    const allMethodInfos: any[] = [];
    const methodKeys = new Map<string, string>();

    for (const method of methods) {
        // 检查方法是否有效
        if (!isValidMethod(method)) {
            continue;
        }

        // 提取参数信息
        const params = method.getParameters().map(p => ({
            name: p.getName(),
            type: getTypeFullName(p.getType())
        }));
        
        const outer = method.getOuterMethod();
        let outerSignature = "";
        if (outer && isValidMethod(outer)) {
            outerSignature = generateFunctionSignature(outer);
        }
        
        // 构建方法信息对象
        const signature = generateFunctionSignature(method);
        methodKeys.set(method.getSignature().toString(), signature);
        allMethodInfos.push({
            name: method.getName(),
            signature,
            originalSignature: method.getSignature(),
            code: method.getCode(),
            beginLine: method.getLine(),
            //endLine: method.getEndLine(),
            filename: method.getDeclaringArkFile().getName(),
            modifiers: method.getModifiers(),
            access: method.isPublic() ? 'public' : method.isPrivate() ? 'private' : method.isProtected() ? 'protected' : 'default',
            params: params,
            outermethod: outerSignature,
            returnType: getTypeFullName(method.getReturnType()),
            //callees: callees
        });
    }

    // 根据 allMethodInfos 生成 entryPoints
    const entryPoints = allMethodInfos.map(info => info.originalSignature);
    // 构建调用图
    const callGraph = scene.makeCallGraphCHA(entryPoints);

    // 为每个方法节点生成 callees
    for (const methodInfo of allMethodInfos) {
        const cgNode = callGraph.getCallGraphNodeByMethod(methodInfo.originalSignature);
        if (!cgNode || !cgNode.getOutgoingEdges) {
            methodInfo.callees = [];
            continue;
        }
        // 获取所有被调用方法
        const outgoingEdges = Array.from(cgNode.getOutgoingEdges());
        const callees = outgoingEdges
            .map((edge: any) => {
                const dstNode = edge.getDstNode();
                if (dstNode && dstNode.getMethod()) {
                    return methodKeys.get(dstNode.getMethod().toString());
                }
                return null;
            })
            .filter((sig: any) => !!sig);
        methodInfo.callees = [...new Set(callees)];
    }



    for (const file of files) {
        const classes: ArkClass[] = file.getClasses();
        for (const cls of classes) {
            // 判断类节点是否合法
            if (!isValidClass(cls)) {
                continue;
            }

            // 提取类内方法信息
            const methods = cls.getMethods()
                .filter((m: ArkMethod) => methodKeys.has(m.getSignature().toString()))
                .map((m: ArkMethod) => ({
                    signature: generateFunctionSignature(m),
                }));

            // 提取类属性（可选）
            const attributes = cls.getFields ? cls.getFields().map(f => ({
                name: f.getName(),
                type: getTypeFullName(f.getType()),
                access: f.isPrivate() ? 'private' : f.isProtected() ? 'protected' : 'public',
                classKeys: referencedClassKeys(f.getType()),
            })) : [];
            const parent = cls.getSuperClass();
            const interfaces = cls.getImplementedInterfaceNames()
                .map(name => cls.getImplementedInterface(name))
                .filter((target): target is ArkClass => target != null);
            
            allClassInfos.push({
                name: cls.getName(),
                //signature: cls.getSignature(),//注意修改

                fullname: cls.getName()+' '+file.getName(),
                classKey: classKey(cls),
                code: cls.getCode(),
                parentKey: parent ? classKey(parent) : null,
                interfaceKeys: interfaces.map(classKey),
                filename: file.getName(),
                beginLine: cls.getLine(),
                methods,
                attributes,
                modifiers: cls.getModifiers()
            });
        }
    }

    // ArkAnalyzer does not expose top-level arrow variables or bodyless abstract
    // declarations as usable methods. Recover only these source declarations.
    for (const file of files) {
        const filename = file.getName();
        const sourcePath = path.join(projectDir, filename);
        if (!fs.existsSync(sourcePath)) continue;
        const source = ts.createSourceFile(filename, fs.readFileSync(sourcePath, "utf8"), ts.ScriptTarget.Latest, true);
        const addDeclaration = (name: string, owner: string, node: ts.Node,
                                parameters: ts.NodeArray<ts.ParameterDeclaration>, returnType: ts.TypeNode | undefined,
                                code: string) => {
            const params = parameters.map(p => ({
                name: p.name.getText(source),
                type: p.type?.getText(source) || "unknown"
            }));
            const signature = `${filename}:${owner ? owner + "." : ""}${name}(${params.map(p => p.type).join(", ")}) → ${returnType?.getText(source) || "unknown"}`;
            if (allMethodInfos.some(info => info.signature === signature)) return;
            allMethodInfos.push({
                name, signature, originalSignature: signature, code,
                beginLine: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
                filename, modifiers: 0, access: owner ? "public" : "default",
                params, outermethod: "", returnType: returnType?.getText(source) || "unknown", callees: []
            });
            if (owner) {
                const classInfo = allClassInfos.find(info => info.name === owner && info.filename === filename);
                if (classInfo) classInfo.methods.push({ signature });
            }
        };
        for (const statement of source.statements) {
            if (ts.isVariableStatement(statement)) {
                for (const declaration of statement.declarationList.declarations) {
                    if (ts.isIdentifier(declaration.name) && declaration.initializer && ts.isArrowFunction(declaration.initializer)) {
                        const arrow = declaration.initializer;
                        addDeclaration(declaration.name.text, "", statement, arrow.parameters, arrow.type, statement.getText(source));
                    }
                }
            } else if (ts.isClassDeclaration(statement) && statement.name) {
                for (const member of statement.members) {
                    if (ts.isMethodDeclaration(member) && !member.body &&
                        member.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.AbstractKeyword)) {
                        addDeclaration(member.name.getText(source), statement.name.text, member,
                                       member.parameters, member.type, member.getText(source));
                    }
                }
            }
        }
    }
    // 确保输出目录存在
    if (!fs.existsSync(outputFile)) {
        fs.mkdirSync(outputFile, { recursive: true });
    }


    // 写入前移除 originalSignature 字段
    const methodsOutputFile = path.resolve(outputFile, "methods.jsonl");
    const outputMethodInfos = allMethodInfos.map(info => {
        const { originalSignature, ...rest } = info;
        return rest;
    });
    const methodStream = fs.createWriteStream(methodsOutputFile, { flags: "w" });
    outputMethodInfos.forEach(obj => {
        methodStream.write(JSON.stringify(obj) + "\n");
    });
    methodStream.end();
    console.log(`All method info has been written to ${methodsOutputFile}`);

    // 将类信息以 jsonl 格式写入文件
    const classesOutputFile = path.resolve(outputFile, "classes.jsonl");
    const classStream = fs.createWriteStream(classesOutputFile, { flags: "w" });
    allClassInfos.forEach(obj => {
        classStream.write(JSON.stringify(obj) + "\n");
    });
    classStream.end();
    console.log(`All class info has been written to ${classesOutputFile}`);
}

main().catch(err => {
    console.error(err);
    process.exitCode = 1;
});
