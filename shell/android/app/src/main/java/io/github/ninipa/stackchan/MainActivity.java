package io.github.ninipa.stackchan;

import android.Manifest;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

import org.json.JSONObject;

/**
 * 通用壳：首次启动在本地 www/index.html 填服务器地址 + App Token，
 * 之后加载 &lt;base&gt;/app。APK 里不再烧任何 server URL / token。
 */
public class MainActivity extends BridgeActivity {

    private static final String PREFS = "chan_shell";
    private static final String KEY_BASE = "base";
    private static final String KEY_TOKEN = "token";
    private static final String FALLBACK_LOCAL = "https://localhost";
    private static final String TAILSCALE_PKG = "com.tailscale.ipn";
    private static final String TAILSCALE_APK =
            "https://pkgs.tailscale.com/stable/tailscale-android-universal-1.104.6.apk";
    private static final int REQ_STORAGE = 1001;
    private static final int REQ_NOTIFICATIONS = 1002;

    private static final int INK = Color.rgb(33, 29, 23);
    private static final int MUTED = Color.rgb(124, 114, 97);
    private static final int CREAM = Color.rgb(245, 240, 231);
    private static final int CORAL = Color.rgb(196, 85, 47);
    private static final int LINE = Color.rgb(230, 223, 210);

    private WebView webView;
    private LinearLayout errorView;
    private TextView errorTitle;
    private TextView errorDetail;
    private boolean loadFailed = false;
    private volatile boolean destroyed = false;
    private SharedPreferences prefs;

