// Joern tool metadata and CPGQL query builders (HTTP /query-sync, no MCP).

function escapeCPGQL(str) {
  if (typeof str !== 'string') return str;
  return str.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

function _requireArg(args, key) {
  var val = args[key];
  if (val === undefined || val === null || val === '') {
    throw new Error('missing required argument: ' + key);
  }
  return String(val);
}

/** Build CPGQL for a named tool. Throws on unknown tool or missing args. */
function buildQuery(toolName, args) {
  args = args || {};
  switch (toolName) {
    case 'ping':
    case 'check_connection':
      return 'version';
    case 'get_help':
      return 'help';
    case 'load_cpg': {
      var path = _requireArg(args, 'cpg_filepath');
      return [
        'importCpg("' + escapeCPGQL(path) + '")',
        'load_cpg("' + escapeCPGQL(path) + '")'
      ];
    }
    case 'get_method_callees':
      return 'get_method_callees("' + escapeCPGQL(_requireArg(args, 'method_full_name')) + '")';
    case 'get_method_callers':
      return 'get_method_callers("' + escapeCPGQL(_requireArg(args, 'method_full_name')) + '")';
    case 'get_method_code_by_full_name':
      return 'get_method_code_by_method_full_name("' + escapeCPGQL(_requireArg(args, 'method_full_name')) + '")';
    case 'get_calls_in_method_by_method_full_name':
      return 'get_calls_in_method_by_method_full_name("' + escapeCPGQL(_requireArg(args, 'method_full_name')) + '")';
    case 'get_method_full_name_by_id':
      return 'get_method_full_name_by_id("' + escapeCPGQL(_requireArg(args, 'method_id')) + '")';
    case 'get_method_code_by_id':
      return 'get_method_code_by_id("' + escapeCPGQL(_requireArg(args, 'method_id')) + '")';
    case 'get_call_code_by_id':
      return 'get_call_code_by_id("' + escapeCPGQL(_requireArg(args, 'code_id')) + '")';
    case 'get_method_by_call_id':
      return 'get_method_by_call_id("' + escapeCPGQL(_requireArg(args, 'call_id')) + '")';
    case 'get_referenced_method_full_name_by_call_id':
      return 'get_referenced_method_full_name_by_call_id("' + escapeCPGQL(_requireArg(args, 'call_id')) + '")';
    case 'get_class_full_name_by_id':
      return 'get_class_full_name_by_id("' + escapeCPGQL(_requireArg(args, 'class_id')) + '")';
    case 'get_class_methods_by_class_full_name':
      return 'get_class_methods_by_class_full_name("' + escapeCPGQL(_requireArg(args, 'class_full_name')) + '")';
    case 'get_method_code_by_class_full_name_and_method_name':
      return 'get_method_code_by_class_full_name_and_method_name("' +
        escapeCPGQL(_requireArg(args, 'class_full_name')) + '", "' +
        escapeCPGQL(_requireArg(args, 'method_name')) + '")';
    case 'get_derived_classes_by_class_full_name':
      return 'get_derived_classes_by_class_full_name("' + escapeCPGQL(_requireArg(args, 'class_full_name')) + '")';
    case 'get_parent_classes_by_class_full_name':
      return 'get_parent_classes_by_class_full_name("' + escapeCPGQL(_requireArg(args, 'class_full_name')) + '")';
    case 'find_methods': {
      var namePattern = args.name_pattern;
      var annotation = args.annotation;
      var modifier = args.modifier;
      var fullNamePattern = args.full_name_pattern;
      if (!namePattern && !annotation && !modifier && !fullNamePattern) {
        throw new Error('missing required argument: at least one of name_pattern, annotation, modifier, full_name_pattern');
      }
      var parts = ['cpg.method'];
      if (namePattern) parts.push('.name("' + escapeCPGQL(namePattern) + '")');
      if (fullNamePattern) parts.push('.fullName("' + escapeCPGQL(fullNamePattern) + '")');
      if (annotation) parts.push('.where(_.annotation.name("' + escapeCPGQL(annotation) + '"))');
      if (modifier) parts.push('.where(_.modifier.modifierType("' + escapeCPGQL(String(modifier).toUpperCase()) + '"))');
      parts.push('.map(m => s"id=${m.id}L name=${m.name} fullName=${m.fullName} file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)}").l');
      return parts.join('');
    }
    case 'find_calls': {
      var callee = _requireArg(args, 'callee_name_pattern');
      var scope = args.method_full_name_pattern;
      var callParts = ['cpg.call.name("' + escapeCPGQL(callee) + '")'];
      if (scope) callParts.push('.where(_.method.fullName("' + escapeCPGQL(scope) + '"))');
      callParts.push('.map(c => s"callId=${c.id}L calleeName=${c.name} containingMethod=${c.method.map(_.fullName).headOption.getOrElse("")} file=${c.method.file.name.headOption.getOrElse("")} line=${c.lineNumber.getOrElse(-1)}").l');
      return callParts.join('');
    }
    case 'get_call_arguments':
      return 'cpg.call.id(' + escapeCPGQL(_requireArg(args, 'call_id')) + ').argument.map(a => s"argIndex=${a.order} code=${a.code} typeFullName=${a.evalType.headOption.getOrElse("")} nodeId=${a.id}L").l';
    case 'find_literals': {
      var pattern = _requireArg(args, 'pattern');
      var literalType = args.literal_type || 'any';
      var litParts = ['cpg.literal.code("' + escapeCPGQL(pattern) + '")'];
      if (literalType === 'string') litParts.push('.where(_.typeFullName(".*[Ss]tring.*"))');
      else if (literalType === 'int') litParts.push('.where(_.typeFullName(".*[Ii]nt.*|.*[Ll]ong.*|byte|short"))');
      litParts.push('.map(l => s"literalId=${l.id}L value=${l.code} typeFullName=${l.typeFullName} containingMethod=${l.method.map(_.fullName).headOption.getOrElse("")} file=${l.file.name.headOption.getOrElse("")} line=${l.lineNumber.getOrElse(-1)}").l');
      return litParts.join('');
    }
    case 'get_method_location': {
      var methodId = args.method_id;
      var methodFullName = args.method_full_name;
      if (!methodId && !methodFullName) {
        throw new Error('missing required argument: provide either method_id or method_full_name');
      }
      var mapExpr = '.map(m => s"file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)} lineEnd=${m.lineNumberEnd.getOrElse(-1)} columnStart=${m.columnNumber.getOrElse(-1)} columnEnd=${m.columnNumberEnd.getOrElse(-1)}").headOption.getOrElse("")';
      if (methodId) return 'cpg.method.id(' + escapeCPGQL(methodId) + ')' + mapExpr;
      return 'cpg.method.fullName("' + escapeCPGQL(methodFullName) + '")' + mapExpr;
    }
    case 'get_dataflow': {
      var source = _requireArg(args, 'source_pattern');
      var sink = _requireArg(args, 'sink_pattern');
      return 'val __src = cpg.call.name("' + escapeCPGQL(source) + '");' +
        'val __snk = cpg.call.name("' + escapeCPGQL(sink) + '");' +
        '__snk.reachableByFlows(__src).map(flow => flow.elements.map(n => s"${n.code}@${n.file.name.headOption.getOrElse("")}:${n.lineNumber.getOrElse(-1)}").mkString(" -> ")).l';
    }
    default:
      throw new Error('unknown tool: ' + toolName);
  }
}

window.buildQuery = buildQuery;

window.JOERN_TOOLS = {

  // ── Connectivity ──────────────────────────────────────────────
  ping: {
    group: "Connectivity",
    params: [],
    description: "Check Joern server connectivity"
  },
  check_connection: {
    group: "Connectivity",
    params: [],
    description: "Full connection verification with error messages"
  },
  get_help: {
    group: "Connectivity",
    params: [],
    description: "List available Joern tools"
  },
  parse_source: {
    group: "Connectivity",
    params: [
      { name: "source_code", type: "text", label: "Source Code" },
      { name: "sample_id", type: "text", label: "Sample ID (default: playground-tool)" },
      { name: "language", type: "text", label: "Language" }
    ],
    _custom: true,
    description: "Parse source code into CPG (custom — calls /api/parse)"
  },

  // ── CPG Loading ───────────────────────────────────────────────
  load_cpg: {
    group: "CPG Loading",
    params: [{ name: "cpg_filepath", type: "text", label: "CPG Filepath" }],
    description: "Load CPG file into Joern REPL"
  },

  // ── Method Analysis ───────────────────────────────────────────
  get_method_callees: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    description: "Get methods called by specified method"
  },
  get_method_callers: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    description: "Get methods that call specified method"
  },
  get_method_code_by_full_name: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    description: "Get source code by method full name"
  },
  get_method_code_by_id: {
    group: "Method Analysis",
    params: [{ name: "method_id", type: "text", label: "Method ID (e.g. 123L)" }],
    description: "Get source code by method node ID"
  },
  get_method_full_name_by_id: {
    group: "Method Analysis",
    params: [{ name: "method_id", type: "text", label: "Method ID" }],
    description: "Get full name from method node ID"
  },
  get_calls_in_method_by_method_full_name: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    description: "Get call sites inside a method"
  },

  // ── Call Analysis ─────────────────────────────────────────────
  get_call_code_by_id: {
    group: "Call Analysis",
    params: [{ name: "code_id", type: "text", label: "Call ID" }],
    description: "Get code snippet for a call node"
  },
  get_method_by_call_id: {
    group: "Call Analysis",
    params: [{ name: "call_id", type: "text", label: "Call ID" }],
    description: "Get enclosing method for a call node"
  },
  get_referenced_method_full_name_by_call_id: {
    group: "Call Analysis",
    params: [{ name: "call_id", type: "text", label: "Call ID" }],
    description: "Get callee full name for a call node"
  },

  // ── Class Analysis ────────────────────────────────────────────
  get_class_methods_by_class_full_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }],
    description: "Get all methods of a class"
  },
  get_method_code_by_class_full_name_and_method_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }, { name: "method_name", type: "text", label: "Method Name" }],
    description: "Get method code by class + method name"
  },
  get_class_full_name_by_id: {
    group: "Class Analysis",
    params: [{ name: "class_id", type: "text", label: "Class ID" }],
    description: "Get class full name from node ID"
  },
  get_derived_classes_by_class_full_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }],
    description: "Get subclasses of a type"
  },
  get_parent_classes_by_class_full_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }],
    description: "Get supertypes of a type"
  },

  // ── Vulnerability Hunting ─────────────────────────────────────
  find_methods: {
    group: "Vulnerability Hunting",
    params: [
      { name: "name_pattern", type: "text", label: "Name Pattern" },
      { name: "annotation", type: "text", label: "Annotation" },
      { name: "modifier", type: "text", label: "Modifier" },
      { name: "full_name_pattern", type: "text", label: "Full Name Pattern" }
    ],
    description: "Search methods by name, annotation, or modifier"
  },
  find_calls: {
    group: "Vulnerability Hunting",
    params: [
      { name: "callee_name_pattern", type: "text", label: "Callee Name Pattern" },
      { name: "method_full_name_pattern", type: "text", label: "Method Full Name Pattern (optional)" }
    ],
    description: "Search call sites by callee name/pattern"
  },
  get_dataflow: {
    group: "Vulnerability Hunting",
    params: [
      { name: "source_pattern", type: "text", label: "Source Pattern" },
      { name: "sink_pattern", type: "text", label: "Sink Pattern" },
      { name: "max_depth", type: "number", label: "Max Depth" }
    ],
    description: "Trace taint reachability from source to sink"
  },
  get_call_arguments: {
    group: "Vulnerability Hunting",
    params: [{ name: "call_id", type: "text", label: "Call ID (e.g. 123L)" }],
    description: "Get structured arguments for a call node"
  },
  find_literals: {
    group: "Vulnerability Hunting",
    params: [
      { name: "pattern", type: "text", label: "Pattern" },
      { name: "literal_type", type: "select", label: "Literal Type", options: ["any", "string", "int"] }
    ],
    description: "Search string or numeric literals across CPG"
  },
  get_method_location: {
    group: "Vulnerability Hunting",
    params: [
      { name: "method_id", type: "text", label: "Method ID" },
      { name: "method_full_name", type: "text", label: "Method Full Name" }
    ],
    description: "Get file and line range for a method"
  }
};
