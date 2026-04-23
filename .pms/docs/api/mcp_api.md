# MCP Server API Reference

The `mcp-joern` server exposes Joern's code-analysis capabilities as **Model Context Protocol (MCP)** tools, allowing AI assistants (Claude, Cursor, etc.) to query a live CPG without writing raw CPGQL.

**Package:** `mcp_joern`  
**Entry point:** `python -m mcp_joern.server`  
**Default MCP SSE URL:** `http://localhost:9000`

---

## Transports

| Mode | Config | Use case |
|------|--------|----------|
| `stdio` | `MCP_TRANSPORT=stdio` | IDE plugins (Claude Desktop, Cursor) — direct stdin/stdout |
| `sse` | `MCP_TRANSPORT=sse` (default in Docker) | Remote clients over HTTP Server-Sent Events |

---

## Configuration

Settings are resolved in this order (highest wins): environment variable → `mcp_settings.json` → default.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | Joern HTTP proxy host |
| `PORT` | `8080` | Joern HTTP proxy port |
| `JOERN_AUTH_USERNAME` | `joern` | Basic auth username for the proxy |
| `JOERN_AUTH_PASSWORD` | `joern` | Basic auth password for the proxy |
| `TIMEOUT` | `1800` | Request timeout in seconds |
| `LOG_LEVEL` | `ERROR` | Python logging level |
| `MCP_TRANSPORT` | `stdio` | Transport: `stdio` or `sse` |
| `MCP_HOST` | `0.0.0.0` | Bind address for SSE transport |
| `MCP_PORT` | `9000` | Listen port for SSE transport |
| `JOERN_SESSION_ID` | *(auto-generated)* | Sticky-routing session ID sent to the HTTP proxy |

### `mcp_settings.json`

```jsonc
{
  "host": "127.0.0.1",
  "port": 8080,
  "username": "joern",
  "password": "joern",
  "timeout": 1800,
  "autoApprove": ["ping", "check_connection", ...]   // tools that skip user confirmation
}
```

---

## Tool Reference

All tools communicate via the HTTP proxy (`POST /query-sync`). Return values are parsed from Joern's Scala REPL output format.

### Connectivity

#### `ping`
Check whether the Joern server is reachable.

**Parameters:** none  
**Returns:** `str` — `"pong"` on success, error message otherwise.

---

#### `check_connection`
Perform a full round-trip health check (executes a trivial CPGQL expression).

**Parameters:** none  
**Returns:** `str` — confirmation message or error.

---

#### `get_help`
List available Joern built-in tools and their signatures.

**Parameters:** none  
**Returns:** `str` — formatted help text from the REPL.

---

### CPG Loading

#### `load_cpg`
Load a CPG into the Joern session via `importCpg()`.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `cpg_filepath` | `str` | Absolute path to the CPG file or directory (as produced by `/parse`) |

**Returns:** `str` — `"true"` on success, `"false"` on failure.

**Note:** Loading a new CPG replaces any previously loaded CPG in the same session. Use a dedicated `X-Session-Id` (via `JOERN_SESSION_ID`) when running parallel analyses.

---

### Method Analysis

#### `get_method_callees`
Return the full names of all methods called by the given method.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `method_full_name` | `str` | Fully-qualified method name (e.g. `com.example.Foo.bar:void(int)`) |

**Returns:** `list[str]` — list of callee full names.

---

#### `get_method_callers`
Return the full names of all methods that call the given method.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `method_full_name` | `str` | Fully-qualified method name |

**Returns:** `list[str]` — list of caller full names.

---

#### `get_method_code_by_full_name`
Retrieve the source code of a method by its full name.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `method_full_name` | `str` | Fully-qualified method name |

**Returns:** `str` — raw source code of the method.

---

#### `get_method_code_by_id`
Retrieve the source code of a method by its CPG node ID.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `method_id` | `str` | Joern method node ID (integer as string) |

**Returns:** `str` — raw source code of the method.

---

#### `get_method_full_name_by_id`
Resolve a CPG method node ID to its fully-qualified name.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `method_id` | `str` | Joern method node ID |

**Returns:** `str` — fully-qualified method name.

---

#### `get_calls_in_method_by_method_full_name`
List all call sites (as call-node IDs) inside a method.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `method_full_name` | `str` | Fully-qualified method name |

**Returns:** `list[str]` — list of call node IDs within that method.

---

### Call Analysis

#### `get_call_code_by_id`
Retrieve the source code snippet for a specific call node.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `code_id` | `str` | Joern call node ID |

