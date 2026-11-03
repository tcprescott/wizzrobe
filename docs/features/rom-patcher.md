# ROM patcher

Two app pages that patch a player's own ROM in their browser, built on the same engine as the [door seed page](../reference/seed-generation.md#alttprdr). No ROM ever reaches the server.

| Page | Who uses it | What it does |
|---|---|---|
| `/t/<slug>/patch/<token>` | anyone with the link | A patch staff host. Shows what it needs (game, CRC32), then **Choose your ROM…** the first time and **Download patched ROM** after that. |
| `/t/<slug>/patcher` | anyone | The player brings a BPS, IPS or UPS file and a ROM. Nothing is uploaded or stored on the server. |
| Admin → Patches | STAFF, `PRESET_MANAGER` | The list beside the open patch (a page of its own on a phone). **Upload patch** in the header; the patch's ⋮ has Edit…, Replace file…, Open player page, Delete patch…. |

Decisions (Thomas): patches come from **both** places (hosted links and the open page); staff upload under **Admin → Patches**, not on the tournament; **no feature flag** (2026-10-16). The pages live in the app only, with layout A (one centred card); Admin → Patches is the side list; everything runs in Docker, with patches on the door seed volume (2026-11-03). The website has no Patches tab: `patches` is in `APP_ONLY_AREAS`.

## How it fits together

- **Model.** `RomPatch` (tenant-scoped): title, the ROM it needs (`game`), format, `source_crc`, optional tournament, download count. See [data-model.md](../reference/data-model.md#rompatch).
- **Files.** `RomPatchStore` writes `<token>.<format>` under `ROM_PATCH_DIR` (default `rom-patches/` on the door seed volume).
- **REST.** `api/routers/rom_patches.py`. Players: `GET /api/patches/{token}` and `/{token}/file`, and `POST /{token}/downloaded` after a save, all `require_reader` and scoped to the community header. Staff: `/api/admin/patches` lists patches plus the tournaments one can be tagged with; upload (`POST`) and replace (`PUT /{id}/file`) send the file as the raw body with the rest in the query, up to 16 MB; `PUT /{id}` edits; `DELETE /{id}` removes it.
- **Checking an upload.** `application/utils/rom_patch_format.read_patch` refuses anything that isn't a BPS (`BPS1`), UPS (`UPS1`) or IPS (`PATCH`), checks a BPS/UPS's own checksum, walks an IPS to its `EOF`, and reads the source CRC32 a BPS/UPS stores in its footer. An IPS names no ROM, so staff may type the CRC; a BPS/UPS's own CRC always wins over a typed one. The app reads the file first too, so a bad file fails before upload.
- **Service.** `RomPatchService` (see [services.md](../reference/services.md#rom_patch_servicepy--rompatchservice)), audited, no events.
- **Browser engine.** `web/src/patcher/formats.ts` applies all three formats behind `applyPatch(patch, rom)`, reusing `bps.ts` for BPS. `matchRom` drops a 512-byte copier header when that is what stands between the ROM and the patch's CRC. A wrong ROM is refused with both CRCs in the message.
- **Remembering ROMs.** `files.ts` keeps every ROM a player picks in IndexedDB (`wiz-rom-patcher` / `roms`), keyed by the CRC32 of the bytes the patch used. The next patch made for the same ROM finds it without asking, on either page. A hosted IPS without a CRC remembers the ROM by its `game` text instead (localStorage `wiz-rom-patcher-games`).
- **Downloads.** The hosted page reports each finished download, and `RomPatchService.record_download` bumps the count shown in Admin → Patches.

## Dev data and tests

`scripts/seed_online.py` seeds two patches per community: `dev-patch-<slug>` (a BPS made for 1 MB of zero bytes, the same stand-in ROM as the door seed: `head -c 1048576 /dev/zero > dev-rom.sfc`) and `dev-ips-<slug>` (an IPS with no CRC). Tests: `tests/services/test_rom_patch_service.py`, `tests/tenancy/test_rom_patch_tenant_isolation.py`, `tests/api/test_rom_patches_api.py`, and `web/src/patcher/formats.test.ts`. The app screens are in the web a11y run (`web/scripts/a11y/patcher.mjs`, `admin-patches.mjs`).
