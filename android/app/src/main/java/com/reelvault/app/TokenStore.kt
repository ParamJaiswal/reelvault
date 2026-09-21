package com.reelvault.app

import android.content.Context
import android.net.Uri
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONObject

/**
 * Secure token storage: refresh token encrypted with an Android Keystore
 * AES-GCM key. The plaintext never touches disk; access tokens live in RAM.
 */
object TokenStore {
    private const val KS_ALIAS = "reelvault_master"
    private const val PREFS = "rv_secure_prefs"
    private const val KEY_REFRESH_ENC = "refresh_enc"
    private const val KEY_IV = "refresh_iv"
    private const val KEY_SERVER = "server_url"
    private const val KEY_USERNAME = "username"

    @Volatile var accessToken: String? = null   // memory only

    fun serverUrl(ctx: Context): String =
        ctx.getSharedPreferences(PREFS, 0).getString(KEY_SERVER, "") ?: ""

    fun setServerUrl(ctx: Context, url: String) {
        ctx.getSharedPreferences(PREFS, 0).edit()
            .putString(KEY_SERVER, url.trimEnd('/')).apply()
    }

    fun username(ctx: Context): String =
        ctx.getSharedPreferences(PREFS, 0).getString(KEY_USERNAME, "") ?: ""

    /** Returns true if we have a stored session (can try silent refresh). */
    fun hasSession(ctx: Context): Boolean =
        ctx.getSharedPreferences(PREFS, 0).contains(KEY_REFRESH_ENC)

    // ---- keystore ------------------------------------------------------
    private fun masterKey(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(KS_ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(
            KS_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build())
        return gen.generateKey()
    }

    // ---- refresh token at rest ----------------------------------------
    fun saveRefreshToken(ctx: Context, refreshToken: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, masterKey())
        val enc = cipher.doFinal(refreshToken.toByteArray())
        ctx.getSharedPreferences(PREFS, 0).edit()
            .putString(KEY_REFRESH_ENC, Base64.encodeToString(enc, Base64.NO_WRAP))
            .putString(KEY_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .apply()
    }

    fun loadRefreshToken(ctx: Context): String? {
        val prefs = ctx.getSharedPreferences(PREFS, 0) ?: return null
        val enc = prefs.getString(KEY_REFRESH_ENC, null) ?: return null
        val iv = prefs.getString(KEY_IV, null) ?: return null
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, masterKey(),
                        GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)))
            String(cipher.doFinal(Base64.decode(enc, Base64.NO_WRAP)))
        } catch (e: Exception) {
            Log.w("TokenStore", "decrypt failed (key rotated?)", e)
            clear(ctx); null
        }
    }

    fun clear(ctx: Context) {
        accessToken = null
        ctx.getSharedPreferences(PREFS, 0).edit().clear().apply()
    }

    // ---- login response parsing ---------------------------------------
    fun applyLoginResponse(ctx: Context, body: String): Boolean = try {
        val j = JSONObject(body)
        accessToken = j.optString("access_token", "")
        saveRefreshToken(ctx, j.optString("refresh_token", ""))
        true
    } catch (e: Exception) { false }
}
