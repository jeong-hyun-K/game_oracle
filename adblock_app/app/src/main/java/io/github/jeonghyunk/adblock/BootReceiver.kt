package io.github.jeonghyunk.adblock

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.VpnService

/** 휴대폰 재부팅 또는 앱 업데이트 후, 켜져 있던 상태였다면 차단을 다시 시작합니다. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != Intent.ACTION_MY_PACKAGE_REPLACED
        ) return
        if (!AdBlockState.isEnabled(context)) return
        // VPN 권한이 이미 허용된 경우에만 (아니면 앱을 열어 직접 켜야 함)
        if (VpnService.prepare(context) != null) return
        try {
            AdBlockVpnService.start(context)
        } catch (_: Exception) {
        }
    }
}
