package com.jeonghyun.onedrivedl

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color
import androidx.lifecycle.viewmodel.compose.viewModel
import com.jeonghyun.onedrivedl.ui.App
import com.jeonghyun.onedrivedl.ui.MainViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme(
                colorScheme = lightColorScheme(
                    primary = Color(0xFF0F6CBD),
                    onPrimary = Color.White,
                    primaryContainer = Color(0xFFD6E8FA),
                    secondaryContainer = Color(0xFFE6EEF7),
                ),
            ) {
                val vm: MainViewModel = viewModel()
                App(vm)
            }
        }
    }
}
