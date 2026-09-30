(function () {
  function renderMermaid() {
    if (!window.mermaid) return;

    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
    });
    window.mermaid.run({ querySelector: '.mermaid' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderMermaid);
  } else {
    renderMermaid();
  }
})();
