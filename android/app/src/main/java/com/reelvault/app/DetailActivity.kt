package com.reelvault.app

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import android.webkit.WebView
import android.webkit.WebViewClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Compact list model. */
data class SummaryItem(
    val id: Int, val title: String, val summary: String, val status: String,
    val categories: List<String>, val deadline: String)

fun parseSummaries(body: String): List<SummaryItem> = try {
    val arr = JSONObject(body).optJSONArray("items") ?: JSONArray()
    (0 until arr.length()).map { i ->
        val o = arr.getJSONObject(i)
        val cats = mutableListOf<String>()
        val cArr = o.optJSONArray("categories")
        if (cArr != null) for (k in 0 until cArr.length()) cats.add(cArr.getString(k))
        SummaryItem(
            id = o.getInt("id"),
            title = o.optString("title", ""),
            summary = o.optString("summary", ""),
            status = o.optString("status", ""),
            categories = cats,
            deadline = o.optString("deadline_iso", "").take(10))
    }
} catch (_: Exception) { emptyList() }

/** Detail view rendered as styled HTML in a WebView (server data injected). */
class DetailActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val reelId = intent.getIntExtra("id", -1)
        val title = intent.getStringExtra("title") ?: ""
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(Modifier.fillMaxSize()) {
                    Column(Modifier.fillMaxSize()) {
                        TextButton(onClick = { finish() }) { Text("← Back") }
                        Text(
                            "  $title",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.padding(bottom = 8.dp)
                        )
                        var html by remember { mutableStateOf("<p>Loading…</p>") }
                        LaunchedEffect(reelId) {
                            CoroutineScope(Dispatchers.IO).launch {
                                val ctx = applicationContext
                                var resp = Repo.getJson(ctx, "/summaries/$reelId")
                                if (resp.first == 401 && Repo.tryRefresh(ctx)) {
                                    resp = Repo.getJson(ctx, "/summaries/$reelId")
                                }
                                html = renderDetail(resp.second ?: "{}",
                                                    resp.first == 200)
                                launch(Dispatchers.Main) { /* set via state */ }
                            }
                        }
                        AndroidView(
                            modifier = Modifier.fillMaxSize(),
                            factory = { c ->
                                WebView(c).apply {
                                    settings.javaScriptEnabled = false
                                    webViewClient = WebViewClient()
                                    loadDataWithBaseURL(null, detailShell(html),
                                                        "text/html", "utf-8", null)
                                    // keep updating when html changes
                                    setBackgroundColor(0xFF0B0E14.toInt())
                                }
                            },
                            update = { wv ->
                                wv.loadDataWithBaseURL(null, detailShell(html),
                                                       "text/html", "utf-8", null)
                            })
                    }
                }
            }
        }
    }

    private fun esc(s: String?) = (s ?: "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    private fun fmtTs(sec: Double?): String {
        if (sec == null || sec < 0) return ""
        val m = (sec / 60).toInt(); val s = (sec % 60).toInt()
        return "%02d:%02d".format(m, s)
    }

    private fun renderDetail(body: String, ok: Boolean): String {
        if (!ok) return "<p class='err'>Couldn't load (are you on the right network?)</p>"
        return try {
            val o = JSONObject(body)
            val sb = StringBuilder()
            sb.append("<div class='chip ${o.optString("status")}'>")
                .append(esc(o.optString("status"))).append("</div>")
            if (!o.isNull("confidence"))
                sb.append("<span class='conf'>confidence ")
                    .append((o.getDouble("confidence") * 100).toInt())
                    .append("%</span>")
            sb.append("<h2>Summary</h2><p>").append(esc(o.optString("summary")))
                .append("</p>")
            val kt = o.optJSONArray("key_takeaways")
            if (kt != null && kt.length() > 0) {
                sb.append("<h2>Key takeaways</h2><ul>")
                for (i in 0 until kt.length()) sb.append("<li>").append(esc(kt.getString(i))).append("</li>")
                sb.append("</ul>")
            }
            val facts = o.optJSONArray("facts")
            if (facts != null && facts.length() > 0) {
                sb.append("<h2>Extracted knowledge</h2>")
                for (i in 0 until facts.length()) {
                    val f = facts.getJSONObject(i)
                    sb.append("<div class='fact'><b>").append(esc(f.optString("field")))
                        .append("</b> ").append(esc(f.optString("value")))
                    val q = f.optString("evidence_quote")
                    if (q.isNotEmpty())
                        sb.append("<div class='ev'>“").append(esc(q)).append("” ")
                        .append("<span class='ts'>@").append(fmtTs(f.optDouble("evidence_t_s", -1.0).takeIf { !f.isNull("evidence_t_s") }))
                            .append("</span></div>")
                    sb.append("</div>")
                }
            }
            val ai = o.optJSONArray("action_items")
            if (ai != null && ai.length() > 0) {
                sb.append("<h2>Action items</h2><ul>")
                for (i in 0 until ai.length()) sb.append("<li>").append(esc(ai.getString(i))).append("</li>")
                sb.append("</ul>")
            }
            val src = o.optString("source_url")
            if (src.isNotEmpty())
                sb.append("<p class='src'>source: ").append(esc(src)).append("</p>")
            sb.toString()
        } catch (e: Exception) {
            "<p class='err'>Parse error: ${esc(e.message)}</p>"
        }
    }

    private fun detailShell(inner: String): String = """
<!DOCTYPE html><html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
 body{background:#0b0e14;color:#e8ecf4;font-family:system-ui,sans-serif;
      margin:0;padding:16px;line-height:1.55}
 h2{font-size:15px;text-transform:uppercase;letter-spacing:.08em;
    color:#8b96ab;margin:22px 0 8px}
 p{margin:6px 0}
 ul{padding-left:20px;margin:6px 0}
 li{margin:5px 0}
 .chip{display:inline-block;background:#171d2a;border:1px solid #232c3f;
       border-radius:99px;padding:3px 12px;font-size:12px}
 .chip.completed{border-color:#3ddc97;color:#3ddc97}
 .chip.failed{border-color:#ff6b6b;color:#ff6b6b}
 .conf{font-size:12px;color:#8b96ab;margin-left:8px}
 .fact{background:#121722;border:1px solid #232c3f;border-radius:10px;
       padding:10px 12px;margin:8px 0}
 .ev{color:#9fb0c8;font-size:13px;margin-top:6px;font-style:italic}
 .ts{color:#6ea8fe;font-style:normal;font-size:12px}
 .err{color:#ff8a8a}
 .src{color:#66718a;font-size:12px;word-break:break-all}
</style></head><body>$inner</body></html>"""

    companion object {
        fun open(ctx: Context, id: Int, title: String) {
            ctx.startActivity(Intent(ctx, DetailActivity::class.java)
                .putExtra("id", id).putExtra("title", title))
        }
    }
}
