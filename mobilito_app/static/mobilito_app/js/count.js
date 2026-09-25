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
 * Modal share counting screen (design §9.1, §18; roadmap Phase 5).
 *
 * Every tap updates the count at once and is queued in localStorage
 * before it is sent, one request per tap (each with its own id, so a
 * retry is stored once). Taps made without a connection wait in the
 * queue and go when it comes back; the queue also survives the page
 * being closed or reloaded. Finish sends the phone's own totals once
 * every tap is through, and waits for a connection if need be.
 *
 * A tap leaves the queue only when the server has stored it (204) or
 * refused it for good (400, 404, 409). Anything else, including a
 * 403 (e.g. a CSRF token rotated by confirming the email address on
 * this phone) or a redirect to a sign-in page, keeps it queued.
 *
 * Dependencies are passed in (env) so Jest can test it.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.MobilitoCount = api;
    document.addEventListener('DOMContentLoaded', function () {
      const element = document.querySelector('[data-count]');
      if (element) {
        api.initCounter(element, api.browserEnv(root));
      }
    });
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const MODES = ['ped', 'bike', 'car', 'tc'];
  const RETRY_MS = 10000;
  // Only mention sending when a backlog builds up; one tap in flight
  // is normal and shouldn't flicker a message.
  const BACKLOG_NOTICE = 3;
  // Refused for good: retrying can't help, so don't keep the tap.
  const DROP_STATUSES = [400, 404, 409];
  const GPS_OPTIONS = { enableHighAccuracy: false, maximumAge: 30000 };
  // A request hanging on a dead mobile connection would otherwise
  // hold up the queue until the browser gives up, which can take
  // minutes. Timing out reads as "offline"; retries are safe.
  const REQUEST_TIMEOUT_MS = 15000;

  function cookie(doc, name) {
    const match = (doc.cookie || '').match(
      new RegExp('(?:^|; )' + name + '=([^;]*)')
    );
    return match ? decodeURIComponent(match[1]) : null;
  }

  function browserEnv(root) {
    let storage = null;
    try {
      storage = root.localStorage;
    } catch (err) {
      storage = null; // private mode etc.: keep going in memory
    }
    const nav = root.navigator || {};
    return {
      fetch: root.fetch.bind(root),
      storage: storage,
      now: function () {
        return Date.now();
      },
      uuid: function () {
        return root.crypto.randomUUID();
      },
      navigate: function (url) {
        root.location.assign(url);
      },
      addOnline: function (fn) {
        root.addEventListener('online', fn);
      },
      addBeforeUnload: function (fn) {
        root.addEventListener('beforeunload', fn);
      },
      setInterval: root.setInterval.bind(root),
      wakeLock: nav.wakeLock || null,
      geolocation: nav.geolocation || null,
    };
  }

  function formatElapsed(ms) {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    return minutes + ':' + (rest < 10 ? '0' : '') + rest;
  }

  function initCounter(element, env) {
    const doc = element.ownerDocument;
    const config = JSON.parse(doc.getElementById('count-config').textContent);
    const key = 'mobilito-count-' + config.sessionId;
    const status = element.querySelector('[data-status]');
    const timer = element.querySelector('[data-timer]');
    const shortPanel = element.querySelector('[data-short]');
    const shortDialog = element.querySelector('[data-short-dialog]');
    const buttons = element.querySelectorAll('[data-mode]');
    const finishButton = element.querySelector('[data-finish]');
    // The phone's clock may be off: measure time on the server's.
    const clockOffset =
      typeof config.serverNow === 'number' ? config.serverNow - env.now() : 0;

    function serverNow() {
      return env.now() + clockOffset;
    }

    function load() {
      try {
        const saved = env.storage && env.storage.getItem(key);
        if (saved) {
          return JSON.parse(saved);
        }
      } catch (err) {
        // Unreadable: start from what the server has.
      }
      return null;
    }

    const state = load() || {
      totals: Object.assign(
        { ped: 0, bike: 0, car: 0, tc: 0 },
        config.totals || {}
      ),
      pending: [],
      finishing: null,
    };

    function save() {
      try {
        if (env.storage) {
          env.storage.setItem(key, JSON.stringify(state));
        }
      } catch (err) {
        // Storage full or blocked: taps still go out while online.
      }
    }

    function forget() {
      try {
        if (env.storage) {
          env.storage.removeItem(key);
        }
      } catch (err) {
        // Nothing to do.
      }
    }

    function say(message) {
      status.textContent = message
        ? element.getAttribute('data-msg-' + message)
        : '';
    }

    function render() {
      MODES.forEach(function (mode) {
        const number = element.querySelector('[data-total="' + mode + '"]');
        if (number) {
          number.textContent = String(state.totals[mode] || 0);
        }
      });
    }

    function tick() {
      if (timer) {
        timer.textContent = formatElapsed(serverNow() - config.startedAt);
      }
    }

    function setEnabled(enabled) {
      buttons.forEach(function (button) {
        button.disabled = !enabled;
      });
      finishButton.disabled = !enabled;
    }

    function post(url, body) {
      let signal;
      if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
        signal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
      } else if (typeof AbortController !== 'undefined') {
        // Older browsers (and jsdom) lack AbortSignal.timeout.
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
        // A redirect means "not stored" (e.g. to a sign-in page);
        // don't let fetch follow it to a 200.
        redirect: 'manual',
        headers: {
          'Content-Type': 'application/json',
          // Read at send time: signing in on this phone rotates it.
          'X-CSRFToken': cookie(doc, 'csrftoken') || config.csrfToken,
        },
        body: JSON.stringify(body),
      });
    }

    // Last known position, attached to taps when location use is on
    // (§20.4). Never asks for permission itself beyond what the start
    // page already did; any failure just means taps without one.
    let position = null;
    let watchId = null;
    if (config.useDeviceLocation && env.geolocation) {
      try {
        watchId = env.geolocation.watchPosition(
          function (fix) {
            position = {
              lat: fix.coords.latitude,
              lon: fix.coords.longitude,
            };
          },
          function () {
            position = null;
          },
          GPS_OPTIONS
        );
      } catch (err) {
        position = null;
      }
    }

    let flushing = false;
    let finishInFlight = false;
    let lastFlush = 0;

    function flush() {
      if (flushing) {
        return Promise.resolve();
      }
      flushing = true;
      lastFlush = env.now();
      function stop(message) {
        flushing = false;
        say(state.finishing ? 'finishing' : message);
        return undefined;
      }
      function next() {
        if (!state.pending.length) {
          flushing = false;
          if (state.finishing) {
            return sendFinish();
          }
          say(null);
          return Promise.resolve();
        }
        if (state.pending.length >= BACKLOG_NOTICE && !state.finishing) {
          say('syncing');
        }
        return post(config.eventUrl, state.pending[0]).then(
          function (response) {
            if (response.status === 409 || response.status === 404) {
              // Closed elsewhere (finished or discarded in another
              // tab or device): show what there is, rather than keep
              // counting into nothing.
              leave(
                response.status === 409 ? config.resultsUrl : config.homeUrl
              );
              return undefined;
            }
            if (
              response.status === 204 ||
              DROP_STATUSES.indexOf(response.status) !== -1
            ) {
              state.pending.shift();
              save();
              return next();
            }
            // Anything else (403, redirect, 429, 5xx): keep the tap
            // and try again later.
            return stop('error');
          },
          function () {
            return stop('offline');
          }
        );
      }
      return next();
    }

    function tap(mode) {
      if (state.finishing) {
        return;
      }
      state.totals[mode] = (state.totals[mode] || 0) + 1;
      const event = {
        event_id: env.uuid(),
        mode: mode,
        client_timestamp: serverNow(),
      };
      if (position) {
        event.lat = position.lat;
        event.lon = position.lon;
      }
      state.pending.push(event);
      save();
      render();
      flush();
    }

    let leaving = false;

    function leave(url) {
      // Nothing left unsent: don't let beforeunload ask "leave?".
      leaving = true;
      forget();
      env.navigate(url);
    }

    function done(response) {
      if (response.status === 400 || response.status === 404) {
        // Refused for good: the count is gone (discarded elsewhere,
        // or deleted unconfirmed) or what's stored here is unusable.
        // Retrying forever would trap the page.
        leave(config.homeUrl);
        return undefined;
      }
      if (response.status !== 200) {
        say('error');
        return undefined;
      }
      return response.json().then(
        function (data) {
          leave(data.redirect);
        },
        function () {
          say('error');
        }
      );
    }

    function sendFinish() {
      if (finishInFlight) {
        return Promise.resolve();
      }
      finishInFlight = true;
      say('sending');
      return post(config.finishUrl, state.finishing)
        .then(done, function () {
          say('finishing');
        })
        .then(function () {
          finishInFlight = false;
        });
    }

    function hideShort() {
      shortPanel.hidden = true;
      finishButton.focus();
    }

    function finish() {
      if (watchId !== null && env.geolocation.clearWatch) {
        // No more taps: save the battery while the finish goes out.
        env.geolocation.clearWatch(watchId);
      }
      state.finishing = {
        totals: Object.assign({}, state.totals),
        finished_at: serverNow(),
      };
      save();
      setEnabled(false);
      shortPanel.hidden = true;
      say('sending');
      return flush();
    }

    const shortStatus = element.querySelector('[data-short-status]');
    const shortButtons = shortPanel.querySelectorAll('button');

    function sayInPanel(message) {
      // Next to the buttons: the main status line is behind the panel.
      const target = shortStatus || status;
      target.textContent = message
        ? element.getAttribute('data-msg-' + message)
        : '';
    }

    function discard() {
      shortButtons.forEach(function (button) {
        button.disabled = true;
      });
      sayInPanel(null);
      function failed(message) {
        shortButtons.forEach(function (button) {
          button.disabled = false;
        });
        sayInPanel(message);
      }
      return post(config.discardUrl, {}).then(
        function (response) {
          if (response.status === 409) {
            leave(config.resultsUrl); // already finished elsewhere
            return undefined;
          }
          if (response.status === 404) {
            leave(config.homeUrl); // already gone
            return undefined;
          }
          if (response.status !== 200) {
            failed('discardfailed');
            return undefined;
          }
          return response.json().then(
            function (data) {
              leave(data.redirect);
            },
            function () {
              failed('discardfailed');
            }
          );
        },
        function () {
          failed('nodiscard');
        }
      );
    }

    buttons.forEach(function (button) {
      button.addEventListener('click', function () {
        tap(button.getAttribute('data-mode'));
        // Visible feedback even for taps too quick for :active.
        button.classList.add('is-tapped');
        setTimeout(function () {
          button.classList.remove('is-tapped');
        }, 120);
      });
    });
    finishButton.addEventListener('click', function () {
      const elapsed = (serverNow() - config.startedAt) / 1000;
      if (elapsed < config.minSeconds) {
        shortPanel.hidden = false;
        if (shortDialog) {
          shortDialog.focus();
        }
        return;
      }
      finish();
    });
    element.querySelector('[data-keep]').addEventListener('click', finish);
    element.querySelector('[data-discard]').addEventListener('click', discard);
    element
      .querySelector('[data-resume]')
      .addEventListener('click', hideShort);
    shortPanel.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        hideShort();
      }
    });

    // "What counts as what?" overlay.
    const guide = element.querySelector('[data-guide]');
    const guideOpen = element.querySelector('[data-guide-open]');
    if (guide && guideOpen) {
      const closeGuide = function () {
        guide.hidden = true;
        guideOpen.focus();
      };
      guideOpen.addEventListener('click', function () {
        guide.hidden = false;
        element.querySelector('[data-guide-dialog]').focus();
      });
      element
        .querySelector('[data-guide-close]')
        .addEventListener('click', closeGuide);
      guide.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
          closeGuide();
        }
      });
      guide.addEventListener('click', function (event) {
        if (event.target === guide) {
          closeGuide(); // a tap on the backdrop, outside the card
        }
      });
    }

    // Keep the screen on: quiet streets can go longer between taps
    // than a phone's screen timeout. Best effort.
    function keepAwake() {
      if (env.wakeLock && doc.visibilityState !== 'hidden') {
        env.wakeLock.request('screen').catch(function () {});
      }
    }
    keepAwake();
    doc.addEventListener('visibilitychange', keepAwake);

    if (env.addBeforeUnload) {
      env.addBeforeUnload(function (event) {
        if (!leaving && (state.pending.length || state.finishing)) {
          // Browsers show their own "leave this page?" text.
          event.preventDefault();
          event.returnValue = '';
        }
      });
    }

    env.addOnline(flush);
    env.setInterval(function () {
      tick();
      if (
        (state.pending.length || state.finishing) &&
        env.now() - lastFlush >= RETRY_MS
      ) {
        flush();
      }
    }, 1000);

    render();
    tick();
    if (state.finishing) {
      // Reopened while a finish was waiting for a connection.
      setEnabled(false);
    }
    const ready = flush();
    return {
      state: state,
      tap: tap,
      finish: finish,
      discard: discard,
      flush: flush,
      ready: ready,
    };
  }

  return {
    initCounter: initCounter,
    browserEnv: browserEnv,
    formatElapsed: formatElapsed,
    MODES: MODES,
    RETRY_MS: RETRY_MS,
  };
});
