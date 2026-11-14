# StackChan Android WebView 壳（通用版）

基于 Capacitor 8 的轻量 WebView 壳。**APK 不烧服务器地址/Token**：首次启动在本地 setup 页填写，
之后加载 `<base>/app`。

- 应用名：**StackChan**
- 包名：`io.github.ninipa.stackchan`
- 版本：**1.5**（versionCode 6）
- 产物：`android/app/build/outputs/apk/debug/app-debug.apk`
  或 `android/app/build/outputs/apk/release/app-release.apk`

壳只负责：原生外壳、图标/启动图、返回键、首启配置、照片下载、错误诊断。
页面与数据在服务器的 `chan-behavior` 上，**改 UI 不需要重发 APK**。

## 首次启动

1. 填服务器地址：`http://<LAN_IP>:8100` 或 `https://<machine>.ts.net`
2. 填 App Token：服务器 `selfhost/behavior/.env` 的 `APP_TOKEN`
3. 点「测试连接」→「打开」

配置存在 SharedPreferences + localStorage；下次启动自动打开。地址要和固件的
`CHAN_SERVER_HOST` 一致（设备走 LAN，手机可走 Tailscale）。

## 重新配置

- 原生错误页的「重新配置服务器」会清掉保存的地址/token，回到 setup 页；
- 也可以在 Web 设置页改 server/token（影响页面请求；壳下次仍用保存的 base）；
- 清除 App 数据 = 重新配置。

## 照片下载

- 页面使用服务端签名的短时 URL；`DownloadManager` 不需要 `X-App-Token`，APK 不存 token。
- 文件保存到系统 **「下载」**；完成/失败有 Toast（Android 13+ 没给通知权限也能看到）。
- API < 29 会请求 `WRITE_EXTERNAL_STORAGE`；Android 13+ 会请求 `POST_NOTIFICATIONS`。

## 网络

- **LAN HTTP 可用**：`usesCleartextTraffic="true"` + Capacitor `allowMixedContent`。
- **Tailscale 可选**：填 `https://<machine>.ts.net`，手机装 Tailscale 并保持 VPN；
  Funnel 保持关闭。
- 不要把 8000/8003/8100 端口转发到公网。
- iOS 没有原生壳：Safari 打开同一个地址，**添加到主屏幕**即可（PWA）。

## 构建

前置：JDK 21、Android SDK 36、Node/npm。release 签名可选
`android/keystore.properties`（gitignored）；缺失时回退 debug 签名。

```bash
cd shell
npm install
npx cap sync android

cd android
JAVA_HOME="<JDK 21 路径>" ANDROID_HOME="$HOME/Library/Android/sdk" \
./gradlew assembleDebug
# release: ./gradlew assembleRelease
```

构建后核对：

```bash
APK=app/build/outputs/apk/debug/app-debug.apk
unzip -p "$APK" assets/capacitor.config.json          # 不应有 server.url
unzip -p "$APK" classes.dex | strings | grep -E 'ts\.net|[0-9a-f]{64}|sk-'
# 应无输出
unzip -p "$APK" assets/public/index.html | grep '连接 StackChan'
```

## 发布身份

- 包名：`io.github.ninipa.stackchan`。fork 后自己发布就改成自己的反向域名，
  同时改 `applicationId`、`namespace`、`strings.xml` 的 `package_name`/`custom_url_scheme`、
  Java 包路径、`capacitor.config.json` 的 `appId`。
- 签名用**专用 release keystore**，放在仓库外（例如 `~/.config/stackchan/`），
  由 `android/keystore.properties`（gitignored）指向；`.jks` 和密码不要提交/发给别人，离线备份。
- 换 keystore/包名 = 用户不能覆盖升级，只能卸载重装；`versionCode` 必须单调递增。
- 应用名「StackChan」是 M5Stack 的产品名；公开项目加免责声明，或改项目名。
- 验签：

  ```bash
  JAVA_HOME=<JDK 21> apksigner verify --print-certs app-release.apk
  ```

## 图标 / 启动图

```bash
python3 shell/tools/generate_assets.py
```

生成 legacy/round/adaptive 图标和 splash。设计是白色机器人头 + 黑屏 + 薄荷眼 + 珊瑚按钮/天线。

## 已知边界

- Tailscale 关闭或服务器掉线时，壳只能报「不可达」。
- WebView 的 localStorage 按 origin 隔离；setup 页的配置通过 `#cfg=` 交给远程页。
- **APK 不含 token / 服务器地址，可以分享**。
- Target SDK 36；其他机型如果状态栏遮挡，需要补 insets。
- UI 更新走服务端；壳/原生能力变化才需要重发 APK。
