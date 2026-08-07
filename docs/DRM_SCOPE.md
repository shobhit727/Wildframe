# DRM Scope: Widevine + FairPlay + PlayReady

**Status**: ⚠️ Known gap — no content protection today.
**Last Updated**: August 7, 2026

## Current State

Wildframe streams **plaintext** HLS/DASH today:

- `streaming-service` returns unencrypted manifest paths (`/manifests/{episode_id}/{protocol}.m3u8`) — `services/streaming-service/app/services/__init__.py`
- `media-pipeline` packaging stages (`package_hls` / `package_dash` in `app/core/stages.py`) are in-process stubs; the ffmpeg encode stage exists but there is no encryption pass
- `apps/web` plays with **hls.js ^1.4.0** (`components/player/VideoPlayer.tsx`) — no EME (Encrypted Media Extensions) usage
- No content-encryption keys, no license endpoint, no `#EXT-X-SESSION-KEY` / `#EXT-X-FAIRPLAY` / `ContentProtection` elements, no Widevine/PlayReady/FairPlay registration

## What "Adding Multi-DRM" Actually Means

Streaming DRM is a **three-part system**, all three of which are missing:

1. **Packaging / encryption** — the player's segments must be encrypted (AES-CBC/AES-CTR) with a per-asset content key, and the manifest must carry DRM system init info.
2. **Key management** — content keys encrypted (wrapped) and held in a secure key store, plus rules for releasing them (entitlements, device binding, offline licensing).
3. **License server** — the DRM system that validates a client's `requestMediaKeySystemAccess` session and returns a signed license (only decodable by devices certified by the provider). A license server is *always* provider-specific and cannot be self-invented: Widevine licenses must be signed by Google's Widevine infrastructure, FairPlay requires Apple certs (FPS), PlayReady requires Microsoft.

### Target architecture

```
playback (Shaka Player + EME)
        │ widevine/playready/fairplay license challenge
        ▼
  licensing service (new or in streaming-service)
        │ validates user entitlement (billing-service) + device binding (JWT from api-gateway)
        │ then talks to the provider's license endpoint (or local SDK key-wrapped license issuer)
        ▼
  key service (KMS/KV) + DRM certs (Google CDM cert, Apple FPS cert, PlayReady cert)
        ▲
media-pipeline / packager (Shaka Packager or Bitmovin Element)
        ↑  encrypted CMS (SAMPLE-AES / CENC) with per-asset content keys
```

### Where things plug into the current code

| Layer | Today | Target |
|-------|-------|--------|
| media-pipeline `package_hls`/`package_dash` (FFM stubs) | plaintext mux | Shaka Packager / ffmpeg `-encryption_scheme cenc` writing `ContentProtection` + `#EXT-X-SESSION-KEY`; encrypted segments + key ID IV in init |
| streaming-service manifest generation | plain `.m3u8`/`.mpd` | include DRM init data: MPD `<ContentProtection schemeIdUri=...>` for Widevine/PlayReady; HLS `#EXT-X-FAIRPLAY`, `#EXT-X-SESSION-KEY` for AES-128 |
| (new) license handler | — | `POST /drm/widevine/license`, `/drm/playready/license`, `/drm/fairplay/license`; validates subscription (billing) + stream rights, returns signed/licensed tokens. FairPlay additionally needs Apple's key provider + Far `pichannel` (device provisioning cert); iOS Safari routes: Apple FPS `.ttp`/.tok |
| frontend player | hls.js (clear) | **Shaka Player** (multi-DRM) or hls.js + EME shims; `navigator.requestMediaKeySystemAccess('com.widevine.alpha'/'com.apple.fps'/'com.microsoft.playready')`, attach license to `video` element, forward challenge with user JWT as license blob |

### Licensing / certificates (the non-code step that gates all of it)

- **Widevine**: license server vendor agreement + signing of a code-signing certs (also device provisioning if a L1 DICTS urgent). Dev mode uses L3 CDM.
- **FairPlay (Apple)**: an **FPS (FairPlay Streaming) grant + deployment package**, then FPS deploy kit issue — you need a paid Apple account and obtain a `.cer` from the FairPlay Streaming integration flow; every test device **must** be registered (device registration file `.plikp` with a registered device).
- **PlayReady**: Microsoft does not require a paid bilateral agreement but requires the *PlayReady CA* provisioning. Getting v3+ `KeyRotation`.

Estimate: certificate/procurement + agreement. **The legal/procurement step in parallel is 1 single sprint to months depending on org** — this is usually the true longest runway, not the code.

### Effort estimate (engineering, one senior+1 mid streaming/DRM engineer)

| Work item | Effort |
|-----------|--------|
| Provider certs & agreements (procurement/legal, can run parallel) | 2 wks–2 months (blocking) |
| Packager integration (replaces `package_hls`/`package_dash` stubs): Shaka Packager or Bitmovin Element with key wrap via KMS | 1–2 wks |
| KMS / secret store for content keys + key rotation + per-asset key ID map | 3–5 days |
| License service (Widevine + PlayReady endpoints, FairPlay FPS handler) + entitlement check against billing | 1–2 wks |
| Manifest DRM descriptors (MPD ContentProtection, HLS `#EXT-X-FAIRPLAY`, session keys) in streaming-service | 3–5 days |
| Frontend EME via Shaka Player, license request wiring with JWT, DRM error/support UX | 1–2 wks |
| Widevine L1 device provisioning + OEM-only paths (if desired) | 2+ wks (device vendor dependent) |
| **Total (Widevine+FairPlay+PlayReady, L3)** | **~4–6 weeks code + certificate runway** |

### Optional / smaller alternatives

- **Clearkey (AES-128 SAMPLE-AES)** — fully in-repo, no vendor agreement: encrypt via ffmpeg, serve key in a KV, license via `clearkey` JSON. Good for R&D/test + falling for CI, but *weak* security (key is transparent to a debugger).
- **SaaS Multi-DRM** (Bitmovin, DRT M, Axinom, ESpot, Lookseat, etc.) drastically reduces license-server work but keeps the packaging + EME integration.
- **FairPlay-only** for Apple + Widevine L3 elsewhere (reduces to `PlayReady` + one FPS grant).

## Decisions needed

1. **In-house license server vs SaaS DRM vs hybrid** (Big Buck costs vs. control)
2. **Widevine Level** — L3 (SW CDM, permissive) vs L1 (hardware-backed, device signing + OEM effort)
3. **Provider** — Google Widevine, Apple FPS, Microosft Play core vendor agreements signed yet?
4. **Offline / downloads** — adds offline licenses (aka persisted-content) + time-limited key window
5. **Test/CI implications** — EME controlled env in CI; currently all tests would need a clear-key path.

## Conclusion

Not **implemented** is the DB (all 3 pieces + certs); it is a coherent, well-scoped multi-part project. The non-code bottleneck — Google/Apple/Microsoft certificate + agreements — starts *now*, then packaging/EME together ~4–6 weeks (Widevine L3 + FairPlay + Play/Ready). Add to the roadmap as its own EPIC, with clear Key OKRs.