(() => {
  const toggle = document.getElementById('mobile-menu');
  const sidebar = document.getElementById('sidebar');
  const mobile = window.matchMedia('(max-width:767px)');
  const sync = () => {
    const open = document.body.classList.contains('nav-open');
    toggle?.setAttribute('aria-expanded', String(open));
    if (sidebar) sidebar.inert = mobile.matches && !open;
  };
  const close = () => {
    if (mobile.matches && sidebar?.contains(document.activeElement)) toggle?.focus();
    document.body.classList.remove('nav-open');
    sync();
  };
  document.getElementById('sidebar-scrim')?.addEventListener('click', close);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  new MutationObserver(sync).observe(document.body, {attributes: true, attributeFilter: ['class']});
  mobile.addEventListener('change', sync);
  sidebar?.addEventListener('click', e => { if (e.target.closest('[data-view]')) close(); });
  sync();

  // Thu gọn / mở rộng thanh bên (desktop), nhớ lựa chọn trong localStorage.
  const collapseButton = document.getElementById('sidebar-toggle');
  const collapsedClass = 'sidebar-collapsed';
  const readStored = () => { try { return localStorage.getItem('sidebar-collapsed') === '1'; } catch { return false; } };
  const store = (value) => { try { localStorage.setItem('sidebar-collapsed', value ? '1' : '0'); } catch { /* ignore */ } };
  const labelLinks = (collapsed) => {
    document.querySelectorAll('.side-nav .nav-link, .sidebar-bottom .nav-link').forEach((link) => {
      const label = link.querySelector('span')?.textContent?.trim();
      if (collapsed && label) link.title = label;
      else link.removeAttribute('title');
    });
  };
  const applyCollapsed = (collapsed, persist) => {
    document.body.classList.toggle(collapsedClass, collapsed);
    collapseButton?.setAttribute('aria-expanded', String(!collapsed));
    collapseButton?.setAttribute('aria-label', collapsed ? 'Mở rộng thanh bên' : 'Thu gọn thanh bên');
    if (collapseButton) collapseButton.title = collapsed ? 'Mở rộng thanh bên' : 'Thu gọn thanh bên';
    labelLinks(collapsed);
    if (persist) store(collapsed);
  };
  applyCollapsed(readStored(), false);
  collapseButton?.addEventListener('click', () => {
    applyCollapsed(!document.body.classList.contains(collapsedClass), true);
  });
})();
