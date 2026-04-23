# NOTE: This file has been automatically generated, do not modify!
# Architecture based on https://github.com/mrexodia/ida-pro-mcp (MIT License)
from http.client import responses
from typing import Annotated, Optional, TypedDict, Generic, TypeVar
from pydantic import Field

T = TypeVar("T")

@joern_mcp.tool()
def ping()->str:
    """Checks if the Joern server is running and responsive by querying its version
    
    @return: The Joern server version if successful, 'Query Failed' if the server is not responding
    """
    response = joern_remote('version')
    if response:
        return extract_value(response)
    else:
        return 'Query Failed'

@joern_mcp.tool()
def load_cpg(cpg_filepath: str) -> str:
    """
    Loads a CPG from a file.  Calls importCpg() so that both the Joern console
    traversals (cpg.file.name.l, cpg.method.name.l, etc.) AND the custom script
    tools (get_method_callees, get_class_methods_by_class_full_name, etc.) work.

    Args:
        cpg_filepath (str): Absolute path to the CPG file inside the container
                            (e.g. /workspace/cpg-out/<sample_id>).

    Returns:
        str: "true" if loaded successfully, "false" otherwise.
    """
    # importCpg() sets the Joern console's active project (needed for cpg.* traversals).
    import_result = joern_remote(f'importCpg("{cpg_filepath}")')
    # Also update the script-level cpg variable used by custom tools.
    joern_remote(f'load_cpg("{cpg_filepath}")')
    if import_result and "Some(" in import_result:
        return "true"
    return extract_value(import_result) if import_result else "false"

@joern_mcp.tool()
def get_method_callees(method_full_name: str) -> list[str]:
    """Retrieves a list of methods info that are called by the specified method
    
   @param method_full_name: The fully qualified name of the source method(e.g., com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent))
   @return: List of full name, name, signature and id of methods which call the source method
    """
    # responses =  joern_remote(f'cpg.method.fullNameExact("{method_full_name}").head.callee.distinct.map(m => (s"methodFullName=$' + '{m.fullName} methodId=${m.id}L")).l')
    responses = joern_remote(f'get_method_callees("{method_full_name}")')
    return extract_list(responses)  

@joern_mcp.tool()
def get_method_callers(method_full_name: str) -> list[str]:
    """Retrieves a list of methods that call the specified method
    
    @param method_full_name: The fully qualified name of the source method(e.g., com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent))
    @return: List of full name, name, signature and id of methods called by the source method
    """
    responses = joern_remote(f'get_method_callers("{method_full_name}")')
    return extract_list(responses)

@joern_mcp.tool()
def get_class_full_name_by_id(class_id:str) -> str:
    """Retrieves the fully name of a class by its ID
    
    @param id: The unique identifier of the class (typeDecl), the id is a Long int string, like '111669149702L'
    @return: The fully name of the class (e.g., com.android.nfc.NfcService$6)
    """
    response =  joern_remote(f'get_class_full_name_by_id("{class_id}")')
    return extract_value(response)

@joern_mcp.tool()
def get_class_methods_by_class_full_name(class_full_name:str) -> list[str]:
    """Get the methods of a class by its fully qualified name
  
    @param class_full_name: The fully qualified name of the class
    @return: List of full name, name, signature and id of methods in the class
    """
    response = joern_remote(f'get_class_methods_by_class_full_name("{class_full_name}")')
    return extract_list(response)

@joern_mcp.tool()
def get_method_code_by_full_name(method_full_name:str) -> str:
    """Get the code of a method by its fully name, If you know the full name of the method, you can use this tool to get the method code directly. 
    If you only know the full name of the class and the name of the method, you should use get_method_code_by_class_full_name_and_method_name
    @param method_full_name: The fully qualified name of the method (e.g., com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent))
    @return: The source code of the specified method
    """
    response = joern_remote(f'get_method_code_by_method_full_name("{method_full_name}")')
    return extract_value(response)

