/* EBMS — Ethiopian calendar helper (browser)
 * ------------------------------------------------------------------
 * 1. window.EthCal.toEthiopian(Date|"YYYY-MM-DD") → {year, month, day}
 *    window.EthCal.toGregorian(y, m, d)          → Date
 *    window.EthCal.format(eth, lang)              → "መስከረም 2፣ 2019"
 * 2. Every <input type="date"> on the page gets a small Ethiopian-calendar
 *    badge underneath showing the equivalent E.C. date, plus an "E.C." button
 *    that lets the user type/select an Ethiopian date which is converted
 *    into the underlying Gregorian value (the server always receives
 *    Gregorian ISO dates, so no backend changes are needed).
 * 3. Elements with data-et-date="YYYY-MM-DD" get their text filled with the
 *    Ethiopian equivalent (useful for read-only tables).
 * Same JDN algorithm as web/ethiopian_calendar.py.
 */
(function () {
  'use strict';
  var EPOCH = 1723856;
  var MONTHS_AM = ['መስከረም','ጥቅምት','ኅዳር','ታኅሣሥ','ጥር','የካቲት','መጋቢት','ሚያዝያ','ግንቦት','ሰኔ','ሐምሌ','ነሐሴ','ጳጉሜን'];
  var MONTHS_EN = ['Meskerem','Tikimt','Hidar','Tahsas','Tir','Yekatit','Megabit','Miyazya','Ginbot','Sene','Hamle','Nehase','Pagume'];

  function fdiv(a, b) { return Math.floor(a / b); }
  function gToJdn(y, m, d) {
    var a = fdiv(14 - m, 12), yy = y + 4800 - a, mm = m + 12 * a - 3;
    return d + fdiv(153 * mm + 2, 5) + 365 * yy + fdiv(yy, 4) - fdiv(yy, 100) + fdiv(yy, 400) - 32045;
  }
  function jdnToG(jdn) {
    var a = jdn + 32044, b = fdiv(4 * a + 3, 146097), c = a - fdiv(146097 * b, 4);
    var d = fdiv(4 * c + 3, 1461), e = c - fdiv(1461 * d, 4), m = fdiv(5 * e + 2, 153);
    var day = e - fdiv(153 * m + 2, 5) + 1, month = m + 3 - 12 * fdiv(m, 10), year = 100 * b + d - 4800 + fdiv(m, 10);
    return new Date(Date.UTC(year, month - 1, day));
  }
  function eToJdn(y, m, d) { return EPOCH + 365 + 365 * (y - 1) + fdiv(y, 4) + 30 * m + d - 31; }
  function jdnToE(jdn) {
    var r = ((jdn - EPOCH) % 1461 + 1461) % 1461;
    var n = r % 365 + 365 * fdiv(r, 1460);
    return { year: 4 * fdiv(jdn - EPOCH, 1461) + fdiv(r, 365) - fdiv(r, 1460), month: fdiv(n, 30) + 1, day: n % 30 + 1 };
  }
  function parseG(v) {
    if (v instanceof Date) return isNaN(v) ? null : v;
    if (!v) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v));
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  }
  function toEthiopian(v) {
    var g = parseG(v); if (!g) return null;
    return jdnToE(gToJdn(g.getUTCFullYear(), g.getUTCMonth() + 1, g.getUTCDate()));
  }
  function isLeap(y) { return ((y % 4) + 4) % 4 === 3; }
  function daysIn(y, m) { return m === 13 ? (isLeap(y) ? 6 : 5) : 30; }
  function toGregorian(y, m, d) {
    if (m < 1 || m > 13 || d < 1 || d > daysIn(y, m)) return null;
    return jdnToG(eToJdn(y, m, d));
  }
  function iso(date) { return date.toISOString().slice(0, 10); }
  function format(e, lang) {
    if (!e) return '';
    var names = lang === 'am' ? MONTHS_AM : MONTHS_EN, sep = lang === 'am' ? '፣' : ',';
    return names[e.month - 1] + ' ' + e.day + sep + ' ' + e.year;
  }
  function lang() { return (document.documentElement.getAttribute('lang') || 'en').slice(0, 2); }

  // ── DOM enhancement ───────────────────────────────────────────
  function badgeFor(input) {
    var b = input._etBadge;
    if (!b) {
      b = document.createElement('div');
      b.className = 'et-date-badge small text-muted mt-1';
      b.style.cssText = 'font-size:.78rem;line-height:1.2;display:flex;gap:.4rem;align-items:center;flex-wrap:wrap';
      var txt = document.createElement('span'); txt.className = 'et-date-text';
      var btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'btn btn-link btn-sm p-0 text-decoration-none'; btn.textContent = 'ዓ.ም';
      btn.title = lang() === 'am' ? 'በኢትዮጵያ አቆጣጠር አስገባ' : 'Enter an Ethiopian-calendar date';
      btn.style.fontSize = '.78rem';
      btn.addEventListener('click', function () { openPicker(input); });
      b.appendChild(txt); b.appendChild(btn);
      // insert after the input (or after its input-group wrapper)
      var anchor = input.closest('.input-group') || input;
      anchor.insertAdjacentElement('afterend', b);
      input._etBadge = b;
    }
    return b;
  }
  function refresh(input) {
    var e = toEthiopian(input.value);
    var b = badgeFor(input);
    b.querySelector('.et-date-text').textContent = e ? ('ዓ.ም ' + format(e, lang())) : (lang() === 'am' ? 'ዓ.ም —' : 'E.C. —');
  }
  function openPicker(input) {
    closePicker();
    var e = toEthiopian(input.value) || toEthiopian(new Date());
    var wrap = document.createElement('div');
    wrap.className = 'et-picker card shadow-sm p-2';
    wrap.style.cssText = 'position:absolute;z-index:1080;min-width:260px;font-size:.85rem';
    var L = lang(), names = L === 'am' ? MONTHS_AM : MONTHS_EN;
    var mo = '<select class="form-select form-select-sm et-m">' + names.map(function (n, i) { return '<option value="' + (i + 1) + '"' + (i + 1 === e.month ? ' selected' : '') + '>' + n + '</option>'; }).join('') + '</select>';
    wrap.innerHTML =
      '<div class="d-flex gap-1 mb-2">' +
      '<input type="number" class="form-control form-control-sm et-d" min="1" max="30" value="' + e.day + '" style="width:64px" aria-label="day">' +
      mo +
      '<input type="number" class="form-control form-control-sm et-y" min="1900" max="2200" value="' + e.year + '" style="width:84px" aria-label="year">' +
      '</div>' +
      '<div class="d-flex justify-content-between align-items-center">' +
      '<span class="text-muted et-preview"></span>' +
      '<span><button type="button" class="btn btn-sm btn-outline-secondary et-cancel me-1">' + (L === 'am' ? 'ዝጋ' : 'Close') + '</button>' +
      '<button type="button" class="btn btn-sm btn-primary et-ok">' + (L === 'am' ? 'ተጠቀም' : 'Use') + '</button></span></div>';
    document.body.appendChild(wrap);
    var r = input.getBoundingClientRect();
    wrap.style.left = (window.scrollX + r.left) + 'px';
    wrap.style.top = (window.scrollY + r.bottom + 4) + 'px';
    function current() {
      var d = +wrap.querySelector('.et-d').value, m = +wrap.querySelector('.et-m').value, y = +wrap.querySelector('.et-y').value;
      wrap.querySelector('.et-d').max = daysIn(y, m);
      return toGregorian(y, m, d);
    }
    function preview() {
      var g = current();
      wrap.querySelector('.et-preview').textContent = g ? iso(g) : (L === 'am' ? 'ልክ ያልሆነ ቀን' : 'invalid date');
    }
    wrap.addEventListener('input', preview); preview();
    wrap.querySelector('.et-cancel').addEventListener('click', closePicker);
    wrap.querySelector('.et-ok').addEventListener('click', function () {
      var g = current(); if (!g) return;
      input.value = iso(g);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      closePicker();
    });
    window._etPicker = wrap;
    setTimeout(function () { document.addEventListener('mousedown', outside, { once: true }); }, 0);
    function outside(ev) { if (window._etPicker && !window._etPicker.contains(ev.target)) closePicker(); else if (window._etPicker) document.addEventListener('mousedown', outside, { once: true }); }
  }
  function closePicker() { if (window._etPicker) { window._etPicker.remove(); window._etPicker = null; } }

  function enhance(root) {
    (root || document).querySelectorAll('input[type="date"]:not([data-no-et])').forEach(function (inp) {
      if (inp._etBound) return; inp._etBound = true;
      refresh(inp);
      inp.addEventListener('input', function () { refresh(inp); });
      inp.addEventListener('change', function () { refresh(inp); });
    });
    (root || document).querySelectorAll('[data-et-date]').forEach(function (el) {
      var e = toEthiopian(el.getAttribute('data-et-date'));
      if (e) el.textContent = format(e, lang());
    });
  }

  window.EthCal = { toEthiopian: toEthiopian, toGregorian: toGregorian, format: format, enhance: enhance, MONTHS_AM: MONTHS_AM, MONTHS_EN: MONTHS_EN };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { enhance(); });
  else enhance();
  // Pick up inputs injected later (HTMX / Alpine / modals)
  if (window.MutationObserver) {
    new MutationObserver(function (muts) {
      muts.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) enhance(n); }); });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
})();
