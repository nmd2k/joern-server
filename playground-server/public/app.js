(function() {
  'use strict';

  window.PlaygroundState = Vue.reactive({
    sampleId: 'playground-sample',
    isLoaded: false,
    language: 'c',
    affinityKey: null
  });

  var app = Vue.createApp({
    template: '<div>' +
      '<h1>Joern CPG Playground' +
        '<span v-if="state.affinityKey" class="session-indicator">' +
          '&#9679; {{ state.affinityKey }}' +
        '</span>' +
      '</h1>' +
      '<repo-panel></repo-panel>' +
      '<parse-panel></parse-panel>' +
      '<query-panel></query-panel>' +
      '<div style="text-align:center;margin-top:16px;padding:12px">' +
        '<a :href="graphUrl" target="_blank" class="btn btn-secondary">Open Graph Visualization</a>' +
      '</div>' +
    '</div>',
    data: function() {
      return { state: window.PlaygroundState };
    },
    computed: {
      graphUrl: function() {
        if (this.state.affinityKey) {
          return '/graph?affinity=' + encodeURIComponent(this.state.affinityKey);
        }
        return '/graph';
      }
    }
  });

  app.component('repo-panel', window.RepoPanel);
  app.component('parse-panel', window.ParsePanel);
  app.component('query-panel', window.QueryPanel);

  app.mount('#app');
})();
