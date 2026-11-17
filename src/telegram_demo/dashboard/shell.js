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
})();
