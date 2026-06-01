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

      '<div style="margin-top:14px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
          '<span style="font-size:11px;color:#8b949e;font-weight:600">' +
            'Known CPG sample_ids (out/archive)' +
            '<span v-if="knownSamplesTotal"> &mdash; {{ knownSamplesTotal }} total</span>' +
          '</span>' +
          '<button class="btn btn-secondary btn-xs" @click="fetchKnownSamples(true)" :disabled="knownSamplesLoading">' +
            '{{ knownSamplesLoading ? "Refreshing..." : "Refresh" }}' +
          '</button>' +
        '</div>' +
        '<div v-if="knownSamplesError" class="result-panel"><pre class="error">{{ knownSamplesError }}</pre></div>' +
        '<div v-else-if="knownSamples.length" class="repo-table-wrap" style="max-height:220px;overflow:auto">' +
          '<table class="repo-table">' +
            '<thead><tr><th>sample_id</th><th>State</th><th>Actions</th></tr></thead>' +
            '<tbody>' +
              '<tr v-for="s in filteredKnownSamples" :key="s.sample_id">' +
                '<td class="repo-name-cell"><span class="repo-name">{{ s.sample_id }}</span></td>' +
                '<td>' +
                  '<span class="variant-badge parsed" v-if="s.in_out">out</span>' +
                  '<span class="variant-badge" v-if="s.archived">archive</span>' +
                '</td>' +
                '<td class="actions-cell">' +
                  '<button class="btn btn-secondary btn-xs" @click="loadKnownSample(s)" :disabled="!!busyKey">' +
                    '<span v-if="busyKey===s.sample_id+\'-known-load\'" class="spinner"></span>' +
                    '{{ busyKey===s.sample_id+"-known-load" ? "Loading..." : "Load" }}' +
                  '</button>' +
                '</td>' +
              '</tr>' +
            '</tbody>' +
          '</table>' +
        '</div>' +
        '<div v-else-if="!knownSamplesLoading" class="info-row">No known sample_ids discovered yet. Click Refresh.</div>' +
        '<div v-if="knownSamplesHasMore" style="margin-top:8px;text-align:center">' +
          '<button class="btn btn-secondary btn-xs" @click="loadMoreKnownSamples" :disabled="knownSamplesLoading">' +
            'Load more ({{ knownSamples.length }} / {{ knownSamplesTotal }})' +
          '</button>' +
        '</div>' +
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
        knownSamples: [],
        knownSamplesTotal: 0,
        knownSamplesHasMore: false,
        knownSamplesOffset: 0,
        knownSamplesPageSize: 100,
        knownSamplesLoading: false,
        knownSamplesError: null,
        cpgContainerOut: '/workspace/cpg/out',
        sampleById: {},
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
      },
      filteredKnownSamples: function() {
        if (!this.searchTerm.trim()) return this.knownSamples;
        var q = this.searchTerm.toLowerCase();
        return this.knownSamples.filter(function(s) {
          return s.sample_id.toLowerCase().indexOf(q) !== -1;
        });
      }
    },
    methods: {
      sampleId: function(name, variant) {
        return name + '__' + variant;
      },
      isParsed: function(name, variant) {
        var sid = this.sampleId(name, variant);
        if (this.parsedSet[sid]) return true;
        var row = this.sampleById[sid];
        if (row && (row.in_out || row.archived)) return true;
        return false;
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

      cpgPathFor: function(sampleId) {
        return this.cpgContainerOut.replace(/\/$/, '') + '/' + sampleId;
      },

      markSampleAvailable: function(sampleId, meta) {
        if (!sampleId) return;
        this.parsedSet[sampleId] = meta || { cpg_path: this.cpgPathFor(sampleId) };
      },

      ensureCpgInOut: async function(sampleId) {
        var self = this;
        var known = self.sampleById[sampleId];
        if (known && known.in_out) {
          return { ok: true, cpg_path: known.cpg_path || self.cpgPathFor(sampleId) };
        }
        if (!known || !known.archived) {
          try {
            var lookupResp = await fetch('/api/cpg/lookup?sample_id=' + encodeURIComponent(sampleId));
            var lookupData = await lookupResp.json();
            if (lookupResp.ok && lookupData.ok) {
              known = lookupData;
              self.sampleById[sampleId] = lookupData;
              if (lookupData.in_out) {
                return { ok: true, cpg_path: lookupData.cpg_path || self.cpgPathFor(sampleId) };
              }
            }
          } catch (e) { /* fall through to restore */ }
        }
        var parseResp = await fetch('/api/parse', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sample_id: sampleId, overwrite: true })
        });
        var parseData = await parseResp.json();
        if (!(parseResp.ok && parseData.ok)) {
          return { ok: false, data: parseData };
        }
        if (known) known.in_out = true;
        self.markSampleAvailable(sampleId, parseData);
        return { ok: true, cpg_path: parseData.cpg_path || self.cpgPathFor(sampleId) };
      },

      registerKnownSamples: function(samples) {
        var self = this;
        (samples || []).forEach(function(s) {
          if (!s || !s.sample_id) return;
          self.sampleById[s.sample_id] = s;
          if (s.in_out || s.archived) {
            self.markSampleAvailable(s.sample_id, {
              cpg_path: s.cpg_path || self.cpgPathFor(s.sample_id),
              archived: s.archived,
              in_out: s.in_out
            });
          }
        });
      },

      fetchKnownSamples: async function(reset) {
        var self = this;
        if (reset) {
          self.knownSamplesOffset = 0;
          self.knownSamples = [];
        }
        self.knownSamplesLoading = true;
        self.knownSamplesError = null;
        try {
          var params = new URLSearchParams({
            limit: String(self.knownSamplesPageSize),
            offset: String(self.knownSamplesOffset)
          });
          if (self.searchTerm.trim()) params.set('q', self.searchTerm.trim());
          var resp = await fetch('/api/cpg/samples?' + params.toString());
          var data = await resp.json();
          if (data.ok) {
            if (data.cpg_container_out) self.cpgContainerOut = data.cpg_container_out;
            var page = data.samples || [];
            if (reset || self.knownSamplesOffset === 0) {
              self.knownSamples = page;
            } else {
              self.knownSamples = self.knownSamples.concat(page);
            }
            self.knownSamplesTotal = data.total != null ? data.total : self.knownSamples.length;
            self.knownSamplesHasMore = Boolean(data.has_more);
            self.knownSamplesOffset = self.knownSamples.length;
            self.registerKnownSamples(page);
          } else {
            self.knownSamplesError = data.error || 'Failed to load sample list';
          }
        } catch (e) {
          self.knownSamplesError = 'Network error: ' + e.message;
        } finally {
          self.knownSamplesLoading = false;
        }
      },

      loadMoreKnownSamples: function() {
        this.fetchKnownSamples(false);
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
        var loadKey = sid + '-load';
        self.busyKey = loadKey;
        self.parseLog = null;
        self.parseLogError = false;
        try {
          var ready = await self.ensureCpgInOut(sid);
          if (!ready.ok) {
            self.parseLogError = true;
            self.parseLog = 'Failed to restore from archive:\n' + JSON.stringify(ready.data, null, 2);
            return;
          }
          var cpgPath = ready.cpg_path;
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
            self.markSampleAvailable(sid, { cpg_path: cpgPath });
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

      loadKnownSample: async function(sample) {
        var self = this;
        if (!sample || !sample.sample_id) return;
        var sid = sample.sample_id;
        var loadKey = sid + '-known-load';
        self.busyKey = loadKey;
        self.parseLog = null;
        self.parseLogError = false;
        try {
          var ready = await self.ensureCpgInOut(sid);
          if (!ready.ok) {
            self.parseLogError = true;
            self.parseLog = 'Failed to restore from archive:\n' + JSON.stringify(ready.data, null, 2);
            return;
          }
          var cpgPath = ready.cpg_path;
          var loadResp = await fetch('/api/query-sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Affinity-Key': sid },
            body: JSON.stringify({ query: 'importCpg("' + cpgPath + '")' })
          });
          var loadData = await loadResp.json();
          if (loadResp.ok && loadData.success !== false) {
            if (window.PlaygroundState) {
              window.PlaygroundState.affinityKey = sid;
              window.PlaygroundState.isLoaded = true;
              window.PlaygroundState.sampleId = sid;
            }
            self.parseLog = 'Loaded CPG: ' + sid + '\n' + (loadData.stdout || '');
          } else {
            self.parseLogError = true;
            self.parseLog = 'Failed to load CPG:\n' + JSON.stringify(loadData, null, 2);
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
      var self = this;
      self.fetchDatasets().then(function() {
        self.fetchKnownSamples(true);
      });
    }
  };
})();
