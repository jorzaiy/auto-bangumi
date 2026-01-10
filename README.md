# Auto Bangumi

从 [Mikan](https://mikanime.tv/) RSS 订阅自动下载番剧，通过 Aria2 下载后上传到夸克网盘。

## 工作流程

```
Mikan RSS → 解析 torrent → 转换磁力链接 → Aria2 下载 → 上传到夸克网盘
```

## 功能

- 多 RSS 订阅管理（增删改查）
- 自动解析 Mikan RSS URL 提取 bangumiId 和 subgroupid
- **自动将 torrent 转换为磁力链接**
- 通过 Aria2 RPC 下载
- 下载完成后自动上传到夸克网盘（通过 OpenList API）
- 正则过滤（默认只下载 1080p）
- 历史记录去重，避免重复下载
- 支持启用/禁用单个订阅
- 支持 curl_cffi 绕过反爬虫（可选）

## 安装

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install feedparser requests python-dotenv

# 可选：安装 curl_cffi 绕过反爬虫
pip install curl_cffi
```

## 配置

复制 `.env.example` 为 `.env` 并修改：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# OpenList/Alist 配置
ALIST_HOST=http://127.0.0.1:5244
ALIST_TOKEN=your_alist_token_here
TARGET_PATH=/Anime

# Aria2 配置
ARIA2_HOST=http://localhost:6800/jsonrpc
ARIA2_SECRET=your_aria2_secret
DOWNLOAD_DIR=/root/downloads

# 正则过滤 (可选，留空则不过滤)
FILTER_REGEX=1080[pP]
```

### 配置说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ALIST_HOST` | OpenList/Alist 服务地址 | `http://127.0.0.1:5244` |
| `ALIST_TOKEN` | OpenList API Token | 空 |
| `TARGET_PATH` | 夸克网盘目标目录 | `/Anime` |
| `ARIA2_HOST` | Aria2 RPC 地址 | `http://localhost:6800/jsonrpc` |
| `ARIA2_SECRET` | Aria2 RPC 密钥 | 空 |
| `DOWNLOAD_DIR` | 本地下载目录 | `/root/downloads` |
| `FILTER_REGEX` | 正则过滤规则 | `1080[pP]` |
| `SUBSCRIPTIONS_FILE` | 订阅数据文件 | `subscriptions.json` |
| `HISTORY_FILE` | 下载历史文件 | `downloaded.json` |

## Aria2 配置

确保 Aria2 已安装并配置 RPC：

```bash
# 安装 aria2
apt install aria2

# 创建配置目录
mkdir -p ~/.aria2

# 创建配置文件
cat > ~/.aria2/aria2.conf << 'EOF'
# 基础配置
dir=/root/downloads
continue=true
max-concurrent-downloads=3
max-connection-per-server=16

# RPC 配置
enable-rpc=true
rpc-listen-all=false
rpc-listen-port=6800
rpc-secret=your_secret_here

# BT 配置
bt-enable-lpd=true
bt-max-peers=50
seed-time=0

# DHT 配置（重要：不依赖单一 tracker）
enable-dht=true
dht-listen-port=6881-6999

# Tracker 超时配置（避免频繁报错）
bt-tracker-connect-timeout=10
bt-tracker-timeout=60

# 公共 tracker（备用）
bt-tracker=udp://tracker.opentrackr.org:1337/announce,udp://open.stealth.si:80/announce,udp://exodus.desync.com:6969/announce,udp://tracker.torrent.eu.org:451/announce

# 日志级别（减少超时日志刷屏）
log-level=warn
EOF

# 启动 aria2（或配置 systemd 服务）
aria2c --conf-path=~/.aria2/aria2.conf -D
```

## 使用方法

### 订阅管理

```bash
# 添加订阅（注意使用 mikanime.tv 域名）
python auto_bangumi.py add "https://mikanime.tv/RSS/Bangumi?bangumiId=3633&subgroupid=534" --name "葬送的芙莉莲"

# 列出所有订阅
python auto_bangumi.py list

# 更新订阅
python auto_bangumi.py update 1 --name "新名称"   # 改名
python auto_bangumi.py update 1 --disable         # 禁用
python auto_bangumi.py update 1 --enable          # 启用

# 删除订阅（支持 ID 或名称）
python auto_bangumi.py remove 1
python auto_bangumi.py remove "葬送的芙莉莲"
```

### 运行下载检查

```bash
# 检查 RSS 更新并添加到 Aria2 下载
python auto_bangumi.py run

# 或直接运行（默认执行 run）
python auto_bangumi.py
```

### 上传已下载的文件

```bash
# 将下载完成的文件上传到夸克网盘
python auto_bangumi.py upload
```

### 定时任务

使用 cron 定时检查更新和上传：

```bash
# 编辑 crontab
crontab -e

# 每小时检查 RSS 更新
0 * * * * cd /root/auto-bangumi && .venv/bin/python auto_bangumi.py run >> run.log 2>&1

# 每 2 小时上传已完成的下载
0 */2 * * * cd /root/auto-bangumi && .venv/bin/python auto_bangumi.py upload >> upload.log 2>&1
```

**常用 cron 时间格式：**

| 格式 | 说明 |
|------|------|
| `* * * * *` | 每分钟 |
| `*/30 * * * *` | 每 30 分钟 |
| `0 * * * *` | 每小时 |
| `0 8 * * *` | 每天 8:00 |
| `0 8,20 * * *` | 每天 8:00 和 20:00 |
| `0 */6 * * *` | 每 6 小时 |

## 命令参考

| 命令 | 说明 |
|------|------|
| `add <url> [--name 名称]` | 添加新订阅 |
| `list` | 列出所有订阅 |
| `remove <id\|名称>` | 删除订阅 |
| `update <id\|名称> [选项]` | 更新订阅 |
| `run` | 检查 RSS 更新并下载 |
| `upload` | 上传已完成的下载到夸克 |

### update 命令选项

| 选项 | 说明 |
|------|------|
| `--name, -n` | 设置新名称 |
| `--url, -u` | 设置新 URL |
| `--enable` | 启用订阅 |
| `--disable` | 禁用订阅 |

## 数据文件

| 文件 | 说明 |
|------|------|
| `subscriptions.json` | 订阅列表 |
| `downloaded.json` | 已下载记录（用于去重） |

## 获取 Mikan RSS 地址

1. 访问 [mikanime.tv](https://mikanime.tv/)（注意：使用此域名，mikanani.tv 有反爬虫）
2. 搜索你想订阅的番剧
3. 选择字幕组
4. 复制页面上的 RSS 订阅地址

RSS 地址格式：`https://mikanime.tv/RSS/Bangumi?bangumiId=xxxx&subgroupid=xxxx`

## 获取 OpenList Token

1. 登录 OpenList 管理后台
2. 进入「设置」-「其他」
3. 复制「令牌」

## 注意事项

- **域名选择**：使用 `mikanime.tv` 而不是 `mikanani.tv`，后者有反爬虫保护
- **下载目录**：确保 Aria2 的下载目录 (`DOWNLOAD_DIR`) 与 OpenList 挂载的本地存储路径一致
- **上传后清理**：`upload` 命令会在上传成功后自动删除本地文件

## License

MIT
