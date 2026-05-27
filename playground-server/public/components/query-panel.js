(function() {
  'use strict';

  var tmpl = document.createElement('template');
  tmpl.id = 'query-panel-template';
  tmpl.innerHTML = '<div class="panel">' +
    '<div class="panel-header" @click="panelOpen=!panelOpen">' +
      '<h2><span class="chevron" :class="{open:panelOpen}">&#9654;</span> CPGQL Query Editor</h2>' +
      '<span v-if="affinityKey" class="badge badge-green" style="font-size:10px">{{ affinityKey }}</span>' +
    '</div>' +
    '<div class="panel-body" :class="{show:panelOpen}">' +
      '<div v-if="!affinityKey" class="info-banner">' +
        'No CPG loaded. Parse &amp; load a repo above, or paste code in the Parse panel.' +
      '</div>' +
      '<div class="form-group">' +
        '<label>Raw CPGQL Query</label>' +
        '<textarea v-model="rawQuery" class="mono" rows="6" placeholder=\'e.g. cpg.method.name("main").l\' @keydown="handleKeydown"></textarea>' +
      '</div>' +
      '<div style="margin-bottom:10px">' +
        '<button class="btn btn-primary" @click="runRawQuery" :disabled="queryRunning">' +
          '<span v-if="queryRunning" class="spinner"></span>' +
          '{{ queryRunning ? \'Running...\' : \'Run Query\' }}' +
        '</button>' +
        '<span v-if="lastLatency !== null" class="info-row" style="margin-left:10px">{{ lastLatency }}ms</span>' +
      '</div>' +
      '<div v-if="rawResult" class="result-panel">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
          '<span style="font-size:11px;color:#8b949e">Result</span>' +
          '<button class="copy-btn" @click="copyResult">Copy</button>' +
        '</div>' +
        '<pre :class="{error:rawError}">{{ rawError || formattedRawResult }}</pre>' +
      '</div>' +
      '<div style="margin-top:12px">' +
        '<span style="font-size:11px;color:#8b949e;font-weight:600">Query History (last 20)</span>' +
        '<ul v-if="queryHistory.length" class="history-list">' +
          '<li v-for="(h,i) in queryHistory" :key="i" class="history-item" @click="replayQuery(h)">' +
            '<span class="q">{{ h.query.substring(0, 80) }}{{ h.query.length>80?\'...\':\'\' }}</span>' +
            '<span class="ts">{{ formatTime(h.timestamp) }}</span>' +
          '</li>' +
        '</ul>' +
        '<div v-else class="info-row">No queries yet</div>' +
      '</div>' +
    '</div>' +
  '</div>';
  document.body.appendChild(tmpl);

  window.QueryPanel = {
    template: '#query-panel-template',
    data: function() {
      return {
        panelOpen: true,
        rawQuery: 'cpg.method.name.l.take(10)',
        rawResult: null,
        rawError: null,
        queryRunning: false,
        queryHistory: [],
        lastLatency: null
      };
    },
    computed: {
      affinityKey: function() {
        return window.PlaygroundState ? window.PlaygroundState.affinityKey : null;
      },
      formattedRawResult: function() {
        return this.formatResult(this.rawResult);
      }
    },
    methods: {
      _buildHeaders: function() {
        var h = { 'Content-Type': 'application/json' };
        var key = window.PlaygroundState ? window.PlaygroundState.affinityKey : null;
        if (key) h['X-Affinity-Key'] = key;
        return h;
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
      formatTime: function(ts) {
        var d = new Date(ts);
        return d.toLocaleTimeString();
      },
      handleKeydown: function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          this.runRawQuery();
        }
      },
      runRawQuery: async function() {
        if (!this.rawQuery.trim()) return;
        return this._runQuery(this.rawQuery.trim());
      },
      _runQuery: async function(query) {
        var self = this;
        self.queryRunning = true;
        self.rawResult = null;
        self.rawError = null;
        self.lastLatency = null;
        var t0 = performance.now();
        try {
          var resp = await fetch('/api/query-sync', {
            method: 'POST',
            headers: self._buildHeaders(),
            body: JSON.stringify({ query: query })
          });
          var data = await resp.json();
          self.lastLatency = Math.round(performance.now() - t0);
          if (resp.ok && data.success !== false) {
            self.rawResult = data;
          } else {
            self.rawError = JSON.stringify(data, null, 2);
            self.rawResult = data;
          }
        } catch (e) {
          self.rawError = 'Network error: ' + e.message;
          self.lastLatency = Math.round(performance.now() - t0);
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
      }
    }
  };
})();
