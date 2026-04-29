(function() {
  'use strict';

  var toolGroups = ['Connectivity', 'CPG Loading', 'Method Analysis', 'Call Analysis', 'Class Analysis', 'Vulnerability Hunting'];

  var tmpl = document.createElement('template');
  tmpl.id = 'tools-panel-template';
  tmpl.innerHTML = '<div class="panel">' +
    '<div class="panel-header" @click="panelOpen=!panelOpen">' +
      '<h2><span class="chevron" :class="{open:panelOpen}">&#9654;</span> MCP Tool Runner</h2>' +
    '</div>' +
    '<div class="panel-body" :class="{show:panelOpen}">' +
      '<div class="form-group">' +
        '<label>Select Tool</label>' +
        '<select v-model="selectedTool" @change="onToolChange">' +
          '<option value="">-- Choose a tool --</option>' +
          '<optgroup v-for="grp in toolGroups" :key="grp" :label="grp">' +
            '<option v-for="t in toolsByGroup[grp]" :key="t.key" :value="t.key">{{ t.key }}</option>' +
          '</optgroup>' +
        '</select>' +
      '</div>' +
      '<div v-if="toolDef" class="param-grid" :class="{full:!toolDef.params.length}">' +
        '<div v-for="p in toolDef.params" :key="p.name" class="form-group">' +
          '<label>{{ p.label }}</label>' +
          '<input v-if="p.type===\'text\' || p.type===\'number\'" v-model="toolParams[p.name]"' +
                 ' :type="p.type" :placeholder="p.label">' +
          '<select v-else-if="p.type===\'select\'" v-model="toolParams[p.name]">' +
            '<option v-for="o in p.options" :key="o" :value="o">{{ o }}</option>' +
          '</select>' +
        '</div>' +
      '</div>' +
      '<div v-if="toolDef" style="margin-top:8px">' +
        '<button class="btn btn-primary" @click="executeTool" :disabled="toolRunning">' +
          '<span v-if="toolRunning" class="spinner"></span>' +
          '{{ toolRunning ? \'Executing...\' : \'Execute\' }}' +
        '</button>' +
      '</div>' +
      '<div v-if="toolResult" class="result-panel">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
          '<span style="font-size:11px;color:#8b949e">Tool Result ({{ selectedTool }})</span>' +
          '<button class="copy-btn" @click="copyToolResult">Copy</button>' +
        '</div>' +
        '<pre :class="{error:toolError}">{{ toolError || formattedToolResult }}</pre>' +
      '</div>' +
    '</div>' +
  '</div>';
  document.body.appendChild(tmpl);

  window.ToolsPanel = {
    template: '#tools-panel-template',
    data: function() {
      return {
        panelOpen: true,
        selectedTool: '',
        toolParams: {},
        toolDef: null,
        toolRunning: false,
        toolResult: null,
        toolError: null,
        TOOLS: window.JOERN_TOOLS || {},
        toolGroups: toolGroups,
        toolsByGroup: {}
      };
    },
    computed: {
      formattedToolResult: function() {
        return this.formatResult(this.toolResult);
      }
    },
    created: function() {
      var self = this;
      self.toolsByGroup = {};
      for (var i = 0; i < toolGroups.length; i++) {
        self.toolsByGroup[toolGroups[i]] = [];
      }
      var Tools = window.JOERN_TOOLS || {};
      var keys = Object.keys(Tools);
      for (var j = 0; j < keys.length; j++) {
        var key = keys[j];
        var def = Tools[key];
        var g = def.group;
        if (!self.toolsByGroup[g]) {
          self.toolsByGroup[g] = [];
          if (toolGroups.indexOf(g) === -1) toolGroups.push(g);
        }
        self.toolsByGroup[g].push({ key: key, group: g });
      }
    },
    methods: {
      escapeCPGQL: function(str) {
        if (typeof str !== 'string') return str;
        return str.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
      },
      formatResult: function(val) {
        if (!val) return '';
        if (typeof val === 'object' && !Array.isArray(val) && val.stdout !== undefined) {
          val = val.stdout;
        }
        if (typeof val === 'string') {
          val = val.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '');
          try { return JSON.stringify(JSON.parse(val), null, 2); } catch (e) { return val; }
        }
        try {
          var s = JSON.stringify(val, null, 2);
          return s.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '');
        } catch (e) { return String(val); }
      },
      onToolChange: function() {
        this.toolResult = null;
        this.toolError = null;
        if (!this.selectedTool) { this.toolDef = null; this.toolParams = {}; return; }
        var Tools = window.JOERN_TOOLS || {};
        var def = Tools[this.selectedTool];
        this.toolDef = def;
        this.toolParams = {};
        if (def && def.params) {
          for (var i = 0; i < def.params.length; i++) {
            var p = def.params[i];
            if (p.type === 'select') this.toolParams[p.name] = p.options ? p.options[0] : '';
            else this.toolParams[p.name] = '';
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
            var sampleId = self.toolParams.sample_id || 'playground-tool';
            var lang = self.toolParams.language || '';
            var resp = await fetch('/api/parse', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                source_code: self.toolParams.source_code || '',
                sample_id: sampleId,
                language: lang,
                overwrite: true
              })
            });
            var parseData = await resp.json();
            if (resp.ok && parseData.ok && parseData.cpg_path) {
              var cpgPath = parseData.cpg_path;
              await fetch('/api/query-sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: 'importCpg("' + self.escapeCPGQL(cpgPath) + '")' })
              });
              await fetch('/api/query-sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: 'load_cpg("' + self.escapeCPGQL(cpgPath) + '")' })
              });
              self.toolResult = parseData;
            } else {
              self.toolError = JSON.stringify(parseData, null, 2);
              self.toolResult = parseData;
            }
          } else {
            var resp = await fetch('/mcp/tools/' + self.selectedTool, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
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
          self.toolError = 'Network error: ' + e.message;
        } finally {
          self.toolRunning = false;
        }
      },
      copyToolResult: function() {
        var text = this.formattedToolResult;
        navigator.clipboard.writeText(text).catch(function() {});
      }
    }
  };
})();
