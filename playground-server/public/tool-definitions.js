// Tool definitions for MCP → CPGQL translation

function escapeCPGQL(str) {
  if (typeof str !== 'string') return str;
  return str.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

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
