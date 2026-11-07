package com.adblockdns.toggle;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.provider.Settings;

import java.util.Locale;

/**
 * 안드로이드 "개인 DNS" 설정을 읽고 바꾸는 기능을 모아둔 클래스.
 * VPN을 쓰지 않으므로 VPN을 막는 앱에서도 동작한다.
 */
public final class DnsController {

    public static final String HOST_ADGUARD = "dns.adguard-dns.com";
    public static final String HOST_ADGUARD_FAMILY = "family.adguard-dns.com";

    public static final String ADB_GRANT_COMMAND =
            "adb shell pm grant com.adblockdns.toggle android.permission.WRITE_SECURE_SETTINGS";

    // 시스템 설정 키 (Settings.Global에 숨겨진 키)
    private static final String KEY_MODE = "private_dns_mode";
    private static final String KEY_SPECIFIER = "private_dns_specifier";
    private static final String MODE_HOSTNAME = "hostname";
    private static final String MODE_AUTOMATIC = "opportunistic";

    private static final String PREFS = "settings";
    private static final String PREF_HOST = "host";

    private DnsController() {}

    /** adb로 권한을 받았는지 여부. 받았으면 버튼 한 번으로 켜고 끌 수 있다. */
    public static boolean hasWritePermission(Context context) {
        return context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS)
                == PackageManager.PERMISSION_GRANTED;
    }

    /** 사용자가 고른 광고 차단 DNS 주소. */
    public static String getChosenHost(Context context) {
        return prefs(context).getString(PREF_HOST, HOST_ADGUARD);
    }

    public static void setChosenHost(Context context, String host) {
        prefs(context).edit().putString(PREF_HOST, host).apply();
    }

    /** 지금 고른 광고 차단 DNS가 켜져 있는지. */
    public static boolean isEnabled(Context context) {
        String current = getCurrentHost(context);
        return current != null && current.equalsIgnoreCase(getChosenHost(context));
    }

    /**
     * 현재 개인 DNS에 설정된 주소. 설정이 꺼져 있거나 "자동"이면 null.
     * 시스템 설정을 못 읽는 기기에서는 현재 네트워크 정보로 대신 확인한다.
     */
    public static String getCurrentHost(Context context) {
        try {
            String mode = Settings.Global.getString(context.getContentResolver(), KEY_MODE);
            if (mode != null) {
                if (!MODE_HOSTNAME.equals(mode)) return null;
                return Settings.Global.getString(context.getContentResolver(), KEY_SPECIFIER);
            }
        } catch (SecurityException ignored) {
            // 일부 기기는 숨겨진 설정 읽기를 막는다. 아래 방법으로 확인.
        }
        try {
            ConnectivityManager cm = context.getSystemService(ConnectivityManager.class);
            Network network = cm.getActiveNetwork();
            if (network == null) return null;
            LinkProperties lp = cm.getLinkProperties(network);
            return lp == null ? null : lp.getPrivateDnsServerName();
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** 광고 차단 DNS 켜기. 권한이 없으면 false. */
    public static boolean enable(Context context) {
        if (!hasWritePermission(context)) return false;
        try {
            Settings.Global.putString(context.getContentResolver(), KEY_SPECIFIER, getChosenHost(context));
            Settings.Global.putString(context.getContentResolver(), KEY_MODE, MODE_HOSTNAME);
            return true;
        } catch (SecurityException e) {
            return false;
        }
    }

    /** 광고 차단 끄기. 개인 DNS를 기본값인 "자동"으로 되돌린다. 권한이 없으면 false. */
    public static boolean disable(Context context) {
        if (!hasWritePermission(context)) return false;
        try {
            Settings.Global.putString(context.getContentResolver(), KEY_MODE, MODE_AUTOMATIC);
            return true;
        } catch (SecurityException e) {
            return false;
        }
    }

    /** 켜져 있으면 끄고, 꺼져 있으면 켠다. 권한이 없으면 false. */
    public static boolean toggle(Context context) {
        return isEnabled(context) ? disable(context) : enable(context);
    }

    /**
     * 사용자가 직접 입력한 주소를 정리한다. "https://" 같은 앞부분과 경로를 떼어낸다.
     * 올바르지 않으면 null.
     */
    public static String normalizeHost(String input) {
        if (input == null) return null;
        String host = input.trim().toLowerCase(Locale.ROOT);
        int scheme = host.indexOf("://");
        if (scheme >= 0) host = host.substring(scheme + 3);
        int slash = host.indexOf('/');
        if (slash >= 0) host = host.substring(0, slash);
        if (host.endsWith(".")) host = host.substring(0, host.length() - 1);
        if (host.isEmpty() || !host.contains(".") || !host.matches("[a-z0-9.-]+")) return null;
        return host;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
