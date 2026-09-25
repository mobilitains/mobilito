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

const { initMapWidget, initAll, pinStyle, GPS_ZOOM } = require(
  '../../../../core/static/core/js/map_widget.js'
);

// Minimal stand-in for the slice of Leaflet the widget uses.
function fakeLeaflet() {
  const created = { maps: [], layers: [] };
  function evented(obj) {
    obj.handlers = {};
    obj.on = function (name, fn) {
      (obj.handlers[name] = obj.handlers[name] || []).push(fn);
      return obj;
    };
    obj.fire = function (name, event) {
      (obj.handlers[name] || []).forEach((fn) => fn(event));
    };
    return obj;
  }
  const L = {
    map(el) {
      const map = evented({
        el,
        center: { lat: 0, lng: 0 },
        zoom: 0,
        setView(center, zoom, options) {
          map.lastSetViewOptions = options;
          // Like Leaflet: synchronous, zoomstart without originalEvent.
          map.fire('movestart');
          if (zoom !== map.zoom) {
            map.fire('zoomstart', {});
          }
          map.center = { lat: center[0], lng: center[1] };
          map.zoom = zoom;
          map.fire('moveend');
          return map;
        },
        getCenter() {
          const c = map.center;
          return { wrap: () => ({ lat: c.lat, lng: c.lng }) };
        },
        getZoom: () => map.zoom,
        getBounds: () => ({ toBBoxString: () => '-2,47,-1,48' }),
      });
      created.maps.push(map);
      return map;
    },
    tileLayer: () => ({ addTo() {} }),
    canvas: (options) => ({ canvas: options }),
    circleMarker: (latlng, style) => evented({ latlng, style }),
    // Simulates the user dragging the map to a new centre.
    drag(map, lat, lng) {
      map.fire('dragstart');
      map.fire('movestart');
      map.center = { lat, lng };
      map.fire('moveend');
    },
    geoJSON(data, options) {
      const layer = {
        options,
        pins: [],
        addTo() {
          return layer;
        },
        clearLayers() {
          layer.pins = [];
        },
        addData(geojson) {
          geojson.features.forEach((feature) => {
            const [lon, lat] = feature.geometry.coordinates;
            const pin = options.pointToLayer(feature, { lat, lng: lon });
            options.onEachFeature(feature, pin);
            layer.pins.push(pin);
          });
        },
      };
      created.layers.push(layer);
      return layer;
    },
  };
  return { L, created };
}

function buildWidget(config) {
  document.body.innerHTML = `
    <div data-mobilito-map="map">
      <script type="application/json" id="map-config">${JSON.stringify(
        config
      )}</script>
      <div class="mobilito-map"><p data-map-fallback>No map</p></div>
      <button data-map-gps></button>
      <input type="checkbox" data-map-device-location
             ${config.useDeviceLocation ? 'checked' : ''}>
      <div data-map-gps-status data-msg-off="OFF" data-msg-denied="DENIED"
           data-msg-unavailable="UNAVAILABLE" data-msg-nogeo="NOGEO"></div>
      <input data-map-field="lat"><input data-map-field="lon">
      <input data-map-field="device_lat">
      <input data-map-field="device_lon">
      <input data-map-field="device_accuracy">
      <button data-map-confirm data-map-requires-js hidden></button>
      <div data-map-confirmed data-msg-moved="MOVED" data-msg-located="LOCATED"
           data-msg-failed="CONFIRM FAILED"></div>
      <div data-map-sheet data-msg-loading="LOADING" data-msg-failed="FAILED">
        <div data-map-sheet-body></div>
      </div>
    </div>`;
  return document.querySelector('[data-mobilito-map]');
}

const BASE_CONFIG = {
  center: [47.2184, -1.5536],
  zoom: 13,
  crosshair: true,
  gps: false,
  pinsUrl: '',
  tileUrl: 'https://tiles/{z}/{x}/{y}.png',
  tileAttribution: 'OSM',
  useDeviceLocation: true,
};

