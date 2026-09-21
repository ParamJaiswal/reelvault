package com.reelvault.app

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * ShareIntakeActivity — receives SEND intents (plain-text reel URLs and
 * video, PDF or image attachments) from Instagram / other apps. Sends to
 * the server, shows instant "Saved ✓", then finishes so the user returns
 * to the source app.
 *
 * Also handles VIEW intents for instagram.com/reel/... links (optional).
 */
class ShareIntakeActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val sharedText = extractSharedText(intent)
        val fileUri = extractStreamUri(intent)

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(Modifier.fillMaxSize()) {
                    when {
                        sharedText != null -> SavingState("Link received")
                        fileUri != null -> SavingState("File received")
                        else -> NoShareContent()
                    }
                }
            }
        }

        when {
            sharedText != null -> submitUrl(sharedText)
            fileUri != null -> uploadFile(fileUri)
            else -> finish()
        }
    }

    private fun extractSharedText(intent: Intent?): String? =
        intent?.getStringExtra(Intent.EXTRA_TEXT)?.trim()?.takeIf { it.isNotEmpty() }
            ?: intent?.dataString?.takeIf { it.contains("instagram.com") }

    private fun extractStreamUri(intent: Intent?): android.net.Uri? =
        intent?.getParcelableExtraCompat<android.net.Uri>(Intent.EXTRA_STREAM)

    @Suppress("DEPRECATION")
    private inline fun <reified T : android.os.Parcelable> Intent.getParcelableExtraCompat(key: String): T? =
        if (android.os.Build.VERSION.SDK_INT >= 33)
            getParcelableExtra(key, T::class.java)
        else @Suppress("DEPRECATION") getParcelableExtra(key) as? T

    private var submitted = false

    private fun submitUrl(text: String) {
        if (submitted) return
        submitted = true
        val url = text.lines().firstOrNull { it.startsWith("http") } ?: text
        Repo.sendUrl(this, url) { ok ->
            runOnUiThread {
                Toast.makeText(
                    this,
                    if (ok) "Saved ✓  processing in background"
                    else "Couldn't reach ReelVault (is the PC app running?)",
                    Toast.LENGTH_LONG
                ).show()
                finish()   // straight back to Instagram
            }
        }
    }

    private fun uploadFile(uri: android.net.Uri) {
        if (submitted) return
        submitted = true
        Repo.uploadStream(this, uri) { ok ->
            runOnUiThread {
                Toast.makeText(
                    this,
                    if (ok) "Saved ✓  processing in background"
                    else "Upload failed",
                    Toast.LENGTH_LONG
                ).show()
                finish()
            }
        }
    }
}

@Composable
private fun SavingState(what: String) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("🎬→🧠", style = MaterialTheme.typography.displayMedium)
        Spacer(Modifier.height(12.dp))
        Text("$what — saving…", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(16.dp))
        CircularProgressIndicator()
        Spacer(Modifier.height(24.dp))
        Text(
            "You'll be returned to Instagram now.\nCheck ReelVault later for the summary.",
            style = MaterialTheme.typography.bodySmall
        )
    }
}

@Composable
private fun NoShareContent() {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("Nothing shared", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(8.dp))
        Text("Share a reel from Instagram to save it here.",
             style = MaterialTheme.typography.bodySmall)
    }
}
