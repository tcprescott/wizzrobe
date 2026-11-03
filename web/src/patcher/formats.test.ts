import assert from 'node:assert/strict';
import { test } from 'vitest';

import {
  applyIps, applyPatch, applyUps, crc32, crcHex, matchRom, PatchError, patchInfo,
} from './formats';

function varint(n: number): number[] {
  const out: number[] = [];
  for (;;) {
    const x = n & 0x7F;
    n = Math.floor(n / 128);
    if (n === 0) { out.push(0x80 | x); return out; }
    out.push(x);
    n -= 1;
  }
}

function u32(n: number): number[] {
  return [n & 0xFF, (n >>> 8) & 0xFF, (n >>> 16) & 0xFF, (n >>> 24) & 0xFF];
}

function withFooter(body: number[], source: Uint8Array, target: Uint8Array): Uint8Array {
  const bytes = [...body, ...u32(crc32(source)), ...u32(crc32(target))];
  return new Uint8Array([...bytes, ...u32(crc32(new Uint8Array(bytes)))]);
}

// UPS: XOR runs after relative skips, each run ended by a zero byte.
function makeUps(source: Uint8Array, target: Uint8Array): Uint8Array {
  const body = [0x55, 0x50, 0x53, 0x31, ...varint(source.length), ...varint(target.length)];
  let last = 0;
  let i = 0;
  while (i < target.length) {
    const s = i < source.length ? source[i]! : 0;
    if ((s ^ target[i]!) === 0) { i++; continue; }
    body.push(...varint(i - last));
    while (i < target.length) {
      const x = (i < source.length ? source[i]! : 0) ^ target[i]!;
      if (x === 0) break;
      body.push(x);
      i++;
    }
    body.push(0);
    i++;
    last = i;
  }
  return withFooter(body, source, target);
}

function makeBps(source: Uint8Array, target: Uint8Array): Uint8Array {
  // One TargetRead of the whole target: simplest valid BPS.
  const body = [0x42, 0x50, 0x53, 0x31, ...varint(source.length), ...varint(target.length), ...varint(0),
    ...varint(((target.length - 1) << 2) | 1), ...target];
  return withFooter(body, source, target);
}

const source = new Uint8Array(1024).map((_, i) => i & 0xFF);

test('IPS writes records, fills RLE runs and grows the file', () => {
  const patch = new Uint8Array([
    ...Buffer.from('PATCH'),
    0, 0, 2, 0, 3, 9, 8, 7,          // write 9 8 7 at 2
    0, 0, 10, 0, 0, 0, 4, 0xAA,       // RLE: 4 × 0xAA at 10
    0, 4, 0, 0, 2, 1, 2,             // write past the end: 1 2 at 1024
    ...Buffer.from('EOF'),
  ]);
  const out = applyIps(patch, source);
  assert.equal(out.length, 1026);
  assert.deepEqual([...out.subarray(0, 6)], [0, 1, 9, 8, 7, 5]);
  assert.deepEqual([...out.subarray(10, 15)], [0xAA, 0xAA, 0xAA, 0xAA, 14]);
  assert.deepEqual([...out.subarray(1024)], [1, 2]);
  assert.equal(source[2], 2, 'the source is not modified');
});

test('IPS truncation after EOF shrinks the file', () => {
  const patch = new Uint8Array([...Buffer.from('PATCH'), ...Buffer.from('EOF'), 0, 0, 16]);
  assert.equal(applyIps(patch, source).length, 16);
});

test('a cut-short IPS is refused', () => {
  const patch = new Uint8Array([...Buffer.from('PATCH'), 0, 0, 2, 0, 9, 1]);
  assert.throws(() => applyIps(patch, source), PatchError);
});

test('UPS round-trips, including a longer target', () => {
  const target = new Uint8Array(1100).map((_, i) => (i * 7) & 0xFF);
  target.set(source.subarray(0, 100));
  const patch = makeUps(source, target);
  assert.deepEqual(patchInfo(patch), { format: 'ups', sourceCrc: crc32(source) });
  assert.deepEqual(applyPatch(patch, source), target);
});

test('BPS through applyPatch, and a copier header is dropped to match', () => {
  const target = source.map(b => b ^ 0x5A);
  const patch = makeBps(source, target);
  const headered = new Uint8Array(512 + source.length);
  headered.set(source, 512);
  assert.deepEqual(applyPatch(patch, headered), target);
  assert.equal(matchRom(headered, crc32(source))!.length, source.length);
});

test('the wrong ROM names both CRCs', () => {
  const patch = makeBps(source, source);
  const other = source.map(b => b ^ 1);
  assert.throws(() => applyPatch(patch, other), e => {
    assert.ok(e instanceof PatchError);
    assert.match(e.message, new RegExp(crcHex(crc32(other))));
    assert.match(e.message, new RegExp(crcHex(crc32(source))));
    return true;
  });
});

test('patchInfo refuses other files and a damaged checksum', () => {
  assert.throws(() => patchInfo(new Uint8Array([1, 2, 3, 4])), /isn't a BPS, IPS or UPS/);
  const patch = makeUps(source, source.map(b => b ^ 3));
  patch[8]! ^= 1;
  assert.throws(() => patchInfo(patch), /damaged/);
  assert.deepEqual(patchInfo(new Uint8Array([...Buffer.from('PATCH'), ...Buffer.from('EOF')])), {
    format: 'ips', sourceCrc: null,
  });
});

test('applyUps refuses a ROM of the wrong size', () => {
  const patch = makeUps(source, source.map(b => b ^ 3));
  assert.throws(() => applyUps(patch, source.subarray(1)), PatchError);
});
