# ReelVault Android app

Minimal Kotlin client for your ReelVault server.

## What it does
- **Share from Instagram**: Share → ReelVault → "Saved ✓" → back to Instagram.
  Handles `text/plain` (post URLs from any app) and `video/*`,
  `application/pdf`, `image/*` attachments (v2).
- **Summaries list / Detail view**: same evidence-backed rendering as the PWA.
- **Login**: server URL + username + password once; the refresh token is
  encrypted with an **Android Keystore** key (hardware-backed) — plaintext
  never touches disk. Access tokens live in memory only, auto-refreshed on 401.

## Build & install (verified 2026-09-21 — APK builds)

Portable toolchain, no admin needed:
- JDK 17: `D:\tools\jdk17` (Adoptium zip)
- Gradle 8.7: `D:\tools\gradle-8.7`
- Android SDK 34: `D:\android-sdk` (cmdline-tools + `sdkmanager
  "platform-tools" "platforms;android-34" "build-tools;34.0.0"`)

```powershell
cd D:\reelvault\android
$env:JAVA_HOME="D:\tools\jdk17"
.\gradlew.bat assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

`local.properties` must contain `sdk.dir=D\:/android-sdk` (escaped colon —
plain `\` or `/` break the Properties parser). First-ever build fixes were
real: Kotlin 2.0 Compose plugin, missing gradle.properties (AndroidX),
missing launcher icon, never-compiled source errors.

## First run
1. Open ReelVault → enter:
   - Server: `http://<PC-IP>:8756` (same Wi-Fi) or
     `https://<pc>.<tailnet>.ts.net` (Tailscale, recommended)
   - Username/password: from `data\.owner_credentials.txt` (or a user you
     created as owner)
2. Share any public reel from Instagram → ReelVault.

## Privacy notes
- Talks ONLY to the server URL you configure. No analytics, no telemetry,
  no third-party libraries beyond AndroidX/Compose.
- HTTP allowed (`usesCleartextTraffic`) so LAN-IP mode works without TLS;
  switch to your Tailscale HTTPS URL for encryption on the wire.
