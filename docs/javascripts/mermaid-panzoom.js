(function () {
  const SCALE_MIN = 0.5;
  const SCALE_MAX = 8;
  const SCALE_STEP = 0.15;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function wrapMermaid(node) {
    if (!node) return;

    const existing = node.closest('.mermaid-panzoom');
    if (existing) {
      const toolbar = existing.querySelector('.mermaid-panzoom__toolbar');
      const content = existing.querySelector('.mermaid-panzoom__content');
      if (toolbar && content) {
        return { wrapper: existing, toolbar, content };
      }
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'mermaid-panzoom';

    const toolbar = document.createElement('div');
    toolbar.className = 'mermaid-panzoom__toolbar';

    const buttons = [
      { action: 'zoom-in', label: '+' },
      { action: 'zoom-out', label: '−' },
      { action: 'reset', label: 'Reset' },
      { action: 'fullscreen', label: 'Fullscreen' },
      { action: 'exit-fullscreen', label: 'Sair tela cheia' },
    ];

    buttons.forEach(({ action, label }) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'mermaid-panzoom__button';
      btn.dataset.action = action;
      btn.textContent = label;
      toolbar.appendChild(btn);
    });

    const content = document.createElement('div');
    content.className = 'mermaid-panzoom__content';

    node.parentNode.insertBefore(wrapper, node);
    content.appendChild(node);
    wrapper.appendChild(toolbar);
    wrapper.appendChild(content);

    return { wrapper, toolbar, content };
  }

  function initMermaidPanzoom(node) {
    const wrap = wrapMermaid(node);
    if (!wrap) return;

    const { wrapper, toolbar, content } = wrap;
    let state = wrapper._panzoomState;
    const isNewState = !state;
    if (!state) {
      state = {
        scale: 1,
        translateX: 0,
        translateY: 0,
        isDragging: false,
        dragStartX: 0,
        dragStartY: 0,
        target: null,
      };
      wrapper._panzoomState = state;
    }
    state.target = node;

    function applyTransform() {
      if (!state.target) return;
      const transform = `translate(${state.translateX}px, ${state.translateY}px) scale(${state.scale})`;
      state.target.style.transformOrigin = '0 0';
      state.target.style.transform = transform;
    }

    function reset() {
      state.scale = 1;
      state.translateX = 0;
      state.translateY = 0;
      applyTransform();
    }

    function zoom(delta) {
      state.scale = clamp(state.scale + delta, SCALE_MIN, SCALE_MAX);
      applyTransform();
    }

    const updateFullscreenButtons = () => {
      const isFullscreen = wrapper.classList.contains('is-fullscreen');
      toolbar.querySelectorAll('button[data-action="fullscreen"]').forEach((btn) => {
        btn.style.display = isFullscreen ? 'none' : '';
      });
      toolbar.querySelectorAll('button[data-action="exit-fullscreen"]').forEach((btn) => {
        btn.style.display = isFullscreen ? '' : 'none';
      });
    };

    const setFullscreen = (enabled) => {
      wrapper.classList.toggle('is-fullscreen', enabled);
      updateFullscreenButtons();
    };

    const bindToolbar = () => {
      toolbar.querySelectorAll('button[data-action]').forEach((button) => {
        if (button.dataset.bound === '1') return;
        button.dataset.bound = '1';
        button.addEventListener('click', (event) => {
          event.preventDefault();
          const action = button.dataset.action;
          if (!action) return;

          if (action === 'zoom-in') zoom(SCALE_STEP);
          if (action === 'zoom-out') zoom(-SCALE_STEP);
          if (action === 'reset') reset();
          if (action === 'fullscreen') {
            setFullscreen(true);
          }
          if (action === 'exit-fullscreen') {
            setFullscreen(false);
          }
        });
      });
    };

    const bindContent = () => {
      if (content.dataset.bound === '1') return;
      content.dataset.bound = '1';
      content.addEventListener(
        'wheel',
        (event) => {
          event.preventDefault();
          const step = event.ctrlKey ? SCALE_STEP * 2 : SCALE_STEP;
          const delta = event.deltaY > 0 ? -step : step;
          zoom(delta);
        },
        { passive: false },
      );

      content.addEventListener('mousedown', (event) => {
        state.isDragging = true;
        state.dragStartX = event.clientX - state.translateX;
        state.dragStartY = event.clientY - state.translateY;
        content.classList.add('is-dragging');
      });

      content.addEventListener('touchstart', (event) => {
        if (event.touches.length !== 1) return;
        const touch = event.touches[0];
        state.isDragging = true;
        state.dragStartX = touch.clientX - state.translateX;
        state.dragStartY = touch.clientY - state.translateY;
        content.classList.add('is-dragging');
      });
    };

    const bindWindow = () => {
      if (wrapper.dataset.boundWindow === '1') return;
      wrapper.dataset.boundWindow = '1';

      window.addEventListener('mousemove', (event) => {
        if (!state.isDragging) return;
        state.translateX = event.clientX - state.dragStartX;
        state.translateY = event.clientY - state.dragStartY;
        applyTransform();
      });

      window.addEventListener('touchmove', (event) => {
        if (!state.isDragging || event.touches.length !== 1) return;
        const touch = event.touches[0];
        state.translateX = touch.clientX - state.dragStartX;
        state.translateY = touch.clientY - state.dragStartY;
        applyTransform();
      });

      window.addEventListener('mouseup', () => {
        if (!state.isDragging) return;
        state.isDragging = false;
        content.classList.remove('is-dragging');
      });

      window.addEventListener('touchend', () => {
        if (!state.isDragging) return;
        state.isDragging = false;
        content.classList.remove('is-dragging');
      });

      window.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          setFullscreen(false);
        }
      });
    };

    bindToolbar();
    bindContent();
    bindWindow();
    updateFullscreenButtons();

    reset();
  }

  function initAll() {
    document.querySelectorAll('.mermaid').forEach((node) => initMermaidPanzoom(node));
  }

  const observer = new MutationObserver(() => initAll());

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initAll();
      observer.observe(document.body, { childList: true, subtree: true });
    });
  } else {
    initAll();
    observer.observe(document.body, { childList: true, subtree: true });
  }
})();
