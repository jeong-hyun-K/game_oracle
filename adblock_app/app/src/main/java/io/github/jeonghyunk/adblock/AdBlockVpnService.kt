package io.github.jeonghyunk.adblock

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.system.Os
import android.system.OsConstants
import android.system.StructPollfd
import android.util.Log
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * 휴대폰 안에서만 동작하는 VPN.
 *
 * 외부 VPN 서버로 데이터를 보내지 않습니다. 모든 앱의 DNS 질의(주소 찾기)만 이 서비스로 오게 하고,
 * 광고 서버 주소면 0.0.0.0 으로 답해서 광고가 로드되지 못하게 합니다.
 * 일반 데이터 통신(웹페이지, 영상 등)은 VPN을 거치지 않고 평소처럼 바로 나갑니다.
 */
class AdBlockVpnService : VpnService() {

    companion object {
        private const val TAG = "AdBlockVpn"
        const val ACTION_START = "io.github.jeonghyunk.adblock.START"
        const val ACTION_STOP = "io.github.jeonghyunk.adblock.STOP"
        const val ACTION_RESTART = "io.github.jeonghyunk.adblock.RESTART"

        // VPN 내부에서만 쓰는 가상 주소
        private const val VPN_ADDRESS = "10.111.222.1"
        private const val VPN_DNS = "10.111.222.2"

        // 광고가 아닌 질의를 전달할 실제 DNS 서버 (Cloudflare, Google)
        private val UPSTREAM_DNS = listOf("1.1.1.1", "8.8.8.8")
        private const val UPSTREAM_TIMEOUT_MS = 4000

        private const val CHANNEL_ID = "adblock_status"
        private const val NOTIFICATION_ID = 1

        fun start(ctx: Context) {
            val i = Intent(ctx, AdBlockVpnService::class.java).setAction(ACTION_START)
            ctx.startForegroundService(i)
        }

        fun stop(ctx: Context) {
            val i = Intent(ctx, AdBlockVpnService::class.java).setAction(ACTION_STOP)
            ctx.startService(i)
        }

        /** 제외 앱 목록 등 VPN 설정이 바뀌었을 때, 켜져 있으면 다시 시작해서 적용 */
        fun restartIfRunning(ctx: Context) {
            if (!AdBlockState.running) return
            val i = Intent(ctx, AdBlockVpnService::class.java).setAction(ACTION_RESTART)
            ctx.startService(i)
        }
    }

