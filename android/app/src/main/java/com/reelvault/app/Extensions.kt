package com.reelvault.app

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** Small helper so composables can call Repo-style IO without imports. */
fun Repo.ioLaunch(block: suspend () -> Unit) =
    CoroutineScope(Dispatchers.IO).launch { block() }

/** GET helper used by the summaries list. */
fun Repo.getJson(ctx: android.content.Context, path: String):
        Pair<Int, String?> = try {
    val conn = java.net.URL(TokenStore.serverUrl(ctx) + path)
        .openConnection() as java.net.HttpURLConnection
    conn.requestMethod = "GET"
    conn.connectTimeout = 8000
    conn.readTimeout = 30000
    TokenStore.accessToken?.let {
        conn.setRequestProperty("Authorization", "Bearer $it")
    }
    Pair(conn.responseCode,
         (if (conn.responseCode < 400) conn.inputStream else conn.errorStream)
             ?.bufferedReader()?.readText())
} catch (e: Exception) {
    android.util.Log.e("rv.Repo", "get $path", e); Pair(0, null)
}
