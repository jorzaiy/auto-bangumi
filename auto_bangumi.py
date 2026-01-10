import feedparser
import requests
import json
import os
import re
import argparse
import hashlib
import time
import glob
import shutil
from datetime import datetime
from urllib.parse import urlparse, parse_qs, quote
from dotenv import load_dotenv

# 尝试导入 curl_cffi 用于绕过反爬虫
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    print("提示: 安装 curl_cffi 可以绕过 mikanani.tv 的反爬虫 (pip install curl_cffi)")

# 加载 .env 文件
load_dotenv()

# --- 配置部分 ---
# Alist 配置
ALIST_HOST = os.getenv("ALIST_HOST", "http://127.0.0.1:5244")
ALIST_TOKEN = os.getenv("ALIST_TOKEN", "")
TARGET_PATH = os.getenv("TARGET_PATH", "/Anime")

# Aria2 配置
ARIA2_HOST = os.getenv("ARIA2_HOST", "http://localhost:6800/jsonrpc")
ARIA2_SECRET = os.getenv("ARIA2_SECRET", "")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/root/downloads")

# 数据文件
SUBSCRIPTIONS_FILE = os.getenv("SUBSCRIPTIONS_FILE", "subscriptions.json")
HISTORY_FILE = os.getenv("HISTORY_FILE", "downloaded.json")

# 正则过滤 (可选，比如只下 1080p)
FILTER_REGEX = os.getenv("FILTER_REGEX", r"1080[pP]")

# --- 订阅管理 ---

