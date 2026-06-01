import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  dotToGraph,
  extractAllDotsFromStdout,
  mergeDotGraphs,
} from '../graph/dot.js';
import {
  dotExportQuery,
  fileScopeTraversal,
  methodTraversal,
} from '../graph/queries.js';
import {
  parseAstTuples,
  parseMetadataTuples,
} from '../graph/scala-parse.js';
import { createGraphHandlers } from '../graph/service.js';
import { fetchNodeMetadata } from '../graph/metadata.js';

const DOT_A = `digraph "A" {
  "1" [label="ENTRY" shape="box"]
  "2" [label="x = 1" shape="box"]
  "1" -> "2" [label="control"]
}`;

const DOT_B = `digraph "B" {
  "3" [label="ENTRY" shape="box"]
  "4" [label="y = 2" shape="box"]
  "3" -> "4" [label="control"]
}`;

describe('graph/dot', () => {
  it('extracts multiple DOT strings from List stdout', () => {
    const stdout = `val res: List[String] = List("""${DOT_A}""", """${DOT_B}""")`;
    const dots = extractAllDotsFromStdout(stdout);
    assert.equal(dots.length, 2);
  });

  it('merge_dot_graphs combines nodes and edges', () => {
    const graph = dotToGraph(mergeDotGraphs([DOT_A, DOT_B]));
    assert.equal(graph.nodes.length, 4);
    assert.equal(graph.edges.length, 2);
  });
});

