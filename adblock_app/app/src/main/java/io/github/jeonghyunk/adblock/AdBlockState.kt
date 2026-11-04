package io.github.jeonghyunk.adblock

import android.content.Context
import android.content.SharedPreferences
import java.util.concurrent.ConcurrentLinkedDeque
import java.util.concurrent.atomic.AtomicLong

/**
 * 화면(MainActivity)과 차단 서비스(AdBlockVpnService)가 함께 쓰는 상태.
 * 둘은 같은 프로세스에서 돌아가므로 메모리로 공유하고, 남겨야 하는 값만 저장합니다.
 */
object AdBlockState {
    private const val PREFS = "adblock_prefs"
    private const val KEY_ENABLED = "enabled"
    private const val KEY_BLOCKED_TOTAL = "blocked_total"
    private const val KEY_ALLOWLIST = "allowlist"
    private const val KEY_LIST_UPDATED_AT = "list_updated_at"
    private const val KEY_EXCLUDED_APPS = "excluded_apps"
    private const val RECENT_MAX = 100

    /** 차단 서비스가 실제로 동작 중인지 */
    @Volatile var running: Boolean = false

    /** 지금까지 차단한 광고 요청 수 (누적) */
    val blockedCount = AtomicLong(0)

    /** 최근 차단된 주소 (최신순) */
    val recentBlocked = ConcurrentLinkedDeque<String>()

    /** 사용자가 허용한 주소. 차단 목록보다 우선합니다. */
    @Volatile var allowlist: Set<String> = emptySet()
        private set

    private var loaded = false

    private fun prefs(ctx: Context): SharedPreferences =
        ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Synchronized
    fun load(ctx: Context) {
        if (loaded) return
        val p = prefs(ctx)
        blockedCount.set(p.getLong(KEY_BLOCKED_TOTAL, 0))
        allowlist = p.getStringSet(KEY_ALLOWLIST, emptySet())!!.toSet()
        loaded = true
    }

    fun recordBlocked(domain: String) {
        blockedCount.incrementAndGet()
        recentBlocked.remove(domain)
        recentBlocked.addFirst(domain)
        while (recentBlocked.size > RECENT_MAX) recentBlocked.pollLast()
    }

    fun saveCount(ctx: Context) {
        prefs(ctx).edit().putLong(KEY_BLOCKED_TOTAL, blockedCount.get()).apply()
    }

    fun resetCount(ctx: Context) {
        blockedCount.set(0)
        recentBlocked.clear()
        saveCount(ctx)
    }

    fun isEnabled(ctx: Context) = prefs(ctx).getBoolean(KEY_ENABLED, false)

    fun setEnabled(ctx: Context, enabled: Boolean) {
        prefs(ctx).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    fun addAllowed(ctx: Context, domain: String) {
        val d = Blocklist.normalize(domain) ?: return
        allowlist = allowlist + d
        recentBlocked.remove(d)
        prefs(ctx).edit().putStringSet(KEY_ALLOWLIST, allowlist).apply()
    }

    fun removeAllowed(ctx: Context, domain: String) {
        allowlist = allowlist - domain
        prefs(ctx).edit().putStringSet(KEY_ALLOWLIST, allowlist).apply()
    }

    /** VPN에서 제외할 앱(패키지 이름). 이 앱들은 광고 차단 VPN을 거치지 않습니다. */
    fun excludedApps(ctx: Context): Set<String> =
        prefs(ctx).getStringSet(KEY_EXCLUDED_APPS, emptySet())!!.toSet()

    fun setExcludedApps(ctx: Context, apps: Set<String>) {
        prefs(ctx).edit().putStringSet(KEY_EXCLUDED_APPS, apps).apply()
    }

    fun listUpdatedAt(ctx: Context) = prefs(ctx).getLong(KEY_LIST_UPDATED_AT, 0)

    fun setListUpdatedAt(ctx: Context, time: Long) {
        prefs(ctx).edit().putLong(KEY_LIST_UPDATED_AT, time).apply()
    }
}
