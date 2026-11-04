plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "io.github.jeonghyunk.adblock"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.jeonghyunk.adblock"
        minSdk = 26          // 안드로이드 8.0 이상
        targetSdk = 35
        versionCode = 2
        versionName = "1.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

// 앱 자체는 외부 라이브러리 없이 안드로이드 기본 API만 사용합니다. (JUnit 은 테스트 전용)
dependencies {
    testImplementation("junit:junit:4.13.2")
}
