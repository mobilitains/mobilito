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

const { initCounter } = require(
  '../../../../mobilito_app/static/mobilito_app/js/count.js'
);

const CONFIG = {
  sessionId: 7,
  startedAt: 1000000,
  minSeconds: 120,
  eventUrl: '/counts/7/event/',
  finishUrl: '/counts/7/finish/',
  discardUrl: '/counts/7/discard/',
  totals: { ped: 0, bike: 0, car: 0, tc: 0 },
  csrfToken: 'tok',
  homeUrl: '/',
  resultsUrl: '/counts/7/',
};

function build(config = CONFIG) {
  document.body.innerHTML = `
    <script type="application/json" id="count-config">${JSON.stringify(
      config
    )}</script>
    <div data-count data-msg-offline="OFFLINE" data-msg-syncing="SYNCING"
         data-msg-finishing="FINISHING" data-msg-error="ERROR"
         data-msg-sending="SENDING" data-msg-nodiscard="NODISCARD"
         data-msg-discardfailed="DISCARDFAILED">
      <button data-finish></button>
      <span data-timer></span>
      <div data-status></div>
      ${['ped', 'bike', 'car', 'tc']
        .map(
          (m) =>
            `<button data-mode="${m}"><span data-total="${m}">0</span></button>`
        )
        .join('')}
      <div data-short hidden>
        <div data-short-dialog tabindex="-1">
          <button data-keep></button><button data-discard></button>
          <button data-resume></button><p data-short-status></p>
        </div>
      </div>
    </div>`;
  return document.querySelector('[data-count]');
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

function response(status, body = {}) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

function makeEnv(overrides = {}) {
  let id = 0;
  let clock = CONFIG.startedAt + 10 * 60 * 1000;
  const online = [];
  const env = {
    fetch: jest.fn((url) =>
      url === CONFIG.finishUrl
        ? response(200, { redirect: '/counts/7/' })
        : url === CONFIG.discardUrl
          ? response(200, { redirect: '/' })
          : response(204)
    ),
    storage: memoryStorage(),
    now: () => clock,
    uuid: () => `id-${++id}`,
    navigate: jest.fn(),
    addOnline: (fn) => online.push(fn),
    addBeforeUnload: (fn) => {
      env.beforeUnload = fn;
    },
    setInterval: jest.fn((fn) => {
      env.tickFn = fn;
    }),
    wakeLock: { request: jest.fn(() => Promise.resolve({})) },
    geolocation: null,
    goOnline: () => Promise.all(online.map((fn) => fn())),
    setClock: (ms) => {
      clock = ms;
    },
    ...overrides,
  };
  return env;
}

const flushPromises = () => new Promise((r) => setTimeout(r, 0));

function sentBodies(env, url) {
  return env.fetch.mock.calls
    .filter(([u]) => u === url)
    .map(([, options]) => JSON.parse(options.body));
}

describe('tapping', () => {
  test('counts at once and sends each tap with its own id', async () => {
    const element = build();
    const env = makeEnv();
    initCounter(element, env);
    element.querySelector('[data-mode="car"]').click();
    element.querySelector('[data-mode="car"]').click();
    element.querySelector('[data-mode="ped"]').click();
    expect(element.querySelector('[data-total="car"]').textContent).toBe(
      '2'
    );
    await flushPromises();
    const sent = sentBodies(env, CONFIG.eventUrl);
    expect(sent.map((b) => b.mode)).toEqual(['car', 'car', 'ped']);
    expect(new Set(sent.map((b) => b.event_id)).size).toBe(3);
    const headers = env.fetch.mock.calls[0][1].headers;
    expect(headers['X-CSRFToken']).toBe('tok');
  });

  test('offline taps wait and go when the connection returns', async () => {
    const element = build();
    let offline = true;
    const env = makeEnv();
    env.fetch.mockImplementation(() =>
      offline ? Promise.reject(new Error('offline')) : response(204)
    );
    const counter = initCounter(element, env);
    element.querySelector('[data-mode="bike"]').click();
    element.querySelector('[data-mode="bike"]').click();
    await flushPromises();
    expect(element.querySelector('[data-status]').textContent).toBe(
      'OFFLINE'
    );
    expect(counter.state.pending).toHaveLength(2);
    offline = false;
    await env.goOnline();
    await flushPromises();
    expect(counter.state.pending).toHaveLength(0);
    expect(element.querySelector('[data-status]').textContent).toBe('');
  });

  test('a rotated CSRF token (403) keeps the tap', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation(() => response(403));
    const counter = initCounter(element, env);
    element.querySelector('[data-mode="tc"]').click();
    await flushPromises();
    expect(counter.state.pending).toHaveLength(1);
    expect(element.querySelector('[data-status]').textContent).toBe('ERROR');
  });

  test('a redirect (e.g. to sign in) keeps the tap', async () => {
    const element = build();
    const env = makeEnv();
    // What fetch gives with redirect: 'manual'.
    env.fetch.mockImplementation(() =>
      Promise.resolve({ ok: false, status: 0, type: 'opaqueredirect' })
    );
    const counter = initCounter(element, env);
    element.querySelector('[data-mode="tc"]').click();
    await flushPromises();
    expect(counter.state.pending).toHaveLength(1);
    expect(env.fetch.mock.calls[0][1].redirect).toBe('manual');
  });

  test('the CSRF token is read from the cookie at send time', async () => {
    document.cookie = 'csrftoken=fresh';
    const element = build();
    const env = makeEnv();
    initCounter(element, env);
    element.querySelector('[data-mode="car"]').click();
    await flushPromises();
    expect(env.fetch.mock.calls[0][1].headers['X-CSRFToken']).toBe('fresh');
    document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
  });

  test('taps carry the last known position when allowed', async () => {
    const element = build({ ...CONFIG, useDeviceLocation: true });
    const env = makeEnv({
      geolocation: {
        watchPosition: (ok) =>
          ok({ coords: { latitude: 47.2, longitude: -1.5 } }),
      },
    });
    initCounter(element, env);
    element.querySelector('[data-mode="car"]').click();
    await flushPromises();
    const [sent] = sentBodies(env, CONFIG.eventUrl);
    expect(sent.lat).toBe(47.2);
    expect(sent.lon).toBe(-1.5);
  });

  test('no position when location use is off', async () => {
    const element = build({ ...CONFIG, useDeviceLocation: false });
    const watchPosition = jest.fn();
    const env = makeEnv({ geolocation: { watchPosition } });
    initCounter(element, env);
    expect(watchPosition).not.toHaveBeenCalled();
  });

  test('a server hiccup keeps the tap for later', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation(() => response(503));
    const counter = initCounter(element, env);
    element.querySelector('[data-mode="tc"]').click();
    await flushPromises();
    expect(counter.state.pending).toHaveLength(1);
  });

  test('a count closed elsewhere goes to its results', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation(() => response(409));
    initCounter(element, env);
    element.querySelector('[data-mode="tc"]').click();
    await flushPromises();
    expect(env.navigate).toHaveBeenCalledWith('/counts/7/');
  });

  test('requests time out rather than hang', async () => {
    const element = build();
    const env = makeEnv();
    initCounter(element, env);
    element.querySelector('[data-mode="tc"]').click();
    await flushPromises();
    expect(env.fetch.mock.calls[0][1].signal).toBeDefined();
  });

  test('a refused tap is not retried forever', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation(() => response(400));
    const counter = initCounter(element, env);
    element.querySelector('[data-mode="tc"]').click();
    await flushPromises();
    expect(counter.state.pending).toHaveLength(0);
  });

  test('the queue survives a reload', async () => {
    const storage = memoryStorage();
    let env = makeEnv({ storage });
    env.fetch.mockImplementation(() => Promise.reject(new Error('off')));
    initCounter(build(), env);
    document.querySelector('[data-mode="ped"]').click();
    await flushPromises();

    env = makeEnv({ storage });
    const counter = initCounter(build(), env);
    await counter.ready;
    await flushPromises();
    expect(document.querySelector('[data-total="ped"]').textContent).toBe(
      '1'
    );
    expect(sentBodies(env, CONFIG.eventUrl)).toHaveLength(1);
  });

  test('works without storage', async () => {
    const element = build();
    const env = makeEnv({ storage: null });
    initCounter(element, env);
    element.querySelector('[data-mode="car"]').click();
    await flushPromises();
    expect(sentBodies(env, CONFIG.eventUrl)).toHaveLength(1);
  });

  test('starts from the server totals', () => {
    const element = build({ ...CONFIG, totals: { ped: 4 } });
    initCounter(element, makeEnv());
    expect(element.querySelector('[data-total="ped"]').textContent).toBe(
      '4'
    );
  });
});

