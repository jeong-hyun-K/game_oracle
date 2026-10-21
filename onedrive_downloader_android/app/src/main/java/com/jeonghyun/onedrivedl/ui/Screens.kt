package com.jeonghyun.onedrivedl.ui

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.work.WorkInfo
import com.jeonghyun.onedrivedl.graph.DriveItem
import com.jeonghyun.onedrivedl.ui.MainViewModel.Screen
import com.jeonghyun.onedrivedl.work.DownloadWorker
import com.jeonghyun.onedrivedl.work.DownloadWorker.Companion.formatBytes

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun App(vm: MainViewModel) {
    val snackbar = remember { SnackbarHostState() }
    val message = vm.message
    LaunchedEffect(message) {
        if (message != null) {
            snackbar.showSnackbar(message)
            vm.consumeMessage()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("OneDrive 폴더 다운로더", maxLines = 1) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                    actionIconContentColor = MaterialTheme.colorScheme.onPrimary,
                    navigationIconContentColor = MaterialTheme.colorScheme.onPrimary,
                ),
                navigationIcon = {
                    if (vm.screen == Screen.BROWSE && vm.path.size > 1) {
                        IconButton(onClick = { vm.goUp() }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "상위 폴더")
                        }
                    }
                },
                actions = { if (vm.screen != Screen.SETUP) OverflowMenu(vm) },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (vm.screen) {
                Screen.SETUP -> SetupScreen(vm)
                Screen.LOGIN -> LoginScreen(vm)
                Screen.BROWSE -> BrowseScreen(vm)
            }
        }
    }
}

@Composable
private fun OverflowMenu(vm: MainViewModel) {
    var open by remember { mutableStateOf(false) }
    var confirmReset by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }) { Icon(Icons.Default.MoreVert, contentDescription = "메뉴") }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            if (vm.screen == Screen.BROWSE) {
                DropdownMenuItem(text = { Text("새로 고침") }, onClick = { open = false; vm.load() })
                DropdownMenuItem(text = { Text("다운로드 기록 초기화") }, onClick = { open = false; confirmReset = true })
                DropdownMenuItem(text = { Text("로그아웃") }, onClick = { open = false; vm.signOut() })
            }
            DropdownMenuItem(text = { Text("Client ID 변경") }, onClick = { open = false; vm.editClientId() })
        }
    }
    if (confirmReset) {
        AlertDialog(
            onDismissRequest = { confirmReset = false },
            title = { Text("다운로드 기록 초기화") },
            text = { Text("이미 받은 파일 기록을 지웁니다. 다음에 다운로드하면 같은 파일을 다시 받아 중복이 생길 수 있습니다. 휴대폰에 저장된 파일은 지워지지 않습니다.") },
            confirmButton = { TextButton(onClick = { confirmReset = false; vm.resetRecords() }) { Text("초기화") } },
            dismissButton = { TextButton(onClick = { confirmReset = false }) { Text("취소") } },
        )
    }
}

// ───────────────────────── 1. Client ID 설정 ─────────────────────────

