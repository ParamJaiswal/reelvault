/* Onboarding + empty-state polish: first-visit welcome, demo button,
   phone-setup guide, and a smarter empty state for new users. */

const ONBOARD_KEY = "rv_onboarded_v1";

function needsOnboarding() {
  return !localStorage.getItem(ONBOARD_KEY);
}

async function runDemo() {
  toast("Loading demo reel…", "ok");
  try {
    // fetch the bundled sample video and upload it through the real API
    const res = await fetch("/demo/sample_reel.mp4");
    if (!res.ok) throw new Error("Demo asset missing");
    const blob = await res.blob();
    const fd = new FormData();
    fd.append("file", blob, "sample_reel.mp4");
    fd.append("url", "https://www.instagram.com/reel/DEMO000001/");
    fd.append("caption", "Demo reel — Zylker Analytics Data Analyst Intern hiring in Bangalore. Apply by September 15 at zylker.example.com/careers");
    const d = await apiForm("/api/reels/upload", fd);
    localStorage.setItem(ONBOARD_KEY, "1");
    toast("Demo saved! Watch it become knowledge →", "ok");
    watchShared(d.reel_id);
  } catch (e) {
    toast(e.message || "Demo failed", "error");
  }
}
window.runDemo = runDemo;

function welcomeHTML() {
  return `
  <div class="welcome">
    <div class="w-badge">🎬 → 🧠</div>
    <h2>Turn Instagram Reels into searchable knowledge</h2>
    <p class="w-sub">Paste a <b>public</b> Reel link below — or share one from your phone.
    ReelVault downloads it, transcribes the speech, reads on-screen text, and extracts
    <b>jobs, skills, deadlines & links</b> with proof for every fact. Everything runs on
    this PC — nothing goes to the cloud.</p>
    <div class="w-cards">
      <div class="w-card"><span>1️⃣</span><b>Add a Reel</b><small>Paste any public instagram.com/reel/… link above</small></div>
      <div class="w-card"><span>2️⃣</span><b>AI reads it</b><small>Speech → transcript · visuals → OCR · facts extracted</small></div>
      <div class="w-card"><span>3️⃣</span><b>Use it</b><small>Search everything · Radar ranks deadlines · Ask questions</small></div>
    </div>
    <button class="btn primary" style="margin-top:16px" onclick="runDemo()">▶ Try the demo reel</button>
    <p class="w-hint">No account needed. Private/login-walled reels can't be auto-fetched —
    use “＋ File” for those.</p>
  </div>`;
}

function injectWelcome() {
  // replace the generic empty state inside inbox when there are no reels
  const observer = new MutationObserver(() => {
    const empty = $("#view .empty");
    if (empty && state.view === "inbox") {
      empty.outerHTML = welcomeHTML();
      observer.disconnect();
    }
  });
  observer.observe($("#view"), { childList: true, subtree: true });
  setTimeout(() => observer.disconnect(), 3000);
}

// hook into nav(): after every inbox render, swap empty state for welcome
(function () {
  if (needsOnboarding()) {
    const orig = VIEWS.inbox.render;
    let armed = true;
    VIEWS.inbox.render = async function () {
      await orig();
      if (armed && !state.reels.length) { injectWelcome(); armed = false; }
    };
  }
})();

// phone setup card inside Settings (append after render)
const _renderSettings = renderSettings;
renderSettings = async function () {
  await _renderSettings();
  const ip = location.hostname && location.hostname !== "127.0.0.1"
    ? location.hostname : "<your-PC-IP>";
  const div = document.createElement("div");
  div.innerHTML = `
    <div class="sec" style="max-width:720px;margin-top:8px;border:1px solid var(--border);border-radius:14px;padding:16px;background:var(--panel)">
      <h4>📱 Use from your phone</h4>
      <ol style="color:var(--muted);line-height:1.9;margin:.4em 0;padding-left:20px">
        <li>Keep this PC running & connect phone to the same Wi-Fi</li>
        <li>Phone browser → <code>http://${esc(ip)}:8756</code></li>
        <li>Chrome menu → <b>Add to Home screen</b> → install</li>
        <li>In Instagram: <b>Share → ReelVault</b> on any public reel ✨</li>
      </ol>
      <p style="font-size:12px;color:var(--muted)">If it doesn't connect, allow port 8756 through Windows Firewall once.</p>
    </div>`;
  $("#view").appendChild(div.firstChild);
};
