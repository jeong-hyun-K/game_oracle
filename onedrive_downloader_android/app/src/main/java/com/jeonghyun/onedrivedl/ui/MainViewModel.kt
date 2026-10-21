package com.jeonghyun.onedrivedl.ui

import android.app.Application
import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.jeonghyun.onedrivedl.auth.AuthException
import com.jeonghyun.onedrivedl.auth.AuthManager
import com.jeonghyun.onedrivedl.auth.DeviceCode
import com.jeonghyun.onedrivedl.data.DownloadRecords
import com.jeonghyun.onedrivedl.graph.DriveItem
import com.jeonghyun.onedrivedl.graph.GraphClient
import com.jeonghyun.onedrivedl.work.DownloadWorker
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainViewModel(app: Application) : AndroidViewModel(app) {

    enum class Screen { SETUP, LOGIN, BROWSE }

    data class Crumb(val id: String?, val name: String)

    private val auth = AuthManager.get(app)
    private val graph = GraphClient(auth)
    private val records = DownloadRecords(app)
    private val settings = app.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var screen by mutableStateOf(
        when {
            auth.clientId.isNullOrBlank() -> Screen.SETUP
            auth.isSignedIn -> Screen.BROWSE
            else -> Screen.LOGIN
        },
    )
        private set

    // 로그인
    var deviceCode by mutableStateOf<DeviceCode?>(null)
        private set
    var loginBusy by mutableStateOf(false)
        private set
    var loginError by mutableStateOf<String?>(null)
        private set
    private var loginJob: Job? = null

    // 폴더 탐색
    var path by mutableStateOf(listOf(Crumb(null, "내 OneDrive")))
        private set
    var items by mutableStateOf<List<DriveItem>>(emptyList())
        private set
    var loading by mutableStateOf(false)
        private set
    var browseError by mutableStateOf<String?>(null)
        private set
    private var loadJob: Job? = null

    // 옵션
    var wifiOnly by mutableStateOf(settings.getBoolean("wifi_only", true))
        private set

    var message by mutableStateOf<String?>(null)
        private set

    /** 진행 중이거나 마지막으로 끝난 다운로드 작업 */
    val work: StateFlow<WorkInfo?> = WorkManager.getInstance(app)
        .getWorkInfosForUniqueWorkFlow(DownloadWorker.UNIQUE_WORK)
        .map { list -> list.firstOrNull { !it.state.isFinished } ?: list.lastOrNull() }
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    var dismissedWorkId by mutableStateOf<String?>(settings.getString("dismissed_work", null))
        private set

    init {
        if (screen == Screen.BROWSE) load()
    }

    val currentClientId: String get() = auth.clientId.orEmpty()

    fun saveClientId(id: String) {
        auth.clientId = id
        deviceCode = null
        loginError = null
        screen = Screen.LOGIN
    }

    fun editClientId() {
        loginJob?.cancel()
        screen = Screen.SETUP
    }

    fun startLogin() {
        loginJob?.cancel()
        loginError = null
        deviceCode = null
        loginBusy = true
        loginJob = viewModelScope.launch {
            try {
                val code = auth.startDeviceLogin()
                deviceCode = code
                auth.waitForLogin(code)
                deviceCode = null
                path = listOf(Crumb(null, "내 OneDrive"))
                screen = Screen.BROWSE
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                deviceCode = null
                loginError = e.message ?: "로그인 중 오류가 발생했습니다."
            } finally {
                loginBusy = false
            }
        }
    }

    fun signOut() {
        auth.signOut()
        items = emptyList()
        path = listOf(Crumb(null, "내 OneDrive"))
        screen = Screen.LOGIN
    }

    fun openFolder(item: DriveItem) {
        path = path + Crumb(item.id, item.name)
        load()
    }

    /** 상위 폴더로. 이미 최상위면 false */
    fun goUp(): Boolean {
        if (path.size <= 1) return false
        path = path.dropLast(1)
        load()
        return true
    }

    fun goTo(index: Int) {
        if (index >= path.size - 1) return
        path = path.take(index + 1)
        load()
    }

    fun load() {
        loadJob?.cancel()
        val folderId = path.last().id
        loading = true
        browseError = null
        loadJob = viewModelScope.launch {
            try {
                val children = graph.listChildren(folderId)
                items = children.sortedWith(compareByDescending<DriveItem> { it.isFolder }.thenBy { it.name.lowercase() })
            } catch (e: CancellationException) {
                throw e
            } catch (e: AuthException) {
                if (e.needsLogin) {
                    loginError = e.message
                    screen = Screen.LOGIN
                } else {
                    browseError = e.message
                }
            } catch (e: Exception) {
                browseError = e.message ?: "폴더를 불러오지 못했습니다."
            } finally {
                loading = false
            }
        }
    }

    fun updateWifiOnly(v: Boolean) {
        wifiOnly = v
        settings.edit().putBoolean("wifi_only", v).apply()
    }

    fun startDownload() {
        val running = work.value?.state?.isFinished == false
        if (running) {
            message = "이미 다운로드가 진행 중입니다."
            return
        }
        val target = path.last()
        DownloadWorker.start(getApplication(), target.id, target.name, wifiOnly)
        message = "'${target.name}' 폴더 다운로드를 시작합니다."
    }

    fun cancelDownload() = DownloadWorker.cancel(getApplication())

    fun dismissResult(id: String) {
        dismissedWorkId = id
        settings.edit().putString("dismissed_work", id).apply()
    }

    fun resetRecords() {
        viewModelScope.launch {
            val n = withContext(Dispatchers.IO) {
                records.count().also { records.clear() }
            }
            message = "다운로드 기록 ${n}건을 지웠습니다. 다음 다운로드 때 모두 다시 받습니다."
        }
    }

    fun consumeMessage() {
        message = null
    }
}
