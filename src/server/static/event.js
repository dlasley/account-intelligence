(function () {
  var script = document.currentScript;
  var KEY = script && script.getAttribute('data-key');
  var INGEST_URL =
    (script && script.getAttribute('data-url')) ||
    'https://api.example.com/event';
  var BATCH_SIZE = 10;
  var FLUSH_INTERVAL_MS = 2000;
  var MAX_RETRIES = 3;

  var identity = null;
  var pending = [];
  var timer = null;

  function uuid() {
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
      return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, function (c) {
        return (
          (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4))))
        ).toString(16);
      });
    }
    return 'evt-' + Date.now() + '-' + Math.random().toString(36).slice(2);
  }

  function flush(useBeacon) {
    if (pending.length === 0 || !KEY) return;
    var batch = { events: pending.splice(0, pending.length) };
    var body = JSON.stringify(batch);
    if (useBeacon && typeof navigator !== 'undefined' && navigator.sendBeacon) {
      try {
        navigator.sendBeacon(
          INGEST_URL + '?key=' + encodeURIComponent(KEY),
          body
        );
        return;
      } catch (_e) {}
    }
    sendWithRetry(INGEST_URL, body, 0);
  }

  function sendWithRetry(url, body, attempt) {
    if (typeof fetch === 'undefined') return;
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + KEY,
      },
      body: body,
      keepalive: true,
    })
      .then(function (resp) {
        if (resp.status >= 500 && attempt < MAX_RETRIES) {
          setTimeout(function () {
            sendWithRetry(url, body, attempt + 1);
          }, Math.pow(2, attempt) * 500);
        }
      })
      .catch(function () {
        if (attempt < MAX_RETRIES) {
          setTimeout(function () {
            sendWithRetry(url, body, attempt + 1);
          }, Math.pow(2, attempt) * 500);
        }
      });
  }

  function scheduleFlush() {
    if (timer) return;
    timer = setTimeout(function () {
      timer = null;
      flush(false);
    }, FLUSH_INTERVAL_MS);
  }

  function process(call) {
    if (!call || !call.length) return;
    var verb = call[0];
    if (verb === 'identify') {
      identity = call[1] || {};
      return;
    }
    if (verb === 'track') {
      var name = call[1];
      var props = call[2] || {};
      pending.push({
        contact_email: identity && identity.email,
        event: name,
        properties: props,
        event_id: uuid(),
        occurred_at: new Date().toISOString(),
      });
      if (pending.length >= BATCH_SIZE) {
        flush(false);
      } else {
        scheduleFlush();
      }
    }
  }

  var existing = (typeof window !== 'undefined' && window.signal) || [];
  var preload = existing.slice ? existing.slice() : [];
  for (var i = 0; i < preload.length; i++) process(preload[i]);

  if (typeof window !== 'undefined') {
    window.signal = { push: process };
    window.addEventListener('beforeunload', function () {
      flush(true);
    });
    window.addEventListener('pagehide', function () {
      flush(true);
    });
  }
})();
