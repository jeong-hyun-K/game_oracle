package com.jeonghyun.onedrivedl.data

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import android.webkit.MimeTypeMap
import java.io.IOException
import java.io.OutputStream

/**
 * 받은 파일을 공용 저장소에 저장한다 (저장소 권한 불필요, Android 10 이상).
 * - 사진·동영상: 사진/OneDrive/...  → Google 포토 '기기의 사진'에 폴더로 나타남
 * - 그 밖의 파일: 다운로드/OneDrive/...
 */
class MediaSaver(context: Context) {

    enum class Kind { IMAGE, VIDEO, OTHER }

    private val resolver = context.applicationContext.contentResolver

    fun resolveMime(name: String, graphMime: String?): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return EXTRA_MIME[ext]
            ?: MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
            ?: graphMime
            ?: "application/octet-stream"
    }

    fun kindOf(mime: String): Kind = when {
        mime.startsWith("image/") -> Kind.IMAGE
        mime.startsWith("video/") -> Kind.VIDEO
        else -> Kind.OTHER
    }

    /**
     * @param subDir "OneDrive" 아래 하위 경로 (예: "여행/2023"), 빈 문자열이면 OneDrive 바로 아래
     * @param write 출력 스트림에 내용을 쓰는 함수. 예외가 나면 만들던 파일은 지워진다.
     */
    fun save(subDir: String, name: String, mime: String, takenAtMs: Long?, write: (OutputStream) -> Unit): Uri {
        val kind = kindOf(mime)
        val collection = when (kind) {
            Kind.IMAGE -> MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
            Kind.VIDEO -> MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
            Kind.OTHER -> MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        }
        val root = if (kind == Kind.OTHER) "Download/OneDrive" else "Pictures/OneDrive"
        val relativePath = if (subDir.isEmpty()) "$root/" else "$root/$subDir/"

        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, sanitize(name))
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
            if (kind != Kind.OTHER && takenAtMs != null) {
                put(MediaStore.MediaColumns.DATE_TAKEN, takenAtMs)
            }
        }
        val uri = resolver.insert(collection, values)
            ?: throw IOException("저장 위치를 만들 수 없습니다: $relativePath$name")
        try {
            resolver.openOutputStream(uri)?.use(write)
                ?: throw IOException("파일을 열 수 없습니다: $name")
            resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
            return uri
        } catch (t: Throwable) {
            runCatching { resolver.delete(uri, null, null) }
            throw t
        }
    }

    /** 이전에 저장한 파일이 아직 남아 있는지 */
    fun exists(uri: Uri): Boolean = runCatching {
        resolver.query(uri, arrayOf(MediaStore.MediaColumns._ID), null, null, null)?.use { it.count > 0 } ?: false
    }.getOrDefault(false)

    companion object {
        private val EXTRA_MIME = mapOf(
            "heic" to "image/heic",
            "heif" to "image/heif",
            "dng" to "image/x-adobe-dng",
            "webp" to "image/webp",
            "mov" to "video/quicktime",
            "mkv" to "video/x-matroska",
            "m4v" to "video/x-m4v",
            "3gp" to "video/3gpp",
        )

        private val ILLEGAL = Regex("[\\\\/:*?\"<>|\\u0000-\\u001F]")

        /** 휴대폰 저장소에서 쓸 수 없는 문자 제거 */
        fun sanitize(name: String): String {
            val cleaned = name.replace(ILLEGAL, "_").trim().trimEnd('.').trimStart('.')
            return cleaned.ifEmpty { "_" }
        }
    }
}
