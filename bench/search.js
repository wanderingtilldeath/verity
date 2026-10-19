// k6 run -e BASE=http://127.0.0.1:8000 -e VUS=50 -e QUERIES=bench/queries.json bench/search.js
// ENDPOINT: search (default) | suggest | related. Closed-loop VUs: a 10 s ramp-in (tagged
// phase=ramp, excluded from the thresholds' sub-metrics) then DURATION of measured load.
import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://127.0.0.1:8000';
const VUS = parseInt(__ENV.VUS || '10');
const DURATION = __ENV.DURATION || '60s';
const ENDPOINT = __ENV.ENDPOINT || 'search';
const WARMUP = __ENV.WARMUP || '10s';

const data = new SharedArray('queries', () => JSON.parse(open(__ENV.QUERIES || './queries.json')));
const esTook = new Trend('es_took_ms', true);

export const options = {
  scenarios: {
    ramp: { executor: 'constant-vus', vus: VUS, duration: WARMUP, exec: 'ramp', gracefulStop: '0s' },
    measure: { executor: 'constant-vus', vus: VUS, duration: DURATION, startTime: WARMUP, exec: 'measure' },
  },
  // Thresholds exist so k6 reports the phase=measure sub-metrics in --summary-export.
  thresholds: {
    'http_req_duration{phase:measure}': ['max>=0'],
    'http_req_failed{phase:measure}': ['rate>=0'],
    'es_took_ms{phase:measure}': ['max>=0'],
    'http_reqs{phase:measure}': ['count>=0'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

function url() {
  const item = data[Math.floor(Math.random() * data.length)];
  if (ENDPOINT === 'related') return `${BASE}/related/${item.id}`;
  const q = encodeURIComponent(item.q);
  if (ENDPOINT === 'suggest') return `${BASE}/suggest?q=${q}`;
  return `${BASE}/search?q=${q}&size=10`;
}

function hit(phase) {
  const res = http.get(url(), { tags: { phase } });
  const ok = check(res, { 'status 200': (r) => r.status === 200 }, { phase });
  if (ok && phase === 'measure') {
    const took = res.json('took_ms');
    if (took !== undefined) esTook.add(took, { phase });
  }
}

export function ramp() { hit('ramp'); }
export function measure() { hit('measure'); }
