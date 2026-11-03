import assert from 'node:assert/strict';
import { test } from 'vitest';

import { applyBps, crc32 } from './bps';
import {
  adjustRom, buildSeedRom, parseSprite, RomError, stripHeader, writeChecksum,
} from './door';

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

// A patch exercising all four actions: keep 4 source bytes, write 3 new ones,
// copy source bytes from offset 1, then repeat the last target byte (RLE).
function makePatch(source: Uint8Array, target: Uint8Array, actions: number[][]): Uint8Array {
  const body = [0x42, 0x50, 0x53, 0x31, ...varint(source.length), ...varint(target.length), ...varint(0)];
  for (const a of actions) body.push(...a);
  body.push(...u32(crc32(source)), ...u32(crc32(target)));
  const bytes = Uint8Array.from(body);
  return Uint8Array.from([...bytes, ...u32(crc32(bytes))]);
}

const signed = (n: number) => varint(Math.abs(n) * 2 + (n < 0 ? 1 : 0));

test('applies every BPS action', () => {
  const source = Uint8Array.from([10, 11, 12, 13, 14, 15]);
  const target = Uint8Array.from([10, 11, 12, 13, 1, 2, 3, 11, 12, 12, 12, 12]);
  const patch = makePatch(source, target, [
    varint((4 - 1) * 4 + 0),
    [...varint((3 - 1) * 4 + 1), 1, 2, 3],
    [...varint((2 - 1) * 4 + 2), ...signed(1)],
    [...varint((3 - 1) * 4 + 3), ...signed(8)],
  ]);
  assert.deepEqual(applyBps(patch, source), target);
});

test('refuses the wrong ROM and a damaged patch', () => {
  const source = Uint8Array.from([1, 2, 3, 4]);
  const patch = makePatch(source, source, [varint(3 * 4)]);
  assert.throws(() => applyBps(patch, Uint8Array.from([1, 2, 3, 5])), { code: 'source' });
  const damaged = patch.slice();
  damaged[6]! ^= 1;
  assert.throws(() => applyBps(damaged, source), { code: 'patch' });
});

test('strips a copier header and checks the size', () => {
  const rom = new Uint8Array(0x100000 + 0x200);
  rom[0x200] = 7;
  assert.equal(stripHeader(rom)[0], 7);
  assert.throws(() => stripHeader(new Uint8Array(0x80000)), RomError);
});

test('builds a 2MB ROM from the base patch and the seed writes', () => {
  const source = new Uint8Array(0x100000).fill(5);
  const base = Uint8Array.from(source);
  const patch = makePatch(source, base, [varint((0x100000 - 1) * 4)]);
  const rom = buildSeedRom(source, patch, { [0x180215]: [1, 2, 3] });
  assert.equal(rom.length, 0x200000);
  assert.equal(rom[0], 5);
  assert.deepEqual([...rom.subarray(0x180215, 0x180218)], [1, 2, 3]);
  assert.throws(() => buildSeedRom(new Uint8Array(0x100000), patch, {}), { code: 'version' });
});

test('writes the cosmetic settings where the randomizer does', () => {
  const rom = new Uint8Array(0x200000);
  adjustRom(rom, {
    heartBeep: 'quarter', heartColor: 'green', menuSpeed: 'instant', quickswap: false, music: false,
    reduceFlashing: true,
  });
  assert.equal(rom[0x180033], 0x80);
  assert.equal(rom[0x187020], 2);
  assert.equal(rom[0x6DD9A], 0x20);
  assert.equal(rom[0x180048], 0xE8);
  assert.equal(rom[0x18004B], 0);
  assert.equal(rom[0x18021A], 1);
  assert.deepEqual([...rom.subarray(0x0D00E7, 0x0D00E9)], [0xC4, 0x58]);
  assert.equal(rom[0x18017F], 1);
  // "NINTENDO" centred in 28 columns: 10 spaces, then N.
  assert.equal(rom[0x118002 + 9], 0x9F);
  assert.equal(rom[0x118002 + 10], 0x6A);
  assert.equal(rom[0x118020 + 10], 0x90);
});

test('the checksum and its complement add up', () => {
  const rom = new Uint8Array(0x200000).fill(3);
  writeChecksum(rom);
  const crc = rom[0x7FDE]! | (rom[0x7FDF]! << 8);
  const inv = rom[0x7FDC]! | (rom[0x7FDD]! << 8);
  assert.equal(crc ^ inv, 0xFFFF);
  assert.equal(crc, ((0x200000 - 4) * 3 + 0x1FE) & 0xFFFF);
});

test('palette shuffle changes palettes, and a seed repeats it', () => {
  const rom = new Uint8Array(0x200000).map((_, i) => (i * 31) & 0xFF);
  const one = adjustRom(rom.slice(), { owPalettes: 'shuffled' }, null, () => 0.5);
  const two = adjustRom(rom.slice(), { owPalettes: 'shuffled' }, null, () => 0.5);
  const plain = adjustRom(rom.slice(), {}, null, () => 0.5);
  assert.deepEqual(one, two);
  assert.notDeepEqual(one, plain);
});

function zspr({ palette = 0x7C, author = 'Some One' } = {}) {
  const name = [...'Spr'].flatMap((c) => [c.charCodeAt(0), 0]);
  const by = [...author].flatMap((c) => [c.charCodeAt(0), 0]);
  const ascii = [...author].map((c) => c.charCodeAt(0));
  const strings = [...name, 0, 0, ...by, 0, 0, ...ascii, 0];
  const spriteAt = 0x1D + strings.length;
  const paletteAt = spriteAt + 0x7000;
  const out = new Uint8Array(paletteAt + palette);
  out.set([0x5A, 0x53, 0x50, 0x52, 1], 0);
  const view = new DataView(out.buffer);
  view.setUint32(9, spriteAt, true);
  view.setUint16(13, 0x7000, true);
  view.setUint32(15, paletteAt, true);
  view.setUint16(19, palette, true);
  out.set(strings, 0x1D);
  out.fill(0x11, spriteAt, paletteAt);
  out.fill(0x22, paletteAt, paletteAt + palette);
  return out;
}

test('reads a zspr sprite and credits its author', () => {
  const sprite = parseSprite(zspr());
  assert.equal(sprite.author, 'Some One');
  assert.equal(sprite.palette!.length, 0x78);
  assert.equal(sprite.gloves!.length, 4);
  const rom = adjustRom(new Uint8Array(0x200000), {}, sprite);
  assert.equal(rom[0x80000], 0x11);
  assert.equal(rom[0xDD308], 0x22);
  assert.equal(rom[0xDEDF5], 0x22);
  assert.equal(rom[0x118002 + 10], 0x6F); // "SOME ONE" starts with S
});

test('a sprite without a palette keeps Link\'s colours', () => {
  const sprite = parseSprite(zspr({ palette: 0 }));
  const rom = new Uint8Array(0x200000).fill(9);
  adjustRom(rom, {}, sprite);
  assert.equal(rom[0xDD308], 9);
  assert.throws(() => parseSprite(new Uint8Array(100)), RomError);
});