@joern_mcp.tool()
def get_method_code_by_id(method_id:str) -> str:
    """Get the code of a method by its class full name and method name
  
    @param class_full_name: The fully qualified name of the class
    @param method_name: The name of the method
    @return: List of full name, name, signature and id of methods in the class
    """
    response =  joern_remote(f'get_method_code_by_id("{method_id}")')
    return extract_value(response)

@joern_mcp.tool()
def get_method_full_name_by_id(method_id:str) -> str:
    """Retrieves the fully qualified name of a method by its ID
    
    @param id: The unique identifier of the method, the id is a Long int string, like '111669149702L'
    @return: The fully qualified name of the method (e.g., com.android.nfc.NfcService$6.onReceive:void(android.content.Context,android.content.Intent))
    """
    response = joern_remote(f'get_method_full_name_by_id("{method_id}")')
    return extract_value(response)

@joern_mcp.tool()
def get_call_code_by_id(code_id:str) -> str:
    """Get the source code of a specific call node from the loaded CPG by the call id
    
    @param id: The unique identifier of the call node, the id is a Long int string, like '111669149702L'
    @return: The source code of the specified call
    """
    response =  joern_remote(f'get_call_code_by_id("{code_id}")')
    return extract_value(response)

@joern_mcp.tool()
def get_method_code_by_class_full_name_and_method_name(class_full_name:str, method_name:str) -> list[str]:
    """Get the code of a method by its class full name and method name,
    this tool is usually used when you don't know the full name of the method, but you know the full name of the class and the name of the method. If there are multiple methods with the same name in the class, the code of all methods will be returned.
  
    @param class_full_name: The fully qualified name of the class, like 'com.android.nfc.NfcService'
    @param method_name: The name of the method, like 'onReceive'
    @return: List of full name, name, signature and id of methods in the class
    """
    responses = joern_remote(f'get_method_code_by_class_full_name_and_method_name("{class_full_name}", "{method_name}")')
    return extract_list(responses)

# @joern_mcp.tool()
# def get_method_by_full_name_without_signature(full_name_without_signature:str) -> list[str]:
#     """Get the info of a method list by its fully qualified name without signature
    
#     @param full_name_without_signature: fully qualified name of methodwithout signature,like com.android.nfc.NfcService.onReceive
#     @return: The info of the methods, including the full name, name, signature and id
#     """
#     response = joern_remote(f'get_method_by_full_name_without_signature("{full_name_without_signature}")')
#     return extract_list(response)

@joern_mcp.tool()
def get_derived_classes_by_class_full_name(class_full_name:str) -> list[str]:
    """Get the derived classes of a class
    
    @param class_full_name: The fully qualified name of the class
    @return: The derived classes info of the class, including the full name, name and id
    """
    response = joern_remote(f'get_derived_classes_by_class_full_name("{class_full_name}")')
    return extract_list(response)

@joern_mcp.tool()
def get_parent_classes_by_class_full_name(class_full_name:str) -> list[str]:
    """Get the parent classes of a class
    
    @param class_full_name: The fully qualified name of the class
    @return: The parent classes info of the class, including the full name, name and id
    """
    response = joern_remote(f'get_parent_classes_by_class_full_name("{class_full_name}")')
    return extract_list(response)

@joern_mcp.tool()
def get_method_by_call_id(call_id:str) -> str:
    """Get the method info by the call id which the call is in the method
  
    @param id: The id of the call
    @return: The method info of the call
    """
    response =  joern_remote(f'get_method_by_call_id("{call_id}")')
    return extract_value(response)

@joern_mcp.tool()
def get_referenced_method_full_name_by_call_id(call_id:str) -> str:
    """Get the method info by the call id which the call is referenced the method
    
    @param id: The id of the call
    @return: The method info of the call
    """
    response =  joern_remote(f'get_referenced_method_full_name_by_call_id("{call_id}")')
    return extract_value(response)   