**Returns:** `str` — code snippet at the call site.

---

#### `get_method_by_call_id`
Find the enclosing method for a given call node.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `call_id` | `str` | Joern call node ID |

**Returns:** `str` — full name of the enclosing method.

---

#### `get_referenced_method_full_name_by_call_id`
Resolve the callee full name referenced by a call node.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `call_id` | `str` | Joern call node ID |

**Returns:** `str` — fully-qualified name of the method being called.

---

### Class / Type Analysis

#### `get_class_methods_by_class_full_name`
List all methods declared in a class.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `class_full_name` | `str` | Fully-qualified class name (e.g. `com.example.Foo`) |

**Returns:** `list[str]` — list of method full names.

---

#### `get_method_code_by_class_full_name_and_method_name`
Retrieve source code for a method identified by class + simple method name.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `class_full_name` | `str` | Fully-qualified class name |
| `method_name` | `str` | Simple (unqualified) method name |

**Returns:** `list[str]` — list of matching method source strings (multiple overloads may match).

---

#### `get_class_full_name_by_id`
Resolve a CPG type-decl node ID to its fully-qualified class name.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `class_id` | `str` | Joern type-decl node ID |

**Returns:** `str` — fully-qualified class name.

---

#### `get_derived_classes_by_class_full_name`
List all classes that directly or transitively extend the given class/interface.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `class_full_name` | `str` | Fully-qualified class name |

**Returns:** `list[str]` — list of subclass full names.

---

#### `get_parent_classes_by_class_full_name`
List all supertypes (classes and interfaces) of the given class.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `class_full_name` | `str` | Fully-qualified class name |

**Returns:** `list[str]` — list of supertype full names.

---

### Vulnerability Hunting

These six tools were added in Sprint 4 (NeuralAtlas feature request) to enable agentic vulnerability analysis. Together they give an LLM agent the ability to enumerate entry points, trace taint from source to sink, inspect call arguments, and produce file:line citations — without writing raw CPGQL.

#### `find_methods`
Search for methods globally by name pattern, annotation, modifier, or full-name pattern. At least one filter is required. Primary use: cold-start enumeration of HTTP entry points, sinks, or annotated handlers.

**Parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name_pattern` | `str \| None` | one of four required | Regex matched against the method's simple name (e.g. `"get.*"`, `"onReceive"`) |
| `annotation` | `str \| None` | one of four required | Annotation name to filter by (e.g. `"RequestMapping"`, `"Override"`) |
| `modifier` | `str \| None` | one of four required | Modifier type, case-insensitive (e.g. `"public"`, `"static"`) |
| `full_name_pattern` | `str \| None` | one of four required | Regex matched against the method's fully-qualified name |

**Returns:** `list[str]` — each item: `"id=<id>L name=<name> fullName=<fullName> file=<file> lineStart=<line>"`

**CPGQL equivalent:** `cpg.method.name("<pattern>").where(_.annotation.name("<ann>")).l`

---

#### `find_calls`
Search for call sites globally by callee name pattern. Primary use: sink enumeration (`exec`, `query`, `eval`, `Runtime.*`, etc.).

**Parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `callee_name_pattern` | `str` | Yes | Regex matched against the callee method name |
| `method_full_name_pattern` | `str \| None` | No | Restrict to call sites inside methods whose full name matches this pattern |

**Returns:** `list[str]` — each item: `"callId=<id>L calleeName=<name> containingMethod=<fullName> file=<file> line=<line>"`

**CPGQL equivalent:** `cpg.call.name("<pattern>").l`

---

#### `get_dataflow`
Find taint flows from a source call pattern to a sink call pattern using Joern's `reachableByFlows` engine. Returns an empty list when no flow exists — this is not an error.

**Parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `source_pattern` | `str` | Yes | — | Regex for the source call name (e.g. `"getParameter"`, `"readLine"`) |
| `sink_pattern` | `str` | Yes | — | Regex for the sink call name (e.g. `"exec"`, `"query"`, `"eval"`) |
| `max_depth` | `int` | No | `12` | Traversal depth cap (hard ceiling: 20). Joern's engine uses its own internal depth; this parameter is a forward-compatibility hint. |

**Returns:** `list[str]` — each string is one taint path; nodes within a path are separated by `" -> "`, each node formatted as `"<code>@<file>:<line>"`. Empty list = no reachable flow.

**CPGQL equivalent:** `sink.reachableByFlows(source).l`

---

#### `get_call_arguments`
Return structured argument information for a call node by its ID. Eliminates brittle raw-code re-parsing when an agent needs to reason about which argument is attacker-controlled.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `call_id` | `str` | Call node ID as a Long string (e.g. `"111669149702L"`) |

**Returns:** `list[str]` — each item: `"argIndex=<n> code=<code> typeFullName=<type> nodeId=<id>L"`

**CPGQL equivalent:** `cpg.call.id(<id>).argument.l`

---

#### `find_literals`
Search for string or numeric literals in the CPG by value pattern. Primary use: finding hardcoded credentials, SQL fragments, suspicious URLs, crypto constants, and magic numbers.

**Parameters**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `pattern` | `str` | Yes | — | Regex matched against the literal value (e.g. `"password"`, `"SELECT.*FROM"`) |
| `literal_type` | `str` | No | `"any"` | Type filter: `"string"`, `"int"`, or `"any"` |

**Returns:** `list[str]` — each item: `"literalId=<id>L value=<value> typeFullName=<type> containingMethod=<fullName> file=<file> line=<line>"`

**CPGQL equivalent:** `cpg.literal.code("<pattern>").l`

---

#### `get_method_location`
Return the file path and line/column range for any method. Required for producing `file:line` citations in vulnerability reports. Accepts either a node ID or a fully-qualified name; `method_id` takes precedence when both are provided.

**Parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `method_id` | `str \| None` | one of two required | Method node ID as a Long string (e.g. `"111669149702L"`) |
| `method_full_name` | `str \| None` | one of two required | Fully-qualified method name (e.g. `"com.Foo.bar:void()"`) |

**Returns:** `str` — `"file=<file> lineStart=<n> lineEnd=<n> columnStart=<n> columnEnd=<n>"`, or `""` if the method is not found.

---

## Return Value Parsing

Joern returns Scala REPL output. The MCP server parses it into Python types:

| REPL output example | Python return |
|--------------------|---------------|
| `val res0: String = "main"` | `"main"` |
| `val res0: List[String] = List("a", "b")` | `["a", "b"]` |
| `val res0: Boolean = true` | `"true"` |
| `val res0: List[String] = List()` | `[]` |

---

## Typical Workflow

```
1. Parse source code  →  POST /parse (HTTP API)  →  CPG on disk
2. Load CPG           →  load_cpg(cpg_filepath)
3. Enumerate sinks    →  find_calls / find_methods / find_literals
4. Trace taint        →  get_dataflow(source_pattern, sink_pattern)
5. Inspect arguments  →  get_call_arguments(call_id)
6. Deep-dive code     →  get_method_code_by_full_name / get_call_code_by_id
7. Cite location      →  get_method_location(method_full_name=...)
8. Clean up           →  POST /cleanup (HTTP API)
```

### Example: find all callers of a vulnerable method

```python
# (via MCP tool calls from an AI assistant)
load_cpg("/workspace/cpg-out/my-project")
callers = get_method_callers("com.example.Dao.rawQuery:ResultSet(String)")
for caller in callers:
    code = get_method_code_by_full_name(caller)
    # inspect code for injection patterns …
```

### Example: agentic SQL injection hunt

```python
# 1. Load CPG
load_cpg("/workspace/cpg-out/my-project")

# 2. Find all call sites that reach SQL execution
flows = get_dataflow(source_pattern="getParameter", sink_pattern="query")

# 3. For each flow, inspect the sink argument
for flow in flows:
    # flow = "getParameter(...)@Foo.java:12 -> ... -> query(...)@Dao.java:58"
    sink_calls = find_calls(callee_name_pattern="query")
    for call in sink_calls:
        # call = "callId=123L calleeName=query containingMethod=... file=Dao.java line=58"
        args = get_call_arguments("123L")
        # args = ["argIndex=0 code=userInput typeFullName=String nodeId=456L", ...]

# 4. Get file:line for the report
location = get_method_location(method_full_name="com.example.Dao.executeQuery:void(String)")
# location = "file=Dao.java lineStart=55 lineEnd=70 columnStart=4 columnEnd=5"
```

---

## IDE Integration (stdio)

Add to your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "joern": {
      "command": "python",
      "args": ["-m", "mcp_joern.server"],
      "env": {
        "HOST": "127.0.0.1",
        "PORT": "8080",
        "JOERN_AUTH_USERNAME": "joern",
        "JOERN_AUTH_PASSWORD": "change-me",
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

## Remote Integration (SSE)

Point your MCP client at `http://<host>:9000` (or the value of `MCP_PORT`). The server advertises all 21 tools at the SSE endpoint automatically.
