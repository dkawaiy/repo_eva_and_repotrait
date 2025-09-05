import ujson.{Arr, Obj}
import java.nio.file.{Files, Paths}
import scala.util.Using

// JS-specific method filtering: not external, no <unknown> filename, no <empty> code, and name clean
def isValidMethod(m: Method): Boolean = {
  val name = m.name.stripPrefix("<").stripSuffix(">")
  m.code != "<empty>" && name == m.name && name!=":program"
}

// JS-specific typedef filtering: must start with "class "
def isValidTypeDecl(s: TypeDecl): Boolean = {
  s.code.startsWith("class ")
}

def getTypeFullName(t: String): String = {
  if (t == "ANY") "auto" else t
}

def generateFunctionSignature(m: Method): String = {
  def clean(s: String): String =
    s.replaceAll("[\\n\\t\\r]", "").trim

  val name = clean(m.name)
  val returnType = clean(getTypeFullName(m.methodReturn.typeFullName))
  val parameters = m.parameter.map(p => clean(getTypeFullName(p.typeFullName))).mkString(", ")

  s"$returnType $name($parameters)"
}
/*
def generateFunctionSignature(m: Method): String = {
  val name = m.name
  val returnType = getTypeFullName(m.methodReturn.typeFullName)
  val parameters = m.parameter.map(p => getTypeFullName(p.typeFullName)).mkString(", ")
  s"$returnType $name($parameters)"
}
*/
@main def exec(path: String, output: String) = {
  importCode(path)

  val methods = cpg.method
    .filter(isValidMethod)
    .map { m =>
      val callees = m.callOut
        .filter(_.methodFullName.nonEmpty)
        .map(c => cpg.method.fullNameExact(c.methodFullName).headOption)
        .collect { case Some(callee) if isValidMethod(callee) => generateFunctionSignature(callee) }
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

  val structs = cpg.typeDecl
    .filter(isValidTypeDecl)
    .map { c =>
      val methods = c.method
        .filter(isValidMethod)
        .map(generateFunctionSignature)
        .toList

      val attributes = c.member
        .map(f => Obj(
          "name" -> f.name,
          "type" -> getTypeFullName(f.typeFullName),
          "modifier" -> f.modifier.modifierType
        ))
        .toList

      Obj(
        "name" -> c.name,
        "fullname" -> c.fullName,
        "filename" -> c.filename,
        "beginLine" -> c.lineNumber.head,
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
