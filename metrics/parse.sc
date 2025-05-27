import ujson.{Arr, Obj}
import java.nio.file.{Files, Paths}
import scala.util.Using

def isValidMethod(m: Method): Boolean = {
    !m.isExternal && !m.filename.startsWith("<") && !m.code.startsWith("<")
}

def isValidStruct(s: TypeDecl): Boolean = {
    val regex = """(class|struct)[\w\s]*\{""".r
    !s.isExternal && !s.filename.startsWith("<") && !s.code.startsWith("<") &&
        regex.findFirstIn(s.code).isDefined
}

def getTypeFullName(t: String): String = {
    if (t == "ANY") {
        return "auto"
    }
    t
}

def generateFunctionSignature(m: Method): String = {
    val name = m.name
    val returnType = getTypeFullName(m.methodReturn.typeFullName)
    val parameters = m.parameter.map(p => s"${getTypeFullName(p.typeFullName)}").mkString(", ")
    s"$returnType $name($parameters)"
}


// Note: don't write Chinese comments
@main def exec(path: String, output: String) = {
    importCode(path)

    // get methods with callers
    val methods = cpg.method
      .filter(isValidMethod)
      .map { m =>
        val callees = m.callOut
          .filter(_.methodFullName.nonEmpty)
          .map(c => cpg.method.fullNameExact(c.methodFullName).head)
          .filter(isValidMethod)
          .map(generateFunctionSignature)
          .distinct
          .toList
        Obj(
            "name" -> m.name,
            "signature" -> generateFunctionSignature(m),
            "beginLine" -> m.lineNumber.head,
            "endLine" -> m.lineNumberEnd.head,
            "filename" -> m.filename,
            "modifier" -> m.modifier.modifierType,
            "params" -> Arr.from(m.parameter
              .map(p => Obj("name" -> p.name, "type" -> getTypeFullName(p.typeFullName)))
              .toList),
            "returnType" -> getTypeFullName(m.methodReturn.typeFullName),
            "callees" -> Arr.from(callees)
        )
     }

    // get structs/classes with attributes and methods
    val structs = cpg.typeDecl
      .filter(isValidStruct)
      .map { c =>
        val methods = c.method
          .filter(isValidMethod)
          .map(generateFunctionSignature)
          .toList
        val attributes = c.member
          .map(f => Obj("name" -> f.name, "type" -> getTypeFullName(f.typeFullName), "modifier" -> f.modifier.modifierType))
          .toList
        Obj(
            "name" -> c.name,
            "fullname" -> c.fullName,
            "filename" -> c.filename,
            "beginLine" -> c.lineNumber.head,
            "methods" -> Arr.from(methods),
            "attributes" -> Arr.from(attributes)
        )
     }

    val outputPath = Paths.get(output, "methods.jsonl")
    // write to file
    Using(Files.newBufferedWriter(outputPath)) { writer =>
      methods.foreach { method =>
        writer.write(method.render() + "\n")
      }
    }.get

    val structPath = Paths.get(output, "structs.jsonl")
    // write to file
    Using(Files.newBufferedWriter(structPath)) { writer =>
      structs.foreach { s =>
        writer.write(s.render() + "\n")
      }
    }.get

    // close the project
    delete
}