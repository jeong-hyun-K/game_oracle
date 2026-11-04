package io.github.jeonghyunk.adblock

import android.app.Activity
import android.content.Intent
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageView
import android.widget.ListView
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast

/**
 * VPN에서 제외할 앱 고르기.
 * "VPN 연결에서는 서비스를 이용할 수 없습니다" 같은 안내를 띄우는 앱을 여기서 체크하면,
 * 그 앱은 광고 차단 VPN을 거치지 않고 평소처럼 통신합니다. (그 앱의 광고는 차단되지 않음)
 */
class ExcludedAppsActivity : Activity() {

    private class AppItem(val pkg: String, val label: String, val icon: Drawable?)

    private lateinit var listView: ListView
    private lateinit var progress: ProgressBar
    private lateinit var countText: TextView

    private var allApps: List<AppItem> = emptyList()
    private var shown: List<AppItem> = emptyList()
    private val excluded = HashSet<String>()
    private var initialExcluded: Set<String> = emptySet()
    private var query = ""

    private val adapter = object : BaseAdapter() {
        override fun getCount() = shown.size
        override fun getItem(position: Int) = shown[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val v = convertView ?: layoutInflater.inflate(R.layout.item_app, parent, false)
            val item = shown[position]
            v.findViewById<ImageView>(R.id.appIcon).setImageDrawable(item.icon)
            v.findViewById<TextView>(R.id.appName).text = item.label
            v.findViewById<TextView>(R.id.appPackage).text = item.pkg
            v.findViewById<CheckBox>(R.id.appCheck).isChecked = item.pkg in excluded
            return v
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_excluded_apps)
        listView = findViewById(R.id.appList)
        progress = findViewById(R.id.loading)
        countText = findViewById(R.id.excludedCount)

        initialExcluded = AdBlockState.excludedApps(this)
        excluded.addAll(initialExcluded)
        updateCount()

        listView.adapter = adapter
        listView.setOnItemClickListener { _, _, position, _ ->
            val pkg = shown[position].pkg
            if (!excluded.remove(pkg)) excluded.add(pkg)
            AdBlockState.setExcludedApps(this, excluded.toSet())
            adapter.notifyDataSetChanged()
            updateCount()
        }

        findViewById<EditText>(R.id.search).addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                query = s?.toString()?.trim().orEmpty()
                applyFilter()
            }
        })

        loadApps()
    }

    /** 화면을 떠날 때, 바뀐 게 있으면 VPN을 다시 시작해서 바로 적용 */
    override fun onStop() {
        super.onStop()
        val now = excluded.toSet()
        if (now != initialExcluded) {
            initialExcluded = now
            if (AdBlockState.running) {
                AdBlockVpnService.restartIfRunning(this)
                Toast.makeText(this, R.string.excluded_applied, Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun loadApps() {
        progress.visibility = View.VISIBLE
        val pm = packageManager
        Thread {
            // 홈 화면에 아이콘이 있는 앱만 (시스템 내부 앱 제외)
            val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val apps = pm.queryIntentActivities(launcher, 0)
                .map { it.activityInfo.applicationInfo }
                .distinctBy { it.packageName }
                .filter { it.packageName != packageName }
                .map { info ->
                    AppItem(
                        pkg = info.packageName,
                        label = info.loadLabel(pm).toString(),
                        icon = try { info.loadIcon(pm) } catch (_: Exception) { null },
                    )
                }
                // 이미 제외한 앱을 위로, 그 다음 이름순
                .sortedWith(compareBy<AppItem>({ it.pkg !in excluded }, { it.label.lowercase() }))
            runOnUiThread {
                allApps = apps
                progress.visibility = View.GONE
                applyFilter()
            }
        }.start()
    }

    private fun applyFilter() {
        shown = if (query.isEmpty()) allApps else allApps.filter {
            it.label.contains(query, ignoreCase = true) || it.pkg.contains(query, ignoreCase = true)
        }
        adapter.notifyDataSetChanged()
    }

    private fun updateCount() {
        countText.text = getString(R.string.excluded_count, excluded.size)
    }
}