@Composable
private fun SetupScreen(vm: MainViewModel) {
    var text by rememberSaveable { mutableStateOf(vm.currentClientId) }
    val valid = GUID.matches(text.trim())
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("처음 한 번만 설정하면 됩니다", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        Text(
            "OneDrive 폴더를 읽으려면 Microsoft에 등록한 앱의 'Client ID'가 필요합니다. " +
                "무료이며 약 5분 걸립니다.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("등록 방법", fontWeight = FontWeight.Bold)
                Step(1, "PC나 휴대폰 브라우저에서 entra.microsoft.com 접속 → Microsoft 계정으로 로그인")
                Step(2, "앱 등록 → 새 등록")
                Step(3, "이름: 아무거나 (예: 내 OneDrive 다운로더)\n지원되는 계정 유형: '모든 조직 디렉터리의 계정 및 개인 Microsoft 계정' 선택\n리디렉션 URI: 비워 두기 → [등록]")
                Step(4, "왼쪽 메뉴 '인증' → 맨 아래 '공용 클라이언트 흐름 허용'을 '예'로 → [저장]")
                Step(5, "'개요' 화면의 '애플리케이션(클라이언트) ID'를 복사해 아래에 붙여넣기")
            }
        }
        OutlinedTextField(
            value = text,
            onValueChange = { text = it },
            label = { Text("애플리케이션(클라이언트) ID") },
            placeholder = { Text("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx") },
            singleLine = true,
            isError = text.isNotBlank() && !valid,
            supportingText = { if (text.isNotBlank() && !valid) Text("ID 형식이 올바르지 않습니다") },
            modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = { vm.saveClientId(text.trim()) }, enabled = valid, modifier = Modifier.fillMaxWidth()) {
            Text("저장하고 다음으로")
        }
    }
}

@Composable
private fun Step(n: Int, text: String) {
    Row {
        Text("$n.", fontWeight = FontWeight.Bold, modifier = Modifier.width(22.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium)
    }
}

private val GUID = Regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

// ───────────────────────── 2. 로그인 ─────────────────────────

@Composable
private fun LoginScreen(vm: MainViewModel) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val code = vm.deviceCode

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Microsoft 계정 로그인", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)

        if (code == null) {
            Text(
                "아래 버튼을 누르면 로그인 코드가 나옵니다. 앱은 OneDrive 파일을 읽기만 하며, 아무것도 지우거나 바꾸지 않습니다.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Button(onClick = { vm.startLogin() }, enabled = !vm.loginBusy, modifier = Modifier.fillMaxWidth()) {
                Text(if (vm.loginBusy) "코드 받는 중…" else "로그인 코드 받기")
            }
        } else {
            Text("1. 코드를 복사하세요", style = MaterialTheme.typography.bodyLarge)
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = MaterialTheme.shapes.medium,
                modifier = Modifier.clickable { clipboard.setText(AnnotatedString(code.userCode)) },
            ) {
                SelectionContainer {
                    Text(
                        code.userCode,
                        fontSize = 34.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 24.dp, vertical = 14.dp),
                    )
                }
            }
            OutlinedButton(onClick = { clipboard.setText(AnnotatedString(code.userCode)) }) { Text("코드 복사") }

            Text("2. 로그인 페이지에서 코드를 붙여넣고 Microsoft 계정으로 로그인하세요", style = MaterialTheme.typography.bodyLarge)
            Button(
                onClick = {
                    clipboard.setText(AnnotatedString(code.userCode))
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(code.verificationUri)))
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("로그인 페이지 열기 (코드 자동 복사)") }

            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.padding(end = 10.dp).width(20.dp).height(20.dp), strokeWidth = 2.dp)
                Text("3. 로그인을 마치고 이 앱으로 돌아오면 자동으로 넘어갑니다", style = MaterialTheme.typography.bodyMedium)
            }
            TextButton(onClick = { vm.startLogin() }) { Text("새 코드 받기") }
        }

        vm.loginError?.let { ErrorText(it) }
    }
}

// ───────────────────────── 3. 폴더 탐색 & 다운로드 ─────────────────────────