function field(name) {
  return document.querySelector(`[data-map-field="${name}"]`).value;
}

function status() {
  return document.querySelector('[data-map-gps-status]').textContent.trim();
}

// result: coords to report, or {code} for an error; pass
// {defer: true} to get the callbacks back and answer later.
function fakeGeolocation(result, { defer = false } = {}) {
  return {
    calls: 0,
    pending: null,
    getCurrentPosition(onFix, onError) {
      this.calls += 1;
      const answer = () =>
        result.code ? onError(result) : onFix({ coords: result });
      if (defer) {
        this.pending = answer;
      } else {
        answer();
      }
    },
  };
}

describe('crosshair', () => {
  test('removes the no-map fallback message', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(BASE_CONFIG), { L });
    expect(document.querySelector('[data-map-fallback]')).toBeNull();
  });

  test('moving the map withdraws a confirmed position', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML = '<input name="lat" value="1">';
    L.drag(widget.map, 47.3, -1.6);
    expect(confirmed.querySelector('input')).toBeNull();
    expect(confirmed.textContent).toBe('MOVED');
  });

  test('an address typed before an accidental pan is kept', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    const confirmed = document.querySelector('[data-map-confirmed]');
    const fragment =
      '<input name="lat" value="1">' +
      '<input name="address" value="Suggested">';
    confirmed.innerHTML = fragment;
    confirmed.querySelector('[name=address]').value = 'Near the bakery';
    L.drag(widget.map, 47.3, -1.6);
    confirmed.innerHTML = fragment;
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    expect(confirmed.querySelector('[name=address]').value).toBe(
      'Near the bakery'
    );
  });

  test('an untouched suggestion is replaced by the new one', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML = '<input name="address" value="Old">';
    L.drag(widget.map, 47.3, -1.6);
    confirmed.innerHTML = '<input name="address" value="New">';
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    expect(confirmed.querySelector('[name=address]').value).toBe('New');
  });

  test('the confirmation is scrolled into view', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(BASE_CONFIG), { L });
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.scrollIntoView = jest.fn();
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    expect(confirmed.scrollIntoView).toHaveBeenCalled();
  });

  test('a failed confirm request says so', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(BASE_CONFIG), { L });
    const button = document.querySelector('[data-map-confirm]');
    button.dispatchEvent(new Event('htmx:sendError', { bubbles: true }));
    expect(document.querySelector('[data-map-confirmed]').textContent).toBe(
      'CONFIRM FAILED'
    );
  });

  test('a response for a position the map has left is dropped', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    const button = document.querySelector('[data-map-confirm]');
    const confirmed = document.querySelector('[data-map-confirmed]');
    button.dispatchEvent(
      new Event('htmx:beforeRequest', { bubbles: true })
    );
    L.drag(widget.map, 47.3, -1.6);
    const beforeSwap = new Event('htmx:beforeSwap');
    beforeSwap.detail = { shouldSwap: true };
    confirmed.dispatchEvent(beforeSwap);
    expect(beforeSwap.detail.shouldSwap).toBe(false);
    expect(confirmed.textContent).toBe('MOVED');
  });

  test('a response for a position the map was leaving is dropped', () => {
    // E.g. Confirm tapped while inertia still glides after a flick.
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    widget.map.fire('dragstart');
    widget.map.fire('movestart');
    document
      .querySelector('[data-map-confirm]')
      .dispatchEvent(new Event('htmx:beforeRequest', { bubbles: true }));
    widget.map.center = { lat: 47.3, lng: -1.6 };
    widget.map.fire('moveend');
    const beforeSwap = new Event('htmx:beforeSwap');
    beforeSwap.detail = { shouldSwap: true };
    document
      .querySelector('[data-map-confirmed]')
      .dispatchEvent(beforeSwap);
    expect(beforeSwap.detail.shouldSwap).toBe(false);
  });

  test('a response for the current position is swapped', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(BASE_CONFIG), { L });
    const button = document.querySelector('[data-map-confirm]');
    button.dispatchEvent(
      new Event('htmx:beforeRequest', { bubbles: true })
    );
    const beforeSwap = new Event('htmx:beforeSwap');
    beforeSwap.detail = { shouldSwap: true };
    document
      .querySelector('[data-map-confirmed]')
      .dispatchEvent(beforeSwap);
    expect(beforeSwap.detail.shouldSwap).toBe(true);
  });

  test('a restored address is only restored once', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    const confirmed = document.querySelector('[data-map-confirmed]');
    const fragment = '<input name="lat"><input name="address" value="S">';
    confirmed.innerHTML = fragment;
    confirmed.querySelector('[name=address]').value = 'Typed';
    L.drag(widget.map, 47.3, -1.6);
    confirmed.innerHTML = fragment;
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    L.drag(widget.map, 48, -1);
    confirmed.innerHTML = fragment;
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    // Typed still differs from the suggestion, so it's kept again...
    expect(confirmed.querySelector('[name=address]').value).toBe('Typed');
    // ...but once put back to the suggestion, it's no longer forced.
    confirmed.querySelector('[name=address]').value = 'S';
    L.drag(widget.map, 49, -1);
    confirmed.innerHTML = '<input name="lat"><input name="address" value="T">';
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    expect(confirmed.querySelector('[name=address]').value).toBe('T');
  });

  test('a GPS recentre after confirming does not blame the user', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(
      { latitude: 47.25, longitude: -1.5, accuracy: 8 },
      { defer: true }
    );
    initMapWidget(buildWidget({ ...BASE_CONFIG, gps: true }), {
      L,
      geolocation,
    });
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML = '<input name="lat" value="1">';
    geolocation.pending();
    expect(confirmed.textContent).toBe('LOCATED');
  });

  test('Confirm is hidden while a confirmation is current', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    const button = document.querySelector('[data-map-confirm]');
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML = '<input name="lat" value="1">';
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    expect(button.hidden).toBe(true);
    L.drag(widget.map, 47.3, -1.6);
    expect(button.hidden).toBe(false);
  });

  test('Confirm stays available after an error fragment', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(BASE_CONFIG), { L });
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML = '<div class="alert">Try again</div>';
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    expect(document.querySelector('[data-map-confirm]').hidden).toBe(false);
  });

  test('controls needing the map are shown once it loads', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(BASE_CONFIG), { L });
    expect(document.querySelector('[data-map-confirm]').hidden).toBe(false);
  });

  test('moving before confirming shows nothing', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    L.drag(widget.map, 47.3, -1.6);
    expect(document.querySelector('[data-map-confirmed]').textContent).toBe(
      ''
    );
  });

  test('hidden fields start at the map centre', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(BASE_CONFIG), { L });
    expect(field('lat')).toBe('47.218400');
    expect(field('lon')).toBe('-1.553600');
  });

  test('hidden fields follow the map when it moves', () => {
    const { L } = fakeLeaflet();
    const widget = initMapWidget(buildWidget(BASE_CONFIG), { L });
    widget.map.setView([47.1, -1.6], 15);
    expect(field('lat')).toBe('47.100000');
    expect(field('lon')).toBe('-1.600000');
  });
});

