(function() {
  'use strict';

  window.PlaygroundState = Vue.reactive({
    sampleId: 'playground-sample',
    isLoaded: false,
    language: 'c'
  });

  var app = Vue.createApp({
    template: '<div>' +
      '<h1>Joern CPG Playground</h1>' +
      '<parse-panel></parse-panel>' +
      '<query-panel></query-panel>' +
      '<div style="text-align:center;margin-top:16px;padding:12px">' +
        '<a href="/graph" target="_blank" class="btn btn-secondary">Open Graph Visualization</a>' +
      '</div>' +
    '</div>'
  });

  app.component('parse-panel', window.ParsePanel);
  app.component('query-panel', window.QueryPanel);

  app.mount('#app');
})();
