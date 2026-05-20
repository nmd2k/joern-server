# Getting Started

This guide walks you through setting up and running your first code analysis with Joern Server.

---

## 1. Prerequisites

Make sure you have the following installed on your host system:
- **Docker** and **Docker Compose** (to run the Joern Server)
- **curl** (for API testing)
- **Python 3.10+** (to run client scripts and compile/host the documentation website)

To install the dependencies needed to build and host the documentation site locally, run:
```bash
pip install -r requirements-docs.txt
```

---

## 2. Setup and Configuration

1. **Clone the Repository**:
   Navigate to your local repository clone.

2. **Initialize Environment Variables**:
   Copy the example environment file:
   ```bash
   cp deploy/.env.example deploy/.env
   ```

3. **Configure Authentication**:
   Open `deploy/.env` and configure your credentials. Crucially, set the server password:
   ```env
   JOERN_SERVER_AUTH_PASSWORD=your-secure-password
   ```

---

## 3. Run the Server

Start the Joern Server using the **dev** profile (a single-replica container setup suitable for local exploration):

```bash
docker compose -f deploy/compose.dev.yml up -d
```

Verify that the proxy container is running and healthy:

```bash
docker compose -f deploy/compose.dev.yml ps
```

---

## 4. Verify Server Health

Test the server's HTTP endpoints. Send a request to `/health`:

```bash
curl -s http://127.0.0.1:8080/health
```

**Expected Response**:
```json
{"ok": true}
```

---

## 5. Your First Analysis

Let's parse a simple C code snippet and run a query against it.

### Step A: Parse a Code Snippet

Use the `POST /parse` endpoint to upload and compile a C source snippet into a Code Property Graph (CPG):

```bash
curl -s -X POST http://127.0.0.1:8080/parse \
  -H 'Content-Type: application/json' \
  -d '{
    "sample_id": "hello-world",
    "source_code": "int main() { printf(\"Hello World!\\n\"); return 0; }",
    "language": "c",
    "overwrite": true
  }'
```

This returns compilation metadata indicating the CPG was built successfully:
```json
{
  "ok": true,
  "cpg_path": "/workspace/cpg-out/hello-world",
  "source_hash": "...",
  "cache_hit": false
}
```

### Step B: Run a Query

Now, initialize a query session with a unique header `X-Session-Id` and execute a **CPGQL** query to fetch the methods parsed in the CPG:

1. **Load the CPG** into your session:
   ```bash
   curl -s -X POST http://127.0.0.1:8080/query-sync \
     -H 'Content-Type: application/json' \
     -H 'X-Session-Id: my-first-session' \
     -d '{"query": "importCpg(\"/workspace/cpg-out/hello-world\")"}'
   ```

2. **Query the AST** to retrieve all method names:
   ```bash
   curl -s -X POST http://127.0.0.1:8080/query-sync \
     -H 'Content-Type: application/json' \
     -H 'X-Session-Id: my-first-session' \
     -d '{"query": "cpg.method.name.l"}'
   ```

   **Expected Output**:
   ```json
   {
     "success": true,
     "stdout": "List(main, printf)",
     "stderr": "",
     "latency_ms": 12.3
   }
   ```

---

## Next Steps
- Learn how the request flow works in the [Architecture Guide](ARCHITECTURE.md).
- Dive deep into multi-file repository parsing and advanced session queries in the [Query Guide](query_guide.md).
- Learn how to scale up your deployment behind HAProxy in the [Deployment Guide](deploy.md).