describe('GPS', () => {
  const gpsConfig = { ...BASE_CONFIG, gps: true };
  const fix = { latitude: 47.25, longitude: -1.5, accuracy: 8.4 };

  test('centres on the device and records it as evidence', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix);
    const widget = initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation,
    });
    expect(geolocation.calls).toBe(1);
    expect(widget.map.center).toEqual({ lat: 47.25, lng: -1.5 });
    expect(widget.map.zoom).toBe(GPS_ZOOM);
    // Unanimated, so Leaflet's movestart falls inside moveTo().
    expect(widget.map.lastSetViewOptions).toEqual({ animate: false });
    expect(field('lat')).toBe('47.250000');
    expect(field('device_lat')).toBe('47.250000');
    expect(field('device_lon')).toBe('-1.500000');
    expect(field('device_accuracy')).toBe('8');
    expect(status()).toBe('');
  });

  test('a late fix does not move a map the user has placed', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix, { defer: true });
    const widget = initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation,
    });
    L.drag(widget.map, 47.3, -1.6);
    geolocation.pending();
    expect(widget.map.center).toEqual({ lat: 47.3, lng: -1.6 });
    expect(field('lat')).toBe('47.300000');
    // The fix is still recorded as evidence.
    expect(field('device_lat')).toBe('47.250000');
  });

  test('a zoom or keyboard move by the user also counts', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix, { defer: true });
    const widget = initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation,
    });
    // Keyboard pans and zooms fire movestart but no dragstart.
    widget.map.fire('movestart');
    widget.map.center = { lat: 47.3, lng: -1.6 };
    widget.map.fire('moveend');
    geolocation.pending();
    expect(widget.map.center).toEqual({ lat: 47.3, lng: -1.6 });
  });

  test('switching location off withdraws confirmed GPS evidence', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation: fakeGeolocation(fix),
    });
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML =
      '<input name="lat" value="1"><input name="device_lat" value="2">';
    const checkbox = document.querySelector('[data-map-device-location]');
    checkbox.checked = false;
    checkbox.dispatchEvent(new Event('change'));
    expect(confirmed.querySelector('[name=device_lat]')).toBeNull();
    expect(confirmed.querySelector('[name=lat]')).not.toBeNull();
  });

  test('a swapped confirmation reflects the current GPS state', () => {
    // Location switched off while the confirm request was in flight.
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation: fakeGeolocation(fix),
    });
    const checkbox = document.querySelector('[data-map-device-location]');
    checkbox.checked = false;
    checkbox.dispatchEvent(new Event('change'));
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML =
      '<input name="lat" value="1"><input name="device_lat" value="2">';
    confirmed.dispatchEvent(new Event('htmx:afterSwap'));
    expect(confirmed.querySelector('[name=device_lat]')).toBeNull();
  });

  test('a fix after confirming is added to the confirmation', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix, { defer: true });
    const widget = initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation,
    });
    L.drag(widget.map, 47.3, -1.6);
    const confirmed = document.querySelector('[data-map-confirmed]');
    confirmed.innerHTML = '<input name="lat" value="47.3">';
    geolocation.pending();
    expect(confirmed.querySelector('[name=device_lat]').value).toBe(
      '47.250000'
    );
    expect(confirmed.querySelector('[name=device_accuracy]').value).toBe(
      '8'
    );
  });

  test('a fix leaves an already confirmed location alone', () => {
    // Error re-render: the server sent the confirmation back.
    const { L } = fakeLeaflet();
    const component = buildWidget({ ...gpsConfig, confirmed: true });
    const confirmed = component.querySelector('[data-map-confirmed]');
    confirmed.innerHTML = '<input name="lat" value="47.2184">';
    const widget = initMapWidget(component, {
      L,
      geolocation: fakeGeolocation(fix),
    });
    expect(widget.map.center).toEqual({ lat: 47.2184, lng: -1.5536 });
    expect(confirmed.querySelector('[name=lat]')).not.toBeNull();
  });

  test('an already confirmed location hides the confirm button', () => {
    const { L } = fakeLeaflet();
    const component = buildWidget({ ...BASE_CONFIG, confirmed: true });
    component.querySelector('[data-map-confirmed]').innerHTML =
      '<input name="lat" value="47.2184">';
    initMapWidget(component, { L });
    expect(component.querySelector('[data-map-confirm]').hidden).toBe(true);
  });

  test('an unconfirmed map shows the confirm button', () => {
    const { L } = fakeLeaflet();
    const component = buildWidget(BASE_CONFIG);
    initMapWidget(component, { L });
    expect(component.querySelector('[data-map-confirm]').hidden).toBe(false);
  });

  test('tapping the button always recentres', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix);
    const widget = initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation,
    });
    L.drag(widget.map, 47.3, -1.6);
    document.querySelector('[data-map-gps]').click();
    expect(widget.map.center).toEqual({ lat: 47.25, lng: -1.5 });
  });

  test('the button is busy while locating', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix, { defer: true });
    initMapWidget(buildWidget(gpsConfig), { L, geolocation });
    const button = document.querySelector('[data-map-gps]');
    expect(button.getAttribute('aria-busy')).toBe('true');
    geolocation.pending();
    expect(button.getAttribute('aria-busy')).toBe('false');
    expect(button.disabled).toBe(false);
  });

  test('never calls geolocation when the preference is off', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix);
    initMapWidget(buildWidget({ ...gpsConfig, useDeviceLocation: false }), {
      L,
      geolocation,
    });
    document.querySelector('[data-map-gps]').click();
    expect(geolocation.calls).toBe(0);
    expect(document.querySelector('[data-map-gps]').hidden).toBe(true);
    expect(status()).toBe('OFF');
  });

  test('the checkbox state wins over the config', () => {
    // Browsers can restore a checkbox on reload.
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix);
    buildWidget(gpsConfig);
    document.querySelector('[data-map-device-location]').checked = false;
    initMapWidget(document.querySelector('[data-mobilito-map]'), {
      L,
      geolocation,
    });
    expect(geolocation.calls).toBe(0);
    expect(status()).toBe('OFF');
  });

  test('denied permission explains and clears evidence', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation: fakeGeolocation({ code: 1 }),
    });
    expect(status()).toBe('DENIED');
    expect(field('device_lat')).toBe('');
  });

  test('timeout says to try again', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(gpsConfig), {
      L,
      geolocation: fakeGeolocation({ code: 3 }),
    });
    expect(status()).toBe('UNAVAILABLE');
  });

  test('a failed retry keeps an earlier good fix', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix);
    initMapWidget(buildWidget(gpsConfig), { L, geolocation });
    geolocation.getCurrentPosition = (onFix, onError) =>
      onError({ code: 3 });
    document.querySelector('[data-map-gps]').click();
    expect(field('device_lat')).toBe('47.250000');
    expect(status()).toBe('');
  });

  test('missing geolocation API says so and hides the button', () => {
    const { L } = fakeLeaflet();
    initMapWidget(buildWidget(gpsConfig), { L });
    expect(status()).toBe('NOGEO');
    expect(document.querySelector('[data-map-gps]').hidden).toBe(true);
  });

  test('unticking clears device evidence; re-ticking locates', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix);
    initMapWidget(buildWidget(gpsConfig), { L, geolocation });
    const checkbox = document.querySelector('[data-map-device-location]');

    checkbox.checked = false;
    checkbox.dispatchEvent(new Event('change'));
    expect(field('device_lat')).toBe('');
    expect(document.querySelector('[data-map-gps]').hidden).toBe(true);

    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change'));
    expect(geolocation.calls).toBe(2);
    expect(field('device_lat')).toBe('47.250000');
  });

  test('the GPS button re-reads the position', () => {
    const { L } = fakeLeaflet();
    const geolocation = fakeGeolocation(fix);
    initMapWidget(buildWidget(gpsConfig), { L, geolocation });
    document.querySelector('[data-map-gps]').click();
    expect(geolocation.calls).toBe(2);
  });
});