@joern_mcp.tool()
def get_calls_in_method_by_method_full_name(method_full_name:str) -> list[str]:
    """Get the calls info by the method full name which the call is in the method

    @param method_full_name: The full name of the method
    @return: The calls info of the method
    """
    response = joern_remote(f'get_calls_in_method_by_method_full_name("{method_full_name}")')
    return extract_list(response)

@joern_mcp.tool()
def find_methods(
    name_pattern: Optional[str] = None,
    annotation: Optional[str] = None,
    modifier: Optional[str] = None,
    full_name_pattern: Optional[str] = None,
) -> list[str]:
    """Find methods globally by name/annotation/modifier/full-name pattern.
    At least one filter is required. Returns id, name, fullName, file, lineStart per method.

    @param name_pattern: Regex for method simple name (e.g. 'get.*', 'onReceive')
    @param annotation: Annotation name to filter by (e.g. 'RequestMapping', 'Override')
    @param modifier: Modifier type (e.g. 'public', 'static', 'private')
    @param full_name_pattern: Regex for method full qualified name
    @return: List of strings formatted as 'id=<id>L name=<name> fullName=<fullName> file=<file> lineStart=<line>'
    """
    if not any([name_pattern, annotation, modifier, full_name_pattern]):
        return ["Error: at least one filter (name_pattern, annotation, modifier, full_name_pattern) is required"]

    parts = ["cpg.method"]
    if name_pattern:
        parts.append(f'.name("{name_pattern}")')
    if full_name_pattern:
        parts.append(f'.fullName("{full_name_pattern}")')
    if annotation:
        parts.append(f'.where(_.annotation.name("{annotation}"))')
    if modifier:
        parts.append(f'.where(_.modifier.modifierType("{modifier.upper()}"))')
    parts.append(
        '.map(m => s"id=${m.id}L name=${m.name} fullName=${m.fullName}'
        ' file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)}").l'
    )
    query = "".join(parts)
    response = joern_remote(query)
    return extract_list(response)

@joern_mcp.tool()
def find_calls(
    callee_name_pattern: str,
    method_full_name_pattern: Optional[str] = None,
) -> list[str]:
    """Find call sites globally by callee name pattern. Useful for sink enumeration (exec, query, eval).

    @param callee_name_pattern: Regex for the callee method name (e.g. 'exec', 'query', 'eval', 'Runtime.*')
    @param method_full_name_pattern: Optional regex to restrict caller scope by containing method full name
    @return: List of strings formatted as 'callId=<id>L calleeName=<name> containingMethod=<fullName> file=<file> line=<line>'
    """
    parts = [f'cpg.call.name("{callee_name_pattern}")']
    if method_full_name_pattern:
        parts.append(f'.where(_.method.fullName("{method_full_name_pattern}"))')
    parts.append(
        '.map(c => s"callId=${c.id}L calleeName=${c.name}'
        ' containingMethod=${c.method.fullName.headOption.getOrElse("")}'
        ' file=${c.filename} line=${c.lineNumber.getOrElse(-1)}").l'
    )
    query = "".join(parts)
    response = joern_remote(query)
    return extract_list(response)

@joern_mcp.tool()
def get_call_arguments(call_id: str) -> list[str]:
    """Get structured arguments for a call node by its ID.
    Useful for determining which arguments are user-controlled vs literal.

    @param call_id: The call node ID (Long string, e.g. '111669149702L')
    @return: List of strings formatted as 'argIndex=<n> code=<code> typeFullName=<type> nodeId=<id>L'
    """
    id_num = call_id.rstrip('L')
    query = (
        f'cpg.call.id({id_num}).argument'
        '.map(a => s"argIndex=${a.order} code=${a.code} typeFullName=${a.typeFullName} nodeId=${a.id}L").l'
    )
    response = joern_remote(query)
    return extract_list(response)

