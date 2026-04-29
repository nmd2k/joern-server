// Tool definitions for MCP → CPGQL translation

function escapeCPGQL(str) {
  if (typeof str !== 'string') return str;
  return str.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

window.JOERN_TOOLS = {

  // ── Connectivity ──────────────────────────────────────────────
  ping: { group: "Connectivity", params: [], cpgql: () => "version" },
  check_connection: { group: "Connectivity", params: [], cpgql: () => "version" },
  get_help: { group: "Connectivity", params: [], cpgql: () => "help" },
  parse_source: {
    group: "Connectivity",
    params: [
      { name: "source_code", type: "text", label: "Source Code" },
      { name: "sample_id", type: "text", label: "Sample ID (default: playground-tool)" },
      { name: "language", type: "text", label: "Language" }
    ],
    _custom: true,
    cpgql: (p) => `__parse_source__:${escapeCPGQL(p.sample_id || "playground-tool")}:${escapeCPGQL(p.language || "")}`
  },

  // ── CPG Loading ───────────────────────────────────────────────
  load_cpg: {
    group: "CPG Loading",
    params: [{ name: "cpg_filepath", type: "text", label: "CPG Filepath" }],
    cpgql: (p) => `importCpg("${escapeCPGQL(p.cpg_filepath)}");load_cpg("${escapeCPGQL(p.cpg_filepath)}")`
  },

  // ── Method Analysis ───────────────────────────────────────────
  get_method_callees: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    cpgql: (p) => `get_method_callees("${escapeCPGQL(p.method_full_name)}")`
  },
  get_method_callers: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    cpgql: (p) => `get_method_callers("${escapeCPGQL(p.method_full_name)}")`
  },
  get_method_code_by_full_name: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    cpgql: (p) => `get_method_code_by_method_full_name("${escapeCPGQL(p.method_full_name)}")`
  },
  get_method_code_by_id: {
    group: "Method Analysis",
    params: [{ name: "method_id", type: "text", label: "Method ID (e.g. 123L)" }],
    cpgql: (p) => `get_method_code_by_id("${escapeCPGQL(p.method_id)}")`
  },
  get_method_full_name_by_id: {
    group: "Method Analysis",
    params: [{ name: "method_id", type: "text", label: "Method ID" }],
    cpgql: (p) => `get_method_full_name_by_id("${escapeCPGQL(p.method_id)}")`
  },
  get_calls_in_method_by_method_full_name: {
    group: "Method Analysis",
    params: [{ name: "method_full_name", type: "text", label: "Method Full Name" }],
    cpgql: (p) => `get_calls_in_method_by_method_full_name("${escapeCPGQL(p.method_full_name)}")`
  },

  // ── Call Analysis ─────────────────────────────────────────────
  get_call_code_by_id: {
    group: "Call Analysis",
    params: [{ name: "code_id", type: "text", label: "Call ID" }],
    cpgql: (p) => `get_call_code_by_id("${escapeCPGQL(p.code_id)}")`
  },
  get_method_by_call_id: {
    group: "Call Analysis",
    params: [{ name: "call_id", type: "text", label: "Call ID" }],
    cpgql: (p) => `get_method_by_call_id("${escapeCPGQL(p.call_id)}")`
  },
  get_referenced_method_full_name_by_call_id: {
    group: "Call Analysis",
    params: [{ name: "call_id", type: "text", label: "Call ID" }],
    cpgql: (p) => `get_referenced_method_full_name_by_call_id("${escapeCPGQL(p.call_id)}")`
  },

  // ── Class Analysis ────────────────────────────────────────────
  get_class_methods_by_class_full_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }],
    cpgql: (p) => `get_class_methods_by_class_full_name("${escapeCPGQL(p.class_full_name)}")`
  },
  get_method_code_by_class_full_name_and_method_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }, { name: "method_name", type: "text", label: "Method Name" }],
    cpgql: (p) => `get_method_code_by_class_full_name_and_method_name("${escapeCPGQL(p.class_full_name)}", "${escapeCPGQL(p.method_name)}")`
  },
  get_class_full_name_by_id: {
    group: "Class Analysis",
    params: [{ name: "class_id", type: "text", label: "Class ID" }],
    cpgql: (p) => `get_class_full_name_by_id("${escapeCPGQL(p.class_id)}")`
  },
  get_derived_classes_by_class_full_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }],
    cpgql: (p) => `get_derived_classes_by_class_full_name("${escapeCPGQL(p.class_full_name)}")`
  },
  get_parent_classes_by_class_full_name: {
    group: "Class Analysis",
    params: [{ name: "class_full_name", type: "text", label: "Class Full Name" }],
    cpgql: (p) => `get_parent_classes_by_class_full_name("${escapeCPGQL(p.class_full_name)}")`
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
    cpgql: (p) => {
      let q = "cpg.method";
      if (p.name_pattern) q += `.name("${escapeCPGQL(p.name_pattern)}")`;
      if (p.full_name_pattern) q += `.fullName("${escapeCPGQL(p.full_name_pattern)}")`;
      if (p.annotation) q += `.where(_.annotation.name("${escapeCPGQL(p.annotation)}"))`;
      if (p.modifier) q += `.where(_.modifier.modifierType("${escapeCPGQL(p.modifier.toUpperCase())}"))`;
      q += '.map(m => s"id=${m.id}L name=${m.name} fullName=${m.fullName} file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)}").l';
      return q;
    }
  },
  find_calls: {
    group: "Vulnerability Hunting",
    params: [
      { name: "callee_name_pattern", type: "text", label: "Callee Name Pattern" },
      { name: "method_full_name_pattern", type: "text", label: "Method Full Name Pattern (optional)" }
    ],
    cpgql: (p) => {
      let q = `cpg.call.name("${escapeCPGQL(p.callee_name_pattern)}")`;
      if (p.method_full_name_pattern) q += `.where(_.method.fullName("${escapeCPGQL(p.method_full_name_pattern)}"))`;
      q += '.map(c => s"callId=${c.id}L calleeName=${c.name} containingMethod=${c.method.map(_.fullName).headOption.getOrElse("")} file=${c.method.file.name.headOption.getOrElse("")} line=${c.lineNumber.getOrElse(-1)}").l';
      return q;
    }
  },
  get_dataflow: {
    group: "Vulnerability Hunting",
    params: [
      { name: "source_pattern", type: "text", label: "Source Pattern" },
      { name: "sink_pattern", type: "text", label: "Sink Pattern" },
      { name: "max_depth", type: "number", label: "Max Depth" }
    ],
    cpgql: (p) => {
      const src = escapeCPGQL(p.source_pattern);
      const snk = escapeCPGQL(p.sink_pattern);
      return 'val __src = cpg.call.name("' + src + '");' +
        'val __snk = cpg.call.name("' + snk + '");' +
        '__snk.reachableByFlows(__src)' +
        '.map(flow => flow.elements.map(n => s"${n.code}@${n.file.name.headOption.getOrElse("")}:${n.lineNumber.getOrElse(-1)}").mkString(" -> "))' +
        '.l';
    }
  },
  get_call_arguments: {
    group: "Vulnerability Hunting",
    params: [{ name: "call_id", type: "text", label: "Call ID (e.g. 123L)" }],
    cpgql: (p) => {
      const cid = escapeCPGQL(p.call_id);
      return 'cpg.call.id(' + cid + ').argument' +
        '.map(a => s"argIndex=${a.order} code=${a.code} typeFullName=${a.evalType.headOption.getOrElse("")} nodeId=${a.id}L")' +
        '.l';
    }
  },
  find_literals: {
    group: "Vulnerability Hunting",
    params: [
      { name: "pattern", type: "text", label: "Pattern" },
      { name: "literal_type", type: "select", label: "Literal Type", options: ["any", "string", "int"] }
    ],
    cpgql: (p) => {
      let q = `cpg.literal.code("${escapeCPGQL(p.pattern)}")`;
      if (p.literal_type === "string") q += '.where(_.typeFullName(".*[Ss]tring.*"))';
      else if (p.literal_type === "int") q += '.where(_.typeFullName(".*[Ii]nt.*|.*[Ll]ong.*|byte|short"))';
      q += '.map(l => s"literalId=${l.id}L value=${l.code} typeFullName=${l.typeFullName} containingMethod=${l.method.map(_.fullName).headOption.getOrElse("")} file=${l.file.name.headOption.getOrElse("")} line=${l.lineNumber.getOrElse(-1)}").l';
      return q;
    }
  },
  get_method_location: {
    group: "Vulnerability Hunting",
    params: [
      { name: "method_id", type: "text", label: "Method ID" },
      { name: "method_full_name", type: "text", label: "Method Full Name" }
    ],
    cpgql: (p) => {
      const mapExpr = '.map(m => s"file=${m.filename} lineStart=${m.lineNumber.getOrElse(-1)} lineEnd=${m.lineNumberEnd.getOrElse(-1)} columnStart=${m.columnNumber.getOrElse(-1)} columnEnd=${m.columnNumberEnd.getOrElse(-1)}").headOption.getOrElse("")';
      if (p.method_id) return `cpg.method.id(${escapeCPGQL(p.method_id)})${mapExpr}`;
      return `cpg.method.fullName("${escapeCPGQL(p.method_full_name)}")${mapExpr}`;
    }
  }
};
