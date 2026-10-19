// k6 run -e LEN=1000 -e VUS=10 -e INPUTS=bench/plagiarism_inputs.json bench/plagiarism.js
// Latency of POST /plagiarism/check as a function of input length (words).
import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://127.0.0.1:8000';
const VUS = parseInt(__ENV.VUS || '1');
const DURATION = __ENV.DURATION || '60s';
const LEN = __ENV.LEN || '1000';

const inputs = new SharedArray('inputs', () => JSON.parse(open(__ENV.INPUTS || './plagiarism_inputs.json'))[LEN]);
const candidates = new Trend('plagiarism_candidates');

export const options = {
  scenarios: {
    measure: { executor: 'constant-vus', vus: VUS, duration: DURATION },
  },
  thresholds: { http_req_duration: ['max>=0'], plagiarism_candidates: ['max>=0'] },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const text = inputs[Math.floor(Math.random() * inputs.length)];
  const res = http.post(`${BASE}/plagiarism/check`, JSON.stringify({ text }), {
    headers: { 'Content-Type': 'application/json' },
  });
  if (check(res, { 'status 200': (r) => r.status === 200 })) candidates.add(res.json('candidates'));
}
