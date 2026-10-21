package com.jeonghyun.onedrivedl.auth

import android.content.Context
import com.jeonghyun.onedrivedl.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.FormBody
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException

/** 다시 로그인해야 하는 경우 needsLogin = true */
class AuthException(message: String, val needsLogin: Boolean = false) : Exception(message)

data class DeviceCode(
    val deviceCode: String,
    val userCode: String,
    val verificationUri: String,
    val expiresInSec: Int,
    val intervalSec: Int,
)

/**
 * Microsoft 계정 로그인 (OAuth 2.0 장치 코드 흐름).
 * 리디렉션 URI나 앱 서명 해시 없이 Client ID 하나로 동작한다.
 */
class AuthManager private constructor(context: Context) {

    private val prefs = context.applicationContext.getSharedPreferences("auth", Context.MODE_PRIVATE)
    private val refreshLock = Mutex()

    var clientId: String?
        get() = prefs.getString(KEY_CLIENT_ID, null)
        set(value) {
            prefs.edit().putString(KEY_CLIENT_ID, value?.trim()).apply()
            signOut()
        }

    val isSignedIn: Boolean
        get() = prefs.getString(KEY_REFRESH, null) != null

    suspend fun startDeviceLogin(): DeviceCode = withContext(Dispatchers.IO) {
        val form = FormBody.Builder()
            .add("client_id", requireClientId())
            .add("scope", SCOPES)
            .build()
        val json = post("$AUTHORITY/devicecode", form)
        if (json.has("error")) throw AuthException(describe(json))
        DeviceCode(
            deviceCode = json.getString("device_code"),
            userCode = json.getString("user_code"),
            verificationUri = json.optString("verification_uri", "https://microsoft.com/devicelogin"),
            expiresInSec = json.optInt("expires_in", 900),
            intervalSec = json.optInt("interval", 5),
        )
    }

    /** 사용자가 브라우저에서 코드를 입력할 때까지 기다렸다가 토큰을 저장한다. */
    suspend fun waitForLogin(code: DeviceCode) = withContext(Dispatchers.IO) {
        var interval = code.intervalSec.coerceAtLeast(1)
        val deadline = System.currentTimeMillis() + code.expiresInSec * 1000L
        while (System.currentTimeMillis() < deadline) {
            delay(interval * 1000L)
            val form = FormBody.Builder()
                .add("grant_type", "urn:ietf:params:oauth:grant-type:device_code")
                .add("client_id", requireClientId())
                .add("device_code", code.deviceCode)
                .build()
            val json = post("$AUTHORITY/token", form)
            when (json.optString("error")) {
                "" -> {
                    saveTokens(json)
                    return@withContext
                }
                "authorization_pending" -> Unit
                "slow_down" -> interval += 5
                "authorization_declined" -> throw AuthException("로그인이 거부되었습니다.")
                "expired_token" -> throw AuthException("코드가 만료되었습니다. 다시 시도해 주세요.")
                else -> throw AuthException(describe(json))
            }
        }
        throw AuthException("코드가 만료되었습니다. 다시 시도해 주세요.")
    }

    /** 유효한 액세스 토큰을 돌려준다. 만료가 가까우면 자동으로 갱신한다. */
    suspend fun accessToken(forceRefresh: Boolean = false): String = refreshLock.withLock {
        val token = prefs.getString(KEY_ACCESS, null)
        val expiresAt = prefs.getLong(KEY_EXPIRES_AT, 0)
        if (!forceRefresh && token != null && System.currentTimeMillis() < expiresAt - 5 * 60_000) {
            return token
        }
        val refresh = prefs.getString(KEY_REFRESH, null)
            ?: throw AuthException("로그인이 필요합니다.", needsLogin = true)
        withContext(Dispatchers.IO) {
            val form = FormBody.Builder()
                .add("grant_type", "refresh_token")
                .add("client_id", requireClientId())
                .add("refresh_token", refresh)
                .add("scope", SCOPES)
                .build()
            val json = post("$AUTHORITY/token", form)
            if (json.has("error")) {
                if (json.optString("error") == "invalid_grant") signOut()
                throw AuthException("로그인이 만료되었습니다. 다시 로그인해 주세요.", needsLogin = true)
            }
            saveTokens(json)
            json.getString("access_token")
        }
    }

    fun signOut() {
        prefs.edit().remove(KEY_ACCESS).remove(KEY_REFRESH).remove(KEY_EXPIRES_AT).apply()
    }

    private fun saveTokens(json: JSONObject) {
        val editor = prefs.edit()
            .putString(KEY_ACCESS, json.getString("access_token"))
            .putLong(KEY_EXPIRES_AT, System.currentTimeMillis() + json.optLong("expires_in", 3600) * 1000)
        json.optString("refresh_token").takeIf { it.isNotEmpty() }?.let { editor.putString(KEY_REFRESH, it) }
        editor.apply()
    }

    private fun requireClientId(): String =
        clientId?.takeIf { it.isNotBlank() } ?: throw AuthException("Client ID가 설정되지 않았습니다.")

    private fun post(url: String, form: FormBody): JSONObject {
        val request = Request.Builder().url(url).post(form).build()
        Http.client.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            return try {
                JSONObject(body)
            } catch (e: Exception) {
                throw IOException("로그인 서버 응답 오류 (HTTP ${response.code})")
            }
        }
    }

    private fun describe(json: JSONObject): String {
        val desc = json.optString("error_description").lineSequence().firstOrNull().orEmpty()
        return when {
            desc.contains("AADSTS7000218") || desc.contains("AADSTS70002") ->
                "Azure 앱 등록에서 '공용 클라이언트 흐름 허용'을 '예'로 켜 주세요.\n($desc)"
            desc.contains("AADSTS700016") ->
                "Client ID를 찾을 수 없습니다. ID가 맞는지 확인해 주세요.\n($desc)"
            desc.contains("AADSTS50020") || desc.contains("AADSTS50194") ->
                "앱 등록의 '지원되는 계정 유형'에 개인 Microsoft 계정이 포함되어야 합니다.\n($desc)"
            desc.isNotEmpty() -> desc
            else -> json.optString("error", "알 수 없는 오류")
        }
    }

    companion object {
        private const val AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
        private const val SCOPES = "Files.Read offline_access"
        private const val KEY_CLIENT_ID = "client_id"
        private const val KEY_ACCESS = "access_token"
        private const val KEY_REFRESH = "refresh_token"
        private const val KEY_EXPIRES_AT = "expires_at"

        @Volatile private var instance: AuthManager? = null

        fun get(context: Context): AuthManager =
            instance ?: synchronized(this) {
                instance ?: AuthManager(context).also { instance = it }
            }
    }
}
