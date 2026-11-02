# Store apps (iOS and Android)

The new app in `web/` ships three ways: the website, a Home Screen install, and
the iOS and Android store apps. The store apps are Capacitor shells around the
same build (`web/ios`, `web/android`), with the screens bundled inside rather
than loaded from the site, so they open on bad venue Wi-Fi and only talk to the
API. Spec: [specs/mobile-app.md](../specs/mobile-app.md) step 12.

## What differs inside the store apps

| | Website / Home Screen | Store apps |
|---|---|---|
| Push | Web push through the app's service worker (`web/sw/sw.js`) | APNs on iPhone, FCM on Android (`@capacitor/push-notifications`) |
| Sign-in | Redirect to `/oauth/authorize` and back to `/auth/callback` | The system's in-app browser, back on the app scheme `<APP_ID>:/auth/callback` (RFC 8252), handed to the router by `web/src/native/bridge.ts` |
| Links | Open the site | `https://<domain>/t/…` opens the app (universal links / app links) when installed |
| Origin | The site | `capacitor://localhost` (iOS), `https://localhost` (Android); CORS allows them once the first-party app has an app-scheme redirect |
| Service worker | Registered (offline app files, push) | Not registered |

`isNative()` / `nativePlatform()` in `web/src/lib/platform.ts` tell the two apart.

## Push

Every DM already mirrors to web push (`WebPushService.mirror_dm`); the same call
now hands it to `NativePushService` too, so store-app installs get exactly the
DMs, links and silent/ping behaviour browsers get.

- **Registration:** Me › Notifications › This phone › Turn on asks for the
  phone's permission, gets its token and `POST /api/me/channels/native-devices`
  (`{platform, token, device_name}`). One row per install in `DevicePushToken`
  (global, like `WebPushSubscription`); a token registered by another account
  moves to it. The app keeps the row id and removes it on sign-out.
- **Delivery:** APNs (token auth, HTTP/2) for iPhone when `APNS_*` is set,
  otherwise FCM HTTP v1 for both platforms. The notification carries
  `navigate`, the DM's link; tapping it routes inside the app.
- **Silent:** APNs drops the sound; Android uses the `quiet` channel
  (low importance) instead of `messages`. The app creates both channels at start.
- **Pruning:** a token APNs or FCM reports gone is deleted.

Settings are in the [env-var table](../deployment.md): `APNS_KEY`,
`APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_TOPIC`, `APNS_SANDBOX`,
`FCM_SERVICE_ACCOUNT`. Android also needs the Firebase project's
`google-services.json` in `web/android/app/` at build time (not committed).

## App links

`/.well-known/apple-app-site-association` and `/.well-known/assetlinks.json`
come from `APP_ID`, `APPLE_TEAM_ID` and `ANDROID_CERT_SHA256`
(`AppAssociationService`, `api/app_association.py`) and answer 404 until set.
Only `/t/*` is claimed, so sign-in, the API and help pages stay in the browser.
The apps name the domain they claim in `web/ios/App/App.xcodeproj`
(`WIZZROBE_LINK_DOMAIN`, used by `App.entitlements`) and in
`web/android/app/src/main/res/values/strings.xml` (`link_domain`).

## The app's service worker

`web/sw/sw.js` is a template; the `wiz-service-worker` plugin in
`web/vite.config.ts` writes `dist/sw.js` with the build's file list baked in.
It precaches the app's own code, styles, woff2 fonts and icons, serves the
cached shell for a navigation when the network is gone, and never caches
`/api` or any other server path, so one person's data never sits in a cache.
It shows web-push notifications, opens their link on its own origin, and
rotates a reissued subscription through `/api/web-push/rotate` with the record
the page saved in IndexedDB when it subscribed.

## Building

```bash
cd web
VITE_API_BASE=https://onsite.speedgaming.org npm run build   # the API the app talks to
npx cap sync                                                 # copies dist/ into both shells
npx cap open ios        # Xcode
npx cap open android    # Android Studio
```

`WIZZROBE_APP_ID` overrides the app id (default in `web/src/native/appId.ts`);
it can't change after the first store upload. Register the app scheme redirect
with `scripts/register_first_party_app.py --redirect <APP_ID>:/auth/callback`
beside the website's. Icons and launch screens come from
`poetry run python scripts/generate_pwa_icons.py --native`.

CI (`.github/workflows/native-apps.yml`) builds Android debug on every pull
request touching `web/`, and iOS for the simulator, unsigned, when run by hand.
Signing and TestFlight / Play uploads are not set up yet.
