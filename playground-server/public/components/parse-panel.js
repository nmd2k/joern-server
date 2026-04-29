(function() {
  'use strict';

  var tmpl = document.createElement('template');
  tmpl.id = 'parse-panel-template';
  tmpl.innerHTML = '<div class="panel">' +
    '<div class="panel-header" @click="panelOpen=!panelOpen">' +
      '<h2><span class="chevron" :class="{open:panelOpen}">&#9654;</span> Parse &amp; Load CPG</h2>' +
      '<span v-if="parseResult" class="badge" :class="parseResult.cache_hit?\'badge-green\':\'badge-yellow\'">' +
        '{{ parseResult.cache_hit ? \'cache hit\' : \'fresh parse\' }}' +
      '</span>' +
    '</div>' +
    '<div class="panel-body" :class="{show:panelOpen}">' +
      '<div class="form-group">' +
        '<label>Source Code</label>' +
        '<textarea v-model="sourceCode" class="mono" rows="8" placeholder="Paste source code here..."></textarea>' +
      '</div>' +
      '<div class="row">' +
        '<div class="form-group" style="flex:0 0 150px">' +
          '<label>Language</label>' +
          '<select v-model="language">' +
            '<option value="c">C</option>' +
            '<option value="cpp">C++</option>' +
            '<option value="cs">C#</option>' +
            '<option value="go">Go</option>' +
            '<option value="java">Java</option>' +
            '<option value="js">JavaScript</option>' +
            '<option value="python">Python</option>' +
            '<option value="ruby">Ruby</option>' +
          '</select>' +
        '</div>' +
        '<div class="form-group" style="flex:1">' +
          '<label>Sample ID</label>' +
          '<input v-model="sampleId" type="text" placeholder="playground-sample">' +
        '</div>' +
        '<div class="form-group" style="flex:0 0 auto;align-self:flex-end">' +
          '<button class="btn btn-primary" @click="doParse" :disabled="parsing">' +
            '<span v-if="parsing" class="spinner"></span>' +
            '{{ parsing ? \'Parsing...\' : \'Parse & Load\' }}' +
          '</button>' +
          '<button class="btn btn-danger" @click="doCleanup" :disabled="!parseResult" style="margin-left:6px">Cleanup</button>' +
        '</div>' +
      '</div>' +
      '<div v-if="parseResult" class="result-bar">' +
        '<span class="badge" :class="parseResult.cache_hit?\'badge-green\':\'badge-yellow\'">' +
          '{{ parseResult.cache_hit ? \'cache_hit\' : \'fresh parse\' }}' +
        '</span>' +
        '<span class="info-row">cpg_path: <code>{{ parseResult.cpg_path }}</code></span>' +
        '<span class="info-row">source_hash: <code>{{ parseResult.source_hash }}</code></span>' +
      '</div>' +
      '<div v-if="parseError" class="result-panel"><pre class="error">{{ parseError }}</pre></div>' +
    '</div>' +
  '</div>';
  document.body.appendChild(tmpl);

  window.ParsePanel = {
    template: '#parse-panel-template',
    data: function() {
      return {
        panelOpen: true,
        sourceCode: '#include <stdio.h>\n\nint add(int a, int b) {\n    return a + b;\n}\n\nint main() {\n    printf("Sum: %d\\n", add(3, 4));\n    return 0;\n}',
        language: 'c',
        sampleId: 'playground-sample',
        parsing: false,
        parseResult: null,
        parseError: null
      };
    },
    methods: {
      escapeCPGQL: function(str) {
        if (typeof str !== 'string') return str;
        return str.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
      },
      doParse: async function() {
        var self = this;
        self.parsing = true;
        self.parseResult = null;
        self.parseError = null;
        try {
          var resp = await fetch('/api/parse', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
            if (window.PlaygroundState) {
              window.PlaygroundState.isLoaded = true;
              window.PlaygroundState.sampleId = self.sampleId;
              window.PlaygroundState.language = self.language;
            }
            try {
              var cpgPath = data.cpg_path || '/workspace/cpg-out/' + self.sampleId;
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
            } catch (e) { /* best-effort */ }
          } else {
            self.parseError = JSON.stringify(data, null, 2);
          }
        } catch (e) {
          self.parseError = 'Network error: ' + e.message;
        } finally {
          self.parsing = false;
        }
      },
      doCleanup: async function() {
        var self = this;
        try {
          await fetch('/api/cleanup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sample_id: self.sampleId })
          });
          self.parseResult = null;
          self.parseError = null;
          if (window.PlaygroundState) {
            window.PlaygroundState.isLoaded = false;
          }
        } catch (e) {
          self.parseError = 'Cleanup error: ' + e.message;
        }
      }
    }
  };
})();
