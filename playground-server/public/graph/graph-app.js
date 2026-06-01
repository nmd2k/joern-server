(function() {
  'use strict';

  function escapeHTML(str) {
    if (typeof str !== 'string') return String(str);
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var SHAPE_COLORS = {
    'box': '#0969da', 'rectangle': '#0969da', 'rect': '#0969da',
    'ellipse': '#1a7f37', 'oval': '#1a7f37', 'circle': '#1a7f37',
    'diamond': '#bf8700', 'plaintext': '#656d76', 'none': '#656d76'
  };

  var TYPE_ENDPOINTS = {
    'cfg': '/api/graph/cfg',
    'dfg': '/api/graph/dfg',
    'pdg': '/api/graph/pdg',
    'ast': '/api/graph/ast'
  };

  function getAffinityKey() {
    var urlParams = new URLSearchParams(window.location.search);
    var key = urlParams.get('affinity');
    if (key) return key;
    try {
      if (window.opener && window.opener.PlaygroundState) {
        return window.opener.PlaygroundState.affinityKey || null;
      }
    } catch (e) {}
    return null;
  }

  function parseStringList(stdout) {
    if (!stdout || typeof stdout !== 'string') return [];
    var strings = [];
    var re = /"((?:[^"\\]|\\.)*)"/g;
    var match;
    while ((match = re.exec(stdout)) !== null) {
      var val = match[1];
      if (val && val.indexOf('List(') === -1 && val.indexOf('val ') === -1) {
        strings.push(val);
      }
    }
    var seen = {};
    return strings.filter(function(s) {
      if (seen[s]) return false;
      seen[s] = true;
      return true;
    });
  }

  function normalizeMeta(meta) {
    if (!meta) return {};
    var out = {};
    var keys = Object.keys(meta);
    for (var i = 0; i < keys.length; i++) {
      out[keys[i]] = meta[keys[i]];
    }
    if (out.line_number != null && out.lineNumber == null) out.lineNumber = out.line_number;
    if (out.column_number != null && out.columnNumber == null) out.columnNumber = out.column_number;
    if (out.node_type == null && out.label) out.node_type = out.label;
    return out;
  }

  var app = Vue.createApp({
    data: function() {
      return {
        affinityKey: getAffinityKey(),
        files: [],
        fileFilter: '',
        filesLoading: false,
        filesError: null,
        graphScope: 'file',
        graphFile: '',
        graphMethod: '',
        nodeLimit: 200,
        graphType: 'cfg',
        graphLoading: false,
        graphError: null,
        graphData: null,
        graphMetadata: null,
        cpgSummary: null,
        selectedNode: null,
        selectedNodeMeta: {},
        detailOpen: false,
        tooltipVisible: false,
        cyInstance: null
      };
    },
    computed: {
      filteredFiles: function() {
        if (!this.fileFilter.trim()) return this.files;
        var q = this.fileFilter.toLowerCase();
        return this.files.filter(function(f) {
          return f.toLowerCase().indexOf(q) !== -1;
        });
      },
      canLoadGraph: function() {
        if (this.graphScope === 'file') return Boolean(this.graphFile.trim());
        if (this.graphScope === 'method') return Boolean(this.graphMethod.trim());
        return true;
      },
      selectedNodeExtraMeta: function() {
        var meta = this.selectedNodeMeta;
        if (!meta) return {};
        var known = ['node_type', 'code', 'lineNumber', 'line_number', 'columnNumber', 'column_number', 'order', 'argumentIndex', 'fullName', 'name', 'id', 'label', 'shape'];
        var extra = {};
        var keys = Object.keys(meta);
        for (var i = 0; i < keys.length; i++) {
          if (known.indexOf(keys[i]) === -1 && meta[keys[i]] !== null && meta[keys[i]] !== undefined) {
            extra[keys[i]] = meta[keys[i]];
          }
        }
        return extra;
      }
    },
    methods: {
      _buildHeaders: function() {
        var h = { 'Content-Type': 'application/json' };
        if (this.affinityKey) h['X-Affinity-Key'] = this.affinityKey;
        return h;
      },

      cpgPathFor: function(sampleId) {
        return '/workspace/cpg/out/' + sampleId;
      },

      ensureCpgLoaded: async function() {
        if (!this.affinityKey) return false;
        try {
          if (window.opener && window.opener.PlaygroundState && window.opener.PlaygroundState.isLoaded
              && window.opener.PlaygroundState.affinityKey === this.affinityKey) {
            return true;
          }
        } catch (e) { /* cross-origin or closed opener */ }
        var cpgPath = this.cpgPathFor(this.affinityKey);
        try {
          var resp = await fetch('/api/query-sync', {
            method: 'POST',
            headers: this._buildHeaders(),
            body: JSON.stringify({ query: 'importCpg("' + cpgPath.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '")' })
          });
          var data = await resp.json();
          return resp.ok && data.success !== false;
        } catch (e) {
          return false;
        }
      },

      fetchFiles: async function() {
        var self = this;
        self.filesLoading = true;
        self.filesError = null;
        try {
          if (self.affinityKey) {
            var loaded = await self.ensureCpgLoaded();
            if (!loaded) {
              self.filesError = 'Failed to load CPG for ' + self.affinityKey + '. Parse it in the Playground first.';
              return;
            }
          }
          var query = 'cpg.method.filterNot(_.fullName.contains("unresolved")).filterNot(_.isExternal).map(_.filename).toSet.l';
          var resp = await fetch('/api/query-sync', {
            method: 'POST',
            headers: self._buildHeaders(),
            body: JSON.stringify({ query: query })
          });
          var data = await resp.json();
          if (resp.ok && data.success !== false) {
            self.files = parseStringList(data.stdout || '').sort();
            self.cpgSummary = { methods: null, files: self.files.length };
          } else {
            self.filesError = 'Query failed: ' + ((data.stdout || data.error || '') + '').substring(0, 200);
          }
        } catch (e) {
          self.filesError = 'Network error: ' + e.message;
        } finally {
          self.filesLoading = false;
        }
      },

      selectFile: function(fileName) {
        this.graphScope = 'file';
        this.graphFile = fileName;
        this.loadGraph();
      },

      onScopeChange: function() {
        this.graphData = null;
        this.graphMetadata = null;
        this.graphError = null;
        this.selectedNode = null;
        this.detailOpen = false;
        if (this.cyInstance) { this.cyInstance.destroy(); this.cyInstance = null; }
      },

      escapeHTML: escapeHTML,

      onTypeChange: function() {
        this.onScopeChange();
      },

      buildGraphBody: function() {
        var body = {
          scope: this.graphScope,
          node_limit: parseInt(this.nodeLimit, 10) || 200
        };
        if (this.graphScope === 'file') body.file_name = this.graphFile.trim();
        if (this.graphScope === 'method') body.method_full_name = this.graphMethod.trim();
        return body;
      },

      loadGraph: async function() {
        var self = this;
        if (!self.canLoadGraph) return;
        self.graphLoading = true;
        self.graphError = null;
        self.graphData = null;
        self.graphMetadata = null;
        self.selectedNode = null;
        self.detailOpen = false;
        if (self.cyInstance) { self.cyInstance.destroy(); self.cyInstance = null; }

        var endpoint = TYPE_ENDPOINTS[self.graphType] || '/api/graph/cfg';

        try {
          if (self.affinityKey) {
            var loaded = await self.ensureCpgLoaded();
            if (!loaded) {
              self.graphError = 'Failed to load CPG session. Load the CPG in the Playground first.';
              return;
            }
          }
          var resp = await fetch(endpoint, {
            method: 'POST',
            headers: self._buildHeaders(),
            body: JSON.stringify(self.buildGraphBody())
          });
          var data = await resp.json();
          if (resp.ok && data.nodes) {
            self.graphData = data;
            self.graphMetadata = data.metadata || null;
            self.renderGraph(data);
          } else {
            var msg = data.error || JSON.stringify(data, null, 2);
            if (data.code === 'stub_method') {
              msg = 'This method has no real control flow (external/unresolved stub). Try File or Whole CPG scope instead.';
            }
            self.graphError = msg;
          }
        } catch (e) {
          self.graphError = 'Network error: ' + e.message;
        } finally {
          self.graphLoading = false;
        }
      },

      renderGraph: function(graphData) {
        var self = this;
        var elements = [];
        var metadata = graphData.metadata || {};

        for (var i = 0; i < graphData.nodes.length; i++) {
          var n = graphData.nodes[i];
          var meta = normalizeMeta(metadata[n.id] || {});
          var label = meta.code || n.label || n.id;
          if (typeof label === 'string' && label.length > 30) label = label.substring(0, 30) + '...';

          var nodeColor = SHAPE_COLORS[n.shape] || null;
          if (!nodeColor) {
            if (n.label && n.label.indexOf('METHOD') !== -1) nodeColor = '#8250df';
            else if (n.label && n.label.indexOf('CALL') !== -1) nodeColor = '#1a7f37';
            else if (n.label && n.label.indexOf('LITERAL') !== -1) nodeColor = '#bf8700';
            else if (n.label && n.label.indexOf('IDENTIFIER') !== -1) nodeColor = '#bc4c00';
            else if (n.label && n.label.indexOf('BLOCK') !== -1) nodeColor = '#0969da';
            else if (n.label && n.label.indexOf('CONTROL') !== -1) nodeColor = '#cf222e';
            else if (n.label && n.label.indexOf('RETURN') !== -1) nodeColor = '#cf222e';
            else nodeColor = '#0969da';
          }

          elements.push({
            data: { id: n.id, label: label, shape: n.shape || '', meta: meta },
            style: { 'background-color': nodeColor }
          });
        }

        for (var j = 0; j < graphData.edges.length; j++) {
          var e = graphData.edges[j];
          elements.push({
            data: { id: 'e' + j, source: e.source, target: e.target, label: e.label || '' }
          });
        }

        self.cyInstance = cytoscape({
          container: document.getElementById('cy'),
          elements: elements,
          style: [
            { selector: 'node', style: {
              'label': 'data(label)', 'text-valign': 'bottom', 'text-halign': 'center',
              'color': '#1f2328', 'font-size': '11px', 'text-outline-width': 2,
              'text-outline-color': '#ffffff', 'width': 50, 'height': 50
            }},
            { selector: 'edge', style: {
              'width': 2, 'line-color': '#d0d7de', 'target-arrow-color': '#0969da',
              'target-arrow-shape': 'triangle', 'curve-style': 'bezier',
              'label': 'data(label)', 'font-size': '10px', 'color': '#656d76',
              'text-background-color': '#ffffff', 'text-background-opacity': 1
            }},
            { selector: ':selected', style: { 'border-width': 3, 'border-color': '#0969da' }}
          ],
          layout: { name: 'breadthfirst', directed: true, spacingFactor: 1.5 },
          minZoom: 0.1, maxZoom: 3
        });

        var tooltip = self.$refs.tooltip;
        var container = self.$refs.graphContainer;

        self.cyInstance.on('mouseover', 'node', function(evt) {
          var node = evt.target; var data = node.data();
          var meta = normalizeMeta(metadata[data.id] || data.meta || {});
          self.showTooltip(evt, data, meta, tooltip, container);
        });
        self.cyInstance.on('mousemove', 'node', function(evt) { self._positionTooltip(evt, tooltip, container); });
        self.cyInstance.on('mouseout', 'node', function() { self.hideTooltip(); });
        self.cyInstance.on('tap', 'node', function(evt) {
          var node = evt.target; var data = node.data();
          self.openDetail(data, normalizeMeta(metadata[data.id] || data.meta || {}));
        });
        self.cyInstance.on('tap', function(evt) { if (evt.target === self.cyInstance) self.closeDetail(); });
      },

      showTooltip: function(evt, data, meta, tooltip, container) {
        var self = this;
        var nodeType = (meta && meta.node_type) || data.shape || data.label || '';
        var code = (meta && meta.code) || data.label || '';
        var lineNumber = (meta && (meta.lineNumber || meta.line_number)) || null;
        var html = '<div class="tt-type" style="color:' + escapeHTML(self._nodeTypeColor(nodeType)) + '">' + escapeHTML(nodeType) + '</div>';
        html += '<div class="tt-code">' + escapeHTML(code) + '</div>';
        if (lineNumber) html += '<div class="tt-line">Line ' + escapeHTML(String(lineNumber)) + '</div>';
        tooltip.innerHTML = html;
        self.tooltipVisible = true;
        self._positionTooltip(evt, tooltip, container);
      },
      _positionTooltip: function(evt, tooltip, container) {
        if (!this.tooltipVisible) return;
        var rect = container.getBoundingClientRect();
        var x = evt.renderedPosition ? evt.renderedPosition.x : (evt.clientX - rect.left);
        var y = evt.renderedPosition ? evt.renderedPosition.y : (evt.clientY - rect.top);
        tooltip.style.left = (x + 15) + 'px';
        tooltip.style.top = (y - 10) + 'px';
      },
      _nodeTypeColor: function(nodeType) {
        if (!nodeType) return '#0969da';
        var t = String(nodeType).toUpperCase();
        if (t.indexOf('METHOD') !== -1 || t.indexOf('FUNC') !== -1) return '#8250df';
        if (t.indexOf('CALL') !== -1) return '#1a7f37';
        if (t.indexOf('LITERAL') !== -1) return '#bf8700';
        if (t.indexOf('IDENTIFIER') !== -1) return '#bc4c00';
        if (t.indexOf('BLOCK') !== -1) return '#0969da';
        if (t.indexOf('CONTROL') !== -1 || t.indexOf('RETURN') !== -1) return '#cf222e';
        if (t.indexOf('PARAM') !== -1) return '#0969da';
        if (t.indexOf('LOCAL') !== -1) return '#1a7f37';
        return '#656d76';
      },
      hideTooltip: function() {
        this.tooltipVisible = false;
        var tooltip = this.$refs.tooltip;
        if (tooltip) tooltip.innerHTML = '';
      },
      openDetail: function(nodeData, meta) {
        this.selectedNode = nodeData;
        var clean = {};
        if (meta) { var keys = Object.keys(meta); for (var i = 0; i < keys.length; i++) { if (meta[keys[i]] !== null) clean[keys[i]] = meta[keys[i]]; } }
        this.selectedNodeMeta = clean;
        this.detailOpen = true;
      },
      closeDetail: function() {
        this.selectedNode = null;
        this.selectedNodeMeta = {};
        this.detailOpen = false;
        if (this.cyInstance) this.cyInstance.elements().unselect();
      }
    },
    mounted: function() {
      var self = this;
      self.$nextTick(function() {
        var urlParams = new URLSearchParams(window.location.search);
        var fileParam = urlParams.get('file');
        var methodParam = urlParams.get('method');
        var typeParam = urlParams.get('type');
        var scopeParam = urlParams.get('scope');
        if (fileParam) { self.graphFile = fileParam; self.graphScope = 'file'; }
        if (methodParam) { self.graphMethod = methodParam; self.graphScope = 'method'; }
        if (scopeParam) self.graphScope = scopeParam;
        if (typeParam && TYPE_ENDPOINTS[typeParam]) self.graphType = typeParam;
        if (self.affinityKey) self.fetchFiles();
      });
    }
  });

  app.mount('#app');
})();
