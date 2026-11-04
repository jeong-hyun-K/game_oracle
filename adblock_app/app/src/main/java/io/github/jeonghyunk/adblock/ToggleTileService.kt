package io.github.jeonghyunk.adblock

import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * 빠른 설정 타일 (상단바를 내려서 누르는 버튼).
 * VPN을 허용하지 않는 앱을 쓸 때 광고 차단을 잠깐 껐다가 다시 켜기 쉽게 합니다.
 */
class ToggleTileService : TileService() {

    companion object {
        /** 차단 상태가 바뀌었을 때 타일 모양을 갱신하도록 시스템에 요청 */
        fun refresh(ctx: Context) {
            try {
                requestListeningState(ctx, ComponentName(ctx, ToggleTileService::class.java))
            } catch (_: Exception) {
            }
        }
    }

    private val handler = Handler(Looper.getMainLooper())

    override fun onStartListening() {
        super.onStartListening()
        updateTile()
    }

    override fun onClick() {
        super.onClick()
        if (AdBlockState.running) {
            AdBlockState.setEnabled(this, false)
            AdBlockVpnService.stop(this)
        } else if (VpnService.prepare(this) != null) {
            // 아직 VPN 허용을 안 했으면 앱을 열어 허용 창을 띄워야 함
            openApp()
            return
        } else {
            try {
                AdBlockState.setEnabled(this, true)
                AdBlockVpnService.start(this)
            } catch (_: Exception) {
                // 일부 기기는 백그라운드에서 서비스 시작을 막음 → 앱을 열어서 켜기
                openApp()
                return
            }
        }
        // 서비스가 켜지거나 꺼지는 데 잠깐 걸리므로 조금 뒤에 다시 갱신
        handler.postDelayed({ updateTile() }, 1500)
        updateTile()
    }

    private fun updateTile() {
        val tile = qsTile ?: return
        val on = AdBlockState.running
        tile.state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = getString(R.string.tile_label)
        if (Build.VERSION.SDK_INT >= 29) {
            tile.subtitle = getString(if (on) R.string.tile_on else R.string.tile_off)
        }
        tile.updateTile()
    }

    private fun openApp() {
        val intent = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(
                PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE)
            )
        } else {
            @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated")
            startActivityAndCollapse(intent)
        }
    }
}