    @Volatile private var stopping = false
    private var worker: Thread? = null
    @Volatile private var tun: ParcelFileDescriptor? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            shutdown(disable = true)
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_RESTART) {
            stopping = true                               // 지금 VPN을 닫고 새 설정으로 다시 열기
        }
        // ACTION_START, 재부팅 후 시작, 시스템의 "항상 켜진 VPN" 시작 모두 여기로 옵니다.
        startForegroundWithNotification()
        startBlocking()
        return START_STICKY
    }

    /** 사용자가 설정에서 VPN을 끄거나 다른 VPN 앱이 켜졌을 때 */
    override fun onRevoke() {
        shutdown(disable = true)
        super.onRevoke()
    }

    override fun onDestroy() {
        stopping = true
        closeTun()
        AdBlockState.running = false
        AdBlockState.saveCount(this)
        super.onDestroy()
    }

    @Synchronized
    private fun startBlocking() {
        worker?.let { old ->
            if (old.isAlive && !stopping) return          // 이미 동작 중
            old.join(3000)                                // 꺼지는 중이면 끝날 때까지 잠깐 대기
        }
        stopping = false
        AdBlockState.load(this)
        worker = Thread(::runVpn, "AdBlockVpnWorker").also { it.start() }
    }

    private fun shutdown(disable: Boolean) {
        stopping = true
        if (disable) AdBlockState.setEnabled(this, false)
        AdBlockState.running = false
        AdBlockState.saveCount(this)
        closeTun()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        ToggleTileService.refresh(this)
    }

    private fun closeTun() {
        try { tun?.close() } catch (_: IOException) { }
        tun = null
    }

    // ───────────────────────── VPN 동작 ─────────────────────────

    private fun runVpn() {
        var pool: ExecutorService? = null
        var myTun: ParcelFileDescriptor? = null
        try {
            Blocklist.ensureLoaded(this)
            if (stopping) return                          // 목록을 읽는 사이 꺼짐 요청이 온 경우

            val builder = Builder()
                .setSession(getString(R.string.app_name))
                .addAddress(VPN_ADDRESS, 32)
                .addDnsServer(VPN_DNS)
                .addRoute(VPN_DNS, 32)                    // DNS 질의만 VPN으로 들어오게 함
                .setConfigureIntent(mainActivityIntent())
            // IPv6 통신은 VPN을 거치지 않고 그대로 통과
            builder.allowFamily(OsConstants.AF_INET6)
            // 이 앱 자신의 통신(차단 목록 업데이트)과 사용자가 고른 앱은 VPN에서 제외
            // (VPN 사용을 막는 앱도 제외하면 VPN 없이 평소처럼 통신합니다)
            for (pkg in AdBlockState.excludedApps(this) + packageName) {
                try { builder.addDisallowedApplication(pkg) } catch (_: Exception) { }   // 삭제된 앱 등
            }
            // VPN 때문에 와이파이가 "데이터 요금제 네트워크"로 보이지 않도록
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) builder.setMetered(false)

            val pfd = builder.establish()
            if (pfd == null) {
                // VPN 권한이 없음 → 앱을 열어 다시 허용해야 함
                Log.w(TAG, "VPN 권한 없음")
                shutdown(disable = true)
                return
            }
            myTun = pfd
            tun = pfd
            AdBlockState.running = true
            AdBlockState.setEnabled(this, true)
            ToggleTileService.refresh(this)

            pool = Executors.newFixedThreadPool(8)
            val input = FileInputStream(pfd.fileDescriptor)
            val output = FileOutputStream(pfd.fileDescriptor)
            val buffer = ByteArray(32767)
            val pollFd = StructPollfd().apply {
                fd = pfd.fileDescriptor
                events = OsConstants.POLLIN.toShort()
            }

            while (!stopping) {
                // 1초마다 깨어나서 꺼짐 요청이 있었는지 확인
                if (Os.poll(arrayOf(pollFd), 1000) <= 0) continue
                val len = input.read(buffer)
                if (len <= 0) continue
                val query = DnsPacket.parse(buffer, len) ?: continue

                if (Blocklist.isBlocked(query.domain)) {
                    AdBlockState.recordBlocked(query.domain)
                    if (AdBlockState.blockedCount.get() % 20 == 0L) AdBlockState.saveCount(this)
                    write(output, DnsPacket.wrapResponse(query, DnsPacket.blockedAnswer(query)))
                } else {
                    pool.execute { forward(query, output) }
                }
            }
        } catch (e: Exception) {
            if (!stopping) Log.e(TAG, "VPN 오류", e)
        } finally {
            pool?.shutdownNow()
            // 내가 연 VPN만 정리 (그 사이 새로 켜진 VPN은 건드리지 않음)
            try { myTun?.close() } catch (_: IOException) { }
            if (tun === myTun) {
                tun = null
                AdBlockState.running = false
                ToggleTileService.refresh(this)
            }
        }
    }

    /** 광고가 아닌 질의를 실제 DNS 서버로 보내고 받은 답을 앱에게 돌려줍니다. */
    private fun forward(query: DnsPacket.Query, output: FileOutputStream) {
        for (server in UPSTREAM_DNS) {
            if (stopping) return
            try {
                DatagramSocket().use { socket ->
                    protect(socket)                       // 이 소켓은 VPN을 거치지 않게
                    socket.soTimeout = UPSTREAM_TIMEOUT_MS
                    val addr = InetAddress.getByName(server)
                    socket.send(DatagramPacket(query.dns, query.dns.size, addr, 53))
                    val buf = ByteArray(8192)
                    val reply = DatagramPacket(buf, buf.size)
                    socket.receive(reply)
                    write(output, DnsPacket.wrapResponse(query, buf.copyOf(reply.length)))
                }
                return
            } catch (_: SocketTimeoutException) {
                // 다음 DNS 서버로 재시도
            } catch (e: IOException) {
                if (stopping) return
            }
        }
    }

    private fun write(output: FileOutputStream, packet: ByteArray) {
        try {
            synchronized(output) { output.write(packet) }
        } catch (_: IOException) {
        }
    }

    // ───────────────────────── 알림 ─────────────────────────

    private fun mainActivityIntent(): PendingIntent = PendingIntent.getActivity(
        this, 0, Intent(this, MainActivity::class.java),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun startForegroundWithNotification() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW)
                .apply { setShowBadge(false) }
        )
        val stopIntent = PendingIntent.getService(
            this, 1, Intent(this, AdBlockVpnService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.notif_title))
            .setContentText(getString(R.string.notif_text))
            .setContentIntent(mainActivityIntent())
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, getString(R.string.notif_stop), stopIntent).build())
            .build()
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
        } catch (e: Exception) {
            // 알림을 띄우지 못해도 차단 기능 자체는 동작하도록 함
            Log.w(TAG, "알림 표시 실패", e)
        }
    }
}
