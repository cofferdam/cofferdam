/* Minimal service worker: exists so the PWA is installable on iOS/Android.
 *
 * Deliberately NETWORK-ONLY. Caching the shell would let a phone keep serving
 * an old UI after a runtime update or an A/B activation (M5/M6), which would
 * make "activate the candidate and see the change" untrustworthy. When offline
 * support is wanted it must be versioned against the runtime build.
 */
self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
  // No respondWith(): every request goes straight to the network.
});