def load_subscriptions():
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return []
    with open(SUBSCRIPTIONS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_subscriptions(subs):
    with open(SUBSCRIPTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(subs, f, ensure_ascii=False, indent=2)

def parse_mikan_url(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    bangumi_id = params.get('bangumiId', [''])[0]
    subgroup_id = params.get('subgroupid', [''])[0]
    return bangumi_id, subgroup_id

def get_next_id(subs):
    if not subs:
        return 1
    return max(s['id'] for s in subs) + 1

def find_subscription(subs, identifier):
    try:
        sub_id = int(identifier)
        for sub in subs:
            if sub['id'] == sub_id:
                return sub
    except ValueError:
        pass
    for sub in subs:
        if sub['name'] == identifier:
            return sub
    return None

def add_subscription(url, name=None):
    subs = load_subscriptions()
    bangumi_id, subgroup_id = parse_mikan_url(url)
    for sub in subs:
        if sub['url'] == url:
            print(f"订阅已存在: #{sub['id']} {sub['name']}")
            return None
    new_sub = {
        'id': get_next_id(subs),
        'name': name or f"订阅_{bangumi_id}",
        'url': url,
        'bangumi_id': bangumi_id,
        'subgroup_id': subgroup_id,
        'enabled': True,
        'added_at': datetime.now().isoformat()
    }
    subs.append(new_sub)
    save_subscriptions(subs)
    print(f"已添加订阅 #{new_sub['id']}: {new_sub['name']}")
    return new_sub

def remove_subscription(identifier):
    subs = load_subscriptions()
    sub = find_subscription(subs, identifier)
    if not sub:
        print(f"未找到订阅: {identifier}")
        return False
    subs.remove(sub)
    save_subscriptions(subs)
    print(f"已删除订阅: #{sub['id']} {sub['name']}")
    return True

def update_subscription(identifier, name=None, url=None, enabled=None):
    subs = load_subscriptions()
    sub = find_subscription(subs, identifier)
    if not sub:
        print(f"未找到订阅: {identifier}")
        return False
    if name is not None:
        sub['name'] = name
    if url is not None:
        sub['url'] = url
        sub['bangumi_id'], sub['subgroup_id'] = parse_mikan_url(url)
    if enabled is not None:
        sub['enabled'] = enabled
    save_subscriptions(subs)
    status = "启用" if sub['enabled'] else "禁用"
    print(f"已更新订阅 #{sub['id']}: {sub['name']} [{status}]")
    return True

def list_subscriptions():
    subs = load_subscriptions()
    if not subs:
        print("暂无订阅，使用 add 命令添加")
        return
    print(f"{'ID':<4} {'名称':<20} {'状态':<6} {'bangumiId':<10} {'subgroupId':<10}")
    print("-" * 60)
    for sub in subs:
        status = "启用" if sub['enabled'] else "禁用"
        print(f"{sub['id']:<4} {sub['name']:<20} {status:<6} {sub['bangumi_id']:<10} {sub['subgroup_id']:<10}")

# --- 历史记录管理 ---

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False)

# --- Torrent 转磁力链接 ---

def bdecode(data):
    def decode_next(data, idx):
        char = chr(data[idx])
        if char == 'i':
            end = data.index(b'e', idx)
            return int(data[idx+1:end]), end + 1
        elif char == 'l':
            idx += 1
            result = []
            while chr(data[idx]) != 'e':
                val, idx = decode_next(data, idx)
                result.append(val)
            return result, idx + 1
        elif char == 'd':
            idx += 1
            result = {}
            while chr(data[idx]) != 'e':
                key, idx = decode_next(data, idx)
                if isinstance(key, bytes):
                    key = key.decode('utf-8', errors='replace')
                val, idx = decode_next(data, idx)
                result[key] = val
            return result, idx + 1
        elif char.isdigit():
            colon = data.index(b':', idx)
            length = int(data[idx:colon])
            start = colon + 1
            return data[start:start+length], start + length
        else:
            raise ValueError(f"Invalid bencode at {idx}")
    result, _ = decode_next(data, 0)
    return result

def bencode(data):
    if isinstance(data, int):
        return f'i{data}e'.encode()
    elif isinstance(data, bytes):
        return f'{len(data)}:'.encode() + data
    elif isinstance(data, str):
        encoded = data.encode('utf-8')
        return f'{len(encoded)}:'.encode() + encoded
    elif isinstance(data, list):
        return b'l' + b''.join(bencode(item) for item in data) + b'e'
    elif isinstance(data, dict):
        items = sorted(data.items())
        return b'd' + b''.join(bencode(k) + bencode(v) for k, v in items) + b'e'
    else:
        raise TypeError(f"Cannot bencode {type(data)}")

def torrent_to_magnet(torrent_url):
    try:
        if HAS_CURL_CFFI:
            resp = cffi_requests.get(torrent_url, impersonate="chrome", timeout=30)
        else:
            resp = requests.get(torrent_url, timeout=30)
        resp.raise_for_status()
        torrent_data = bdecode(resp.content)
        info = torrent_data.get('info', {})
        info_encoded = bencode(info)
        info_hash = hashlib.sha1(info_encoded).hexdigest()
        name = info.get('name', b'')
        if isinstance(name, bytes):
            name = name.decode('utf-8', errors='replace')
        magnet = f"magnet:?xt=urn:btih:{info_hash}"
        if name:
            magnet += f"&dn={quote(name)}"
        if 'announce' in torrent_data:
            announce = torrent_data['announce']
            if isinstance(announce, bytes):
                announce = announce.decode('utf-8', errors='replace')
            magnet += f"&tr={quote(announce)}"
        return magnet
    except Exception as e:
        print(f"  转换磁链失败: {e}")
        return None

# --- RSS 获取 (绕过反爬虫) ---

def fetch_rss(url):
    if HAS_CURL_CFFI:
        try:
            resp = cffi_requests.get(url, impersonate="chrome", timeout=30)
            return feedparser.parse(resp.text)
        except Exception as e:
            print(f"  curl_cffi 获取失败: {e}，尝试普通方式")
    return feedparser.parse(url)

# --- Aria2 RPC ---

def aria2_rpc(method, params=None):
    payload = {
        "jsonrpc": "2.0",
        "id": "auto_bangumi",
        "method": method,
        "params": params or []
    }
    if ARIA2_SECRET:
        if params:
            payload["params"] = [f"token:{ARIA2_SECRET}"] + list(params)
        else:
            payload["params"] = [f"token:{ARIA2_SECRET}"]
    try:
        resp = requests.post(ARIA2_HOST, json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"  Aria2 RPC 错误: {e}")
        return None

def add_to_aria2(uri, filename):
    options = {"dir": DOWNLOAD_DIR}
    result = aria2_rpc("aria2.addUri", [[uri], options])
    if result and "result" in result:
        gid = result["result"]
        print(f"✅ 已添加到 Aria2: {filename} (GID: {gid})")
        return gid
    else:
        print(f"❌ 添加到 Aria2 失败: {result}")
        return None

def get_aria2_status(gid):
    result = aria2_rpc("aria2.tellStatus", [gid])
    if result and "result" in result:
        return result["result"]
    return None

def get_aria2_downloading_files():
    """获取 Aria2 中正在下载和等待中的文件路径列表"""
    downloading_files = set()
    # 获取正在下载的任务
    active = aria2_rpc("aria2.tellActive", [["files"]])
    if active and "result" in active:
        for task in active["result"]:
            for f in task.get("files", []):
                path = f.get("path", "")
                if path:
                    downloading_files.add(path)
    # 获取等待中的任务
    waiting = aria2_rpc("aria2.tellWaiting", [0, 100, ["files"]])
    if waiting and "result" in waiting:
        for task in waiting["result"]:
            for f in task.get("files", []):
                path = f.get("path", "")
                if path:
                    downloading_files.add(path)
    return downloading_files

# --- 上传到夸克 ---

def upload_to_alist(local_path, remote_path):
    url = f"{ALIST_HOST}/api/fs/put"
    file_size = os.path.getsize(local_path)
    headers = {
        "Authorization": ALIST_TOKEN,
        "File-Path": quote(remote_path, safe=''),
        "Content-Length": str(file_size),
    }
    # 根据文件大小动态调整超时时间（每 100MB 增加 60 秒）
    timeout = max(300, (file_size // (100 * 1024 * 1024)) * 60 + 300)
    try:
        with open(local_path, 'rb') as f:
            resp = requests.put(url, headers=headers, data=f, timeout=timeout)
            res_data = resp.json()
            if res_data.get('code') == 200:
                print(f"✅ 上传成功: {remote_path}")
                return True
            else:
                print(f"❌ 上传失败: {res_data}")
                return False
    except Exception as e:
        print(f"⚠️ 上传错误: {e}")
        return False

def process_completed_downloads():
    if not os.path.exists(DOWNLOAD_DIR):
        return
    # 获取 Aria2 中正在下载的文件，避免上传不完整的文件
    downloading_files = get_aria2_downloading_files()
    files = glob.glob(os.path.join(DOWNLOAD_DIR, "*"))
    for filepath in files:
        if filepath.endswith(".aria2"):
            continue
        # 检查文件是否正在下载中
        if filepath in downloading_files:
            print(f"⏳ 跳过 (下载中): {os.path.basename(filepath)}")
            continue
        filename = os.path.basename(filepath)
        remote_path = f"{TARGET_PATH}/{filename}"
        print(f"正在上传: {filename}")
        if upload_to_alist(filepath, remote_path):
            try:
                if os.path.isfile(filepath):
                    os.remove(filepath)
                elif os.path.isdir(filepath):
                    shutil.rmtree(filepath)
                print(f"🗑️ 已清理本地文件: {filename}")
            except Exception as e:
                print(f"⚠️ 清理失败: {e}")

# --- 主逻辑 ---

def check_single_subscription(sub, history):
    print(f"\n检查订阅: {sub['name']}")
    feed = fetch_rss(sub['url'])
    if not feed.entries:
        print(f"  警告: 未获取到任何条目，可能 RSS 获取失败")
        return []
    new_items = []
    for entry in reversed(feed.entries):
        title = entry.title
        guid = entry.get('guid', entry.get('id', entry.link))
        if guid in history:
            continue
        if FILTER_REGEX and not re.search(FILTER_REGEX, title):
            print(f"  跳过 (不匹配规则): {title}")
            continue
        print(f"  发现新番剧: {title}")
        magnet_link = None
        if hasattr(entry, 'enclosures') and entry.enclosures:
            torrent_url = entry.enclosures[0].get('href', '')
            if torrent_url:
                print(f"  转换磁链中...")
                magnet_link = torrent_to_magnet(torrent_url)
                if magnet_link:
                    print(f"  磁链: {magnet_link[:60]}...")
        if magnet_link and add_to_aria2(magnet_link, title):
            new_items.append(guid)
        elif not magnet_link:
            print(f"  跳过 (无法获取磁链)")
    return new_items

def run_check():
    subs = load_subscriptions()
    enabled_subs = [s for s in subs if s['enabled']]
    if not enabled_subs:
        print("暂无启用的订阅，使用 add 命令添加")
        return
    print(f"开始检查 RSS 更新... (共 {len(enabled_subs)} 个订阅)")
    history = load_history()
    new_history = history.copy()
    for i, sub in enumerate(enabled_subs, 1):
        print(f"\n[{i}/{len(enabled_subs)}]", end="")
        new_items = check_single_subscription(sub, history)
        new_history.extend(new_items)
    save_history(new_history)
    print("\n检查完成!")

def run_upload():
    print("检查已完成的下载...")
    process_completed_downloads()
    print("上传处理完成!")

# --- CLI 入口 ---

def main():
    parser = argparse.ArgumentParser(description='Mikan 番剧 RSS 订阅管理与自动下载工具')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    add_parser = subparsers.add_parser('add', help='添加新订阅')
    add_parser.add_argument('url', help='RSS 订阅地址')
    add_parser.add_argument('--name', '-n', help='订阅名称 (可选)')
    subparsers.add_parser('list', help='列出所有订阅')
    remove_parser = subparsers.add_parser('remove', help='删除订阅')
    remove_parser.add_argument('identifier', help='订阅 ID 或名称')
    update_parser = subparsers.add_parser('update', help='更新订阅')
    update_parser.add_argument('identifier', help='订阅 ID 或名称')
    update_parser.add_argument('--name', '-n', help='新名称')
    update_parser.add_argument('--url', '-u', help='新 URL')
    update_parser.add_argument('--enable', action='store_true', help='启用订阅')
    update_parser.add_argument('--disable', action='store_true', help='禁用订阅')
    subparsers.add_parser('run', help='运行下载检查')
    subparsers.add_parser('upload', help='上传已下载的文件到夸克')
    args = parser.parse_args()
    if args.command == 'add':
        add_subscription(args.url, args.name)
    elif args.command == 'list':
        list_subscriptions()
    elif args.command == 'remove':
        remove_subscription(args.identifier)
    elif args.command == 'update':
        enabled = None
        if args.enable:
            enabled = True
        elif args.disable:
            enabled = False
        update_subscription(args.identifier, name=args.name, url=args.url, enabled=enabled)
    elif args.command == 'run' or args.command is None:
        run_check()
    elif args.command == 'upload':
        run_upload()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
