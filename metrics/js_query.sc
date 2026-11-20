import ujson.{Arr, Obj}
import java.nio.file.{Files, Paths}
import scala.util.Using
// 引入节点类型
import io.shiftleft.codepropertygraph.generated.nodes.{Method, TypeDecl, MethodRef, Call, Expression}
// 引入 Joern 核心隐式转换
import io.shiftleft.semanticcpg.language._ 

// --- 辅助函数 ---

def resolveMethodName(m: Method, nameMap: Map[String, String]): String = {
  nameMap.getOrElse(m.fullName, m.name)
}

def isValidMethod(m: Method, nameMap: Map[String, String]): Boolean = {
  val realName = resolveMethodName(m, nameMap)
  m.code != "<empty>" && 
  realName != ":program" && 
  !realName.startsWith("<operator>") &&
  // 如果是 lambda 且没在 map 里找到名字，视为纯匿名，这里选择保留（防止漏掉回调逻辑），若想过滤可改为 false
  !(realName.contains("<lambda>") && !nameMap.contains(m.fullName))
}

def isValidTypeDecl(s: TypeDecl): Boolean = {
  s.code.startsWith("class") || (s.name.headOption.exists(_.isUpper) && !s.name.contains("<"))
}

def getTypeFullName(t: String): String = {
  if (t == "ANY") "auto" else t
}

def generateFunctionSignature(m: Method, nameMap: Map[String, String]): String = {
  def clean(s: String): String = s.replaceAll("[\\n\\t\\r]", "").trim
  val name = clean(resolveMethodName(m, nameMap))
  val returnType = clean(getTypeFullName(m.methodReturn.typeFullName))
  val parameters = m.parameter.map(p => clean(getTypeFullName(p.typeFullName))).mkString(", ")
  s"$returnType $name($parameters)"
}

@main def exec(path: String, output: String) = {
  importCode(path)

  println("正在构建匿名函数名称映射...")

  // ======================================================================================
  // 修正部分：直接操作节点对象，不使用 .l 或 .head
  // ======================================================================================
  val assignedMethodsMap = cpg.call
    .name("<operator>.assignment")
    .l // 将 Call 集合先转为 List，避免迭代器并发修改问题
    .flatMap { call =>
      try {
        // 之前的报错证明 argument(n) 返回的是单个 Expression 对象
        val rhs = call.argument(2)
        
        // 使用原生 Scala 类型检查
        if (rhs.isInstanceOf[MethodRef]) {
          val mr = rhs.asInstanceOf[MethodRef]
          // 获取左值代码（变量名）
          val lhsCode = call.argument(1).code 
          Some(mr.methodFullName -> lhsCode)
        } else {
          None
        }
      } catch {
        // 如果 argument(n) 不存在或访问越界，catch 住防止脚本崩溃
        case _: Throwable => None
      }
    }
    .toMap

  println(s"映射构建完成，共找到 ${assignedMethodsMap.size} 个被赋值的函数。")

  // ======================================================================================
  // 主解析流程
  // ======================================================================================

  val methods = cpg.method
    .filter(m => isValidMethod(m, assignedMethodsMap))
    .map { m =>
      val realName = resolveMethodName(m, assignedMethodsMap)
      val signature = generateFunctionSignature(m, assignedMethodsMap)

      // --- 针对你提供的文档：处理动态调用 (METHOD_FULL_NAME 可能为空) ---
      val callees = m.callOut
        .flatMap { c =>
           // 策略 1: 如果 Joern 解析出了 FullName，直接用
           if (c.methodFullName != null && c.methodFullName != "<unknown>" && c.methodFullName.nonEmpty) {
             cpg.method.fullNameExact(c.methodFullName).headOption
           } else {
             // 策略 2: 如果 FullName 为空（JS 常见情况），尝试用 name 模糊匹配
             // 注意：这可能会有误报，但比什么都没有好
             cpg.method.nameExact(c.name).headOption
           }
        }
        .filter(callee => isValidMethod(callee, assignedMethodsMap)) // 过滤掉无效的 callee
        .map(callee => generateFunctionSignature(callee, assignedMethodsMap))
        .distinct
        .toList

      Obj(
        "name" -> realName,
        "signature" -> signature,
        "beginLine" -> m.lineNumber.headOption.getOrElse(-1),
        "endLine" -> m.lineNumberEnd.headOption.getOrElse(-1),
        "filename" -> m.filename,
        "modifier" -> m.modifier.modifierType.headOption.getOrElse(""),
        "params" -> Arr.from(m.parameter
          .map(p => Obj("name" -> p.name, "type" -> getTypeFullName(p.typeFullName)))
          .toList),
        "returnType" -> getTypeFullName(m.methodReturn.typeFullName),
        "callees" -> Arr.from(callees)
      )
    }

  val structs = cpg.typeDecl
    .filter(isValidTypeDecl)
    .map { c =>
      val methods = c.method
        .filter(m => isValidMethod(m, assignedMethodsMap))
        .map(m => generateFunctionSignature(m, assignedMethodsMap))
        .toList

      val attributes = c.member
        .map(f => Obj(
          "name" -> f.name,
          "type" -> getTypeFullName(f.typeFullName),
          "modifier" -> f.modifier.modifierType.headOption.getOrElse("")
        ))
        .toList

      Obj(
        "name" -> c.name,
        "fullname" -> c.fullName,
        "filename" -> c.filename,
        "beginLine" -> c.lineNumber.headOption.getOrElse(-1),
        "inheritsFromTypeFullName" -> Arr.from(c.inheritsFromTypeFullName.toList),
        "methods" -> Arr.from(methods),
        "attributes" -> Arr.from(attributes)
      )
    }

  val outputPath = Paths.get(output, "methods.jsonl")
  Using(Files.newBufferedWriter(outputPath)) { writer =>
    methods.foreach { method =>
      writer.write(method.render() + "\n")
    }
  }.get

  val structPath = Paths.get(output, "typedefs.jsonl")
  Using(Files.newBufferedWriter(structPath)) { writer =>
    structs.foreach { s =>
      writer.write(s.render() + "\n")
    }
  }.get

  delete
}