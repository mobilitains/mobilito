/*
Copyright 2024  Francais pour une Meilleure Mobilité

Author(s): Jeff Abrahamson <jeff@p27.eu>.

This file is part of the mobilito web application.

Mobilito is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

Mobilito is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public
License along with mobilito.  If not, see
<http://www.gnu.org/licenses/>.
*/

/*
 * The dark mode switch (templates/includes/footer.html) reloads the
 * page, which would leave focus at the top and screen readers silent
 * about the new state. So: note the switch in sessionStorage as it's
 * submitted, and on the next page focus it again, which announces
 * "Dark mode, switch, on" (or off). The mark expires after a few
 * seconds, so a submit that never loads a page (offline) doesn't
 * move focus on some later page.
 *
 * Without storage (private mode, blocked site data) the switch still
 * works; focus just starts at the top as before.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    let storage = null;
    try {
      // With site data blocked, merely reading it throws.
      storage = root.sessionStorage;
    } catch (e) {
      return;
    }
    api.watch(document, storage);
    document.addEventListener('DOMContentLoaded', function () {
      api.restoreFocus(document, storage);
    });
  }
})(this, function () {
  'use strict';

  const KEY = 'mobilito-theme-switched';
  const SELECTOR = '[data-mbl-theme-switch]';
  const MAX_AGE_MS = 10000;

  // Capture phase, so it runs even when htmx takes over the submit.
  function watch(doc, storage, now) {
    now = now || Date.now;
    doc.addEventListener(
      'submit',
      function (event) {
        if (event.target.matches && event.target.matches(SELECTOR)) {
          try {
            storage.setItem(KEY, String(now()));
          } catch (e) {
            // No storage: nothing to restore, which is fine.
          }
        }
      },
      true
    );
  }

  function restoreFocus(doc, storage, now) {
    now = now || Date.now;
    let switched = false;
    try {
      const age = now() - Number(storage.getItem(KEY));
      switched = age >= 0 && age < MAX_AGE_MS;
      storage.removeItem(KEY);
    } catch (e) {
      return false;
    }
    const button = switched && doc.querySelector(SELECTOR + ' button');
    if (!button) {
      return false;
    }
    button.focus();
    return true;
  }

  return {
    watch: watch,
    restoreFocus: restoreFocus,
    KEY: KEY,
    MAX_AGE_MS: MAX_AGE_MS,
  };
});
