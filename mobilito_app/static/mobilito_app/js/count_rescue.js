/*
Copyright 2024  Francais pour une Meilleure Mobilité

Author(s): Jeff Abrahamson <jeff@p27.eu>, based in part on the Mobilito
proof of concept in transport-nantes/tn_web.

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
 * Rescue for a count left open too long (counts/stale.html).
 *
 * The counting page (count.js) queues taps, and the finish, in
 * localStorage until they reach the server. If the phone only found
 * a signal once the count had gone stale, or the tab was closed
 * meanwhile, that queue is still here: send it (the server keeps
 * taps made in time, whenever they arrive), then reload, so "keep
 * what was counted" sees everything. The page's buttons wait for it.
 *
 * Same storage format and requests as count.js. Dependencies are
 * passed in (env) so Jest can test it.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.MobilitoCountRescue = api;
    document.addEventListener('DOMContentLoaded', function () {
      const element = document.querySelector('[data-count-rescue]');
      if (element) {
        let storage = null;
        try {
          storage = root.localStorage;
        } catch (err) {
          storage = null;
        }
        api.initRescue(element, {
          fetch: root.fetch.bind(root),
          storage: storage,
          reload: function () {
            root.location.reload();
          },
          navigate: function (url) {
            root.location.assign(url);
          },
        });
      }
    });
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Stored, or refused for good (as in count.js): drop and go on.
  const DROP_STATUSES = [204, 400, 404, 409];
  // As in count.js: a hung request on a dead connection reads as
  // offline, rather than keeping the buttons disabled.
  const REQUEST_TIMEOUT_MS = 15000;

  function cookie(doc, name) {
    const match = (doc.cookie || '').match(
      new RegExp('(?:^|; )' + name + '=([^;]*)')
    );
    return match ? decodeURIComponent(match[1]) : null;
  }

  function initRescue(element, env) {
    const doc = element.ownerDocument;
    const config = JSON.parse(
      doc.getElementById('rescue-config').textContent
    );
    const key = 'mobilito-count-' + config.sessionId;
    const buttons = doc.querySelectorAll('[data-stale-actions] button');
    let state = null;
    try {
      const saved = env.storage && env.storage.getItem(key);
      state = saved ? JSON.parse(saved) : null;
    } catch (err) {
      state = null;
    }
    const pending = (state && state.pending) || [];
    if (!pending.length && !(state && state.finishing)) {
      return Promise.resolve(false); // nothing saved on this phone
    }

    function say(message) {
      element.hidden = false;
      element.textContent = element.getAttribute('data-msg-' + message);
    }

    function setEnabled(enabled) {
      buttons.forEach(function (button) {
        button.disabled = !enabled;
      });
    }

    function save() {
      try {
        env.storage.setItem(key, JSON.stringify(state));
      } catch (err) {
        // Nothing to do: what's sent is sent.
      }
    }

    function forget() {
      // True only if the queue is really gone: reloading with it
      // still there would send it, and reload, for ever.
      try {
        env.storage.removeItem(key);
        return env.storage.getItem(key) === null;
      } catch (err) {
        return false;
      }
    }

    function forgetAndReload() {
      if (forget()) {
        env.reload();
        return true;
      }
      say('error');
      setEnabled(true);
      return false;
    }

    function post(url, body) {
      let signal;
      if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
        signal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
      } else if (typeof AbortController !== 'undefined') {
        const controller = new AbortController();
        setTimeout(function () {
          controller.abort();
        }, REQUEST_TIMEOUT_MS);
        signal = controller.signal;
      }
      return env.fetch(url, {
        method: 'POST',
        signal: signal,
        credentials: 'same-origin',
        redirect: 'manual',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': cookie(doc, 'csrftoken') || config.csrfToken,
        },
        body: JSON.stringify(body),
      });
    }

    function failed(reason) {
      // Keep the queue for next time; the choice is theirs meanwhile.
      // A response is the server refusing (for now); no response
      // means no connection.
      say(reason === 'refused' ? 'error' : 'offline');
      setEnabled(true);
      return false;
    }

    function refused() {
      return failed('refused');
    }

    function sendTaps() {
      if (!pending.length) {
        return Promise.resolve(true);
      }
      return post(config.eventUrl, pending[0]).then(function (response) {
        if (DROP_STATUSES.indexOf(response.status) === -1) {
          return refused();
        }
        pending.shift();
        save();
        return sendTaps();
      }, failed);
    }

    function sendFinish() {
      return post(config.finishUrl, state.finishing).then(function (
        response
      ) {
        if (response.status === 200) {
          return response.json().then(function (data) {
            forget();
            env.navigate(data.redirect);
            return true;
          }, refused);
        }
        if (response.status === 400 || response.status === 404) {
          // Gone, or nothing to keep: nothing more to send.
          return forgetAndReload();
        }
        return refused();
      }, failed);
    }

    setEnabled(false);
    say('sending');
    return sendTaps().then(function (sent) {
      if (!sent) {
        return false;
      }
      if (state.finishing) {
        return sendFinish();
      }
      return forgetAndReload();
    });
  }

  return { initRescue: initRescue };
});
