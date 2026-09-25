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

const { initReportForm, clearDraft, DRAFT_KEY } = require(
  '../../../../mobilito_app/static/mobilito_app/js/report_form.js'
);

function build({
  description = '',
  perspective = '',
  confirmed = true,
  errors = false,
} = {}) {
  document.body.innerHTML = `
    <form data-report-form data-max-photos="2" data-max-photo-bytes="1000"
          data-msg-too-many="TOO MANY" data-msg-too-big="TOO BIG"
          data-msg-count="COUNT %(n)s" data-msg-photo-alt="PHOTO %(n)s"
          data-msg-remove="REMOVE %(n)s" data-msg-sending="SENDING"
          data-msg-need-location="NEED LOCATION"
          data-msg-need-perspective="NEED PERSPECTIVE"
          data-msg-need-photo="NEED PHOTO" data-msg-stalled="STALLED">
      ${errors ? '<div data-report-errors tabindex="-1">ERR</div>' : ''}
      <button type="button" data-map-confirm>Confirm</button>
      <div data-map-confirmed>
        ${confirmed ? '<input type="hidden" name="lat" value="1">' : ''}
      </div>
      <p data-need="location"></p>
      <input type="file" id="photos" multiple data-photo-input>
      <label for="photos">Add</label>
      <div data-photo-previews></div>
      <p data-photo-status></p>
      <p data-need="photos"></p>
      <p data-need="perspective"></p>
      ${['ped', 'bike', 'both']
        .map(
          (v) =>
            `<input type="radio" name="perspective" value="${v}"
               ${v === perspective ? 'checked' : ''}>`
        )
        .join('')}
      <textarea name="description">${description}</textarea>
      <div id="report-tags"></div>
      <button type="submit" data-report-submit></button>
      <p data-sending></p>
    </form>`;
  return document.querySelector('[data-report-form]');
}

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
    removeItem: (k) => {
      delete data[k];
    },
  };
}

// jsdom has no DataTransfer: a minimal stand-in, and an input whose
// files can be set.
function makeEnv(storage = memoryStorage()) {
  return {
    storage,
    makeDataTransfer: () => {
      const list = [];
      return { items: { add: (f) => list.push(f) }, files: list };
    },
    previewUrl: (file) => `blob:${file.name}`,
    revokeUrl: jest.fn(),
  };
}

function choose(form, ...files) {
  const input = form.querySelector('[data-photo-input]');
  Object.defineProperty(input, 'files', {
    value: files,
    writable: true,
    configurable: true,
  });
  input.dispatchEvent(new Event('change'));
}

const photo = (name, size = 100) => ({ name, size, lastModified: 1 });

function submit(form) {
  const event = new Event('submit', { cancelable: true });
  form.dispatchEvent(event);
  return event;
}

const text = (form, selector) => form.querySelector(selector).textContent;

describe('photos', () => {
  test('choices add up, with previews', () => {
    const form = build();
    const report = initReportForm(form, makeEnv());
    choose(form, photo('a.jpg'));
    choose(form, photo('b.jpg'));
    expect(report.files().map((f) => f.name)).toEqual(['a.jpg', 'b.jpg']);
    expect(form.querySelectorAll('.mobilito-photo-preview img')).toHaveLength(
      2
    );
    expect(form.querySelector('[data-photo-input]').files).toHaveLength(2);
    expect(text(form, '[data-photo-status]')).toBe('COUNT 2');
    const alts = [...form.querySelectorAll('.mobilito-photo-preview img')].map(
      (img) => img.alt
    );
    expect(alts).toEqual(['PHOTO 1', 'PHOTO 2']);
  });

  test('the same photo picked again is added once', () => {
    const form = build();
    const report = initReportForm(form, makeEnv());
    choose(form, photo('a.jpg'));
    choose(form, photo('a.jpg'));
    expect(report.files()).toHaveLength(1);
  });

  test('without DataTransfer, photos show but cannot be removed', () => {
    const form = build();
    const env = makeEnv();
    env.makeDataTransfer = () => {
      throw new Error('unsupported');
    };
    const report = initReportForm(form, env);
    choose(form, photo('a.jpg'));
    expect(report.files()).toHaveLength(1);
    expect(form.querySelector('.mobilito-photo-preview button')).toBeNull();
  });

  test('a photo can be removed', () => {
    const form = build();
    const report = initReportForm(form, makeEnv());
    choose(form, photo('a.jpg'), photo('b.jpg'));
    form.querySelector('.mobilito-photo-preview button').click();
    expect(report.files().map((f) => f.name)).toEqual(['b.jpg']);
    const left = form.querySelector('.mobilito-photo-preview button');
    expect(left.getAttribute('aria-label')).toBe('REMOVE 1');
    // Focus stays nearby, then falls back to the add button.
    expect(document.activeElement).toBe(left);
    left.click();
    expect(document.activeElement).toBe(
      form.querySelector('[data-photo-input]')
    );
    expect(text(form, '[data-photo-status]')).toBe('');
  });

  test('too many or too large photos are refused before upload', () => {
    const form = build();
    const report = initReportForm(form, makeEnv());
    choose(form, photo('big.jpg', 5000));
    expect(text(form, '[data-photo-status]')).toBe('TOO BIG');
    choose(form, photo('a.jpg'), photo('b.jpg'), photo('c.jpg'));
    expect(report.files()).toHaveLength(2);
    expect(text(form, '[data-photo-status]')).toBe('COUNT 2 TOO MANY');
  });
});

