# OneDrive 폴더 다운로더 (Android)

OneDrive 폴더를 **하위 폴더까지 통째로** 휴대폰에 내려받는 앱입니다.
zip이 아니라 **폴더 구조 그대로** `다운로드/OneDrive/` 아래에 저장됩니다.

- 예: OneDrive `여행/2023/제주` → 휴대폰 `Download/OneDrive/여행/2023/제주`
- 모든 파일(사진·동영상·문서 등)이 원래 폴더에 그대로 들어가며, 사진은 갤러리 앱에서도 보입니다.
- 백그라운드에서 받으며 알림창에 진행률이 보입니다. 앱을 닫아도 계속 받습니다.
- 끊기거나 중지해도 다시 시작하면 **받은 파일은 건너뛰고 이어받습니다**.
- 동시에 3개씩 받고, 실패하면 자동으로 다시 시도합니다.
- OneDrive는 **읽기 전용**으로 접근합니다. 원본을 지우거나 바꾸지 않습니다.
- Android 10 이상에서 동작합니다.

---

## 1. APK 설치

1. 휴대폰 브라우저로 이 저장소의 **Releases** 페이지를 엽니다:
   https://github.com/jeong-hyun-K/game_oracle/releases
2. 가장 위의 `OneDrive 폴더 다운로더 (빌드 N)`에서 `OneDriveFolderDownloader-buildN.apk`를 누릅니다.
3. "출처를 알 수 없는 앱 설치" 허용 창이 뜨면 브라우저에 허용해 주고 설치합니다.

> Releases에 없으면 저장소의 **Actions** 탭에서 최근 빌드를 열고, 하단 Artifacts에서 zip을 받아 압축을 푸세요.

## 2. Client ID 만들기 (처음 한 번, 약 5분)

OneDrive 폴더 목록을 읽으려면 Microsoft에 앱을 등록해 받은 **Client ID**가 필요합니다. 무료입니다.

1. https://entra.microsoft.com 에 접속해 Microsoft 계정(OneDrive 계정)으로 로그인합니다.
   - 처음이면 Azure 무료 계정 가입(전화번호 인증 등)을 요구할 수 있습니다.
2. 왼쪽 메뉴에서 **앱 등록**을 열고 **새 등록**을 누릅니다.
   - 메뉴가 안 보이면 검색창에 "앱 등록"을 입력하세요.
3. 다음과 같이 입력하고 **[등록]**을 누릅니다.
   - 이름: 아무거나 (예: `내 OneDrive 다운로더`)
   - 지원되는 계정 유형: **모든 조직 디렉터리의 계정 및 개인 Microsoft 계정**
   - 리디렉션 URI: **비워 둡니다**
4. 왼쪽 **인증**(Authentication) 메뉴에서 맨 아래 **공용 클라이언트 흐름 허용**을 **예**로 바꾸고 **[저장]**을 누릅니다.
5. **개요** 화면의 **애플리케이션(클라이언트) ID**(`xxxxxxxx-xxxx-…` 모양)를 복사합니다.

## 3. 사용 방법

1. 앱 첫 화면에 Client ID를 붙여넣고 **[저장하고 다음으로]**를 누릅니다.
2. **[로그인 코드 받기]**를 누르고 **[로그인 페이지 열기]**를 누릅니다.
   - 코드는 자동으로 복사됩니다. 열린 페이지에 붙여넣고 Microsoft 계정으로 로그인한 뒤 앱으로 돌아오세요.
3. 받고 싶은 폴더로 들어가 **['폴더 이름' 폴더 통째로 다운로드]**를 누릅니다.
   - 최상위 화면에서 누르면 OneDrive 전체를 받습니다.
4. **Wi-Fi에서만 받기**(기본 켜짐)를 끄면 모바일 데이터로도 받습니다.

### 저장 위치

`내장 저장공간/Download/OneDrive/<선택한 폴더 이름>/<하위 폴더 구조>`

휴대폰의 **내 파일**(파일 관리자) 앱 → **다운로드** → **OneDrive** 에서 볼 수 있습니다.
최상위 화면에서 받으면 `Download/OneDrive/` 바로 아래에 OneDrive 전체 구조가 그대로 들어갑니다.

## 자주 묻는 질문

- **"공용 클라이언트 흐름 허용" 오류가 나요.** 2-4 단계를 확인하세요.
- **개인 계정으로 로그인이 안 돼요.** 2-3 단계의 계정 유형에 "개인 Microsoft 계정"이 포함되어야 합니다.
- **중간에 멈췄어요.** 같은 폴더를 다시 다운로드하면 이어서 받습니다.
  - Android 15부터는 백그라운드 데이터 작업이 하루 6시간으로 제한됩니다. 아주 큰 폴더는 나눠서 받거나 다음 날 이어서 받으세요.
- **같은 파일을 다시 받고 싶어요.** 메뉴의 **다운로드 기록 초기화**를 누르세요.
- **저장 공간이 부족하다고 나와요.** 시작하기 전에 필요한 용량을 계산해 알려 줍니다. 공간을 비우거나 더 작은 폴더를 고르세요.

## 직접 빌드하기

Android Studio로 `onedrive_downloader_android` 폴더를 열고 실행하거나, 다음 명령을 씁니다.

```bash
cd onedrive_downloader_android
./gradlew assembleRelease
# 결과: app/build/outputs/apk/release/app-release.apk
```

저장소에 push하면 GitHub Actions(`.github/workflows/onedrive-downloader-apk.yml`)가 APK를 빌드해 Releases에 올립니다.

> 서명 키(`app/signing/app.keystore`)는 개인 설치용으로 저장소에 포함되어 있습니다. 같은 키로 서명되어야 기존 앱 위에 업데이트 설치가 됩니다.

## 구성

- Kotlin, Jetpack Compose, WorkManager, OkHttp
- 로그인: Microsoft identity platform 장치 코드 흐름 (`Files.Read offline_access`)
- 파일 읽기: Microsoft Graph API `/me/drive`
- 저장: MediaStore (저장소 권한 불필요)