@Composable
private fun BrowseScreen(vm: MainViewModel) {
    val context = LocalContext.current
    val work by vm.work.collectAsStateWithLifecycle()
    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        vm.startDownload()
    }
    var confirm by remember { mutableStateOf(false) }

    BackHandler(enabled = vm.path.size > 1) { vm.goUp() }

    Column(Modifier.fillMaxSize()) {
        Breadcrumbs(vm)
        HorizontalDivider()

        Box(Modifier.weight(1f).fillMaxWidth()) {
            when {
                vm.loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                vm.browseError != null -> Column(
                    Modifier.align(Alignment.Center).padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    ErrorText(vm.browseError!!)
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = { vm.load() }) {
                        Icon(Icons.Default.Refresh, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                        Text("다시 시도")
                    }
                }
                vm.items.isEmpty() -> Text("빈 폴더입니다", Modifier.align(Alignment.Center))
                else -> LazyColumn(Modifier.fillMaxSize()) {
                    items(vm.items, key = { it.id }) { item -> ItemRow(item) { vm.openFolder(item) } }
                }
            }
        }

        WorkCard(work, vm)

        Surface(tonalElevation = 3.dp) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
                OptionRow("사진·동영상만 받기", vm.mediaOnly, vm::updateMediaOnly)
                OptionRow("사진·동영상을 한 폴더에 모으기 (Google 포토 백업 설정이 쉬워짐)", vm.flatten, vm::updateFlatten)
                OptionRow("Wi-Fi에서만 받기", vm.wifiOnly, vm::updateWifiOnly)
                Button(
                    onClick = { confirm = true },
                    enabled = work?.state?.isFinished != false,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("'${vm.path.last().name}' 폴더 통째로 다운로드", maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }

    if (confirm) {
        val name = vm.path.last().name
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("다운로드 시작") },
            text = {
                Text(
                    "'$name' 폴더와 그 안의 모든 하위 폴더를 휴대폰에 내려받습니다.\n\n" +
                        "• 사진·동영상 → 사진/OneDrive/\n" +
                        (if (vm.mediaOnly) "" else "• 그 밖의 파일 → 다운로드/OneDrive/\n") +
                        "\n이미 받은 파일은 건너뜁니다.",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirm = false
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                    } else {
                        vm.startDownload()
                    }
                }) { Text("시작") }
            },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("취소") } },
        )
    }
}

@Composable
private fun Breadcrumbs(vm: MainViewModel) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 8.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        vm.path.forEachIndexed { i, crumb ->
            if (i > 0) Text("›", Modifier.padding(horizontal = 2.dp), color = MaterialTheme.colorScheme.outline)
            val last = i == vm.path.lastIndex
            Text(
                crumb.name,
                fontWeight = if (last) FontWeight.Bold else FontWeight.Normal,
                color = if (last) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.primary,
                modifier = Modifier.clickable(enabled = !last) { vm.goTo(i) }.padding(6.dp),
            )
        }
    }
}

@Composable
private fun ItemRow(item: DriveItem, onOpen: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .clickable(enabled = item.isFolder, onClick = onOpen)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(if (item.isFolder) "📁" else fileEmoji(item), fontSize = 22.sp, modifier = Modifier.width(36.dp))
        Column(Modifier.weight(1f)) {
            Text(item.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                if (item.isFolder) "항목 ${item.childCount}개" else formatBytes(item.size),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.outline,
            )
        }
        if (item.isFolder) Text("›", fontSize = 20.sp, color = MaterialTheme.colorScheme.outline)
    }
}

private fun fileEmoji(item: DriveItem): String {
    val mime = item.mimeType.orEmpty()
    return when {
        mime.startsWith("image/") -> "🖼️"
        mime.startsWith("video/") -> "🎬"
        else -> "📄"
    }
}