describe('pins', () => {
  const geojson = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [-1.55, 47.21] },
        properties: { kind: 'report', summary_url: '/reports/1/summary/' },
      },
    ],
  };

  function fakeFetch(body, ok = true) {
    const fetch = jest.fn(() =>
      Promise.resolve({ ok, status: ok ? 200 : 500, json: () => body })
    );
    return fetch;
  }

  test('loads pins for the visible area', async () => {
    const { L, created } = fakeLeaflet();
    const fetch = fakeFetch(geojson);
    const widget = initMapWidget(
      buildWidget({ ...BASE_CONFIG, pinsUrl: '/api/pins' }),
      { L, fetch }
    );
    await widget.pinsLoaded;
    expect(fetch.mock.calls[0][0]).toBe(
      '/api/pins?bbox=' + encodeURIComponent('-2,47,-1,48')
    );
    expect(created.layers[0].pins).toHaveLength(1);
    expect(created.layers[0].pins[0].style.fillColor).toBe('#fd7e14');
    // Each pin gets the tolerant canvas renderer.
    expect(created.layers[0].pins[0].style.renderer).toEqual({
      canvas: { tolerance: 10 },
    });
  });

  test('a slow old response does not replace newer pins', async () => {
    const { L, created } = fakeLeaflet();
    const resolvers = [];
    const fetch = jest.fn(
      () => new Promise((resolve) => resolvers.push(resolve))
    );
    const widget = initMapWidget(
      buildWidget({ ...BASE_CONFIG, pinsUrl: '/api/pins' }),
      { L, fetch }
    );
    const second = widget.loadPins();
    const empty = { type: 'FeatureCollection', features: [] };
    resolvers[1]({ ok: true, json: () => empty });
    await second;
    resolvers[0]({ ok: true, json: () => geojson });
    await widget.pinsLoaded;
    expect(created.layers[0].pins).toHaveLength(0);
  });

  test('tapping a pin loads its summary into the bottom sheet', async () => {
    const { L, created } = fakeLeaflet();
    const htmx = { ajax: jest.fn() };
    const show = jest.fn();
    const bootstrap = {
      Offcanvas: { getOrCreateInstance: jest.fn(() => ({ show })) },
    };
    const widget = initMapWidget(
      buildWidget({ ...BASE_CONFIG, pinsUrl: '/api/pins' }),
      { L, fetch: fakeFetch(geojson), htmx, bootstrap }
    );
    await widget.pinsLoaded;
    created.layers[0].pins[0].fire('click');
    expect(htmx.ajax).toHaveBeenCalledWith('GET', '/reports/1/summary/', {
      target: document.querySelector('[data-map-sheet-body]'),
      swap: 'innerHTML',
    });
    expect(show).toHaveBeenCalled();
    expect(
      document.querySelector('[data-map-sheet-body]').textContent
    ).toBe('LOADING');
  });

  test('a failed summary load says so', async () => {
    const { L, created } = fakeLeaflet();
    const htmx = { ajax: jest.fn() };
    const bootstrap = {
      Offcanvas: { getOrCreateInstance: () => ({ show() {} }) },
    };
    const widget = initMapWidget(
      buildWidget({ ...BASE_CONFIG, pinsUrl: '/api/pins' }),
      { L, fetch: fakeFetch(geojson), htmx, bootstrap }
    );
    await widget.pinsLoaded;
    created.layers[0].pins[0].fire('click');
    const body = document.querySelector('[data-map-sheet-body]');
    body.dispatchEvent(new Event('htmx:responseError'));
    expect(body.textContent).toBe('FAILED');
  });

  test('a failed load keeps existing pins', async () => {
    const { L, created } = fakeLeaflet();
    const fetch = fakeFetch(geojson);
    const widget = initMapWidget(
      buildWidget({ ...BASE_CONFIG, pinsUrl: '/api/pins?x=1' }),
      { L, fetch }
    );
    await widget.pinsLoaded;
    fetch.mockImplementation(() =>
      Promise.resolve({ ok: false, status: 500, json: () => ({}) })
    );
    await widget.loadPins();
    expect(created.layers[0].pins).toHaveLength(1);
    expect(fetch.mock.calls[1][0]).toMatch(/^\/api\/pins\?x=1&bbox=/);
  });

  test('unknown kinds get a neutral colour', () => {
    expect(pinStyle({ properties: {} }).fillColor).toBe('#6c757d');
  });
});

describe('initAll', () => {
  test('does nothing without Leaflet', () => {
    buildWidget(BASE_CONFIG);
    expect(initAll(document, {})).toEqual([]);
  });

  test('initialises every widget on the page', () => {
    const { L, created } = fakeLeaflet();
    buildWidget(BASE_CONFIG);
    expect(initAll(document, { L })).toHaveLength(1);
    expect(created.maps).toHaveLength(1);
  });
});
