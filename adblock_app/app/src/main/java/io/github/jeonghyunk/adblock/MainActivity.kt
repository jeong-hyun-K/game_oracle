package io.github.jeonghyunk.adblock

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import android.widget.Toast
import java.text.NumberFormat

class MainActivity : Activity() {

    companion object {
        private const val REQ_VPN = 1
        private const val REQ_NOTIFICATION = 2
        private const val REFRESH_MS = 1000L
    }

    private lateinit var statusCard: LinearLayout
    private lateinit var statusText: TextView
    private lateinit var statusSub: TextView
    private lateinit var toggleButton: Button
    private lateinit var blockedCount: TextView
    private lateinit var listSize: TextView
    private lateinit var updateButton: Button
    private lateinit var recentList: ListView
    private lateinit var recentEmpty: TextView
    private lateinit var recentAdapter: ArrayAdapter<String>

    private val handler = Handler(Looper.getMainLooper())
    private val numberFormat = NumberFormat.getIntegerInstance()
    private var shownRecent: List<String> = emptyList()
    private var updating = false

    private val refresher = object : Runnable {
        override fun run() {
            refresh()
            handler.postDelayed(this, REFRESH_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        AdBlockState.load(this)

        statusCard = findViewById(R.id.statusCard)
        statusText = findViewById(R.id.statusText)
        statusSub = findViewById(R.id.statusSub)
        toggleButton = findViewById(R.id.toggleButton)
        blockedCount = findViewById(R.id.blockedCount)
        listSize = findViewById(R.id.listSize)
        updateButton = findViewById(R.id.updateButton)
        recentList = findViewById(R.id.recentList)
        recentEmpty = findViewById(R.id.recentEmpty)

        recentAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, ArrayList())
        recentList.adapter = recentAdapter
        recentList.setOnItemClickListener { _, _, position, _ ->
            recentAdapter.getItem(position)?.let { confirmAllow(it) }
        }

        toggleButton.setOnClickListener { onToggle() }
        updateButton.setOnClickListener { updateBlocklist() }
        findViewById<Button>(R.id.allowButton).setOnClickListener { showAllowlist() }

        // 차단 목록 개수를 보여주기 위해 미리 불러오기 (수십만 줄이라 백그라운드에서)
        Thread { Blocklist.ensureLoaded(applicationContext) }.start()
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresher)
    }

    override fun onPause() {
        handler.removeCallbacks(refresher)
        super.onPause()
    }

    // ───────────────────────── 켜기 / 끄기 ─────────────────────────

    private fun onToggle() {
        if (AdBlockState.running) {
            AdBlockVpnService.stop(this)
            AdBlockState.setEnabled(this, false)
            AdBlockState.running = false
            refresh()
            return
        }
        requestNotificationPermissionOnce()
        // 처음 한 번은 안드로이드가 "VPN 연결 요청" 확인 창을 띄웁니다.
        val prepare = VpnService.prepare(this)
        if (prepare != null) {
            startActivityForResult(prepare, REQ_VPN)
        } else {
            onActivityResult(REQ_VPN, RESULT_OK, null)
        }
    }

    @Deprecated("Activity 기본 API 사용")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_VPN) return
        if (resultCode == RESULT_OK) {
            AdBlockState.setEnabled(this, true)
            AdBlockVpnService.start(this)
        } else {
            Toast.makeText(this, R.string.vpn_denied, Toast.LENGTH_LONG).show()
        }
    }

    private fun requestNotificationPermissionOnce() {
        if (Build.VERSION.SDK_INT < 33) return
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        val prefs = getSharedPreferences("ui", MODE_PRIVATE)
        if (prefs.getBoolean("asked_notification", false)) return
        prefs.edit().putBoolean("asked_notification", true).apply()
        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFICATION)
    }

    // ───────────────────────── 화면 갱신 ─────────────────────────

    private fun refresh() {
        val on = AdBlockState.running
        statusText.setText(if (on) R.string.status_on else R.string.status_off)
        statusSub.setText(if (on) R.string.status_on_sub else R.string.status_off_sub)
        statusCard.setBackgroundResource(if (on) R.drawable.card_on else R.drawable.card_off)
        toggleButton.setText(if (on) R.string.turn_off else R.string.turn_on)
        toggleButton.setBackgroundResource(if (on) R.drawable.button_stop else R.drawable.button_primary)

        blockedCount.text = numberFormat.format(AdBlockState.blockedCount.get())
        val size = Blocklist.size
        listSize.text = if (size == 0) "…" else numberFormat.format(size)

        val recent = AdBlockState.recentBlocked.toList()
        if (recent != shownRecent) {
            shownRecent = recent
            recentAdapter.clear()
            recentAdapter.addAll(recent)
        }
        recentEmpty.visibility = if (recent.isEmpty()) View.VISIBLE else View.GONE
    }

    // ───────────────────────── 차단 목록 업데이트 ─────────────────────────

    private fun updateBlocklist() {
        if (updating) return
        updating = true
        updateButton.isEnabled = false
        updateButton.setText(R.string.updating)
        val app = applicationContext
        Thread {
            val message = try {
                val count = Blocklist.update(app)
                getString(R.string.update_done, numberFormat.format(count))
            } catch (e: Exception) {
                getString(R.string.update_failed, e.message ?: e.javaClass.simpleName)
            }
            runOnUiThread {
                updating = false
                updateButton.isEnabled = true
                updateButton.setText(R.string.update_list)
                Toast.makeText(this, message, Toast.LENGTH_LONG).show()
                refresh()
            }
        }.start()
    }

    // ───────────────────────── 허용 목록 ─────────────────────────

    private fun confirmAllow(domain: String) {
        AlertDialog.Builder(this)
            .setTitle(R.string.allow_confirm_title)
            .setMessage(getString(R.string.allow_confirm_msg, domain))
            .setPositiveButton(R.string.allow) { _, _ ->
                AdBlockState.addAllowed(this, domain)
                Toast.makeText(this, getString(R.string.allowed_toast, domain), Toast.LENGTH_SHORT).show()
                refresh()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun showAllowlist() {
        val items = AdBlockState.allowlist.sorted()
        val builder = AlertDialog.Builder(this)
            .setTitle(R.string.allowlist_title)
            .setPositiveButton(R.string.allowlist_add) { _, _ -> showAddAllowed() }
            .setNegativeButton(R.string.close, null)
        if (items.isEmpty()) {
            builder.setMessage(R.string.allowlist_empty)
        } else {
            builder.setItems(items.toTypedArray()) { _, which -> confirmRemoveAllowed(items[which]) }
        }
        builder.show()
    }

    private fun confirmRemoveAllowed(domain: String) {
        AlertDialog.Builder(this)
            .setTitle(R.string.allowlist_remove_title)
            .setMessage(domain)
            .setPositiveButton(R.string.remove) { _, _ ->
                AdBlockState.removeAllowed(this, domain)
                showAllowlist()
            }
            .setNegativeButton(R.string.cancel) { _, _ -> showAllowlist() }
            .show()
    }

    private fun showAddAllowed() {
        val input = EditText(this).apply {
            hint = getString(R.string.allowlist_add_hint)
            isSingleLine = true
        }
        val pad = (20 * resources.displayMetrics.density).toInt()
        val container = LinearLayout(this).apply {
            setPadding(pad, pad / 2, pad, 0)
            addView(input, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.allowlist_add)
            .setView(container)
            .setPositiveButton(R.string.add) { _, _ ->
                AdBlockState.addAllowed(this, input.text.toString())
                showAllowlist()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }
}
