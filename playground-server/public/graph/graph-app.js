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

  function isUnresolvedMethod(fullName) {
    return !fullName || fullName.indexOf('unresolved') !== -1 || fullName.indexOf('<unresolved') !== -1;
  }

  function parseMethodList(stdout) {
    if (!stdout || typeof stdout !== 'string') return [];
    var methods = [];
    var strings = [];
    var re = /"((?:[^"\\]|\\.)*)"/g;
    var match;
    while ((match = re.exec(stdout)) !== null) {
      strings.push(match[1]);
    }
    var nums = stdout.match(/,\s*(-?\d+)\s*\)/g) || [];
    var numValues = nums.map(function(s) {
      var m = s.match(/-?\d+/);
      return m ? parseInt(m[0]) : -1;
    });
    var tupleCount = Math.min(Math.floor(strings.length / 3), numValues.length);
    for (var i = 0; i < tupleCount; i++) {
      var fullName = strings[i * 3];
      var name = strings[i * 3 + 1];
      var file = strings[i * 3 + 2];
      var line = numValues[i];
      if (fullName && name && !isUnresolvedMethod(fullName)) {
        methods.push({ fullName: fullName, name: name, file: file || '', line: line > 0 ? line : null });
      }
    }
    methods.sort(function(a, b) {
      if (a.file !== b.file) return a.file < b.file ? -1 : 1;
      if (a.line !== null && b.line !== null) return a.line - b.line;
      return a.name < b.name ? -1 : 1;
    });
    return methods;
  }

  var app = Vue.createApp({
    data: function() {
      return {
        affinityKey: getAffinityKey(),
        methods: [],
        methodFilter: '',
        methodsLoading: false,
        methodsError: null,
        cpgSummary: null,
        graphMethod: '',
        graphType: 'cfg',
        graphLoading: false,
        graphError: null,
        graphData: null,
        graphMetadata: null,
        selectedNode: null,
        selectedNodeMeta: {},
        detailOpen: false,
        tooltipVisible: false,
        cyInstance: null
      };
    },
    computed: {
      filteredMethods: function() {
        if (!this.methodFilter.trim()) return this.methods;
        var q = this.methodFilter.toLowerCase();
        return this.methods.filter(function(m) {
          return m.fullName.toLowerCase().indexOf(q) !== -1 ||
                 m.name.toLowerCase().indexOf(q) !== -1 ||
                 m.file.toLowerCase().indexOf(q) !== -1;
        });
      },
      selectedNodeExtraMeta: function() {
        var meta = this.selectedNodeMeta;
        if (!meta) return {};
        var known = ['node_type', 'code', 'lineNumber', 'columnNumber', 'order', 'argumentIndex', 'fullName', 'name', 'id', 'label', 'shape'];
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

      ensureCpgLoaded: async function() {
        if (!this.affinityKey) return false;
        var cpgPath = '/workspace/cpg-out/' + this.affinityKey;
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

      fetchMethods: async function() {
        var self = this;
        self.methodsLoading = true;
        self.methodsError = null;
        try {
          if (self.affinityKey) {
            var loaded = await self.ensureCpgLoaded();
            if (!loaded) {
              self.methodsError = 'Failed to load CPG for ' + self.affinityKey + '. Parse it in the Playground first.';
              return;
            }
          }
          var query = 'cpg.method.filterNot(_.fullName.contains("unresolved")).map(m => (m.fullName, m.name, m.filename, m.lineNumber.getOrElse(-1))).l';
          var resp = await fetch('/api/query-sync', {
            method: 'POST',
            headers: self._buildHeaders(),
            body: JSON.stringify({ query: query })
          });
          var data = await resp.json();
          if (resp.ok && data.success !== false) {
            self.methods = parseMethodList(data.stdout || '');
            var files = {};
            for (var i = 0; i < self.methods.length; i++) {
              if (self.methods[i].file) files[self.methods[i].file] = true;
            }
            self.cpgSummary = { methods: self.methods.length, files: Object.keys(files).length };
          } else {
            self.methodsError = 'Query failed: ' + ((data.stdout || data.error || '') + '').substring(0, 200);
          }
        } catch (e) {
          self.methodsError = 'Network error: ' + e.message;
        } finally {
          self.methodsLoading = false;
        }
      },

      selectMethod: function(fullName) {
        this.graphMethod = fullName;
        this.loadGraph();
      },

      escapeHTML: escapeHTML,

      onTypeChange: function() {
        this.graphData = null;
        this.graphMetadata = null;
        this.selectedNode = null;
        this.detailOpen = false;
        this.graphError = null;
        if (this.cyInstance) { this.cyInstance.destroy(); this.cyInstance = null; }
      },

      loadGraph: async function() {
        var self = this;
        if (!self.graphMethod.trim()) return;
        self.graphLoading = true;
        self.graphError = null;
        self.graphData = null;
        self.graphMetadata = null;
        self.selectedNode = null;
        self.detailOpen = false;
        if (self.cyInstance) { self.cyInstance.destroy(); self.cyInstance = null; }

        var endpoint = TYPE_ENDPOINTS[self.graphType] || '/api/graph/cfg';

        try {
          var resp = await fetch(endpoint, {
            method: 'POST',
            headers: self._buildHeaders(),
            body: JSON.stringify({ method_full_name: self.graphMethod.trim() })
          });
          var data = await resp.json();
          if (resp.ok && data.nodes) {
            self.graphData = data;
            self.graphMetadata = data.metadata || null;
            self.renderGraph(data);
          } else {
            var msg = data.error || JSON.stringify(data, null, 2);
            if (data.code === 'stub_method') {
              msg = 'This method has no real control flow (external/unresolved stub). Choose a method from your project source files in the list.';
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
          var meta = metadata[n.id] || {};
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
          var meta = metadata[data.id] || {};
          self.showTooltip(evt, data, meta, tooltip, container);
        });
        self.cyInstance.on('mousemove', 'node', function(evt) { self._positionTooltip(evt, tooltip, container); });
        self.cyInstance.on('mouseout', 'node', function() { self.hideTooltip(); });
        self.cyInstance.on('tap', 'node', function(evt) {
          var node = evt.target; var data = node.data();
          self.openDetail(data, metadata[data.id] || {});
        });
        self.cyInstance.on('tap', function(evt) { if (evt.target === self.cyInstance) self.closeDetail(); });
      },

      showTooltip: function(evt, data, meta, tooltip, container) {
        var self = this;
        var nodeType = (meta && meta.node_type) || data.shape || data.label || '';
        var code = (meta && meta.code) || data.label || '';
        var lineNumber = (meta && meta.lineNumber) || null;
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
        var methodParam = urlParams.get('method');
        var typeParam = urlParams.get('type');
        if (methodParam) self.graphMethod = methodParam;
        if (typeParam && TYPE_ENDPOINTS[typeParam]) self.graphType = typeParam;
        if (self.affinityKey) self.fetchMethods();
      });
    }
  });

  app.mount('#app');
})();
