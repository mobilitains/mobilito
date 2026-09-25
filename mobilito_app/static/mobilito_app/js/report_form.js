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
 * Infrastructure report form (design §9.2, §18; roadmap Phase 6).
 *
 * - Photos: each choice adds to the selection (phones often pick one
 *   photo at a time), with previews and a remove button, checked for
 *   count and size before anything is uploaded. The chosen files
 *   are kept in the real file input (via DataTransfer), so the form
 *   still submits normally.
 * - Draft: perspective, description and ticked tags are kept in
 *   sessionStorage, so an accidental reload or back-and-forth
 *   doesn't lose them. Photos can't be kept that way. The draft is
 *   cleared by the page shown once the report is saved
 *   ([data-report-sent]), not at submit: the upload can fail.
 *
 * Dependencies are passed in (env) so Jest can test it.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.MobilitoReport = api;
    document.addEventListener('DOMContentLoaded', function () {
      let storage = null;
      try {
        storage = root.sessionStorage;
      } catch (err) {
        storage = null;
      }
      if (document.querySelector('[data-report-sent]')) {
        api.clearDraft(storage);
      }
      const form = document.querySelector('[data-report-form]');
      if (form) {
        api.initReportForm(form, {
          storage: storage,
          makeDataTransfer: function () {
            return new root.DataTransfer();
          },
          previewUrl: function (file) {
            return root.URL.createObjectURL(file);
          },
          revokeUrl: function (url) {
            root.URL.revokeObjectURL(url);
          },
        });
      }
    });
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DRAFT_KEY = 'mobilito-report-draft';
  const STALLED_MS = 90000;

  function clearDraft(storage) {
    try {
      if (storage) {
        storage.removeItem(DRAFT_KEY);
      }
    } catch (err) {
      // Nothing to do.
    }
  }

  function sameFile(a, b) {
    // Each pick makes new File objects: compare what identifies one.
    return (
      a.name === b.name &&
      a.size === b.size &&
      a.lastModified === b.lastModified
    );
  }

  function initReportForm(form, env) {
    const doc = form.ownerDocument;
    const input = form.querySelector('[data-photo-input]');
    const previews = form.querySelector('[data-photo-previews]');
    const status = form.querySelector('[data-photo-status]');
    const maxPhotos = parseInt(form.getAttribute('data-max-photos'), 10);
    const maxBytes = parseInt(form.getAttribute('data-max-photo-bytes'), 10);
    let files = [];
    let urls = [];
    // Without DataTransfer the input can't be edited: it holds the
    // latest choice only, and photos can't be removed one by one.
    let canEdit = true;

    function msg(key, n) {
      const text = form.getAttribute('data-msg-' + key) || '';
      return text.replace('%(n)s', String(n));
    }

    function need(section, text) {
      const box = form.querySelector('[data-need="' + section + '"]');
      if (box) {
        box.textContent = text || '';
      }
    }

    function showStatus(skipped) {
      // A neutral count, plus why any files were left out.
      const parts = [];
      if (files.length) {
        parts.push(msg('count', files.length));
      }
      skipped.forEach(function (key) {
        parts.push(msg(key, maxPhotos));
      });
      status.textContent = parts.join(' ');
    }

    function syncInput() {
      // Put the whole selection back in the input, so it submits.
      try {
        const transfer = env.makeDataTransfer();
        files.forEach(function (file) {
          transfer.items.add(file);
        });
        input.files = transfer.files;
      } catch (err) {
        // No DataTransfer: the input keeps the latest choice as is,
        // which is what the previews then show. The server checks
        // count and size again.
        canEdit = false;
        files = Array.prototype.slice.call(input.files || []);
      }
    }

    function render() {
      urls.forEach(env.revokeUrl);
      urls = [];
      previews.innerHTML = '';
      files.forEach(function (file, index) {
        const url = env.previewUrl(file);
        urls.push(url);
        const item = doc.createElement('div');
        item.className = 'mobilito-photo-preview';
        const image = doc.createElement('img');
        image.src = url;
        image.alt = msg('photo-alt', index + 1);
        const remove = doc.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-light';
        remove.setAttribute('aria-label', msg('remove', index + 1));
        remove.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
        remove.addEventListener('click', function () {
          files.splice(index, 1);
          syncInput();
          render();
          showStatus([]);
          // Keep keyboard focus nearby: the next photo's remove
          // button, else the previous one, else the file input
          // (visually hidden, its label shows the focus).
          const buttons = previews.querySelectorAll('button');
          const next = buttons[Math.min(index, buttons.length - 1)];
          (next || input).focus();
        });
        item.appendChild(image);
        if (canEdit) {
          item.appendChild(remove);
        }
        previews.appendChild(item);
      });
    }

    input.addEventListener('change', function () {
      const skipped = [];
      Array.prototype.forEach.call(input.files || [], function (file) {
        const known = files.some(function (other) {
          return sameFile(other, file);
        });
        if (known) {
          return;
        }
        let reason = null;
        if (file.size > maxBytes) {
          reason = 'too-big';
        } else if (files.length >= maxPhotos) {
          reason = 'too-many';
        } else {
          files.push(file);
        }
        if (reason && skipped.indexOf(reason) === -1) {
          skipped.push(reason);
        }
      });
      syncInput();
      render();
      // Without DataTransfer the input kept the whole latest pick,
      // skipped files included: don't claim otherwise (the server
      // checks again).
      showStatus(canEdit ? skipped : []);
      if (files.length) {
        need('photos', '');
      }
    });

    // Draft.
    function field(name) {
      return form.querySelectorAll('[name="' + name + '"]');
    }

    function readDraft() {
      try {
        const saved = env.storage && env.storage.getItem(DRAFT_KEY);
        return saved ? JSON.parse(saved) : null;
      } catch (err) {
        return null;
      }
    }

    function saveDraft() {
      const perspective = form.querySelector('[name="perspective"]:checked');
      const description = form.querySelector('[name="description"]');
      const draft = {
        perspective: perspective ? perspective.value : '',
        description: description ? description.value : '',
        tags: Array.prototype.map.call(
          form.querySelectorAll('[name="tags"]:checked'),
          function (box) {
            return box.value;
          }
        ),
      };
      try {
        if (env.storage) {
          env.storage.setItem(DRAFT_KEY, JSON.stringify(draft));
        }
      } catch (err) {
        // Full or blocked: the form still works.
      }
    }

    function applyTags(draft) {
      if (!draft || !draft.tags) {
        return;
      }
      field('tags').forEach(function (box) {
        if (draft.tags.indexOf(box.value) !== -1) {
          box.checked = true;
        }
      });
    }

    // The server sent the form back: take screen readers to why.
    const serverErrors = form.querySelector('[data-report-errors]');
    if (serverErrors) {
      serverErrors.focus();
    }

    const draft = readDraft();
    if (draft) {
      // Don't overwrite what the server just sent back (an error
      // re-render carries the submitted values).
      const description = form.querySelector('[name="description"]');
      if (description && !description.value && draft.description) {
        description.value = draft.description;
      }
      if (!form.querySelector('[name="perspective"]:checked')) {
        field('perspective').forEach(function (radio) {
          radio.checked = radio.value === draft.perspective;
        });
      }
      applyTags(draft);
    }
    form.addEventListener('input', saveDraft);
    form.addEventListener('change', saveDraft);
    // Tags arrive later, swapped in when the location is confirmed.
    doc.addEventListener('htmx:oobAfterSwap', function () {
      applyTags(readDraft());
    });

    form.addEventListener('change', function (event) {
      if (event.target.name === 'perspective') {
        need('perspective', '');
      }
    });
    doc.addEventListener('htmx:afterSwap', function () {
      if (form.querySelector('[name="lat"]')) {
        need('location', '');
      }
    });

    function missing() {
      // Checked here so nobody waits for an upload to learn this;
      // the server checks again.
      const gaps = [];
      if (!form.querySelector('[name="lat"]')) {
        gaps.push(['location', 'need-location']);
      }
      if (!form.querySelector('[name="perspective"]:checked')) {
        gaps.push(['perspective', 'need-perspective']);
      }
      if (!files.length && !(input.files && input.files.length)) {
        gaps.push(['photos', 'need-photo']);
      }
      return gaps;
    }

    const button = form.querySelector('[data-report-submit]');
    const sending = form.querySelector('[data-sending]');
    let stalled = null;

    form.addEventListener('submit', function (event) {
      ['location', 'perspective', 'photos'].forEach(function (section) {
        need(section, '');
      });
      const gaps = missing();
      if (gaps.length) {
        event.preventDefault();
        gaps.forEach(function (gap) {
          need(gap[0], msg(gap[1]));
        });
        const first = form.querySelector('[data-need="' + gaps[0][0] + '"]');
        if (first.scrollIntoView) {
          first.scrollIntoView({ block: 'center' });
        }
        const control = {
          location: '[data-map-confirm]',
          perspective: '[name="perspective"]',
          photos: '[data-photo-input]',
        }[gaps[0][0]];
        const target = form.querySelector(control);
        if (target) {
          target.focus({ preventScroll: true });
        }
        return;
      }
      if (button) {
        button.disabled = true; // no double submission
      }
      if (sending) {
        sending.textContent = msg('sending');
        // No progress events for a plain form post: after a while,
        // say what to do if the connection dropped. The button stays
        // disabled, so the report can't be sent twice.
        stalled = setTimeout(function () {
          sending.textContent = msg('sending') + ' ' + msg('stalled');
        }, STALLED_MS);
      }
    });

    // Coming back with the back button restores the page as it was:
    // make the button usable again. Sending the same form twice is
    // safe: the server recognises it (submission_id) and shows the
    // report already made.
    if (doc.defaultView) {
      doc.defaultView.addEventListener('pageshow', function () {
        if (button) {
          button.disabled = false;
        }
        clearTimeout(stalled);
        if (sending) {
          sending.textContent = '';
        }
      });
    }

    return {
      files: function () {
        return files;
      },
    };
  }

  return {
    initReportForm: initReportForm,
    clearDraft: clearDraft,
    DRAFT_KEY: DRAFT_KEY,
  };
});
