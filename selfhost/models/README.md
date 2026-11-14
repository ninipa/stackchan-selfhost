# SenseVoiceSmall（本地 ASR）

`docker-compose.yml` 把 `models/SenseVoiceSmall/model.pt` 挂进 xiaozhi 容器作为本地 ASR 模型。
它 **893MB，不入库**；每台部署自己下载。

## 下载

```bash
pip install modelscope
./selfhost/models/download-model.sh
```

脚本会下到：

```
selfhost/models/SenseVoiceSmall/model.pt
```

如果下载到的是子目录（例如 `SenseVoiceSmall/iic/SenseVoiceSmall/model.pt`），把 `model.pt`
移到 `selfhost/models/SenseVoiceSmall/model.pt`，或者改 `docker-compose.yml` 的挂载路径。

## 许可

SenseVoiceSmall 的权重遵循 **FunASR Model Open Source License Agreement**，
不要随仓库分发；从 ModelScope 官方源下载。代码侧 FunASR 为 MIT。

## 为什么不用云 ASR

- 家庭对话不出局域网；
- 没有按次计费；
- 代价是容器常驻约 3GB 内存，重启后加载 8–10 秒。

## 验证

```bash
ls -lh selfhost/models/SenseVoiceSmall/model.pt
./selfhost/tools/doctor.sh
```

`doctor.sh` 会检查文件存在且 >100MB。
