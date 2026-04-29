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
        panels: { parse: true, query: true, tool: true },
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
          var resp = await fetch("/parse", {
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
              await fetch("/query-sync", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: 'importCpg("' + escapeCPGQL(cpgPath) + '")' })
              });
              await fetch("/query-sync", {
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
          await fetch("/cleanup", {
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
          var resp = await fetch("/query-sync", {
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
            var sampleId = self.toolParams.sample_id || "playground-tool";
            var lang = self.toolParams.language || "";
            var resp = await fetch("/parse", {
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
              await fetch("/query-sync", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: 'importCpg("' + escapeCPGQL(cpgPath) + '")' })
              });
              await fetch("/query-sync", {
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
            var query;
            try {
              query = def.cpgql(self.toolParams);
            } catch (e) {
              self.toolError = "CPGQL generation error: " + e.message;
              self.toolRunning = false;
              return;
            }
            var resp2 = await fetch("/query-sync", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ query: query })
            });
            var data = await resp2.json();
            if (resp2.ok && data.success !== false) {
              self.toolResult = data;
            } else {
              self.toolError = JSON.stringify(data, null, 2);
              self.toolResult = data;
            }
            self._addHistory(query, self.toolResult);
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