describe('submit', () => {
  test('says what is missing instead of uploading', () => {
    const form = build({ confirmed: false });
    initReportForm(form, makeEnv());
    const event = submit(form);
    expect(event.defaultPrevented).toBe(true);
    expect(text(form, '[data-need="location"]')).toBe('NEED LOCATION');
    expect(text(form, '[data-need="perspective"]')).toBe('NEED PERSPECTIVE');
    expect(text(form, '[data-need="photos"]')).toBe('NEED PHOTO');
    expect(form.querySelector('[data-report-submit]').disabled).toBe(false);
    // Keyboard users land on the first thing to fix.
    expect(document.activeElement).toBe(
      form.querySelector('[data-map-confirm]')
    );
  });

  test('focus goes to the first missing choice', () => {
    const form = build();
    initReportForm(form, makeEnv());
    submit(form);
    expect(document.activeElement).toBe(
      form.querySelector('[name="perspective"]')
    );
  });

  test("the server's error summary gets focus", () => {
    const form = build({ errors: true });
    initReportForm(form, makeEnv());
    expect(document.activeElement).toBe(
      form.querySelector('[data-report-errors]')
    );
  });

  test('a long upload says what to do if it stalled', () => {
    jest.useFakeTimers();
    try {
      const form = build({ perspective: 'bike' });
      initReportForm(form, makeEnv());
      choose(form, photo('a.jpg'));
      submit(form);
      jest.advanceTimersByTime(90000);
      expect(text(form, '[data-sending]')).toBe('SENDING STALLED');
      expect(form.querySelector('[data-report-submit]').disabled).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  test('messages clear as things are fixed', () => {
    const form = build();
    initReportForm(form, makeEnv());
    submit(form);
    choose(form, photo('a.jpg'));
    expect(text(form, '[data-need="photos"]')).toBe('');
    const radio = form.querySelector('[value="ped"]');
    radio.checked = true;
    radio.dispatchEvent(new Event('change', { bubbles: true }));
    expect(text(form, '[data-need="perspective"]')).toBe('');
  });

  test('a complete form is sent once, with a progress note', () => {
    const form = build({ perspective: 'bike' });
    initReportForm(form, makeEnv());
    choose(form, photo('a.jpg'));
    expect(submit(form).defaultPrevented).toBe(false);
    expect(form.querySelector('[data-report-submit]').disabled).toBe(true);
    expect(text(form, '[data-sending]')).toBe('SENDING');
    // Back to the page (e.g. the upload failed): usable again.
    window.dispatchEvent(new Event('pageshow'));
    expect(form.querySelector('[data-report-submit]').disabled).toBe(false);
    expect(text(form, '[data-sending]')).toBe('');
  });
});

describe('draft', () => {
  test('is saved as the user types and chooses', () => {
    const storage = memoryStorage();
    const form = build();
    initReportForm(form, makeEnv(storage));
    form.querySelector('[value="bike"]').checked = true;
    form.querySelector('textarea').value = 'Broken kerb';
    form.dispatchEvent(new Event('input'));
    expect(JSON.parse(storage.data[DRAFT_KEY])).toEqual({
      perspective: 'bike',
      description: 'Broken kerb',
      tags: [],
    });
  });

  test('is restored after a reload', () => {
    const storage = memoryStorage({
      [DRAFT_KEY]: JSON.stringify({
        perspective: 'ped',
        description: 'Pavement blocked',
        tags: ['3'],
      }),
    });
    const form = build();
    initReportForm(form, makeEnv(storage));
    expect(form.querySelector('textarea').value).toBe('Pavement blocked');
    expect(form.querySelector('[value="ped"]').checked).toBe(true);
  });

  test("doesn't overwrite what the server sent back", () => {
    const storage = memoryStorage({
      [DRAFT_KEY]: JSON.stringify({
        perspective: 'ped',
        description: 'old',
        tags: [],
      }),
    });
    const form = build({ description: 'new', perspective: 'both' });
    initReportForm(form, makeEnv(storage));
    expect(form.querySelector('textarea').value).toBe('new');
    expect(form.querySelector('[value="both"]').checked).toBe(true);
  });

  test('tags swapped in later are re-ticked', () => {
    const storage = memoryStorage({
      [DRAFT_KEY]: JSON.stringify({ tags: ['3'] }),
    });
    const form = build();
    initReportForm(form, makeEnv(storage));
    document.getElementById('report-tags').innerHTML =
      '<input type="checkbox" name="tags" value="3">' +
      '<input type="checkbox" name="tags" value="4">';
    document.dispatchEvent(new Event('htmx:oobAfterSwap'));
    const boxes = document.querySelectorAll('[name="tags"]');
    expect(boxes[0].checked).toBe(true);
    expect(boxes[1].checked).toBe(false);
  });

  test('is kept at submit (the upload can fail)', () => {
    const storage = memoryStorage({ [DRAFT_KEY]: '{}' });
    const form = build({ perspective: 'bike' });
    initReportForm(form, makeEnv(storage));
    choose(form, photo('a.jpg'));
    submit(form);
    expect(storage.data[DRAFT_KEY]).toBeDefined();
  });

  test('is cleared once the report is saved', () => {
    const storage = memoryStorage({ [DRAFT_KEY]: '{}' });
    clearDraft(storage);
    expect(storage.data[DRAFT_KEY]).toBeUndefined();
    clearDraft(null); // no storage: no error
  });
});
