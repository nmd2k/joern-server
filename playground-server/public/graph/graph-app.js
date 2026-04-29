(function() {
  'use strict';

  function escapeHTML(str) {
    if (typeof str !== 'string') return String(str);
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var SHAPE_COLORS = {
    'box': '#58a6ff',
    'rectangle': '#58a6ff',
    'rect': '#58a6ff',
    'ellipse': '#3fb950',
    'oval': '#3fb950',
    'circle': '#3fb950',
    'diamond': '#f0883e',
    'plaintext': '#8b949e',
    'none': '#8b949e'
  };

  var TYPE_ENDPOINTS = {
    'cfg': '/api/graph/cfg',
    'dfg': '/api/graph/dfg',
    'pdg': '/api/graph/pdg',
    'ast': '/api/graph/ast'
  };

  var app = Vue.createApp({
    data: function() {
      return {
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
      escapeHTML: escapeHTML,
      onTypeChange: function() {
        var self = this;
        self.graphData = null;
        self.graphMetadata = null;
        self.selectedNode = null;
        self.detailOpen = false;
        self.graphError = null;
        if (self.cyInstance) {
          self.cyInstance.destroy();
          self.cyInstance = null;
        }
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
        if (self.cyInstance) {
          self.cyInstance.destroy();
          self.cyInstance = null;
        }

        var endpoint = TYPE_ENDPOINTS[self.graphType] || '/api/graph/cfg';

        try {
          var resp = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ method_full_name: self.graphMethod.trim() })
          });
          var data = await resp.json();
          if (resp.ok && data.nodes) {
            self.graphData = data;
            self.graphMetadata = data.metadata || null;
            self.renderGraph(data);
          } else {
            self.graphError = JSON.stringify(data, null, 2);
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
          if (typeof label === 'string' && label.length > 30) {
            label = label.substring(0, 30) + '...';
          }

          var nodeColor = SHAPE_COLORS[n.shape] || null;
          if (!nodeColor) {
            if (n.label && n.label.indexOf('METHOD') !== -1) nodeColor = '#d2a8ff';
            else if (n.label && n.label.indexOf('CALL') !== -1) nodeColor = '#3fb950';
            else if (n.label && n.label.indexOf('LITERAL') !== -1) nodeColor = '#d29922';
            else if (n.label && n.label.indexOf('IDENTIFIER') !== -1) nodeColor = '#f0883e';
            else if (n.label && n.label.indexOf('BLOCK') !== -1) nodeColor = '#79c0ff';
            else if (n.label && n.label.indexOf('CONTROL') !== -1) nodeColor = '#ff7b72';
            else if (n.label && n.label.indexOf('RETURN') !== -1) nodeColor = '#ff7b72';
            else nodeColor = '#58a6ff';
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
              'label': 'data(label)',
              'text-valign': 'bottom',
              'text-halign': 'center',
              'color': '#c9d1d9',
              'font-size': '11px',
              'text-outline-width': 2,
              'text-outline-color': '#0d1117',
              'width': 50,
              'height': 50
            }},
            { selector: 'edge', style: {
              'width': 2,
              'line-color': '#30363d',
              'target-arrow-color': '#58a6ff',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              'label': 'data(label)',
              'font-size': '10px',
              'color': '#484f58',
              'text-background-color': '#0d1117',
              'text-background-opacity': 1
            }},
            { selector: ':selected', style: {
              'border-width': 3,
              'border-color': '#58a6ff'
            }}
          ],
          layout: { name: 'breadthfirst', directed: true, spacingFactor: 1.5 },
          minZoom: 0.1,
          maxZoom: 3
        });

        var tooltip = self.$refs.tooltip;
        var container = self.$refs.graphContainer;

        self.cyInstance.on('mouseover', 'node', function(evt) {
          var node = evt.target;
          var data = node.data();
          var meta = metadata[data.id] || {};
          self.showTooltip(evt, data, meta, tooltip, container);
        });

        self.cyInstance.on('mousemove', 'node', function(evt) {
          self._positionTooltip(evt, tooltip, container);
        });

        self.cyInstance.on('mouseout', 'node', function() {
          self.hideTooltip();
        });

        self.cyInstance.on('tap', 'node', function(evt) {
          var node = evt.target;
          var data = node.data();
          var meta = metadata[data.id] || {};
          self.openDetail(data, meta);
        });

        self.cyInstance.on('tap', function(evt) {
          if (evt.target === self.cyInstance) {
            self.closeDetail();
          }
        });
      },
      showTooltip: function(evt, data, meta, tooltip, container) {
        var self = this;
        var nodeType = (meta && meta.node_type) || data.shape || data.label || '';
        var code = (meta && meta.code) || data.label || '';
        var lineNumber = (meta && meta.lineNumber) || null;

        var html = '<div class="tt-type" style="color:' + escapeHTML(self._nodeTypeColor(nodeType)) + '">' + escapeHTML(nodeType) + '</div>';
        html += '<div class="tt-code">' + escapeHTML(code) + '</div>';
        if (lineNumber) {
          html += '<div class="tt-line">Line ' + escapeHTML(String(lineNumber)) + '</div>';
        }

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
        if (!nodeType) return '#58a6ff';
        var t = String(nodeType).toUpperCase();
        if (t.indexOf('METHOD') !== -1 || t.indexOf('FUNC') !== -1) return '#d2a8ff';
        if (t.indexOf('CALL') !== -1) return '#3fb950';
        if (t.indexOf('LITERAL') !== -1) return '#d29922';
        if (t.indexOf('IDENTIFIER') !== -1) return '#f0883e';
        if (t.indexOf('BLOCK') !== -1) return '#79c0ff';
        if (t.indexOf('CONTROL') !== -1 || t.indexOf('RETURN') !== -1) return '#ff7b72';
        if (t.indexOf('PARAM') !== -1) return '#79c0ff';
        if (t.indexOf('LOCAL') !== -1) return '#3fb950';
        return '#8b949e';
      },
      hideTooltip: function() {
        this.tooltipVisible = false;
        var tooltip = this.$refs.tooltip;
        if (tooltip) tooltip.innerHTML = '';
      },
      openDetail: function(nodeData, meta) {
        this.selectedNode = nodeData;
        var clean = {};
        if (meta) {
          var keys = Object.keys(meta);
          for (var i = 0; i < keys.length; i++) {
            if (meta[keys[i]] !== null) clean[keys[i]] = meta[keys[i]];
          }
        }
        this.selectedNodeMeta = clean;
        this.detailOpen = true;
      },
      closeDetail: function() {
        this.selectedNode = null;
        this.selectedNodeMeta = {};
        this.detailOpen = false;
        if (this.cyInstance) {
          this.cyInstance.elements().unselect();
        }
      }
    },
    mounted: function() {
      var self = this;
      self.$nextTick(function() {
        var urlParams = new URLSearchParams(window.location.search);
        var methodParam = urlParams.get('method');
        var typeParam = urlParams.get('type');
        if (methodParam) {
          self.graphMethod = methodParam;
        }
        if (typeParam && TYPE_ENDPOINTS[typeParam]) {
          self.graphType = typeParam;
        }
      });
    }
  });

  app.mount('#app');
})();
