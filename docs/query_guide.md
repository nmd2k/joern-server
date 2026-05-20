# Query and Parsing Guide

This guide details how to ingest source code and execute Code Property Graph Query Language (CPGQL) queries against Joern Server.

---

## 1. Ingestion Modes (Parsing)

To query a codebase, you must first compile it into a CPG using one of the parsing endpoints.

### Mode A: Snippet Parse (`POST /parse`)
Use this for single files or small code fragments (< 200 LOC).

```json
POST /parse
{
  "sample_id": "my-snippet",
  "source_code": "int add(int a, int b) { return a + b; }",
  "language": "c",
  "overwrite": true
}
```

### Mode B: Repository Ingestion via NDJSON (`POST /parse/repo`)
To parse a multi-file project, convert your directory tree into a Newline-Delimited JSON (NDJSON) stream and POST it. Each line represents a file:

```json
{"path": "src/main.c", "content": "#include <stdio.h>\n..."}
{"path": "src/utils.c", "content": "..."}
```

Send the stream:
```bash
curl -s -X POST \
  "http://127.0.0.1:8080/parse/repo?sample_id=my-project&language=c&overwrite=true" \
  -H 'Content-Type: application/x-ndjson' \
  --data-binary @repo.ndjson
```

### Mode C: Heavy Repository Upload (`POST /parse/repo/upload`)
For large codebases, stage a zipped archive first to prevent network timeouts:

1. **Upload Archive**:
   ```bash
   curl -s -X POST "http://127.0.0.1:8080/parse/repo/upload" \
     -F 'archive=@/path/to/project.zip'
   ```
   *Response:* `{"ok": true, "upload_id": "550e8400-e29b-..."}`

2. **Trigger Parse**:
   ```bash
   curl -s -X POST "http://127.0.0.1:8080/parse/repo" \
     -H 'Content-Type: application/json' \
     -d '{
       "sample_id": "heavy-project",
       "upload_id": "550e8400-e29b-...",
       "language": "c"
     }'
   ```

---

## 2. Session Management & Affinity

Joern executes queries in a session-scoped manner. To persist state across multiple requests, you must pass the `X-Session-Id` header.

> [!IMPORTANT]
> When running scaled deployments behind HAProxy, the `X-Session-Id` header is used for sticky session routing. Always use a consistent, unique session ID (like a UUID) for your queries to ensure they hit the same replica hosting your loaded CPG.

### 1. Bind the CPG to the Session
Before running queries, import the compiled CPG:
```bash
curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: my-analysis-session' \
  -d '{"query": "importCpg(\"/workspace/cpg-out/my-project\")"}'
```

### 2. Execute Consecutive Queries
Use the same `X-Session-Id` header for subsequent analysis queries:
```bash
curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: my-analysis-session' \
  -d '{"query": "cpg.method.name.l"}'
```

---

## 3. Querying with CPGQL

Joern uses CPGQL for traversing Code Property Graphs. Here are some common query examples:

### AST Traversals
Get all methods in the program:
```scala
cpg.method.name.l
```

Get all calls to a specific function (e.g., `strcpy`):
```scala
cpg.call.name("strcpy").l
```

### Data Flow Analysis (Reachable Flows)
Find variables reaching a sensitive sink starting from a source parameter:
```scala
val source = cpg.method.name("input").parameter
val sink = cpg.call.name("memcpy").argument(2)
sink.reachableByFlows(source).p
```

---

## 4. Structured Graph Endpoints

Instead of evaluating raw CPGQL and parsing the standard output strings, the Joern Server proxy offers structured graph endpoints that return JSON representations of specific method flows:

| Endpoint | Role | Body Parameters |
|---|---|---|
| `POST /graph/cfg` | Control Flow Graph | `{"method_full_name": "main:int()"}` |
| `POST /graph/ast` | Abstract Syntax Tree | `{"method_full_name": "main:int()"}` |
| `POST /graph/dfg` | Data Flow Graph | `{"method_full_name": "main:int()"}` |
| `POST /graph/ddg` | Data Dependence Graph | `{"method_full_name": "main:int()"}` |
| `POST /graph/pdg` | Program Dependence Graph | `{"method_full_name": "main:int()"}` |

### Example Request
```bash
curl -s -X POST http://127.0.0.1:8080/graph/cfg \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: my-analysis-session' \
  -d '{"method_full_name": "main:int()"}'
```
This returns a structured JSON graph consisting of nodes, edges, and node properties, making it simple to visualize or consume program graphs in downstream client applications.
