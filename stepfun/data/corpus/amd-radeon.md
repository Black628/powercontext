# 在 AMD Radeon 平台部署 seekdb

## 申请资源

登录 AMD 平台账号，进入 Active Instance 申请算力，选择带 GPU 的实例启动。
实验环境启动后进入 Notebook，打开 Terminal 即可执行命令。

## 启动 seekdb

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
docker exec -it seekdb mysql -h127.0.0.1 -P2881 -uroot -e "select version();"
```

端口 2881 是 MySQL 协议端口，2886 是 obshell 图形面板。
镜像拉不动时可以换 quay.io/oceanbase/seekdb 或 ghcr.io/oceanbase/seekdb。

## 获取遥测 ID

部署成功后，在运行目录下的 run/telemetry.json 中读取 content.id，即 36 位 UUID。

```bash
python3 -c 'import json; print(json.load(open("run/telemetry.json"))["content"]["id"])'
```

## 资源要求

至少 1 核 CPU、2GB 内存、5GB 磁盘。内存不足时实例会启动失败，可通过
docker logs seekdb 查看具体报错。实验结束后记得 Active Instance → Destroy 释放环境。
