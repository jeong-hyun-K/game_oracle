#!/usr/bin/env bash
# Winlator(LGPL-2.1) 원본에 배틀넷 원클릭 패치를 적용해 APK를 빌드합니다.
# 필요: JDK 17+, Android SDK (platforms;android-35, build-tools;35.0.0, ndk;24.0.8215888, cmake;3.22.1)
set -euo pipefail
UPSTREAM=https://github.com/brunodev85/winlator-app
COMMIT=2c33741d50c5daab0521a098be5c969f63b9e887   # 패치를 만든 기준 커밋 (Winlator 11.2)
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${WORK:-$HERE/build}"
: "${ANDROID_HOME:?ANDROID_HOME 을 Android SDK 경로로 지정하세요}"

if [ ! -d "$WORK/.git" ]; then
  git clone "$UPSTREAM" "$WORK"
fi
cd "$WORK"
git fetch --depth 1 origin "$COMMIT"
git checkout -f "$COMMIT"
git clean -fdq
git apply "$HERE/battlenet-one-click.patch"
echo "sdk.dir=$ANDROID_HOME" > local.properties
chmod +x gradlew
./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk "$HERE/Winlator-BattleNet.apk"
echo "완료: $HERE/Winlator-BattleNet.apk"
