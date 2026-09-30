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

const { watch, restoreFocus, KEY, MAX_AGE_MS } = require(
  '../../../../core/static/core/js/theme_switch.js'
);

function fakeStorage() {
  const data = {};
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
    removeItem: (k) => {
      delete data[k];
    },
  };
}

function brokenStorage() {
  const fail = () => {
    throw new Error('SecurityError');
  };
  return { getItem: fail, setItem: fail, removeItem: fail };
}

function page() {
  document.body.innerHTML = `
    <form id="other"><button id="other-button">Go</button></form>
    <form data-mbl-theme-switch>
      <button name="theme" value="dark" role="switch" aria-checked="false">
        Dark mode
      </button>
    </form>`;
  return document.querySelector('[data-mbl-theme-switch] button');
}

// Like htmx, which handles the submit on the form itself.
function submit(form) {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    e.stopImmediatePropagation();
  });
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

describe('theme switch focus', () => {
  test('submitting the switch marks it, and the next page focuses it', () => {
    const storage = fakeStorage();
    page();
    watch(document, storage);
    submit(document.querySelector('[data-mbl-theme-switch]'));
    expect(Number(storage.getItem(KEY))).toBeGreaterThan(0);

    const button = page(); // the reloaded page
    expect(restoreFocus(document, storage)).toBe(true);
    expect(document.activeElement).toBe(button);
    expect(storage.getItem(KEY)).toBeNull();
  });

  test('other forms are ignored', () => {
    const storage = fakeStorage();
    page();
    watch(document, storage);
    submit(document.getElementById('other'));
    expect(storage.getItem(KEY)).toBeNull();
    expect(restoreFocus(document, storage)).toBe(false);
    expect(document.activeElement).toBe(document.body);
  });

  test('only the next page, once', () => {
    const storage = fakeStorage();
    storage.setItem(KEY, String(Date.now()));
    page();
    expect(restoreFocus(document, storage)).toBe(true);
    document.activeElement.blur();
    expect(restoreFocus(document, storage)).toBe(false);
    expect(document.activeElement).toBe(document.body);
  });

  test('a page without the switch clears the mark', () => {
    const storage = fakeStorage();
    storage.setItem(KEY, String(Date.now()));
    document.body.innerHTML = '<p>No footer</p>';
    expect(restoreFocus(document, storage)).toBe(false);
    expect(storage.getItem(KEY)).toBeNull();
  });

  test('an old mark (a submit that never loaded) is ignored', () => {
    const storage = fakeStorage();
    storage.setItem(KEY, '1000');
    page();
    expect(restoreFocus(document, storage, () => 1000 + MAX_AGE_MS)).toBe(
      false
    );
    expect(document.activeElement).toBe(document.body);
    expect(storage.getItem(KEY)).toBeNull();
  });

  test('without storage, nothing breaks', () => {
    const storage = brokenStorage();
    page();
    watch(document, storage);
    expect(() =>
      submit(document.querySelector('[data-mbl-theme-switch]'))
    ).not.toThrow();
    expect(restoreFocus(document, storage)).toBe(false);
  });
});