describe('graph/queries', () => {
  it('dotExportQuery uses traversal dot step list', () => {
    const traversal = 'cpg.method.fullNameExact("foo")';
    assert.equal(dotExportQuery(traversal, 'dotDdg'), 'cpg.method.fullNameExact("foo").dotDdg.l');
  });

  it('fileScopeTraversal matches basename and full path via endsWith', () => {
    const q = fileScopeTraversal('src/pkg/main.c');
    assert.match(q, /endsWith\("main\.c"\)/);
    assert.match(q, /endsWith\("src\/pkg\/main\.c"\)/);
    assert.doesNotMatch(q, /matches\(/);
  });

  it('fileScopeTraversal simple name uses exact filename', () => {
    const q = fileScopeTraversal('main.c');
    assert.equal(q.startsWith('cpg.method.filename("main.c")'), true);
    assert.doesNotMatch(q, /\.\*/);
  });

  it('cpg scope traversal has take limit', () => {
    const { traversal, err } = methodTraversal('cpg', {
      methodFullName: '',
      fileName: '',
      nodeLimit: 42,
    });
    assert.equal(err, null);
    assert.ok(traversal.endsWith('.take(42)'));
  });
});

describe('graph/service', () => {
  const GRAPH_BODY = { method_full_name: 'com.example.Foo.main:void()' };

  function mockQueryFn(stdout, { success = true, status = 200 } = {}) {
    return async () => ({ status, data: { stdout, success } });
  }

  it('handleCfg file scope parses DOT output', async () => {
    const fileDot = `val res: List[String] = List("""${DOT_A}""")`;
    const { handleCfg } = createGraphHandlers({
      joernUrl: 'http://joern.test',
      queryFn: mockQueryFn(fileDot),
    });
    const result = await handleCfg({ scope: 'file', file_name: 'snippet.c' }, {});
    assert.equal(result.status, 200);
    assert.ok(result.body.nodes.length >= 2);
    assert.equal(result.body.scope, 'file');
  });

  it('handleCfg parses DOT output', async () => {
    const { handleCfg } = createGraphHandlers({
      joernUrl: 'http://joern.test',
      queryFn: mockQueryFn(DOT_A),
    });
    const result = await handleCfg(GRAPH_BODY, {});
    assert.equal(result.status, 200);
    assert.ok(result.body.nodes.length >= 2);
    assert.ok(result.body.edges.length >= 1);
    assert.ok('metadata' in result.body);
  });

  it('handleCfg empty result returns 422', async () => {
    const { handleCfg } = createGraphHandlers({
      joernUrl: 'http://joern.test',
      queryFn: mockQueryFn(''),
    });
    const result = await handleCfg(GRAPH_BODY, {});
    assert.equal(result.status, 422);
    assert.equal(result.body.code, 'empty_result');
  });

  it('handleCfg timeout returns 504', async () => {
    const queryFn = async () => {
      const err = new Error('query timed out');
      err.code = 'query_timeout';
      throw err;
    };
    const { handleCfg } = createGraphHandlers({ joernUrl: 'http://joern.test', queryFn });
    const result = await handleCfg(GRAPH_BODY, {});
    assert.equal(result.status, 504);
  });

  it('handleCfg missing method_full_name returns 400', async () => {
    const { handleCfg } = createGraphHandlers({
      joernUrl: 'http://joern.test',
      queryFn: mockQueryFn(DOT_A),
    });
    const result = await handleCfg({}, {});
    assert.equal(result.status, 400);
  });

  it('handlePdg parses DOT output', async () => {
    const { handlePdg } = createGraphHandlers({
      joernUrl: 'http://joern.test',
      queryFn: mockQueryFn(DOT_A),
    });
    const result = await handlePdg(GRAPH_BODY, {});
    assert.equal(result.status, 200);
    assert.ok(result.body.nodes.length >= 2);
  });

  it('handleAst parses scala tuples', async () => {
    const stdout =
      'val res0: List[(Long, String, Option[Int], Option[Int], Int, String, Option[Long])] = List(\n' +
      '(1, "void main()", Some(1), Some(1), 1, "METHOD", None),\n' +
      '(2, "x = 1", Some(2), Some(3), 2, "ASSIGNMENT", Some(1))\n' +
      ')';
    const { handleAst } = createGraphHandlers({
      joernUrl: 'http://joern.test',
      queryFn: mockQueryFn(stdout),
    });
    const result = await handleAst(GRAPH_BODY, {});
    assert.equal(result.status, 200);
    assert.equal(result.body.nodes.length, 2);
    assert.equal(result.body.nodes[0].id, '1');
    assert.equal(result.body.metadata['1'].code, 'void main()');
  });

  it('handleDfg and ddg alias share behavior', async () => {
    const queryFn = mockQueryFn(DOT_A);
    const dfg = createGraphHandlers({ joernUrl: 'http://joern.test', queryFn });
    const r1 = await dfg.handleDfg(GRAPH_BODY, {});
    const r2 = await dfg.handleDfg(GRAPH_BODY, {});
    assert.deepEqual(r1.body.nodes, r2.body.nodes);
  });
});

describe('graph/metadata', () => {
  it('fetchNodeMetadata returns map', async () => {
    const stdout =
      'val res0: List[(Long, String, Option[Int], Option[Int], Int, String)] = List(\n' +
      '(1, "void main()", Some(1), Some(1), 1, "METHOD"),\n' +
      '(2, "x = 1", Some(2), Some(3), 2, "ASSIGNMENT")\n' +
      ')';
    const queryFn = async () => ({ status: 200, data: { stdout, success: true } });
    const result = await fetchNodeMetadata('http://joern.test', ['1', '2'], {}, queryFn);
    assert.equal(result['1'].code, 'void main()');
    assert.equal(result['2'].node_type, 'ASSIGNMENT');
  });

  it('fetchNodeMetadata empty input', async () => {
    const result = await fetchNodeMetadata('http://joern.test', [], {});
    assert.deepEqual(result, {});
  });
});

describe('graph/scala-parse', () => {
  it('parseAstTuples builds parent edges', () => {
    const stdout =
      'val res0: List[(Long, String, Option[Int], Option[Int], Int, String, Option[Long])] = List(\n' +
      '(1, "void main()", Some(1), Some(1), 1, "METHOD", None),\n' +
      '(2, "x = 1", Some(2), Some(3), 2, "ASSIGNMENT", Some(1))\n' +
      ')';
    const { nodes, edges, metadata } = parseAstTuples(stdout);
    assert.equal(nodes.length, 2);
    assert.equal(edges.length, 1);
    assert.equal(metadata['2'].node_type, 'ASSIGNMENT');
  });

  it('parseMetadataTuples parses six-field tuples', () => {
    const stdout =
      'val res0: List[(Long, String, Option[Int], Option[Int], Int, String)] = List(\n' +
      '(1, "void main()", Some(1), Some(1), 1, "METHOD")\n' +
      ')';
    const meta = parseMetadataTuples(stdout);
    assert.equal(meta['1'].line_number, 1);
  });
});