    /**
     * 下载完成/失败时在应用内提示。Android 13+ 没给通知权限时，DownloadManager
     * 自带的完成通知会被系统隐藏，这个 Toast 是保底的“已完成 + 存在哪”提示。
     */
    private final BroadcastReceiver downloadReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!DownloadManager.ACTION_DOWNLOAD_COMPLETE.equals(intent.getAction())) {
                return;
            }
            long id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L);
            if (id < 0) {
                return;
            }
            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            if (dm == null) {
                return;
            }
            DownloadManager.Query query = new DownloadManager.Query().setFilterById(id);
            try (Cursor cursor = dm.query(query)) {
                if (cursor == null || !cursor.moveToFirst()) {
                    return;
                }
                int status = cursor.getInt(
                        cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                String title = cursor.getString(
                        cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_TITLE));
                if (title == null || title.isEmpty()) {
                    title = "照片";
                }
                if (status == DownloadManager.STATUS_SUCCESSFUL) {
                    Toast.makeText(MainActivity.this,
                            "已保存到系统「下载」：" + title, Toast.LENGTH_LONG).show();
                } else if (status == DownloadManager.STATUS_FAILED) {
                    int reason = cursor.getInt(
                            cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON));
                    Toast.makeText(MainActivity.this,
                            "下载失败（错误 " + reason + "）", Toast.LENGTH_LONG).show();
                }
            } catch (Exception ignored) {
                // 查询失败就不打扰用户；系统通知里仍能看到结果
            }
        }
    };

    private static final class ErrorInfo {
        final String title;
        final String detail;

        ErrorInfo(String title, String detail) {
            this.title = title;
            this.detail = detail;
        }
    }

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Bridge bridge = getBridge();
        if (bridge == null) {
            return;
        }
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        webView = bridge.getWebView();
        // 本地 setup 页用它保存/读取服务器地址与 token
        webView.addJavascriptInterface(new SetupBridge(), "ChanSetup");
        // WebView 自己不处理下载；照片的「保存到手机」交给系统 DownloadManager。
        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) ->
                enqueueDownload(url, contentDisposition, mimeType));

        IntentFilter downloadFilter = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(downloadReceiver, downloadFilter, Context.RECEIVER_EXPORTED);
        } else {
            registerReceiver(downloadReceiver, downloadFilter);
        }

        bridge.setWebViewClient(new BridgeWebViewClient(bridge) {
            /**
             * 通用壳没有固定的 server.url，Capacitor 默认会把“非 localhost”的导航
             * 交给系统浏览器。这里放行已保存的服务器 host 和 localhost，让
             * setup 页的 location.replace(base + "/app") 留在 WebView 里。
             */
            private boolean isAppUrl(Uri uri) {
                if (uri == null) {
                    return false;
                }
                String scheme = uri.getScheme();
                if (!"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme)) {
                    return false;
                }
                String host = uri.getHost();
                if (host == null) {
                    return false;
                }
                if ("localhost".equalsIgnoreCase(host) || "127.0.0.1".equals(host)
                        || "::1".equals(host)) {
                    return true;
                }
                String base = prefs != null ? prefs.getString(KEY_BASE, "") : "";
                try {
                    String baseHost = Uri.parse(base).getHost();
                    return baseHost != null && baseHost.equalsIgnoreCase(host);
                } catch (Exception ignored) {
                    return false;
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (request != null && isAppUrl(request.getUrl())) {
                    return false;
                }
                return super.shouldOverrideUrlLoading(view, request);
            }

            @Override
            @SuppressWarnings("deprecation")
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (isAppUrl(Uri.parse(url))) {
                    return false;
                }
                return super.shouldOverrideUrlLoading(view, url);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (!loadFailed) {
                    hideError();
                    injectBridgeFlags(view);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request.isForMainFrame()) {
                    loadFailed = true;
                    ErrorInfo info = mapWebError(error.getErrorCode());
                    String url = request.getUrl() != null ? request.getUrl().toString() : "";
                    showError(info.title, info.detail + "\n\n" + url + "\n错误 "
                            + error.getErrorCode() + " · " + error.getDescription());
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                            WebResourceResponse response) {
                super.onReceivedHttpError(view, request, response);
                if (request.isForMainFrame() && response.getStatusCode() >= 400) {
                    loadFailed = true;
                    String url = request.getUrl() != null ? request.getUrl().toString() : "";
                    showError("服务返回 HTTP " + response.getStatusCode(),
                            url + "\n检查服务器地址是否指对、容器是否在运行。");
                }
            }
        });

        createErrorView();
        requestNotificationPermission();
    }

    /** 页面加载完成时告诉前端：当前在壳里，下载走原生 DownloadManager。 */
    private void injectBridgeFlags(WebView view) {
        view.evaluateJavascript("window.__CHAN_SHELL__=true;", null);
    }

    /**
     * 照片下载：请求由系统 DownloadManager 发起，不是 WebView。
     * 页面用的是服务端签名的短时 URL，所以这里不需要再带 X-App-Token。
     */
    private void enqueueDownload(String url, String contentDisposition, String mimeType) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q
                && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
            Toast.makeText(this, "请允许存储权限后重试下载", Toast.LENGTH_LONG).show();
            return;
        }
        try {
            String fileName = URLUtil.guessFileName(
                    url, contentDisposition != null ? contentDisposition : "", mimeType);
            if (fileName == null || fileName.isEmpty()) {
                fileName = "chan-photo.jpg";
            }
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setMimeType(mimeType != null ? mimeType : "image/jpeg");
            request.setTitle(fileName);
            request.setDescription("StackChan 照片");
            request.setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(
                    Environment.DIRECTORY_DOWNLOADS, fileName);

            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            if (dm == null) {
                throw new IllegalStateException("DownloadManager 不可用");
            }
            dm.enqueue(request);
            Toast.makeText(this, "已开始下载：" + fileName, Toast.LENGTH_SHORT).show();
        } catch (Exception e) {
            Toast.makeText(this, "下载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIFICATIONS);
        }
    }

    private ErrorInfo mapWebError(int code) {
        switch (code) {
            case WebViewClient.ERROR_HOST_LOOKUP:
                return new ErrorInfo("域名解析失败",
                        "检查服务器地址；用 Tailscale/MagicDNS 的话确认已连接。");
            case WebViewClient.ERROR_CONNECT:
                return new ErrorInfo("连不上服务器",
                        "检查地址、端口，以及服务器是否开机/联网。");
            case WebViewClient.ERROR_TIMEOUT:
                return new ErrorInfo("连接超时",
                        "服务器可能关机/断网，或地址不通。");
            case WebViewClient.ERROR_FAILED_SSL_HANDSHAKE:
                return new ErrorInfo("HTTPS 证书失败",
                        "检查 HTTPS 证书 / Tailscale Serve 配置。");
            default:
                return new ErrorInfo("打不开 StackChan",
                        "WebView 无法加载页面。");
        }
    }

    private void createErrorView() {
        ViewGroup content = findViewById(android.R.id.content);
        if (content == null) {
            return;
        }
        errorView = new LinearLayout(this);
        errorView.setOrientation(LinearLayout.VERTICAL);
        errorView.setGravity(Gravity.CENTER_HORIZONTAL);
        errorView.setBackgroundColor(CREAM);
        errorView.setPadding(dp(28), dp(36), dp(28), dp(28));
        errorView.setClickable(true);
        errorView.setFocusable(true);
        errorView.setVisibility(View.GONE);

        ImageView icon = new ImageView(this);
        icon.setImageResource(R.mipmap.ic_launcher_round);
        LinearLayout.LayoutParams iconLp = new LinearLayout.LayoutParams(dp(76), dp(76));
        iconLp.gravity = Gravity.CENTER_HORIZONTAL;
        errorView.addView(icon, iconLp);

        errorTitle = new TextView(this);
        errorTitle.setTextColor(INK);
        errorTitle.setTextSize(22);
        errorTitle.setTypeface(Typeface.DEFAULT_BOLD);
        errorTitle.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams titleLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        titleLp.topMargin = dp(18);
        errorView.addView(errorTitle, titleLp);

        errorDetail = new TextView(this);
        errorDetail.setTextColor(MUTED);
        errorDetail.setTextSize(13);
        errorDetail.setLineSpacing(dp(3), 1f);
        errorDetail.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams detailLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        detailLp.topMargin = dp(10);
        errorView.addView(errorDetail, detailLp);

        Button retry = makeButton("重试", true);
        retry.setOnClickListener(v -> retry());
        LinearLayout.LayoutParams retryLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(50));
        retryLp.topMargin = dp(26);
        errorView.addView(retry, retryLp);

        Button reconfigure = makeButton("重新配置服务器", false);
        reconfigure.setOnClickListener(v -> reconfigure());
        LinearLayout.LayoutParams reLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(50));
        reLp.topMargin = dp(10);
        errorView.addView(reconfigure, reLp);

        Button tailscale = makeButton("打开 Tailscale", false);
        boolean installed = getPackageManager().getLaunchIntentForPackage(TAILSCALE_PKG) != null;
        tailscale.setText(installed ? "打开 Tailscale" : "安装 Tailscale");
        tailscale.setOnClickListener(v -> openTailscale());
        LinearLayout.LayoutParams tsLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(50));
        tsLp.topMargin = dp(10);
        errorView.addView(tailscale, tsLp);

        Button network = makeButton("打开网络设置", false);
        network.setOnClickListener(v -> {
            try {
                startActivity(new Intent(Settings.ACTION_WIRELESS_SETTINGS));
            } catch (Exception ignored) {
            }
        });
        LinearLayout.LayoutParams netLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(50));
        netLp.topMargin = dp(10);
        errorView.addView(network, netLp);

        content.addView(errorView, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private Button makeButton(String text, boolean primary) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setTextColor(primary ? Color.WHITE : INK);
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(16));
        bg.setColor(primary ? CORAL : CREAM);
        if (!primary) {
            bg.setStroke(dp(1), LINE);
        }
        button.setBackground(bg);
        return button;
    }

    private void retry() {
        loadFailed = false;
        hideError();
        if (webView != null) {
            webView.reload();
        }
    }

    /** 清掉已保存的 server/token，回到本地 setup 页。 */
    private void reconfigure() {
        if (prefs != null) {
            prefs.edit().remove(KEY_BASE).remove(KEY_TOKEN).apply();
        }
        loadFailed = false;
        hideError();
        Bridge bridge = getBridge();
        String local = bridge != null ? bridge.getLocalUrl() : null;
        if (local == null || local.isEmpty()) {
            local = FALLBACK_LOCAL;
        }
        if (webView != null) {
            webView.loadUrl(local + "/index.html?setup=1");
        }
    }

    private void openTailscale() {
        Intent launch = getPackageManager().getLaunchIntentForPackage(TAILSCALE_PKG);
        if (launch != null) {
            startActivity(launch);
            return;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(TAILSCALE_APK)));
        } catch (Exception ignored) {
        }
    }

    private void showError(String title, String detail) {
        if (errorView == null) {
            return;
        }
        errorTitle.setText(title);
        errorDetail.setText(detail);
        errorView.setVisibility(View.VISIBLE);
        errorView.bringToFront();
    }

    private void hideError() {
        if (errorView != null) {
            errorView.setVisibility(View.GONE);
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    public void onBackPressed() {
        if (errorView != null && errorView.getVisibility() == View.VISIBLE) {
            super.onBackPressed();
            return;
        }
        WebView web = getBridge() != null ? getBridge().getWebView() : null;
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    public void onDestroy() {
        destroyed = true;
        try {
            unregisterReceiver(downloadReceiver);
        } catch (IllegalArgumentException ignored) {
            // 未注册（Bridge 为空提前 return）或已注销
        }
        super.onDestroy();
    }

    /** 只暴露 server/token 的读写，不碰其他原生能力。 */
    public class SetupBridge {
        @JavascriptInterface
        public String getConfig() {
            if (prefs == null) {
                return "{}";
            }
            try {
                return new JSONObject()
                        .put("base", prefs.getString(KEY_BASE, ""))
                        .put("token", prefs.getString(KEY_TOKEN, ""))
                        .toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public void save(String base, String token) {
            if (prefs == null) {
                return;
            }
            String b = base == null ? "" : base.trim().replaceAll("/+$", "");
            String t = token == null ? "" : token.trim();
            prefs.edit().putString(KEY_BASE, b).putString(KEY_TOKEN, t).apply();
        }

        @JavascriptInterface
        public void clear() {
            if (prefs != null) {
                prefs.edit().remove(KEY_BASE).remove(KEY_TOKEN).apply();
            }
        }
    }
}
