package com.adblockdns.toggle;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.RadioGroup;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {

    private TextView statusTitle;
    private TextView statusDetail;
    private Button toggleButton;
    private Button manualButton;
    private TextView modeText;
    private RadioGroup dnsGroup;
    private View customRow;
    private EditText customHost;
    private View adbSection;

    /** 화면을 다시 그리는 동안 라디오 버튼 이벤트를 무시하기 위한 표시. */
    private boolean updatingUi;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusTitle = findViewById(R.id.statusTitle);
        statusDetail = findViewById(R.id.statusDetail);
        toggleButton = findViewById(R.id.toggleButton);
        manualButton = findViewById(R.id.manualButton);
        modeText = findViewById(R.id.modeText);
        dnsGroup = findViewById(R.id.dnsGroup);
        customRow = findViewById(R.id.customRow);
        customHost = findViewById(R.id.customHost);
        adbSection = findViewById(R.id.adbSection);

        ((TextView) findViewById(R.id.adbCommand)).setText(DnsController.ADB_GRANT_COMMAND);

        toggleButton.setOnClickListener(v -> {
            if (!DnsController.toggle(this)) {
                Toast.makeText(this, R.string.toast_failed, Toast.LENGTH_LONG).show();
            }
            refresh();
        });

        manualButton.setOnClickListener(v -> {
            String host = DnsController.getChosenHost(this);
            copy(host);
            Toast.makeText(this, getString(R.string.toast_copied, host), Toast.LENGTH_LONG).show();
            openNetworkSettings();
        });

        findViewById(R.id.copyCommand).setOnClickListener(v -> {
            copy(DnsController.ADB_GRANT_COMMAND);
            Toast.makeText(this, R.string.toast_command_copied, Toast.LENGTH_SHORT).show();
        });

        dnsGroup.setOnCheckedChangeListener((group, checkedId) -> {
            if (updatingUi) return;
            if (checkedId == R.id.radioAdguard) {
                changeHost(DnsController.HOST_ADGUARD);
            } else if (checkedId == R.id.radioFamily) {
                changeHost(DnsController.HOST_ADGUARD_FAMILY);
            } else {
                customRow.setVisibility(View.VISIBLE);
                customHost.requestFocus();
            }
        });

        findViewById(R.id.applyCustom).setOnClickListener(v -> {
            String host = DnsController.normalizeHost(customHost.getText().toString());
            if (host == null) {
                Toast.makeText(this, R.string.toast_custom_invalid, Toast.LENGTH_LONG).show();
                return;
            }
            changeHost(host);
            Toast.makeText(this, getString(R.string.toast_custom_saved, host), Toast.LENGTH_SHORT).show();
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    /** DNS 주소를 바꾼다. 이미 켜져 있었다면 새 주소로 다시 켠다. */
    private void changeHost(String host) {
        boolean wasOn = DnsController.isEnabled(this);
        DnsController.setChosenHost(this, host);
        if (wasOn && DnsController.hasWritePermission(this)) {
            DnsController.enable(this);
        }
        refresh();
    }

    private void refresh() {
        updatingUi = true;
        boolean permission = DnsController.hasWritePermission(this);
        boolean on = DnsController.isEnabled(this);
        String current = DnsController.getCurrentHost(this);
        String chosen = DnsController.getChosenHost(this);

        statusTitle.setText(on ? R.string.state_on : R.string.state_off);
        statusTitle.setTextColor(on ? 0xFF2E7D32 : 0xFF9E9E9E);
        statusDetail.setText(current == null
                ? getString(R.string.state_current_none)
                : getString(R.string.state_current_host, current));

        toggleButton.setVisibility(permission ? View.VISIBLE : View.GONE);
        toggleButton.setText(on ? R.string.button_turn_off : R.string.button_turn_on);
        manualButton.setVisibility(permission ? View.GONE : View.VISIBLE);
        modeText.setText(permission ? R.string.mode_full : R.string.mode_manual);
        adbSection.setVisibility(permission ? View.GONE : View.VISIBLE);

        if (DnsController.HOST_ADGUARD.equals(chosen)) {
            dnsGroup.check(R.id.radioAdguard);
            customRow.setVisibility(View.GONE);
        } else if (DnsController.HOST_ADGUARD_FAMILY.equals(chosen)) {
            dnsGroup.check(R.id.radioFamily);
            customRow.setVisibility(View.GONE);
        } else {
            dnsGroup.check(R.id.radioCustom);
            customRow.setVisibility(View.VISIBLE);
            if (customHost.getText().length() == 0) customHost.setText(chosen);
        }
        updatingUi = false;
    }

    private void copy(String text) {
        ClipboardManager cm = getSystemService(ClipboardManager.class);
        if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("dns", text));
    }

    /** 개인 DNS 메뉴가 있는 네트워크 설정 화면을 연다. 안 되면 설정 첫 화면. */
    private void openNetworkSettings() {
        try {
            startActivity(new Intent(Settings.ACTION_WIRELESS_SETTINGS));
        } catch (ActivityNotFoundException e) {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        }
    }
}
