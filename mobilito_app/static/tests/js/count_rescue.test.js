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

const { initRescue } = require(
  '../../../../mobilito_app/static/mobilito_app/js/count_rescue.js'
);

const KEY = 'mobilito-count-7';

function build() {
  document.body.innerHTML = `
    <script type="application/json" id="rescue-config">${JSON.stringify({
      sessionId: 7,
      eventUrl: '/counts/7/event/',
      finishUrl: '/counts/7/finish/',
      csrfToken: 'tok',
    })}</script>
    <p hidden data-count-rescue data-msg-sending="SENDING"
       data-msg-offline="OFFLINE" data-msg-error="ERROR"></p>
    <form data-stale-actions>
      <button name="action" value="keep">Keep</button>
      <button name="action" value="discard">Throw</button>
    </form>`;
  return document.querySelector('[data-count-rescue]');
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

function env(storage, responses) {
  const calls = [];
  return {
    calls,
    storage,
    reload: jest.fn(),
    navigate: jest.fn(),
    fetch: jest.fn((url, options) => {
      calls.push([url, JSON.parse(options.body)]);
      const next = responses.shift();
      if (next instanceof Error) {
        return Promise.reject(next);
      }
      return Promise.resolve(next);
    }),
  };
}

const tap = (id) => ({ event_id: id, mode: 'bike', client_timestamp: 1 });
const ok = { status: 204 };

test('nothing saved: nothing happens', async () => {
  const element = build();
  const e = env(memoryStorage(), []);
  expect(await initRescue(element, e)).toBe(false);
  expect(e.fetch).not.toHaveBeenCalled();
  expect(element.hidden).toBe(true);
});

test('sends saved taps in order, then reloads', async () => {
  const element = build();
  const storage = memoryStorage({
    [KEY]: JSON.stringify({ pending: [tap('a'), tap('b')], finishing: null }),
  });
  const e = env(storage, [ok, { status: 409 }]);
  const promise = initRescue(element, e);
  expect(element.textContent).toBe('SENDING');
  expect(document.querySelector('button').disabled).toBe(true);
  await promise;
  expect(e.calls.map((c) => c[1].event_id)).toEqual(['a', 'b']);
  expect(e.calls[0][0]).toBe('/counts/7/event/');
  expect(storage.data[KEY]).toBeUndefined();
  expect(e.reload).toHaveBeenCalled();
});

test('a saved finish goes last, and leads to the results', async () => {
  const element = build();
  const finishing = { totals: { bike: 1 }, finished_at: 5 };
  const storage = memoryStorage({
    [KEY]: JSON.stringify({ pending: [tap('a')], finishing }),
  });
  const e = env(storage, [
    ok,
    { status: 200, json: () => Promise.resolve({ redirect: '/counts/7/' }) },
  ]);
  await initRescue(element, e);
  expect(e.calls[1]).toEqual(['/counts/7/finish/', finishing]);
  expect(e.navigate).toHaveBeenCalledWith('/counts/7/');
  expect(storage.data[KEY]).toBeUndefined();
});

test('offline: keeps what is left and lets them choose', async () => {
  const element = build();
  const storage = memoryStorage({
    [KEY]: JSON.stringify({ pending: [tap('a'), tap('b')], finishing: null }),
  });
  const e = env(storage, [ok, new Error('offline')]);
  expect(await initRescue(element, e)).toBe(false);
  expect(element.textContent).toBe('OFFLINE');
  expect(document.querySelector('button').disabled).toBe(false);
  expect(JSON.parse(storage.data[KEY]).pending).toEqual([tap('b')]);
  expect(e.reload).not.toHaveBeenCalled();
});

test('a server refusal keeps the tap, and says so', async () => {
  const element = build();
  const storage = memoryStorage({
    [KEY]: JSON.stringify({ pending: [tap('a')], finishing: null }),
  });
  const e = env(storage, [{ status: 503 }]);
  await initRescue(element, e);
  expect(JSON.parse(storage.data[KEY]).pending).toEqual([tap('a')]);
  expect(element.textContent).toBe('ERROR');
  expect(document.querySelector('button').disabled).toBe(false);
});

test.each([400, 404, 409])('a tap refused for good (%i) is dropped', async (
  status
) => {
  const element = build();
  const storage = memoryStorage({
    [KEY]: JSON.stringify({ pending: [tap('a')], finishing: null }),
  });
  const e = env(storage, [{ status }]);
  await initRescue(element, e);
  expect(storage.data[KEY]).toBeUndefined();
  expect(e.reload).toHaveBeenCalled();
});

test.each([400, 404])('a finish refused for good (%i) is forgotten', async (
  status
) => {
  const element = build();
  const storage = memoryStorage({
    [KEY]: JSON.stringify({ pending: [], finishing: { totals: {} } }),
  });
  const e = env(storage, [{ status }]);
  await initRescue(element, e);
  expect(storage.data[KEY]).toBeUndefined();
  expect(e.reload).toHaveBeenCalled();
});

test('a finish refused for now, or garbled, is kept', async () => {
  for (const response of [
    { status: 403 },
    { status: 200, json: () => Promise.reject(new Error('bad json')) },
  ]) {
    const element = build();
    const storage = memoryStorage({
      [KEY]: JSON.stringify({ pending: [], finishing: { totals: {} } }),
    });
    const e = env(storage, [response]);
    await initRescue(element, e);
    expect(storage.data[KEY]).toBeDefined();
    expect(element.textContent).toBe('ERROR');
    expect(e.reload).not.toHaveBeenCalled();
  }
});

test('never reloads while the queue cannot be cleared', async () => {
  const element = build();
  const storage = memoryStorage({
    [KEY]: JSON.stringify({ pending: [tap('a')], finishing: null }),
  });
  storage.removeItem = () => {
    throw new Error('blocked');
  };
  const e = env(storage, [ok]);
  expect(await initRescue(element, e)).toBe(false);
  expect(e.reload).not.toHaveBeenCalled();
  expect(document.querySelector('button').disabled).toBe(false);
});

test('no storage at all: nothing to rescue', async () => {
  const element = build();
  const e = env(null, []);
  expect(await initRescue(element, e)).toBe(false);
});

test('a request that hangs times out as offline', async () => {
  jest.useFakeTimers();
  try {
    const element = build();
    const storage = memoryStorage({
      [KEY]: JSON.stringify({ pending: [tap('a')], finishing: null }),
    });
    const e = env(storage, []);
    // Never answers, except by being aborted.
    e.fetch = jest.fn(
      (url, options) =>
        new Promise((resolve, reject) => {
          options.signal.addEventListener('abort', () =>
            reject(new Error('aborted'))
          );
        })
    );
    const done = initRescue(element, e);
    expect(document.querySelector('button').disabled).toBe(true);
    jest.advanceTimersByTime(15000);
    await done;
    expect(element.textContent).toBe('OFFLINE');
    expect(document.querySelector('button').disabled).toBe(false);
  } finally {
    jest.useRealTimers();
  }
});
