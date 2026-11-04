package io.github.jeonghyunk.adblock

import android.content.Context
import java.io.BufferedReader
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * 광고 서버 주소 목록.
 *
 * - 기본 목록: 앱에 들어 있는 StevenBlack hosts (assets/hosts.txt, MIT 라이선스)
 * - 추가 목록: 앱 내 전면 광고 SDK 주소 (assets/extra_ads.txt)
 * - 업데이트: "차단 목록 업데이트" 버튼으로 최신 목록을 받아 앱 저장공간에 보관
 */
object Blocklist {
    const val UPDATE_URL = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
    private const val DOWNLOADED_FILE = "hosts_downloaded.txt"
    private const val MIN_VALID_ENTRIES = 1000

    private val IGNORED = setOf(
        "localhost", "localhost.localdomain", "local", "broadcasthost",
        "ip6-localhost", "ip6-loopback", "ip6-localnet", "ip6-mcastprefix",
        "ip6-allnodes", "ip6-allrouters", "ip6-allhosts", "0.0.0.0",
    )

    @Volatile private var domains: Set<String> = emptySet()

    val size: Int get() = domains.size

    /** 기본(또는 업데이트된) 목록과 추가 목록을 읽어 메모리에 올립니다. */
    @Synchronized
    fun load(ctx: Context) {
        val set = HashSet<String>(120_000)
        val downloaded = File(ctx.filesDir, DOWNLOADED_FILE)
        if (downloaded.exists()) {
            downloaded.bufferedReader().use { parseInto(it, set) }
        } else {
            ctx.assets.open("hosts.txt").bufferedReader().use { parseInto(it, set) }
        }
        ctx.assets.open("extra_ads.txt").bufferedReader().use { parseInto(it, set) }
        domains = set
    }

    fun ensureLoaded(ctx: Context) {
        if (domains.isEmpty()) load(ctx)
    }

    /**
     * 최신 목록을 내려받아 교체합니다. 성공하면 주소 개수를, 실패하면 예외를 던집니다.
     * (이 앱 자신의 통신은 VPN에서 제외되어 있으므로 차단 중에도 정상적으로 받아집니다.)
     */
    fun update(ctx: Context): Int {
        val conn = URL(UPDATE_URL).openConnection() as HttpURLConnection
        conn.connectTimeout = 15_000
        conn.readTimeout = 30_000
        try {
            if (conn.responseCode != 200) throw IllegalStateException("서버 응답 ${conn.responseCode}")
            val tmp = File(ctx.filesDir, "$DOWNLOADED_FILE.tmp")
            conn.inputStream.use { input -> tmp.outputStream().use { input.copyTo(it) } }

            val check = HashSet<String>()
            tmp.bufferedReader().use { parseInto(it, check) }
            if (check.size < MIN_VALID_ENTRIES) {
                tmp.delete()
                throw IllegalStateException("받은 목록이 올바르지 않습니다")
            }
            if (!tmp.renameTo(File(ctx.filesDir, DOWNLOADED_FILE))) {
                throw IllegalStateException("파일 저장 실패")
            }
            load(ctx)
            AdBlockState.setListUpdatedAt(ctx, System.currentTimeMillis())
            return size
        } finally {
            conn.disconnect()
        }
    }

    /**
     * 차단 여부 판단. 정확히 일치하거나 상위 주소가 목록에 있으면 차단합니다.
     * 예) 목록에 doubleclick.net 이 있으면 ad.doubleclick.net 도 차단.
     * 허용 목록이 항상 우선합니다.
     */
    fun isBlocked(rawDomain: String): Boolean {
        val domain = normalize(rawDomain) ?: return false
        val allow = AdBlockState.allowlist
        val block = domains
        if (allow.isNotEmpty() && matches(domain, allow)) return false
        return matches(domain, block)
    }

    private fun matches(domain: String, set: Set<String>): Boolean {
        var d = domain
        while (true) {
            if (d in set) return true
            val dot = d.indexOf('.')
            // 최상위 도메인(com, kr 등) 하나만 남으면 중단
            if (dot < 0 || d.indexOf('.', dot + 1) < 0) return false
            d = d.substring(dot + 1)
        }
    }

    fun normalize(input: String): String? {
        var d = input.trim().lowercase()
        d = d.removePrefix("https://").removePrefix("http://")
        d = d.substringBefore('/').trimEnd('.')
        if (d.isEmpty() || d.contains(' ')) return null
        return d
    }

    /** 단위 테스트용: 파일 없이 목록을 직접 지정 */
    internal fun loadFrom(reader: BufferedReader) {
        val set = HashSet<String>()
        parseInto(reader, set)
        domains = set
    }

    /** hosts 형식("0.0.0.0 ads.example.com") 또는 한 줄에 주소 하나 형식을 읽습니다. */
    private fun parseInto(reader: BufferedReader, out: MutableSet<String>) {
        reader.forEachLine { line ->
            val content = line.substringBefore('#').trim()
            if (content.isEmpty()) return@forEachLine
            val parts = content.split(' ', '\t').filter { it.isNotEmpty() }
            val host = when {
                parts.size >= 2 -> parts[1]
                else -> parts[0]
            }.lowercase().trimEnd('.')
            if (host !in IGNORED && host.contains('.')) out.add(host)
        }
    }
}
