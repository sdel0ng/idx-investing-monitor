package com.sdel0ng.idxmonitor;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Menu;
import android.view.MenuItem;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

/**
 * IDX Monitor for Android: a window onto the IDX Investing Monitor running on your own PC.
 * The PC does all the work (data, analysis, alerts); this app only shows it.
 * Needs: PC on, app open, Settings -> Phone access enabled, phone on the same Wi-Fi or Tailscale.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "idx_monitor";
    private static final String KEY_SERVER = "server_url";
    private static final int DEFAULT_PORT = 8766;
    private static final int MENU_RELOAD = 1;
    private static final int MENU_HOME = 2;
    private static final int MENU_SERVER = 3;

    private WebView webView;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        webView = new WebView(this);
        setContentView(webView);
        configureWebView();

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            loadServer();
        }
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setBuiltInZoomControls(false);

        CookieManager.getInstance().setAcceptCookie(true);
        webView.setWebViewClient(new MonitorClient());
    }

    private String savedServer() {
        return prefs.getString(KEY_SERVER, null);
    }

    private void loadServer() {
        String server = savedServer();
        if (TextUtils.isEmpty(server)) {
            showServerDialog();
        } else {
            webView.loadUrl(server);
        }
    }

    /** Accepts "192.168.1.10", "192.168.1.10:8766" or a full http:// address. */
    static String normalizeServer(String input) {
        String value = input == null ? "" : input.trim();
        if (value.isEmpty()) {
            return null;
        }
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://" + value;
        }
        Uri uri = Uri.parse(value);
        String host = uri.getHost();
        if (TextUtils.isEmpty(host)) {
            return null;
        }
        int port = uri.getPort();
        if (port == -1 && "http".equals(uri.getScheme())) {
            port = DEFAULT_PORT;
        }
        String portPart = port == -1 ? "" : ":" + port;
        return uri.getScheme() + "://" + host + portPart + "/";
    }

    private void showServerDialog() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("192.168.1.10:8766");
        String current = savedServer();
        if (current != null) {
            input.setText(current);
        }

        final boolean firstRun = TextUtils.isEmpty(current);
        AlertDialog.Builder builder = new AlertDialog.Builder(this)
                .setTitle("Your PC's address")
                .setMessage("On the PC open IDX Investing Monitor -> Settings -> Phone access and copy the "
                        + "address shown there (home Wi-Fi), or the PC's Tailscale 100.x.x.x address when away.")
                .setView(input)
                .setCancelable(!firstRun)
                .setPositiveButton("Connect", (dialog, which) -> {
                    String server = normalizeServer(input.getText().toString());
                    if (server == null) {
                        Toast.makeText(this, "Address not valid", Toast.LENGTH_LONG).show();
                        showServerDialog();
                        return;
                    }
                    prefs.edit().putString(KEY_SERVER, server).apply();
                    webView.loadUrl(server);
                });
        if (!firstRun) {
            builder.setNegativeButton("Cancel", null);
        }
        builder.show();
    }

    private void showOfflinePage(String reason) {
        String server = savedServer() == null ? "(not set)" : TextUtils.htmlEncode(savedServer());
        String html = "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
                + "<style>body{background:#0a1020;color:#e2e8f0;font-family:sans-serif;padding:28px;line-height:1.5}"
                + "a{display:block;margin:14px 0;padding:14px;border-radius:10px;background:#1e3a5f;color:#fff;"
                + "text-align:center;text-decoration:none;font-weight:bold}small{color:#94a3b8}</style></head><body>"
                + "<h2>Can't reach your PC</h2>"
                + "<p>Address: <b>" + server + "</b><br><small>" + TextUtils.htmlEncode(reason) + "</small></p>"
                + "<p>Check that:<br>1. the PC is on and IDX Investing Monitor is open<br>"
                + "2. Settings &rarr; Phone access is enabled (then the app was restarted)<br>"
                + "3. the phone is on the same Wi-Fi, or Tailscale is connected on both</p>"
                + "<a href='idxapp://reload'>Try again</a>"
                + "<a href='idxapp://setup'>Change PC address</a>"
                + "</body></html>";
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    private boolean isServerUrl(Uri uri) {
        String server = savedServer();
        if (server == null || uri.getHost() == null) {
            return false;
        }
        Uri serverUri = Uri.parse(server);
        return uri.getHost().equalsIgnoreCase(serverUri.getHost()) && uri.getPort() == serverUri.getPort();
    }

    private class MonitorClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            if ("idxapp".equals(uri.getScheme())) {
                if ("setup".equals(uri.getHost())) {
                    showServerDialog();
                } else {
                    loadServer();
                }
                return true;
            }
            if (isServerUrl(uri)) {
                return false;
            }
            // Outside links (IDX announcements, news) open in the phone's browser
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
            } catch (ActivityNotFoundException ignored) {
                Toast.makeText(MainActivity.this, "No browser to open this link", Toast.LENGTH_SHORT).show();
            }
            return true;
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) {
                showOfflinePage(String.valueOf(error.getDescription()));
            }
        }
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(Menu.NONE, MENU_RELOAD, Menu.NONE, "Reload");
        menu.add(Menu.NONE, MENU_HOME, Menu.NONE, "Home");
        menu.add(Menu.NONE, MENU_SERVER, Menu.NONE, "PC address");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        switch (item.getItemId()) {
            case MENU_RELOAD:
                String current = webView.getUrl();
                if (current == null || current.startsWith("data:") || current.startsWith("about:")) {
                    loadServer();
                } else {
                    webView.reload();
                }
                return true;
            case MENU_HOME:
                loadServer();
                return true;
            case MENU_SERVER:
                showServerDialog();
                return true;
            default:
                return super.onOptionsItemSelected(item);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush();
        webView.onPause();
        super.onPause();
    }
}
