/* Web Push client: registers SW, subscribes to push after permission,
   exposes window.enablePush(). Loaded on all pages. */
(async function () {
  const PUBLIC_KEY_URL = "/api/push/public-key";
  const SUB_URL = "/api/push/subscribe";

  function b64ToUint8(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
  }

  async function subscribe(token) {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
    try {
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        const { publicKey } = await fetch(PUBLIC_KEY_URL).then(r => r.json());
        if (!publicKey) return false;
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: b64ToUint8(publicKey),
        });
      }
      const json = sub.toJSON();
      await fetch(SUB_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json",
                   Authorization: `Bearer ${token}` },
        body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys }),
      });
      console.info("[push] subscribed");
      return true;
    } catch (e) {
      console.warn("[push] failed", e);
      return false;
    }
  }

  // ask once per browser after the user has interacted with the app
  document.addEventListener("click", async function once() {
    document.removeEventListener("click", once);
    if (!("Notification" in window) || Notification.permission !== "default")
      return;
    try {
      const perm = await Notification.requestPermission();
      if (perm === "granted") {
        const token = localStorage.getItem("rv_token") ||
          (document.cookie.match(/rv_token=([^;]+)/) || [])[1] || "";
        if (token && location.protocol === "https:") {
          const ok = await subscribe(token);
          if (ok) toast?.("🔔 Reminders enabled", "ok");
        }
      }
    } catch {}
  }, { once: true });

  window.enablePush = subscribe;
})();
