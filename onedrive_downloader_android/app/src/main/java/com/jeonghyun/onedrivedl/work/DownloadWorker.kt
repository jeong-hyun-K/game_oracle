package com.jeonghyun.onedrivedl.work

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Environment
import android.os.StatFs
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.jeonghyun.onedrivedl.MainActivity
import com.jeonghyun.onedrivedl.auth.AuthException
import com.jeonghyun.onedrivedl.auth.AuthManager
import com.jeonghyun.onedrivedl.data.DownloadRecords
import com.jeonghyun.onedrivedl.data.MediaSaver
import com.jeonghyun.onedrivedl.graph.DriveItem
import com.jeonghyun.onedrivedl.graph.GraphClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.io.IOException
import java.util.Collections
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * 선택한 OneDrive 폴더를 하위 폴더까지 통째로 휴대폰에 내려받는다.
 * 1단계: 전체 파일 목록 수집 → 2단계: 동시에 3개씩 다운로드
 */
class DownloadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    private data class Entry(val item: DriveItem, val subDir: String, val mime: String)

    private val auth = AuthManager.get(context)
    private val graph = GraphClient(auth)
    private val saver = MediaSaver(context)
    private val records = DownloadRecords(context)
    private val notificationManager =
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private val done = AtomicInteger()
    private val skipped = AtomicInteger()
    private val failed = AtomicInteger()
    private val bytesDone = AtomicLong()
    private val failedNames: MutableList<String> = Collections.synchronizedList(mutableListOf())
    @Volatile private var current = ""

    override suspend fun doWork(): Result {
        val folderId = inputData.getString(KEY_FOLDER_ID)
        val folderName = inputData.getString(KEY_FOLDER_NAME) ?: "OneDrive"
        val mediaOnly = inputData.getBoolean(KEY_MEDIA_ONLY, false)
        val flatten = inputData.getBoolean(KEY_FLATTEN, false)

        createChannel()
        try {
            setForeground(foregroundInfo("파일 목록 확인 중…", null))
        } catch (e: IllegalStateException) {
            // 앱이 백그라운드일 때 재시도되면 포그라운드 전환이 막힐 수 있음 → 알림 없이 계속 진행
        }

        return try {
            // 1단계: 하위 폴더까지 모든 파일 목록 수집
            val baseDir = if (folderId == null) "" else MediaSaver.sanitize(folderName)
            val entries = scan(folderId, baseDir, mediaOnly, flatten)
            if (isStopped) return Result.failure()

            // 이미 받은 파일 제외
            val pending = entries.filter { e ->
                val rec = records.find(e.item.id)
                val alreadyDone = rec != null && rec.size == e.item.size && saver.exists(rec.uri)
                if (alreadyDone) skipped.incrementAndGet()
                !alreadyDone
            }
            val total = entries.size
            val bytesTotal = pending.sumOf { it.item.size }

            // 저장 공간 확인 (여유 200MB 남김)
            val free = StatFs(Environment.getExternalStorageDirectory().path).availableBytes
            if (bytesTotal > free - 200L * 1024 * 1024) {
                return Result.failure(
                    workDataOf(
                        KEY_ERROR to "저장 공간이 부족합니다.\n필요: ${formatBytes(bytesTotal)} / 남은 공간: ${formatBytes(free)}\n" +
                            "공간을 확보하거나 더 작은 폴더를 선택해 주세요.",
                    ),
                )
            }

            // 2단계: 다운로드
            coroutineScope {
                val reporter = launch {
                    while (true) {
                        report(PHASE_DOWNLOADING, total, bytesTotal)
                        delay(1000)
                    }
                }
                val semaphore = Semaphore(PARALLEL)
                val jobs = pending.map { entry ->
                    launch {
                        semaphore.withPermit {
                            if (!isStopped) downloadWithRetry(entry)
                        }
                    }
                }
                jobs.forEach { it.join() }
                reporter.cancel()
            }
            report(PHASE_DOWNLOADING, total, bytesTotal)

            Result.success(summary(total))
        } catch (e: AuthException) {
            Result.failure(workDataOf(KEY_ERROR to (e.message ?: "로그인이 필요합니다."), KEY_NEEDS_LOGIN to e.needsLogin))
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            // 네트워크 문제 등: 잠시 후 자동 재시도 (받은 파일은 건너뜀)
            if (runAttemptCount < 3) Result.retry()
            else Result.failure(workDataOf(KEY_ERROR to "네트워크 오류: ${e.message}"))
        } catch (e: Exception) {
            Result.failure(workDataOf(KEY_ERROR to "오류: ${e.message ?: e.javaClass.simpleName}"))
        }
    }

    private suspend fun scan(rootId: String?, baseDir: String, mediaOnly: Boolean, flatten: Boolean): List<Entry> {
        val result = mutableListOf<Entry>()
        val queue = ArrayDeque<Pair<String?, String>>()
        queue.add(rootId to baseDir)
        var folders = 0
        while (queue.isNotEmpty() && !isStopped) {
            val (id, dir) = queue.removeFirst()
            for (child in graph.listChildren(id)) {
                if (child.isFolder) {
                    val sub = MediaSaver.sanitize(child.name)
                    queue.add(child.id to if (dir.isEmpty()) sub else "$dir/$sub")
                } else {
                    val mime = saver.resolveMime(child.name, child.mimeType)
                    val isMedia = saver.kindOf(mime) != MediaSaver.Kind.OTHER
                    if (mediaOnly && !isMedia) continue
                    // '한 폴더에 모으기'는 사진·동영상에만 적용
                    val target = if (flatten && isMedia) baseDir else dir
                    result.add(Entry(child, target, mime))
                }
            }
            folders++
            current = dir
            setProgress(workDataOf(KEY_PHASE to PHASE_SCANNING, KEY_TOTAL to result.size, KEY_FOLDERS to folders))
            notificationManager.notify(
                NOTIFICATION_ID,
                buildNotification("파일 목록 확인 중… ${result.size}개 (폴더 ${folders}개)", null),
            )
        }
        return result
    }

    private suspend fun downloadWithRetry(entry: Entry) {
        current = entry.item.name
        var attempt = 0
        while (true) {
            attempt++
            var written = 0L
            try {
                val uri = graph.openContent(entry.item.id).use { response ->
                    val input = response.body!!.byteStream()
                    saver.save(entry.subDir, entry.item.name, entry.mime, entry.item.takenAtMs) { out ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            if (isStopped) throw CancellationException("중지됨")
                            val n = input.read(buffer)
                            if (n < 0) break
                            out.write(buffer, 0, n)
                            written += n
                            bytesDone.addAndGet(n.toLong())
                        }
                    }
                }
                records.put(entry.item.id, uri, entry.item.size)
                done.incrementAndGet()
                return
            } catch (e: AuthException) {
                throw e
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                bytesDone.addAndGet(-written)
                if (attempt >= 3 || isStopped) {
                    failed.incrementAndGet()
                    if (failedNames.size < 10) failedNames.add("${entry.item.name}: ${e.message}".take(150))
                    return
                }
                delay(2000L * attempt)
            }
        }
    }

    private suspend fun report(phase: String, total: Int, bytesTotal: Long) {
        val data = workDataOf(
            KEY_PHASE to phase,
            KEY_TOTAL to total,
            KEY_DONE to done.get(),
            KEY_SKIPPED to skipped.get(),
            KEY_FAILED to failed.get(),
            KEY_BYTES_DONE to bytesDone.get(),
            KEY_BYTES_TOTAL to bytesTotal,
            KEY_CURRENT to current,
        )
        setProgress(data)
        val finished = done.get() + skipped.get() + failed.get()
        val percent = if (bytesTotal > 0) (bytesDone.get() * 100 / bytesTotal).toInt() else 100
        notificationManager.notify(
            NOTIFICATION_ID,
            buildNotification("$finished / $total 개 · ${formatBytes(bytesDone.get())} / ${formatBytes(bytesTotal)}", percent),
        )
    }

    private fun summary(total: Int) = workDataOf(
        KEY_TOTAL to total,
        KEY_DONE to done.get(),
        KEY_SKIPPED to skipped.get(),
        KEY_FAILED to failed.get(),
        KEY_BYTES_DONE to bytesDone.get(),
        KEY_FAILED_NAMES to failedNames.joinToString("\n"),
    )

    private fun foregroundInfo(text: String, percent: Int?) =
        ForegroundInfo(NOTIFICATION_ID, buildNotification(text, percent), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)

    private fun buildNotification(text: String, percent: Int?) =
        NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("OneDrive 폴더 다운로드 중")
            .setContentText(text)
            .setOnlyAlertOnce(true)
            .setOngoing(true)
            .setProgress(100, percent ?: 0, percent == null)
            .setContentIntent(
                PendingIntent.getActivity(
                    applicationContext, 0,
                    Intent(applicationContext, MainActivity::class.java),
                    PendingIntent.FLAG_IMMUTABLE,
                ),
            )
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel, "중지",
                WorkManager.getInstance(applicationContext).createCancelPendingIntent(id),
            )
            .build()

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL_ID, "다운로드 진행", NotificationManager.IMPORTANCE_LOW)
        notificationManager.createNotificationChannel(channel)
    }

    companion object {
        const val UNIQUE_WORK = "onedrive_folder_download"
        private const val CHANNEL_ID = "download"
        private const val NOTIFICATION_ID = 1
        private const val PARALLEL = 3

        const val KEY_FOLDER_ID = "folder_id"
        const val KEY_FOLDER_NAME = "folder_name"
        const val KEY_MEDIA_ONLY = "media_only"
        const val KEY_FLATTEN = "flatten"

        const val KEY_PHASE = "phase"
        const val KEY_TOTAL = "total"
        const val KEY_FOLDERS = "folders"
        const val KEY_DONE = "done"
        const val KEY_SKIPPED = "skipped"
        const val KEY_FAILED = "failed"
        const val KEY_BYTES_DONE = "bytes_done"
        const val KEY_BYTES_TOTAL = "bytes_total"
        const val KEY_CURRENT = "current"
        const val KEY_FAILED_NAMES = "failed_names"
        const val KEY_ERROR = "error"
        const val KEY_NEEDS_LOGIN = "needs_login"

        const val PHASE_SCANNING = "scanning"
        const val PHASE_DOWNLOADING = "downloading"

        fun start(
            context: Context,
            folderId: String?,
            folderName: String,
            mediaOnly: Boolean,
            flatten: Boolean,
            wifiOnly: Boolean,
        ) {
            val request = OneTimeWorkRequestBuilder<DownloadWorker>()
                .setInputData(
                    workDataOf(
                        KEY_FOLDER_ID to folderId,
                        KEY_FOLDER_NAME to folderName,
                        KEY_MEDIA_ONLY to mediaOnly,
                        KEY_FLATTEN to flatten,
                    ),
                )
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
                        .build(),
                )
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(UNIQUE_WORK, ExistingWorkPolicy.KEEP, request)
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(UNIQUE_WORK)
        }

        fun formatBytes(bytes: Long): String {
            val b = bytes.coerceAtLeast(0).toDouble()
            return when {
                b >= 1L shl 30 -> "%.2f GB".format(b / (1L shl 30))
                b >= 1L shl 20 -> "%.1f MB".format(b / (1L shl 20))
                b >= 1L shl 10 -> "%.0f KB".format(b / (1L shl 10))
                else -> "${bytes.coerceAtLeast(0)} B"
            }
        }
    }
}
