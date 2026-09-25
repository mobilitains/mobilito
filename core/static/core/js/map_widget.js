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
 * Reusable map component (roadmap Phase 4, design §9.2, §11.2).
 *
 * Progressive enhancement of core/includes/map_widget.html: the
 * template holds real forms (Confirm location, the device-location
 * preference); this module adds the Leaflet map, keeps the hidden
 * lat/lon fields in step with the crosshair (= map centre), reads
 * GPS when allowed, and shows observation pins whose bottom-sheet
 * summaries are loaded with htmx.
 *
 * Dependencies are passed in (env) so Jest can test it without a
 * browser; in the page they come from the globals.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.MobilitoMap = api;
    document.addEventListener('DOMContentLoaded', function () {
      api.initAll(document, {
        L: root.L,
        htmx: root.htmx,
        bootstrap: root.bootstrap,
        geolocation: root.navigator && root.navigator.geolocation,
        fetch: root.fetch && root.fetch.bind(root),
      });
    });
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const GPS_OPTIONS = {
    enableHighAccuracy: true,
    timeout: 10000,
    maximumAge: 30000,
  };
  // Zoom to at least this when jumping to the device's position:
  // close enough to place the crosshair on one side of a street.
  const GPS_ZOOM = 17;
  const PIN_COLOURS = { count: '#0d6efd', report: '#fd7e14' };
  const PIN_RELOAD_DELAY_MS = 300;

  function readConfig(component, doc) {
    const id = component.getAttribute('data-mobilito-map') + '-config';
    return JSON.parse(doc.getElementById(id).textContent);
  }

  function pinStyle(feature) {
    const kind = feature.properties && feature.properties.kind;
    return {
      radius: 12,
      color: '#ffffff',
      weight: 3,
      fillColor: PIN_COLOURS[kind] || '#6c757d',
      fillOpacity: 0.9,
    };
  }

  function initMapWidget(component, env, doc) {
    doc = doc || component.ownerDocument;
    const L = env.L;
    const config = readConfig(component, doc);
    const mapEl = component.querySelector('.mobilito-map');
    const map = L.map(mapEl).setView(config.center, config.zoom);
    L.tileLayer(config.tileUrl, {
      maxZoom: 19,
      attribution: config.tileAttribution,
    }).addTo(map);

    const fallback = component.querySelector('[data-map-fallback]');
    if (fallback) {
      // Shown only if this script (or Leaflet) never runs.
      fallback.remove();
    }
    // Controls that only make sense with a working map start hidden.
    Array.prototype.forEach.call(
      component.querySelectorAll('[data-map-requires-js]'),
      function (el) {
        el.hidden = false;
      }
    );

    function field(name) {
      return component.querySelector('[data-map-field="' + name + '"]');
    }

    function setField(name, value) {
      const input = field(name);
      if (input) {
        input.value = value;
      }
    }

    function syncCentre() {
      const centre = map.getCenter().wrap();
      setField('lat', centre.lat.toFixed(6));
      setField('lon', centre.lng.toFixed(6));
    }

    // Moves the widget makes itself (GPS recentring); any other move
    // (drag, pinch, wheel, keyboard, zoom buttons) is the user's.
    // Without animation, Leaflet fires movestart synchronously inside
    // setView (an animated zoom defers it to the next frame).
    let programmatic = false;
    function moveTo(latlng, zoom) {
      programmatic = true;
      try {
        map.setView(latlng, zoom, { animate: false });
      } finally {
        programmatic = false;
      }
    }
    function isUserMove() {
      return !programmatic;
    }

    const widget = { map: map, syncCentre: syncCentre };

    if (config.crosshair) {
      initConfirm(component, map, doc, isUserMove);
      map.on('moveend', syncCentre);
      syncCentre();
    }
    if (config.gps) {
      Object.assign(
        widget,
        initGps(component, env, map, config, setField, moveTo, isUserMove)
      );
    }
    if (config.pinsUrl) {
      Object.assign(widget, initPins(component, env, map, config));
    }
    return widget;
  }

  function initConfirm(component, map, doc, isUserMove) {
    const confirmed = component.querySelector('[data-map-confirmed]');
    if (!confirmed) {
      return;
    }
    // What the user typed into the address field survives a
    // re-confirmation (e.g. after an accidental pan).
    let typedAddress = '';

    const button = component.querySelector('[data-map-confirm]');

    // While a confirmation is current the fragment says so, and the
    // button is hidden: tapping it again (taking it for "save")
    // would replace what the user typed, or on a bad connection
    // replace a good confirmation with an error.
    function showButton(show) {
      if (button) {
        button.hidden = !show;
      }
    }

    function say(key) {
      showButton(true);
      confirmed.innerHTML = '';
      const note = doc.createElement('p');
      note.className = 'text-body-secondary';
      note.textContent = confirmed.getAttribute('data-msg-' + key);
      confirmed.appendChild(note);
    }

    // Counts moves, so a confirm response for a position the map
    // has since left can be recognised and dropped.
    let moves = 0;
    let movesAtRequest = null;
    // Say why the confirmation went: the user moved the map, or GPS
    // recentred it (the user did nothing and shouldn't be blamed).
    let lastMove = 'moved';

    // A confirmation describes one position: once the map moves,
    // drop it (its hidden fields too, so the enclosing form can't
    // submit a stale position) and say so.
    // moveend counts too: the hidden fields only catch up on
    // moveend, so a request sent mid-move (inertia after a flick, a
    // zoom animation) carries a position the map is about to leave.
    map.on('moveend', function () {
      moves += 1;
    });
    map.on('movestart', function () {
      moves += 1;
      lastMove = isUserMove() ? 'moved' : 'located';
      if (!confirmed.querySelector('input') && movesAtRequest === null) {
        return;
      }
      const address = confirmed.querySelector('input[name="address"]');
      if (address && address.getAttribute('value') !== address.value) {
        typedAddress = address.value;
      }
      say(lastMove);
    });

    component.addEventListener('htmx:beforeRequest', function (event) {
      if (event.target && event.target.hasAttribute('data-map-confirm')) {
        movesAtRequest = moves;
      }
    });

    confirmed.addEventListener('htmx:beforeSwap', function (event) {
      if (movesAtRequest !== null && movesAtRequest !== moves) {
        // Answer for where the crosshair used to be.
        event.detail.shouldSwap = false;
        say(lastMove);
      }
      movesAtRequest = null;
    });

    // Keep the confirmed GPS evidence in step with the device
    // fields: withdrawn when the user switches location off (§11.2),
    // added when a fix arrives after confirming (or while the
    // confirm request was in flight).
    function currentDevice() {
      const values = {};
      ['device_lat', 'device_lon', 'device_accuracy'].forEach(function (n) {
        const input = component.querySelector('[data-map-field="' + n + '"]');
        values[n] = input ? input.value : '';
      });
      return values;
    }

    function applyDevice(values) {
      if (!confirmed.querySelector('input[name="lat"]')) {
        return;
      }
      ['device_lat', 'device_lon', 'device_accuracy'].forEach(function (n) {
        let input = confirmed.querySelector('input[name="' + n + '"]');
        const value = values && values[n];
        if (value === undefined || value === null || value === '') {
          if (input) {
            input.remove();
          }
          return;
        }
        if (!input) {
          input = doc.createElement('input');
          input.type = 'hidden';
          input.name = n;
          confirmed.appendChild(input);
        }
        input.value = value;
      });
    }

    component.addEventListener('mobilito:device', function (event) {
      applyDevice(event.detail);
    });

    confirmed.addEventListener('htmx:afterSwap', function () {
      const isConfirmed = !!confirmed.querySelector('input[name="lat"]');
      if (isConfirmed && button && doc.activeElement === button) {
        // Don't let focus fall back to the top of the page when the
        // button it's on disappears; the container isn't an input,
        // so no on-screen keyboard pops up.
        confirmed.focus({ preventScroll: true });
      }
      showButton(!isConfirmed);
      applyDevice(currentDevice());
      const address = confirmed.querySelector('input[name="address"]');
      if (address && typedAddress) {
        address.value = typedAddress;
        typedAddress = '';
      }
      // Bring the address into view without a scroll gesture, which
      // on a phone would likely start on the map and pan it.
      if (confirmed.scrollIntoView) {
        confirmed.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    });

    // htmx 2 doesn't swap error responses and says nothing when the
    // network fails.
    function onConfirmFailed(event) {
      if (event.target && event.target.hasAttribute('data-map-confirm')) {
        movesAtRequest = null;
        say('failed');
      }
    }
    component.addEventListener('htmx:responseError', onConfirmFailed);
    component.addEventListener('htmx:sendError', onConfirmFailed);
  }

  function initGps(
    component,
    env,
    map,
    config,
    setField,
    moveTo,
    isUserMove
  ) {
    const button = component.querySelector('[data-map-gps]');
    const checkbox = component.querySelector('[data-map-device-location]');
    const status = component.querySelector('[data-map-gps-status]');
    let haveFix = false;
    let movedByUser = false;

    map.on('movestart', function () {
      if (isUserMove()) {
        movedByUser = true;
      }
    });

    function announceDevice(values) {
      component.dispatchEvent(
        new component.ownerDocument.defaultView.CustomEvent(
          'mobilito:device',
          { detail: values }
        )
      );
    }

    function say(messageKey) {
      if (!status) {
        return;
      }
      status.innerHTML = '';
      if (messageKey) {
        const alert = status.ownerDocument.createElement('div');
        alert.className = 'alert alert-secondary small mt-2 mb-0';
        alert.textContent = status.getAttribute('data-msg-' + messageKey);
        status.appendChild(alert);
      }
    }

    function setBusy(busy) {
      if (button) {
        button.disabled = busy;
        button.setAttribute('aria-busy', busy ? 'true' : 'false');
      }
    }

    function clearDevice() {
      haveFix = false;
      setField('device_lat', '');
      setField('device_lon', '');
      setField('device_accuracy', '');
      announceDevice(null);
    }

    function locate(recentre) {
      // Never touch the geolocation API when the preference is off:
      // no permission prompt may appear (§11.2).
      if (checkbox && !checkbox.checked) {
        return;
      }
      if (!env.geolocation) {
        // Old browser or non-HTTPS page: retrying can't help.
        clearDevice();
        say('nogeo');
        if (button) {
          button.hidden = true;
        }
        return;
      }
      setBusy(true);
      env.geolocation.getCurrentPosition(
        function (position) {
          const coords = position.coords;
          setBusy(false);
          haveFix = true;
          const values = {
            device_lat: coords.latitude.toFixed(6),
            device_lon: coords.longitude.toFixed(6),
            device_accuracy: String(Math.round(coords.accuracy)),
          };
          Object.keys(values).forEach(function (name) {
            setField(name, values[name]);
          });
          announceDevice(values);
          say(null);
          // A slow fix mustn't yank the map away from a spot the
          // user has already picked by hand; an explicit tap on the
          // button always recentres.
          if (recentre || !movedByUser) {
            moveTo(
              [coords.latitude, coords.longitude],
              Math.max(map.getZoom(), GPS_ZOOM)
            );
          }
        },
        function (error) {
          setBusy(false);
          if (error && error.code === 1) {
            // PERMISSION_DENIED: nothing from GPS is trustworthy now,
            // and the observation is unverified (§11.2).
            clearDevice();
            say('denied');
          } else if (!haveFix) {
            say('unavailable');
          }
          // Timeout/unavailable after an earlier good fix: keep it.
        },
        GPS_OPTIONS
      );
    }

    function applyPreference(enabled, recentre) {
      if (button) {
        button.hidden = !enabled;
      }
      if (enabled) {
        locate(recentre);
      } else {
        clearDevice();
        say('off');
      }
    }

    if (button) {
      button.addEventListener('click', function () {
        locate(true);
      });
    }
    if (checkbox) {
      // htmx on the checkbox itself persists the choice.
      checkbox.addEventListener('change', function () {
        applyPreference(checkbox.checked, true);
      });
    }
    // Prefer the checkbox's live state: browsers may restore form
    // state on reload, so it can differ from the server's config.
    applyPreference(
      checkbox ? checkbox.checked : config.useDeviceLocation,
      false
    );

    return { locate: locate };
  }

  function initPins(component, env, map, config) {
    const L = env.L;
    const sheet = component.querySelector('[data-map-sheet]');
    const sheetBody = component.querySelector('[data-map-sheet-body]');
    // A canvas with a generous tolerance makes small pins easy to
    // tap without making them visually large. It must be passed to
    // each marker: L.geoJSON doesn't forward it to pointToLayer's.
    const renderer = L.canvas({ tolerance: 10 });
    // TODO(Phase 7): canvas pins aren't reachable by keyboard or
    // screen reader; the list view (/observations/) is the
    // accessible alternative and should be linked near the map.
    const layer = L.geoJSON(null, {
      pointToLayer: function (feature, latlng) {
        return L.circleMarker(
          latlng,
          Object.assign({ renderer: renderer }, pinStyle(feature))
        );
      },
      onEachFeature: function (feature, pin) {
        pin.on('click', function () {
          openSheet(feature);
        });
      },
    }).addTo(map);

    function showSheetMessage(key, spinner) {
      sheetBody.innerHTML = '';
      const doc = sheetBody.ownerDocument;
      const wrap = doc.createElement('div');
      wrap.setAttribute('role', 'status');
      if (spinner) {
        const spin = doc.createElement('div');
        spin.className = 'spinner-border';
        spin.setAttribute('aria-hidden', 'true');
        wrap.appendChild(spin);
      }
      const text = doc.createElement('span');
      text.className = spinner ? 'visually-hidden' : '';
      text.textContent = sheet.getAttribute('data-msg-' + key);
      wrap.appendChild(text);
      sheetBody.appendChild(wrap);
    }

    function openSheet(feature) {
      const url = feature.properties && feature.properties.summary_url;
      if (!url || !sheet) {
        return;
      }
      showSheetMessage('loading', true);
      const request = env.htmx.ajax('GET', url, {
        target: sheetBody,
        swap: 'innerHTML',
      });
      if (request && request.catch) {
        request.catch(function () {
          showSheetMessage('failed', false);
        });
      }
      env.bootstrap.Offcanvas.getOrCreateInstance(sheet).show();
    }

    if (sheetBody) {
      // htmx 2 doesn't swap error responses; say something instead
      // of leaving the spinner forever.
      sheetBody.addEventListener('htmx:responseError', function () {
        showSheetMessage('failed', false);
      });
      sheetBody.addEventListener('htmx:sendError', function () {
        showSheetMessage('failed', false);
      });
    }

    let latestRequest = 0;

    function loadPins() {
      const requestNumber = ++latestRequest;
      const separator = config.pinsUrl.indexOf('?') === -1 ? '?' : '&';
      const url =
        config.pinsUrl +
        separator +
        'bbox=' +
        encodeURIComponent(map.getBounds().toBBoxString());
      return env
        .fetch(url, { headers: { Accept: 'application/geo+json' } })
        .then(function (response) {
          if (!response.ok) {
            throw new Error('HTTP ' + response.status);
          }
          return response.json();
        })
        .then(function (data) {
          // A slow response for an old view mustn't replace the
          // pins for the current one.
          if (requestNumber !== latestRequest) {
            return;
          }
          layer.clearLayers();
          layer.addData(data);
        })
        .catch(function () {
          // Keep whatever pins are showing; the next move retries.
        });
    }

    let timer = null;
    map.on('moveend', function () {
      clearTimeout(timer);
      timer = setTimeout(loadPins, PIN_RELOAD_DELAY_MS);
    });
    const loaded = loadPins();

    return { loadPins: loadPins, openSheet: openSheet, pinsLoaded: loaded };
  }

  function initAll(doc, env) {
    if (!env.L) {
      return [];
    }
    return Array.prototype.map.call(
      doc.querySelectorAll('[data-mobilito-map]'),
      function (component) {
        return initMapWidget(component, env, doc);
      }
    );
  }

  return {
    initAll: initAll,
    initMapWidget: initMapWidget,
    pinStyle: pinStyle,
    GPS_ZOOM: GPS_ZOOM,
  };
});
