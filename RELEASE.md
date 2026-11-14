# Release

公开仓库是 `M5Stack/StackChan` 的 fork；**发布时才创建 public fork**。GitHub 不允许把公开仓库的
fork 设为 private，所以不要提前建“private fork”；需要私有验证就用本地 staging 或另建 private 普通仓库。
最终仓库名：`ninipa/stackchan-selfhost`。

## 0. 发布前

- 外层工作区和 `stackchan/` 都已提交、`git status` 干净；
- 跑过 `TESTING.md` 的服务端/固件门禁；
- 没有 `deploy.env`、`.env`、`data/`、`dump/`、照片、keystore、token 在待发布树里；
- 确定版本/tag（示例 `v0.1.0`；固件版本仍是 `1.6.2`，APK 1.5/6）。

## 1. 组装 public fork 工作树

```bash
# 完整 clone（发布树需要完整 upstream 历史，不要 --depth 1）
git clone https://github.com/m5stack/StackChan.git /tmp/stackchan-public

cd /path/to/your/workspace
./tools/export-public.sh /tmp/stackchan-public --with-firmware --force
```

`--with-firmware` 会从本地 `stackchan/` 的 git 索引覆盖 `firmware/`（只复制已跟踪文件）；
不复制 `managed_components/`、`xiaozhi-esp32/`、`build*/`、`sdkconfig*`、`local.conf`。

## 2. 本地门禁

```bash
./tools/check-dist.sh /tmp/stackchan-public

# 只扫我们新增的伴生目录（上游 app/server 示例不参与）
gitleaks detect --source /tmp/stackchan-public --no-git --config /tmp/stackchan-public/.gitleaks.toml --no-banner --redact
gitleaks detect --source /tmp/stackchan-public/firmware --no-git --config /tmp/stackchan-public/firmware/.gitleaks.toml --no-banner --redact

# 服务端
cd /tmp/stackchan-public
python3 -m venv .venv
.venv/bin/pip install -r selfhost/setup/requirements.txt
.venv/bin/python -m unittest discover -s selfhost/tests
.venv/bin/python -m unittest discover -s selfhost/setup/tests

# 固件干净构建
cd firmware
cp local.conf.example local.conf
# 填测试 host/token；不要用生产 token
./tools/configure.sh
http_proxy=http://127.0.0.1:7890 https_proxy=http://127.0.0.1:7890 python3 fetch_repos.py
env -u PYTHONPATH SKIP_FETCH=1 ./tools/build.sh
```

## 3. 提交并推送 fork

```bash
cd /tmp/stackchan-public
git checkout -b custom
git add -A
git commit -m "StackChan self-host + custom firmware"
```

创建 public fork（在发布当天做；`gh` 已登录 `ninipa`）：

```bash
gh repo fork m5stack/StackChan --fork-name stackchan-selfhost --clone=false
git remote rename origin upstream
git remote add origin git@github.com:ninipa/stackchan-selfhost.git
git push -u origin custom
```

然后在 GitHub **Settings → Branches** 把默认分支设为 **`custom`**。否则陌生人 clone 默认是
upstream 的 `main`，看不到 README/改动。

## 4. Tag / Release

```bash
git tag -a v0.1.0 -m "StackChan v0.1.0"
git push origin v0.1.0

git archive --format=tar.gz --prefix=stackchan-v0.1.0/ v0.1.0 \
  > /tmp/stackchan-v0.1.0.tar.gz
shasum -a 256 /tmp/stackchan-v0.1.0.tar.gz
```

GitHub Release 附件：源码 tarball + SHA256；APK 可选（本地 release 签名，**不要上传 keystore**）。
Release notes 直接引用 `CHANGELOG.md`。

## 5. 发布后检查

- 在另一个目录 `git clone` 默认分支，按 README 走服务端/固件/App；
- 确认默认分支是 `custom`，README 里没有内网地址/token；
- 确认 `check-dist` / gitleaks 在公开树仍通过；
- 如果发现密钥已推送：立即轮换对应 key/token；公开历史不可靠删除，必要时删库重建。

## 已知限制

- GitHub public repo 的 fork 不能 private；需要私有阶段就先用本地 staging。
- 公开 fork 可能收到 PR/issue；本项目不做 CI/PR 队列，README 已说明。
- `StackChan` 是 M5Stack 产品名；公开项目保留“非官方、仅硬件兼容”的免责声明。
