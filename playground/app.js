(function() {
  'use strict';

  var Tools = window.JOERN_TOOLS;

  function escapeCPGQL(str) {
    if (typeof str !== 'string') return str;
    return str.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
  }

  var toolGroups = ["Connectivity", "CPG Loading", "Method Analysis", "Call Analysis", "Class Analysis", "Vulnerability Hunting"];

  var app = Vue.createApp({
    data() {
      return {
        panels: { parse: true, query: true, tool: true, graph: true },
        sourceCode: '#include <stdio.h>\n\nint add(int a, int b) {\n    return a + b;\n}\n\nint main() {\n    printf("Sum: %d\\n", add(3, 4));\n    return 0;\n}',
        language: "c",
        sampleId: "playground-sample",
        parsing: false,
        parseResult: null,
        parseError: null,
        rawQuery: 'cpg.method.name("add").l',
        rawResult: null,
        rawError: null,
        queryRunning: false,
        queryHistory: [],
        selectedTool: "",
        toolParams: {},
        toolDef: null,
        toolRunning: false,
        toolResult: null,
        toolError: null,
        graphType: "cfg",
        graphMethod: "main",
        graphData: null,
        graphError: null,
        graphLoading: false,
        cyInstance: null,
        TOOLS: Tools,
        toolGroups: toolGroups,
        toolsByGroup: {}
      };
    },
    computed: {
      formattedRawResult() {
        return this.formatResult(this.rawResult);
      },
      formattedToolResult() {
        return this.formatResult(this.toolResult);
      }
    },
    created() {
      this.toolsByGroup = {};
      for (var i = 0; i < toolGroups.length; i++) {
        this.toolsByGroup[toolGroups[i]] = [];
      }
      var keys = Object.keys(Tools);
      for (var j = 0; j < keys.length; j++) {
        var key = keys[j];
        var def = Tools[key];
        var g = def.group;
        if (!this.toolsByGroup[g]) {
          this.toolsByGroup[g] = [];
          if (toolGroups.indexOf(g) === -1) toolGroups.push(g);
        }
        this.toolsByGroup[g].push({ key: key, group: g });
      }
    },
    methods: {
      escapeCPGQL: escapeCPGQL,
      formatResult: function(val) {
        if (!val) return "";
        // Extract stdout from query-sync response wrapper
        if (typeof val === "object" && !Array.isArray(val) && val.stdout !== undefined) {
          val = val.stdout;
        }
        if (typeof val === "string") {
          val = val.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "");
          try { return JSON.stringify(JSON.parse(val), null, 2); } catch (e) { return val; }
        }
        try {
          var s = JSON.stringify(val, null, 2);
          return s.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "");
        } catch (e) { return String(val); }
      },
      formatTime: function(ts) {
        var d = new Date(ts);
        return d.toLocaleTimeString();
      },
      doParse: async function() {
        var self = this;
        self.parsing = true;
        self.parseResult = null;
        self.parseError = null;
        try {
          var resp = await fetch("/api/parse", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source_code: self.sourceCode,
              language: self.language,
              sample_id: self.sampleId,
              overwrite: true
            })
          });
          var data = await resp.json();
          if (resp.ok && data.ok) {
            self.parseResult = data;
            try {
              var cpgPath = data.cpg_path || "/workspace/cpg-out/" + self.sampleId;
              await fetch("/api/query-sync", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: 'importCpg("' + escapeCPGQL(cpgPath) + '")' })
              });
              await fetch("/api/query-sync", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: 'load_cpg("' + escapeCPGQL(cpgPath) + '")' })
              });
            } catch (e) { /* best-effort */ }
          } else {
            self.parseError = JSON.stringify(data, null, 2);
          }
        } catch (e) {
          self.parseError = "Network error: " + e.message;
        } finally {
          self.parsing = false;
        }
      },
      doCleanup: async function() {
        var self = this;
        try {
          await fetch("/api/cleanup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sample_id: self.sampleId })
          });
          self.parseResult = null;
          self.parseError = null;
        } catch (e) {
          self.parseError = "Cleanup error: " + e.message;
        }
      },
      runRawQuery: async function() {
        var self = this;
        if (!self.rawQuery.trim()) return;
        return self._runQuery(self.rawQuery.trim());
      },
      _runQuery: async function(query) {
        var self = this;
        self.queryRunning = true;
        self.rawResult = null;
        self.rawError = null;
        try {
          var resp = await fetch("/api/query-sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: query })
          });
          var data = await resp.json();
          if (resp.ok && data.success !== false) {
            self.rawResult = data;
          } else {
            self.rawError = JSON.stringify(data, null, 2);
            self.rawResult = data;
          }
        } catch (e) {
          self.rawError = "Network error: " + e.message;
        } finally {
          self.queryRunning = false;
          self._addHistory(query, self.rawResult || { error: self.rawError });
        }
      },
      _addHistory: function(query, result) {
        this.queryHistory.unshift({ query: query, result: result, timestamp: Date.now() });
        if (this.queryHistory.length > 20) this.queryHistory.pop();
      },
      replayQuery: function(h) {
        this.rawQuery = h.query;
        this._runQuery(h.query);
      },
      copyResult: function() {
        var text = this.formattedRawResult;
        navigator.clipboard.writeText(text).catch(function() {});
      },
      copyToolResult: function() {
        var text = this.formattedToolResult;
        navigator.clipboard.writeText(text).catch(function() {});
      },
      renderGraph: async function() {
        var self = this;
        if (!self.graphMethod.trim()) return;
        self.graphLoading = true;
        self.graphError = null;
        self.graphData = null;

        try {
          var resp = await fetch("/api/graph/" + self.graphType, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ method_full_name: self.graphMethod.trim() })
          });
          var data = await resp.json();
          if (resp.ok && data.nodes) {
            self.graphData = data;
            self._initCytoscape(data);
          } else {
            self.graphError = JSON.stringify(data, null, 2);
          }
        } catch (e) {
          self.graphError = "Network error: " + e.message;
        } finally {
          self.graphLoading = false;
        }
      },
      _initCytoscape: function(graphData) {
        var self = this;
        if (self.cyInstance) {
          self.cyInstance.destroy();
          self.cyInstance = null;
        }

        var elements = [];

        for (var i = 0; i < graphData.nodes.length; i++) {
          var n = graphData.nodes[i];
          var label = n.code || n.label || n.id;
          if (label.length > 30) label = label.substring(0, 30) + "...";

          var nodeColor = "#58a6ff";
          if (n.label && n.label.indexOf("METHOD") !== -1) nodeColor = "#d2a8ff";
          else if (n.label && n.label.indexOf("CALL") !== -1) nodeColor = "#3fb950";
          else if (n.label && n.label.indexOf("LITERAL") !== -1) nodeColor = "#d29922";
          else if (n.label && n.label.indexOf("IDENTIFIER") !== -1) nodeColor = "#f0883e";
          else if (n.label && n.label.indexOf("BLOCK") !== -1) nodeColor = "#79c0ff";
          else if (n.label && n.label.indexOf("CONTROL") !== -1) nodeColor = "#ff7b72";

          elements.push({
            data: { id: n.id, label: label },
            style: { 'background-color': nodeColor }
          });
        }

        for (var j = 0; j < graphData.edges.length; j++) {
          var e = graphData.edges[j];
          elements.push({
            data: { id: "e" + j, source: e.source, target: e.target, label: e.label || "" }
          });
        }

        self.cyInstance = cytoscape({
          container: document.getElementById('cy'),
          elements: elements,
          style: [
            { selector: 'node', style: { 'label': 'data(label)', 'text-valign': 'bottom', 'text-halign': 'center', 'color': '#c9d1d9', 'font-size': '11px', 'text-outline-width': 2, 'text-outline-color': '#0d1117', 'width': 'mapData(weight, 0, 100, 30, 80)', 'height': 'mapData(weight, 0, 100, 30, 80)' } },
            { selector: 'edge', style: { 'width': 2, 'line-color': '#30363d', 'target-arrow-color': '#58a6ff', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'label': 'data(label)', 'font-size': '10px', 'color': '#484f58', 'text-background-color': '#0d1117', 'text-background-opacity': 1 } },
            { selector: ':selected', style: { 'border-width': 3, 'border-color': '#58a6ff' } }
          ],
          layout: { name: 'breadthfirst', directed: true, spacingFactor: 1.5 },
          minZoom: 0.1,
          maxZoom: 3
        });
      },
      onToolChange: function() {
        this.toolResult = null;
        this.toolError = null;
        if (!this.selectedTool) { this.toolDef = null; this.toolParams = {}; return; }
        var def = Tools[this.selectedTool];
        this.toolDef = def;
        this.toolParams = {};
        if (def.params) {
          for (var i = 0; i < def.params.length; i++) {
            var p = def.params[i];
            if (p.type === "select") this.toolParams[p.name] = p.options ? p.options[0] : "";
            else this.toolParams[p.name] = "";
          }
        }
      },
      executeTool: async function() {
        var self = this;
        if (!self.selectedTool || !self.toolDef) return;
        var def = self.toolDef;
        self.toolRunning = true;
        self.toolResult = null;
        self.toolError = null;

        try {
          if (def._custom) {
            // parse_source — call /api/parse directly
            var sampleId = self.toolParams.sample_id || "playground-tool";
            var lang = self.toolParams.language || "";
            var resp = await fetch("/api/parse", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                source_code: self.toolParams.source_code || self.sourceCode,
                sample_id: sampleId,
                language: lang,
                overwrite: true
              })
            });
            var parseData = await resp.json();
            if (resp.ok && parseData.ok && parseData.cpg_path) {
              var cpgPath = parseData.cpg_path;
              await fetch("/api/query-sync", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: 'importCpg("' + escapeCPGQL(cpgPath) + '")' })
              });
              await fetch("/api/query-sync", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: 'load_cpg("' + escapeCPGQL(cpgPath) + '")' })
              });
              self.toolResult = parseData;
            } else {
              self.toolError = JSON.stringify(parseData, null, 2);
              self.toolResult = parseData;
            }
          } else {
            // MCP tools — call the MCP bridge on the Express server
            var resp = await fetch("/mcp/tools/" + self.selectedTool, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(self.toolParams)
            });
            var data = await resp.json();
            if (resp.ok) {
              self.toolResult = data;
            } else {
              self.toolError = JSON.stringify(data, null, 2);
              self.toolResult = data;
            }
          }
        } catch (e) {
          self.toolError = "Network error: " + e.message;
        } finally {
          self.toolRunning = false;
        }
      }
    },
    mounted() {
      this.onToolChange();
    }
  });

  app.mount('#app');
})();