describe('screen', () => {
  test('keeps the screen awake', () => {
    const env = makeEnv();
    initCounter(build(), env);
    expect(env.wakeLock.request).toHaveBeenCalledWith('screen');
  });

  test('shows elapsed time on the server clock', () => {
    const env = makeEnv();
    // Phone clock 3 minutes slow; count started 10 minutes ago.
    const serverNow = CONFIG.startedAt + 10 * 60 * 1000;
    env.setClock(serverNow - 3 * 60 * 1000);
    const element = build({ ...CONFIG, serverNow });
    initCounter(element, env);
    expect(element.querySelector('[data-timer]').textContent).toBe(
      '10:00'
    );
  });

  test('a slow phone clock does not make a long count "short"', () => {
    const env = makeEnv();
    const serverNow = CONFIG.startedAt + 10 * 60 * 1000;
    env.setClock(serverNow - 9 * 60 * 1000);
    const element = build({ ...CONFIG, serverNow });
    initCounter(element, env);
    element.querySelector('[data-finish]').click();
    expect(element.querySelector('[data-short]').hidden).toBe(true);
  });

  test('warns before leaving with unsent taps', async () => {
    const env = makeEnv();
    env.fetch.mockImplementation(() => Promise.reject(new Error('off')));
    const element = build();
    initCounter(element, env);
    const calm = { preventDefault: jest.fn() };
    env.beforeUnload(calm);
    expect(calm.preventDefault).not.toHaveBeenCalled();
    element.querySelector('[data-mode="car"]').click();
    await flushPromises();
    const event = { preventDefault: jest.fn() };
    env.beforeUnload(event);
    expect(event.preventDefault).toHaveBeenCalled();
  });

  test('retries on a timer, not every second', async () => {
    const env = makeEnv();
    env.fetch.mockImplementation(() => Promise.reject(new Error('off')));
    const element = build();
    initCounter(element, env);
    element.querySelector('[data-mode="car"]').click();
    await flushPromises();
    const calls = env.fetch.mock.calls.length;
    env.tickFn();
    await flushPromises();
    expect(env.fetch.mock.calls.length).toBe(calls);
    env.setClock(env.now() + 11000);
    env.tickFn();
    await flushPromises();
    expect(env.fetch.mock.calls.length).toBe(calls + 1);
  });
});

