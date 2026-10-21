package com.jeonghyun.onedrivedl.graph

import com.jeonghyun.onedrivedl.Http
import com.jeonghyun.onedrivedl.auth.AuthManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.time.Instant

data class DriveItem(
    val id: String,
    val name: String,
    val size: Long,
    val isFolder: Boolean,
    val childCount: Int,
    val mimeType: String?,
    /** 촬영 시각(없으면 파일 생성 시각), epoch ms */
    val takenAtMs: Long?,
)

class GraphException(message: String) : IOException(message)

/** Microsoft Graph API로 OneDrive를 읽는다. */
class GraphClient(private val auth: AuthManager) {

    /** folderId가 null이면 OneDrive 최상위 폴더 */
    suspend fun listChildren(folderId: String?): List<DriveItem> {
        val items = mutableListOf<DriveItem>()
        var next: HttpUrl? = childrenUrl(folderId)
        while (next != null) {
            val url = next
            val json = withContext(Dispatchers.IO) {
                execute(url).use { response ->
                    JSONObject(response.body!!.string())
                }
            }
            val values = json.optJSONArray("value")
            if (values != null) {
                for (i in 0 until values.length()) {
                    parseItem(values.getJSONObject(i))?.let(items::add)
                }
            }
            next = json.optString("@odata.nextLink").takeIf { it.isNotEmpty() }?.toHttpUrl()
        }
        return items
    }

    /** 파일 내용 스트림. 호출한 쪽에서 Response를 닫아야 한다. */
    suspend fun openContent(itemId: String): Response = withContext(Dispatchers.IO) {
        val url = BASE.newBuilder()
            .addPathSegments("me/drive/items")
            .addPathSegment(itemId)
            .addPathSegment("content")
            .build()
        execute(url)
    }

    private fun childrenUrl(folderId: String?): HttpUrl {
        val builder = BASE.newBuilder()
        if (folderId == null) {
            builder.addPathSegments("me/drive/root/children")
        } else {
            builder.addPathSegments("me/drive/items").addPathSegment(folderId).addPathSegment("children")
        }
        return builder
            .addQueryParameter("\$select", "id,name,size,folder,file,photo,fileSystemInfo")
            .addQueryParameter("\$top", "200")
            .build()
    }

    /** 인증·재시도(429/5xx)를 처리하고 성공한 응답만 돌려준다. */
    private suspend fun execute(url: HttpUrl): Response {
        var attempt = 0
        var forceRefresh = false
        while (true) {
            attempt++
            val token = auth.accessToken(forceRefresh)
            val request = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
            val response = try {
                Http.client.newCall(request).execute()
            } catch (e: IOException) {
                if (attempt >= MAX_ATTEMPTS) throw e
                delay(backoffMs(attempt))
                continue
            }
            if (response.isSuccessful) return response

            val code = response.code
            val retryAfter = response.header("Retry-After")?.toLongOrNull()
            val body = response.body?.string().orEmpty()
            response.close()
            when {
                code == 401 && !forceRefresh -> forceRefresh = true
                (code == 429 || code >= 500) && attempt < MAX_ATTEMPTS ->
                    delay(retryAfter?.times(1000) ?: backoffMs(attempt))
                else -> throw GraphException(errorMessage(code, body))
            }
        }
    }

    private fun backoffMs(attempt: Int) = (1000L shl attempt.coerceAtMost(6))

    private fun errorMessage(code: Int, body: String): String {
        val msg = runCatching { JSONObject(body).getJSONObject("error").getString("message") }.getOrNull()
        return when (code) {
            403 -> "접근 권한이 없습니다 (HTTP 403)" + (msg?.let { ": $it" } ?: "")
            404 -> "항목을 찾을 수 없습니다 (HTTP 404)"
            else -> "OneDrive 오류 (HTTP $code)" + (msg?.let { ": $it" } ?: "")
        }
    }

    private fun parseItem(o: JSONObject): DriveItem? {
        val folder = o.optJSONObject("folder")
        val file = o.optJSONObject("file")
        // 폴더도 파일도 아닌 항목(OneNote 전자필기장 등)은 건너뛴다
        if (folder == null && file == null) return null
        val taken = o.optJSONObject("photo")?.optString("takenDateTime")
            ?.takeIf { it.isNotEmpty() }
            ?: o.optJSONObject("fileSystemInfo")?.optString("createdDateTime")?.takeIf { it.isNotEmpty() }
        return DriveItem(
            id = o.getString("id"),
            name = o.getString("name"),
            size = o.optLong("size", 0),
            isFolder = folder != null,
            childCount = folder?.optInt("childCount", 0) ?: 0,
            mimeType = file?.optString("mimeType")?.takeIf { it.isNotEmpty() },
            takenAtMs = taken?.let { runCatching { Instant.parse(it).toEpochMilli() }.getOrNull() },
        )
    }

    companion object {
        private val BASE = "https://graph.microsoft.com/v1.0/".toHttpUrl()
        private const val MAX_ATTEMPTS = 5
    }
}
