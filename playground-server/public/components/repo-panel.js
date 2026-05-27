(function() {
  'use strict';

  var tmpl = document.createElement('template');
  tmpl.id = 'repo-panel-template';
  tmpl.innerHTML = '<div class="panel">' +
    '<div class="panel-header" @click="panelOpen=!panelOpen">' +
      '<h2><span class="chevron" :class="{open:panelOpen}">&#9654;</span> Server Repos</h2>' +
      '<span v-if="activeSession" class="badge badge-green">{{ activeSession }}</span>' +
    '</div>' +
    '<div class="panel-body" :class="{show:panelOpen}">' +

      /* Active session banner */
      '<div v-if="activeSession" class="active-session-bar">' +
        '<span>Active CPG: <strong>{{ activeSession }}</strong></span>' +
        '<button class="btn btn-danger btn-xs" @click="closeSession">Close</button>' +
      '</div>' +

      /* Search / filter */
      '<div class="form-group" style="margin-bottom:10px">' +
        '<input v-model="searchTerm" type="text" placeholder="Filter repos...">' +
      '</div>' +

      /* Loading / error */
      '<div v-if="loading" style="text-align:center;padding:20px"><span class="spinner"></span> Loading datasets...</div>' +
      '<div v-if="loadError" class="result-panel"><pre class="error">{{ loadError }}</pre></div>' +

      /* Repo table */
      '<div v-if="!loading && filteredDatasets.length" class="repo-table-wrap">' +
        '<table class="repo-table">' +
          '<thead><tr><th>Repository</th><th>Variants</th><th>Actions</th></tr></thead>' +
          '<tbody>' +
            '<tr v-for="ds in filteredDatasets" :key="ds.name">' +
              '<td class="repo-name-cell">' +
                '<span class="repo-name">{{ ds.name }}</span>' +
              '</td>' +
              '<td>' +
                '<span v-for="v in ds.variants" :key="v" class="variant-badge" ' +
                  ':class="{active: isActive(ds.name, v), parsed: isParsed(ds.name, v)}">' +
                  '{{ v }}' +
                '</span>' +
              '</td>' +
              '<td class="actions-cell">' +
                '<div class="action-row" v-for="v in ds.variants" :key="v">' +
                  '<span class="variant-label">{{ v }}</span>' +
                  '<button v-if="!isParsed(ds.name, v)" class="btn btn-primary btn-xs" ' +
                    '@click="parseRepo(ds, v)" :disabled="!!busyKey">' +
                    '<span v-if="busyKey===sampleId(ds.name,v)" class="spinner"></span>' +
                    '{{ busyKey===sampleId(ds.name,v) ? \'Parsing...\' : \'Parse\' }}' +
                  '</button>' +
                  '<button v-if="isParsed(ds.name, v) && !isActive(ds.name, v)" class="btn btn-secondary btn-xs" ' +
                    '@click="loadCpg(ds, v)" :disabled="!!busyKey">' +
                    '<span v-if="busyKey===sampleId(ds.name,v)+\'-load\'" class="spinner"></span>' +
                    '{{ busyKey===sampleId(ds.name,v)+\'-load\' ? \'Loading...\' : \'Load\' }}' +
                  '</button>' +
                  '<span v-if="isActive(ds.name, v)" class="badge badge-green" style="font-size:10px">active</span>' +
                '</div>' +
              '</td>' +
            '</tr>' +
          '</tbody>' +
        '</table>' +
      '</div>' +
      '<div v-if="!loading && !loadError && !filteredDatasets.length" class="info-row">' +
        'No datasets found. Place repos in the datasets directory.' +
      '</div>' +

      /* Parse log */
      '<div v-if="parseLog" class="result-panel" style="margin-top:10px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
          '<span style="font-size:11px;color:#8b949e">Last operation</span>' +
          '<button class="copy-btn" @click="parseLog=null">Clear</button>' +
        '</div>' +
        '<pre :class="{error: parseLogError}">{{ parseLog }}</pre>' +
      '</div>' +

    '</div>' +
  '</div>';
  document.body.appendChild(tmpl);

  window.RepoPanel = {
    template: '#repo-panel-template',
    data: function() {
      return {
        panelOpen: true,
        loading: false,
        loadError: null,
        datasets: [],
        searchTerm: '',
        parsedSet: {},
        busyKey: null,
        parseLog: null,
        parseLogError: false
      };
    },
    computed: {
      activeSession: function() {
        return window.PlaygroundState ? window.PlaygroundState.affinityKey : null;
      },
      filteredDatasets: function() {
        if (!this.searchTerm.trim()) return this.datasets;
        var q = this.searchTerm.toLowerCase();
        return this.datasets.filter(function(ds) {
          return ds.name.toLowerCase().indexOf(q) !== -1;
        });
      }
    },
    methods: {
      sampleId: function(name, variant) {
        return name + '__' + variant;
      },
      isParsed: function(name, variant) {
        return !!this.parsedSet[this.sampleId(name, variant)];
      },
      isActive: function(name, variant) {
        if (!window.PlaygroundState) return false;
        return window.PlaygroundState.affinityKey === this.sampleId(name, variant);
      },

      fetchDatasets: async function() {
        var self = this;
        self.loading = true;
        self.loadError = null;
        try {
          var resp = await fetch('/api/datasets');
          var data = await resp.json();
          if (data.ok) {
            self.datasets = data.datasets || [];
          } else {
            self.loadError = data.error || 'Failed to load datasets';
          }
        } catch (e) {
          self.loadError = 'Network error: ' + e.message;
        } finally {
          self.loading = false;
        }
      },

      parseRepo: async function(ds, variant) {
        var self = this;
        var sid = self.sampleId(ds.name, variant);
        self.busyKey = sid;
        self.parseLog = null;
        self.parseLogError = false;
        try {
          var resp = await fetch('/api/parse/repo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              source_root: ds.container_path + '/' + variant,
              sample_id: sid,
              overwrite: true
            })
          });
          var data = await resp.json();
          if (resp.ok && data.ok) {
            self.parsedSet[sid] = data;
            self.parseLog = 'Parsed ' + sid + '\n' +
              'Files: ' + (data.file_count || '?') + '\n' +
              'CPG: ' + (data.cpg_path || '?') + '\n' +
              'Cache hit: ' + (data.cache_hit || false);
            self.loadCpg(ds, variant);
          } else {
            self.parseLogError = true;
            self.parseLog = JSON.stringify(data, null, 2);
          }
        } catch (e) {
          self.parseLogError = true;
          self.parseLog = 'Network error: ' + e.message;
        } finally {
          self.busyKey = null;
        }
      },

      loadCpg: async function(ds, variant) {
        var self = this;
        var sid = self.sampleId(ds.name, variant);
        var cpgPath = '/workspace/cpg-out/' + sid;
        var loadKey = sid + '-load';
        self.busyKey = loadKey;
        self.parseLog = null;
        self.parseLogError = false;
        try {
          var resp = await fetch('/api/query-sync', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Affinity-Key': sid
            },
            body: JSON.stringify({ query: 'importCpg("' + cpgPath + '")' })
          });
          var data = await resp.json();
          if (resp.ok && data.success !== false) {
            self.parsedSet[sid] = self.parsedSet[sid] || { cpg_path: cpgPath };
            if (window.PlaygroundState) {
              window.PlaygroundState.affinityKey = sid;
              window.PlaygroundState.isLoaded = true;
              window.PlaygroundState.sampleId = sid;
            }
            self.parseLog = 'Loaded CPG: ' + sid + '\n' + (data.stdout || '');
          } else {
            self.parseLogError = true;
            self.parseLog = 'Failed to load CPG:\n' + JSON.stringify(data, null, 2);
          }
        } catch (e) {
          self.parseLogError = true;
          self.parseLog = 'Network error: ' + e.message;
        } finally {
          self.busyKey = null;
        }
      },

      closeSession: async function() {
        var self = this;
        if (!window.PlaygroundState || !window.PlaygroundState.affinityKey) return;
        var key = window.PlaygroundState.affinityKey;
        try {
          await fetch('/api/query-sync', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Affinity-Key': key
            },
            body: JSON.stringify({ query: 'close' })
          });
        } catch (e) { /* best-effort */ }
        window.PlaygroundState.affinityKey = null;
        window.PlaygroundState.isLoaded = false;
        self.parseLog = 'Closed session: ' + key;
      }
    },
    mounted: function() {
      this.fetchDatasets();
    }
  };
})();