describe('finishing', () => {
  test('sends totals after every tap is through, then shows results', async () => {
    const element = build();
    const env = makeEnv();
    initCounter(element, env);
    element.querySelector('[data-mode="car"]').click();
    element.querySelector('[data-finish]').click();
    await flushPromises();
    await flushPromises();
    const [finish] = sentBodies(env, CONFIG.finishUrl);
    expect(finish.totals).toEqual({ ped: 0, bike: 0, car: 1, tc: 0 });
    const order = env.fetch.mock.calls.map(([u]) => u);
    expect(order).toEqual([CONFIG.eventUrl, CONFIG.finishUrl]);
    expect(env.navigate).toHaveBeenCalledWith('/counts/7/');
    expect(env.storage.data).toEqual({});
  });

  test('no taps once finishing', () => {
    const element = build();
    const env = makeEnv();
    const counter = initCounter(element, env);
    counter.finish();
    element.querySelector('[data-mode="car"]').click();
    expect(counter.state.totals.car).toBe(0);
  });

  test('a short count asks keep or discard', async () => {
    const element = build();
    const env = makeEnv();
    env.setClock(CONFIG.startedAt + 30 * 1000);
    initCounter(element, env);
    element.querySelector('[data-finish]').click();
    expect(element.querySelector('[data-short]').hidden).toBe(false);
    expect(sentBodies(env, CONFIG.finishUrl)).toHaveLength(0);

    element.querySelector('[data-resume]').click();
    expect(element.querySelector('[data-short]').hidden).toBe(true);

    element.querySelector('[data-finish]').click();
    element.querySelector('[data-discard]').click();
    await flushPromises();
    await flushPromises();
    expect(env.navigate).toHaveBeenCalledWith('/');
  });

  test('keeping a short count finishes it', async () => {
    const element = build();
    const env = makeEnv();
    env.setClock(CONFIG.startedAt + 30 * 1000);
    initCounter(element, env);
    element.querySelector('[data-finish]').click();
    element.querySelector('[data-keep]').click();
    await flushPromises();
    await flushPromises();
    expect(sentBodies(env, CONFIG.finishUrl)).toHaveLength(1);
  });

  test('finishing offline waits and resumes after a reload', async () => {
    const storage = memoryStorage();
    let env = makeEnv({ storage });
    env.fetch.mockImplementation(() => Promise.reject(new Error('off')));
    const first = initCounter(build(), env);
    document.querySelector('[data-mode="ped"]').click();
    first.finish();
    await flushPromises();
    expect(document.querySelector('[data-status]').textContent).toBe(
      'FINISHING'
    );
    expect(document.querySelector('[data-mode="ped"]').disabled).toBe(true);

    env = makeEnv({ storage });
    const second = initCounter(build(), env);
    await second.ready;
    await flushPromises();
    await flushPromises();
    expect(sentBodies(env, CONFIG.finishUrl)[0].totals.ped).toBe(1);
    expect(env.navigate).toHaveBeenCalledWith('/counts/7/');
  });

  test('the short-count dialog takes focus', () => {
    const element = build();
    const env = makeEnv();
    env.setClock(CONFIG.startedAt + 30 * 1000);
    initCounter(element, env);
    element.querySelector('[data-finish]').click();
    expect(document.activeElement).toBe(
      element.querySelector('[data-short-dialog]')
    );
  });

  test('discarding without a connection says it cannot yet', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation(() => Promise.reject(new Error('off')));
    const counter = initCounter(element, env);
    await counter.discard();
    expect(element.querySelector('[data-short-status]').textContent).toBe(
      'NODISCARD'
    );
    expect(element.querySelector('[data-discard]').disabled).toBe(false);
  });

  test('discarding a count finished elsewhere shows it', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation(() => response(409));
    const counter = initCounter(element, env);
    await counter.discard();
    expect(env.navigate).toHaveBeenCalledWith('/counts/7/');
  });

  test('a refused discard says so in the panel', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation(() => response(500));
    const counter = initCounter(element, env);
    await counter.discard();
    expect(element.querySelector('[data-short-status]').textContent).toBe(
      'DISCARDFAILED'
    );
  });

  test('Escape keeps counting', () => {
    const element = build();
    const env = makeEnv();
    env.setClock(CONFIG.startedAt + 30 * 1000);
    initCounter(element, env);
    element.querySelector('[data-finish]').click();
    element
      .querySelector('[data-short]')
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(element.querySelector('[data-short]').hidden).toBe(true);
  });

  test('an unreadable finish response is reported, not thrown', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation((url) =>
      url === CONFIG.finishUrl
        ? Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.reject(new Error('html')),
          })
        : response(204)
    );
    const counter = initCounter(element, env);
    counter.finish();
    await flushPromises();
    await flushPromises();
    expect(element.querySelector('[data-status]').textContent).toBe('ERROR');
    expect(env.navigate).not.toHaveBeenCalled();
  });

  test('no "leave this page?" once the finish went through', async () => {
    const element = build();
    const env = makeEnv();
    const counter = initCounter(element, env);
    counter.finish();
    await flushPromises();
    await flushPromises();
    expect(env.navigate).toHaveBeenCalled();
    const event = { preventDefault: jest.fn() };
    env.beforeUnload(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  test('a finish for a count that is gone stops and goes home', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation((url) =>
      url === CONFIG.finishUrl ? response(404) : response(204)
    );
    const counter = initCounter(element, env);
    counter.finish();
    await flushPromises();
    await flushPromises();
    expect(env.navigate).toHaveBeenCalledWith('/');
    expect(env.storage.data).toEqual({});
  });

  test('a refused finish says so and keeps the data', async () => {
    const element = build();
    const env = makeEnv();
    env.fetch.mockImplementation((url) =>
      url === CONFIG.finishUrl ? response(500) : response(204)
    );
    const counter = initCounter(element, env);
    counter.finish();
    await flushPromises();
    await flushPromises();
    expect(element.querySelector('[data-status]').textContent).toBe('ERROR');
    expect(env.storage.data['mobilito-count-7']).toBeDefined();
  });
});
