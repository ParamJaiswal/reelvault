package com.reelvault.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * MainActivity — login/setup screen + summaries list.
 * - First run: server URL + username + password -> /api/auth/login
 *   (refresh token stored in Android Keystore-encrypted prefs).
 * - Then: GET /summaries list; tapping opens the WebView detail.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { ReelVaultApp() }
    }
}

@Composable
private fun ReelVaultApp() {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var loggedIn by remember {
        mutableStateOf(TokenStore.hasSession(ctx) && TokenStore.serverUrl(ctx).isNotEmpty())
    }
    MaterialTheme(colorScheme = darkColorScheme()) {
        Surface(Modifier.fillMaxSize()) {
            if (loggedIn) SummariesScreen(onLogout = {
                TokenStore.clear(ctx); loggedIn = false
            })
            else LoginScreen(onDone = { loggedIn = true })
        }
    }
}

@Composable
private fun LoginScreen(onDone: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var server by remember { mutableStateOf(TokenStore.serverUrl(ctx)) }
    var user by remember { mutableStateOf(TokenStore.username(ctx)) }
    var pass by remember { mutableStateOf("") }
    var status by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    Column(
        Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.Center
    ) {
        Text("🎬→🧠", fontSize = 40.sp)
        Text("ReelVault", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(20.dp))
        OutlinedTextField(server, { server = it }, Modifier.fillMaxWidth(),
                          label = { Text("Server (https://…ts.net or http://IP:8756)") },
                          singleLine = true)
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(user, { user = it }, Modifier.fillMaxWidth(),
                          label = { Text("Username") }, singleLine = true)
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(pass, { pass = it }, Modifier.fillMaxWidth(),
                          label = { Text("Password") },
                          visualTransformation = PasswordVisualTransformation(),
                          singleLine = true)
        Spacer(Modifier.height(18.dp))
        Button(
            onClick = {
                if (server.isBlank() || user.isBlank() || pass.isBlank()) {
                    status = "Fill in everything"
                    return@Button
                }
                busy = true; status = null
                Repo.login(ctx, server, user, pass) { ok, err ->
                    busy = false
                    status = if (ok) null else err ?: "Login failed"
                    if (ok) onDone()
                }
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth().height(52.dp)
        ) { Text(if (busy) "Signing in…" else "Sign in") }
        status?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, color = Color(0xFFFF8A8A), style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(14.dp))
        Text(
            "Tip — same network as your PC:\nhttp://<PC-IP>:8756\nAnywhere (recommended): https://<pc>.<tailnet>.ts.net",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.outline
        )
    }
}

@Composable
private fun SummariesScreen(onLogout: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var items by remember { mutableStateOf(listOf<SummaryItem>()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    fun load() {
        loading = true; error = null
        Repo.ioLaunch {
            val resp = Repo.getJson(ctx, "/summaries?limit=50")
            if (resp.first == 200) {
                items = parseSummaries(resp.second ?: "[]")
                loading = false
            } else if (resp.first == 401 && Repo.tryRefresh(ctx)) {
                load()
            } else {
                error = "Server unreachable (${resp.first})"; loading = false
            }
        }
    }
    LaunchedEffect(Unit) { load() }

    Scaffold(
        topBar = {
            Row(Modifier.fillMaxWidth().padding(16.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically) {
                Text("🎬 ReelVault", style = MaterialTheme.typography.titleLarge)
                TextButton(onClick = onLogout) { Text("Log out") }
            }
        },
        floatingActionButton = {
            androidx.compose.material3.FloatingActionButton(onClick = { load() }) {
                Text("↻")
            }
        }
    ) { pad ->
        when {
            loading -> Box(Modifier.fillMaxSize().padding(pad),
                           contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            error != null -> Box(Modifier.fillMaxSize().padding(pad),
                                 contentAlignment = Alignment.Center) {
                Text(error!!, color = Color(0xFFFF8A8A))
            }
            else -> {
                androidx.compose.foundation.lazy.LazyColumn(
                    Modifier.padding(pad),
                    contentPadding = PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(items.size) { idx ->
                        val s = items[idx]
                        Card(modifier = Modifier.fillMaxWidth(), onClick = {
                            DetailActivity.open(ctx, s.id, s.title)
                        }) {
                            Column(Modifier.padding(14.dp)) {
                                Text(s.title.ifEmpty { "(untitled)" },
                                     style = MaterialTheme.typography.titleSmall)
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    (s.summary.ifEmpty { s.status }).take(110),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.outline
                                )
                                Spacer(Modifier.height(6.dp))
                                Row(horizontalArrangement =
                                        Arrangement.spacedBy(6.dp)) {
                                    AssistChip(onClick = {},
                                               label = { Text(s.status) })
                                    s.categories.take(2).forEach { c ->
                                        AssistChip(onClick = {},
                                                   label = { Text(c) })
                                    }
                                    if (s.deadline.isNotEmpty()) {
                                        AssistChip(onClick = {},
                                            label = { Text("⏰ ${s.deadline.take(10)}") })
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