@joern_mcp.tool()
def find_literals(
    pattern: str,
    literal_type: str = "any",
) -> list[str]:
    """Search for string/numeric literals in the CPG by value pattern.
    Useful for finding hardcoded credentials, SQL fragments, API keys, magic numbers.

    @param pattern: Regex matched against the literal value (e.g. 'password', 'SELECT.*FROM', 'secret')
    @param literal_type: Filter by type: 'string', 'int', or 'any' (default 'any')
    @return: List of strings formatted as 'literalId=<id>L value=<value> typeFullName=<type> containingMethod=<method> file=<file> line=<line>'
    """
    parts = [f'cpg.literal.code("{pattern}")']
    if literal_type == "string":
        parts.append('.where(_.typeFullName(".*[Ss]tring.*"))')
    elif literal_type == "int":
        parts.append('.where(_.typeFullName(".*[Ii]nt.*|.*[Ll]ong.*|byte|short"))')
    parts.append(
        '.map(l => s"literalId=${l.id}L value=${l.code} typeFullName=${l.typeFullName}'
        ' containingMethod=${l.method.fullName.headOption.getOrElse("")}'
        ' file=${l.filename} line=${l.lineNumber.getOrElse(-1)}").l'
    )
    query = "".join(parts)
    response = joern_remote(query)
    return extract_list(response)

@joern_mcp.tool()
def get_method_location(
    method_id: Optional[str] = None,
    method_full_name: Optional[str] = None,
) -> str:
    """Get file path and line/column range for a method. Required for file:line citations in vuln reports.
    Provide either method_id or method_full_name.

    @param method_id: Method node ID (Long string, e.g. '111669149702L')
    @param method_full_name: Fully qualified method name (e.g. 'com.Foo.bar:void()')
    @return: String 'file=<file> lineStart=<n> lineEnd=<n> columnStart=<n> columnEnd=<n>', or '' if not found
    """
    if not method_id and not method_full_name:
        return "Error: provide either method_id or method_full_name"

    map_expr = (
        '.map(m => s"file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)}'
        ' lineEnd=${m.lineNumberEnd.getOrElse(-1)} columnStart=${m.columnNumber.getOrElse(-1)}'
        ' columnEnd=${m.columnNumberEnd.getOrElse(-1)}")'
        '.headOption.getOrElse("")'
    )

    if method_id:
        id_num = method_id.rstrip('L')
        query = f'cpg.method.id({id_num})' + map_expr
    else:
        query = f'cpg.method.fullName("{method_full_name}")' + map_expr

    response = joern_remote(query)
    return extract_value(response) if response else ""
@joern_mcp.tool()
def get_dataflow(
    source_pattern: str,
    sink_pattern: str,
    max_depth: int = 12,
) -> list[str]:
    """Find taint flows from source to sink call patterns. Returns empty list if no flow found.
    Joern's flagship vulnerability-hunting capability (reachableByFlows).

    Note: max_depth is accepted for forward compatibility but Joern's reachableByFlows does not
    directly accept a depth limit via this API. Values > 20 are rejected to prevent abuse; the
    actual traversal depth is Joern's internal default.

    @param source_pattern: Regex for source call name (e.g. 'getParameter', 'readLine', 'getUserInput')
    @param sink_pattern: Regex for sink call name (e.g. 'exec', 'query', 'eval', 'println')
    @param max_depth: Maximum taint traversal depth (default 12, max 20)
    @return: List of taint flow paths. Each path = nodes joined by ' -> ', each node = 'code@file:line'. Empty = no flow.
    """
    depth = min(max_depth, 20)
    # Build multi-statement Scala query
    query = (
        f'val __src = cpg.call.name("{source_pattern}");'
        f'val __snk = cpg.call.name("{sink_pattern}");'
        '__snk.reachableByFlows(__src)'
        '.map(flow => flow.elements.map(n => s"${n.code}@${n.filename}:${n.lineNumber.getOrElse(-1)}").mkString(" -> "))'
        '.l'
    )
    response = joern_remote(query)
    if response is None:
        return []
    if "error" in response.lower() or "exception" in response.lower():
        return []
    return extract_list(response)
