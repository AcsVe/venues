// Shared interface behaviour: the off-canvas sidebar on phones, the user menu, the password eye.
(function () {
  function sidebar() { return document.getElementById('sidebar'); }
  function overlay() { return document.getElementById('sidebarOverlay'); }
  function setNav(open) {
    var s = sidebar(), o = overlay();
    if (!s) return;
    s.classList.toggle('open', open);
    if (o) o.classList.toggle('open', open);
    if (document.body) document.body.classList.toggle('nav-open', open);
    var t = document.querySelector('[data-nav-toggle]');
    if (t) t.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  window.toggleSidebar = function () { var s = sidebar(); setNav(!(s && s.classList.contains('open'))); };
  window.closeSidebar = function () { setNav(false); };

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t.closest) return;
    if (t.closest('[data-nav-toggle]')) { window.toggleSidebar(); return; }
    if (t.closest('[data-nav-close]')) { setNav(false); return; }
    // a click outside an open menu closes it; a click on a menu entry closes it too
    document.querySelectorAll('details.user-menu[open]').forEach(function (d) {
      if (!d.contains(t) || t.closest('.menu a')) d.removeAttribute('open');
    });
    var eye = t.closest('[data-eye]');
    if (eye) {
      var input = document.getElementById(eye.getAttribute('data-eye'));
      if (!input) return;
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      eye.setAttribute('aria-pressed', show ? 'true' : 'false');
      var i = eye.querySelector('i');
      if (i) { i.classList.toggle('fa-eye', !show); i.classList.toggle('fa-eye-slash', show); }
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      setNav(false);
      document.querySelectorAll('details.user-menu[open]').forEach(function (d) { d.removeAttribute('open'); });
    }
  });
  // leaving phone width with the drawer open must not leave the page locked
  var mq = window.matchMedia ? window.matchMedia('(min-width: 992px)') : null;
  if (mq && mq.addEventListener) mq.addEventListener('change', function (m) { if (m.matches) setNav(false); });
})();