@Composable
private fun OptionRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable { onChange(!checked) },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = checked, onCheckedChange = onChange)
        Text(label, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun WorkCard(work: WorkInfo?, vm: MainViewModel) {
    if (work == null) return
    val id = work.id.toString()
    if (work.state.isFinished && vm.dismissedWorkId == id) return

    val p = if (work.state.isFinished) work.outputData else work.progress
    val total = p.getInt(DownloadWorker.KEY_TOTAL, 0)
    val done = p.getInt(DownloadWorker.KEY_DONE, 0)
    val skipped = p.getInt(DownloadWorker.KEY_SKIPPED, 0)
    val failed = p.getInt(DownloadWorker.KEY_FAILED, 0)
    val bytesDone = p.getLong(DownloadWorker.KEY_BYTES_DONE, 0)
    val bytesTotal = p.getLong(DownloadWorker.KEY_BYTES_TOTAL, 0)

    Card(
        Modifier.fillMaxWidth().padding(10.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            when (work.state) {
                WorkInfo.State.ENQUEUED, WorkInfo.State.BLOCKED -> {
                    Text("대기 중…", fontWeight = FontWeight.Bold)
                    Text(
                        if (vm.wifiOnly) "Wi-Fi에 연결되면 시작합니다." else "네트워크에 연결되면 시작합니다.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    OutlinedButton(onClick = vm::cancelDownload) { Text("취소") }
                }
                WorkInfo.State.RUNNING -> {
                    if (p.getString(DownloadWorker.KEY_PHASE) == DownloadWorker.PHASE_DOWNLOADING) {
                        Text("다운로드 중  ${done + skipped + failed} / $total", fontWeight = FontWeight.Bold)
                        LinearProgressIndicator(
                            progress = { if (bytesTotal > 0) (bytesDone.toFloat() / bytesTotal).coerceIn(0f, 1f) else 1f },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Text(
                            "${formatBytes(bytesDone)} / ${formatBytes(bytesTotal)} · 건너뜀 $skipped · 실패 $failed",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        p.getString(DownloadWorker.KEY_CURRENT)?.let {
                            Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    } else {
                        Text("파일 목록 확인 중… ${total}개 찾음", fontWeight = FontWeight.Bold)
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                    }
                    Text("앱을 닫아도 계속 받습니다.", style = MaterialTheme.typography.bodySmall)
                    OutlinedButton(onClick = vm::cancelDownload) { Text("중지") }
                }
                WorkInfo.State.SUCCEEDED -> {
                    Text(if (failed == 0) "✅ 다운로드 완료" else "⚠️ 일부 파일 실패", fontWeight = FontWeight.Bold)
                    Text("새로 받음 $done · 건너뜀(이미 받음) $skipped · 실패 $failed · ${formatBytes(bytesDone)}")
                    if (failed > 0) {
                        p.getString(DownloadWorker.KEY_FAILED_NAMES)?.takeIf { it.isNotEmpty() }?.let {
                            Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 6, overflow = TextOverflow.Ellipsis)
                        }
                        Text("같은 폴더를 다시 다운로드하면 실패한 파일만 다시 받습니다.", style = MaterialTheme.typography.bodySmall)
                    }
                    Text(
                        "Google 포토에 올리려면: Google 포토 앱 → 컬렉션 → '기기의 사진'에서 OneDrive 폴더를 열고 '백업'을 켜세요.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    TextButton(onClick = { vm.dismissResult(id) }) { Text("닫기") }
                }
                WorkInfo.State.FAILED -> {
                    Text("❌ 다운로드 실패", fontWeight = FontWeight.Bold)
                    Text(p.getString(DownloadWorker.KEY_ERROR) ?: "알 수 없는 오류", style = MaterialTheme.typography.bodySmall)
                    if (p.getBoolean(DownloadWorker.KEY_NEEDS_LOGIN, false)) {
                        TextButton(onClick = { vm.dismissResult(id); vm.signOut() }) { Text("다시 로그인") }
                    } else {
                        TextButton(onClick = { vm.dismissResult(id) }) { Text("닫기") }
                    }
                }
                WorkInfo.State.CANCELLED -> {
                    Text("다운로드를 중지했습니다", fontWeight = FontWeight.Bold)
                    Text("다시 시작하면 받은 파일은 건너뛰고 이어서 받습니다.", style = MaterialTheme.typography.bodySmall)
                    TextButton(onClick = { vm.dismissResult(id) }) { Text("닫기") }
                }
            }
        }
    }
}

@Composable
private fun ErrorText(text: String) {
    Text(text, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
}
