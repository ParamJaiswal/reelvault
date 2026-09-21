package com.reelvault.app

import android.content.Context
import android.net.Uri
import android.util.Log
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Network layer: plain HttpURLConnection (zero dependencies).
 * - Auth: Authorization: Bearer <access>; on 401 tries refresh once.
 * - All calls run on Dispatchers.IO; callbacks arrive on the calling thread.
 */
object Repo {
    private const val TAG = "rv.Repo"
    private val io = CoroutineScope(Dispatchers.IO)

    fun sendUrl(ctx: Context, url: String, done: (Boolean) -> Unit) =
        io.launch { done(postJson(ctx, "/ingest/url",
                                  """{"url":${jsonStr(url)}}""")) }

    fun uploadStream(ctx: Context, uri: Uri, done: (Boolean) -> Unit) =
        io.launch { done(uploadMultipart(ctx, uri)) }

    fun login(ctx: Context, server: String, user: String, pass: String,
              done: (Boolean, String?) -> Unit) = io.launch {
        try {
            TokenStore.setServerUrl(ctx, server)
            val resp = http("$server/api/auth/login", "POST", json =
                """{"username":${jsonStr(user)},"password":${jsonStr(pass)}}""")
            if (resp.code == 200 &&
                TokenStore.applyLoginResponse(ctx, resp.body ?: "{}")) {
                done(true, null)
            } else done(false, parseError(resp.body))
        } catch (e: Exception) {
            Log.e(TAG, "login", e); done(false, e.message)
        }
    }

    fun ping(ctx: Context, done: (Boolean) -> Unit) = io.launch {
        done(http("${TokenStore.serverUrl(ctx)}/healthz", "GET").code == 200)
    }

    // ---- internals ------------------------------------------------------
    private data class Resp(val code: Int, val body: String?)

    private fun jsonStr(s: String): String =
        "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

    private class Req(val conn: HttpURLConnection) {
        fun auth(token: String?) {
            token?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
        }
    }

    private fun open(urlStr: String, method: String): Req {
        val conn = URL(urlStr).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 8000
        conn.readTimeout = 120000
        return Req(conn)
    }

    private fun read(conn: HttpURLConnection): Resp =
        Resp(conn.responseCode,
             (if (conn.responseCode < 400) conn.inputStream else conn.errorStream)
                 ?.bufferedReader()?.readText())

    private fun http(urlStr: String, method: String, json: String? = null,
                     auth: String? = null): Resp = try {
        val r = open(urlStr, method)
        r.auth(auth)
        if (json != null) {
            r.conn.setRequestProperty("Content-Type", "application/json")
            r.conn.doOutput = true
            OutputStreamWriter(r.conn.outputStream).use { it.write(json) }
        }
        read(r.conn)
    } catch (e: Exception) {
        Log.e(TAG, "http $method failed", e); Resp(0, null)
    }

    /** POST JSON with auto-refresh-once-on-401. */
    private fun postJson(ctx: Context, path: String, json: String): Boolean {
        val base = TokenStore.serverUrl(ctx)
        var resp = http("$base$path", "POST", json, TokenStore.accessToken)
        if (resp.code == 401 && tryRefresh(ctx)) {
            resp = http("$base$path", "POST", json, TokenStore.accessToken)
        }
        return resp.code in 200..299
    }

    private fun queryDisplayName(ctx: Context, uri: Uri): String? = try {
        ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
            val idx = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && c.moveToFirst()) c.getString(idx) else null
        }
    } catch (e: Exception) { null }

    /** Streaming multipart upload of a shared video/PDF/image. */
    private fun uploadMultipart(ctx: Context, uri: Uri): Boolean {
        val base = TokenStore.serverUrl(ctx)
        val boundary = "----rv" + System.currentTimeMillis()
        val bytes = ctx.contentResolver.openInputStream(uri)?.use {
            it.readBytes()
        } ?: return false
        val mime = ctx.contentResolver.getType(uri) ?: "application/octet-stream"
        val name = queryDisplayName(ctx, uri) ?: when {
            mime.startsWith("video/") -> "share.mp4"
            mime == "application/pdf" -> "share.pdf"
            mime.startsWith("image/") -> "share.jpg"
            else -> "share.bin"
        }
        val head = ("--$boundary\r\n"
                + "Content-Disposition: form-data; name=\"file\";"
                + " filename=\"$name\"\r\n"
                + "Content-Type: $mime\r\n\r\n").toByteArray()
        val tail = "\r\n--$boundary--\r\n".toByteArray()
        val body = head + bytes + tail

        fun send(auth: String?): Resp = try {
            val conn = URL("$base/ingest/upload").openConnection()
                    as HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 10000
            conn.readTimeout = 180000
            auth?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
            conn.setRequestProperty("Content-Type",
                                    "multipart/form-data; boundary=$boundary")
            conn.setFixedLengthStreamingMode(body.size)
            conn.outputStream.use { it.write(body) }
            read(conn)
        } catch (e: Exception) {
            Log.e(TAG, "multipart", e); Resp(0, null)
        }

        var resp = send(TokenStore.accessToken)
        if (resp.code == 401 && tryRefresh(ctx)) resp = send(TokenStore.accessToken)
        return resp.code in 200..299
    }

    /** Silent refresh using the Keystore-protected refresh token. */
    fun tryRefresh(ctx: Context): Boolean {
        val rt = TokenStore.loadRefreshToken(ctx) ?: return false
        val base = TokenStore.serverUrl(ctx)
        val resp = http("$base/api/auth/refresh", "POST",
                        """{"refresh_token":${jsonStr(rt)}}""")
        return resp.code == 200 && !resp.body.isNullOrBlank() &&
               TokenStore.applyLoginResponse(ctx, resp.body!!)
    }

    private fun parseError(body: String?): String? = try {
        JSONObject(body ?: "").optString("detail", "").ifEmpty { null }
    } catch (_: Exception) { null }
}
